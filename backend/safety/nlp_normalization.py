"""Deterministic natural-language safety-concept extraction layer.

This module sits BEFORE the existing deterministic Safety Engine
(backend/safety/engine.py, backend/safety/rules.py) in the triage pipeline.
It converts free-text patient input into structured safety signals -- never
a final care recommendation. Concretely, it detects a small, controlled set
of high-confidence emergency-concept phrases (and typo/synonym variants of
them) in free text, and maps each match to one of the EXISTING canonical
alias phrases already defined on EMERGENCY_WARNING_SIGN_RULES in
backend/safety/rules.py. Those canonical phrases are then screened by the
unmodified, authoritative evaluate_safety() exactly as if the patient had
typed that exact phrase -- this module never sets is_emergency itself, never
invents a new rule/category, and never touches backend/safety/engine.py or
backend/safety/rules.py.

This is NOT an LLM or generative model. It is:
  1. text normalization (lowercasing, punctuation/contraction handling)
  2. controlled phrase/synonym matching
  3. conservative, bounded-edit-distance typo tolerance for a small curated
     vocabulary (never general-purpose fuzzy matching)
  4. a fixed mapping from detected concept -> existing rule-engine alias

It does not diagnose the patient. It only detects that a red-flag concept is
present in what the patient described, so the existing deterministic rules
can evaluate it. All downstream emergency/urgency wording, the 911 messaging,
and the final EMERGENCY/URGENT_CARE/PRIMARY_CARE/TELEHEALTH decision remain
entirely owned by backend/safety/engine.py and backend/services/triage_service.py.
"""

from __future__ import annotations

import itertools
import re
from typing import Iterable

_APOSTROPHE = re.compile(r"[‘’'`]")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_WHITESPACE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Lowercase and strip punctuation, removing contraction apostrophes
    entirely (rather than turning them into a word boundary) so "can't"
    normalizes to "cant" as a single token, not "can" + "t"."""
    if not isinstance(text, str):
        return ""
    lowered = text.lower()
    without_apostrophes = _APOSTROPHE.sub("", lowered)
    cleaned = _NON_ALNUM.sub(" ", without_apostrophes)
    return _WHITESPACE.sub(" ", cleaned).strip()


def _phrase_in_text(text: str, phrase: str) -> bool:
    """Word-boundary-safe substring match, mirroring backend/safety/engine.py's
    own _phrase_matches so behavior stays predictable and consistent."""
    if not text or not phrase:
        return False
    return f" {phrase} " in f" {text} "


# ---------------------------------------------------------------------------
# Controlled vocabulary: only words that appear in the curated phrases below.
# Typo correction is only ever attempted against this small, fixed list --
# never against arbitrary/general medical vocabulary -- to keep false
# positives extremely unlikely.
# ---------------------------------------------------------------------------
_CRITICAL_WORDS: tuple[str, ...] = (
    "heart", "attack", "cardiac", "arrest", "crushing", "severe", "chest",
    "pressure", "tightness", "crushed", "breathe", "breathing", "breath",
    "shortness", "difficulty", "struggling", "cannot", "cant", "catch",
    "stroke", "drooping", "facial", "face", "slurred", "speech", "speaking",
    "sudden", "suddenly", "weakness", "numbness", "properly", "passed",
    "passing", "pass", "fainted", "fainting", "lost", "consciousness",
    "blackout", "blacked",
)


def _levenshtein(a: str, b: str) -> int:
    """Standard edit distance, bounded by short inputs (safety phrases only)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous_row = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current_row = [i]
        for j, char_b in enumerate(b, start=1):
            insert_cost = current_row[j - 1] + 1
            delete_cost = previous_row[j] + 1
            substitute_cost = previous_row[j - 1] + (char_a != char_b)
            current_row.append(min(insert_cost, delete_cost, substitute_cost))
        previous_row = current_row
    return previous_row[-1]


def _typo_threshold(word_length: int) -> int:
    """Conservative edit-distance budget: tighter for shorter words."""
    return 1 if word_length <= 7 else 2


def _closest_critical_word(token: str) -> str | None:
    """Return a critical-vocabulary word within a conservative edit-distance
    budget of ``token``, or None. Only attempted for tokens of length >= 4
    (shorter tokens are too easy to accidentally collide with an unrelated
    word) and only against critical words of a similar length, to avoid
    matching unrelated vocabulary."""
    if len(token) < 4 or token in _CRITICAL_WORDS:
        return token if token in _CRITICAL_WORDS else None
    threshold = _typo_threshold(len(token))
    best_match: str | None = None
    best_distance = threshold + 1
    for candidate in _CRITICAL_WORDS:
        if abs(len(candidate) - len(token)) > 2:
            continue
        distance = _levenshtein(token, candidate)
        if distance <= threshold and distance < best_distance:
            best_match = candidate
            best_distance = distance
    return best_match


def _typo_corrected(text: str) -> str:
    """Replace only tokens that closely resemble a known critical safety
    word with that word's canonical spelling; every other token (including
    ordinary filler words) is left completely untouched."""
    corrected_tokens = []
    for token in text.split(" "):
        if not token:
            continue
        match = _closest_critical_word(token)
        corrected_tokens.append(match if match else token)
    return " ".join(corrected_tokens)


# ---------------------------------------------------------------------------
# Controlled phrase lists. Every phrase here is pre-normalized to the same
# form _normalize()/_typo_corrected() produce. Each concept group maps to
# ONE existing, unmodified alias phrase already recognized by
# backend/safety/rules.py -- these append targets are never new rule text.
# ---------------------------------------------------------------------------
CARDIAC_EMERGENCY_CONCERN = "CARDIAC_EMERGENCY_CONCERN"
CHEST_PAIN_PRESSURE = "CHEST_PAIN_PRESSURE"
SHORTNESS_OF_BREATH = "SHORTNESS_OF_BREATH"
STROKE_WARNING = "STROKE_WARNING"
SYNCOPE = "SYNCOPE"

# (concept label, phrases that indicate it, existing rules.py alias to append)
_CONCEPT_GROUPS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        CARDIAC_EMERGENCY_CONCERN,
        (
            "heart attack",
            "possible heart attack",
            "having a heart attack",
            "cardiac arrest",
        ),
        "chest pain",
    ),
    (
        CHEST_PAIN_PRESSURE,
        (
            "chest pain",
            "chest pressure",
            "crushing chest pain",
            "severe chest pain",
            "severe chest pressure",
            "chest tightness",
            "chest feels crushed",
            "my chest feels crushed",
            # Same as the existing backend/safety/rules.py RF-CARDIO-001
            # aliases -- added here too so the NLP layer's own phrase list
            # is self-consistent with what the engine already recognizes,
            # not reliant on the engine's independent literal scan alone.
            "pain in my chest",
            "pressure in my chest",
        ),
        "chest pain",
    ),
    (
        SHORTNESS_OF_BREATH,
        (
            "cannot breathe",
            "cant breathe",
            "difficulty breathing",
            "severe difficulty breathing",
            "severe trouble breathing",
            "struggling to breathe",
            "shortness of breath",
            "severe shortness of breath",
            "cant catch my breath",
            "catch my breath",
        ),
        "cannot breathe",
    ),
    (
        STROKE_WARNING,
        (
            "stroke",
            "possible stroke",
            "having a stroke",
            "stroke symptoms",
            "stroke signs",
            "face drooping",
            "facial drooping",
            "slurred speech",
            "sudden trouble speaking",
            "sudden weakness on one side",
            "numbness on one side",
            "difficulty speaking",
            "trouble speaking",
            "cant speak properly",
        ),
        "stroke symptoms",
    ),
    (
        SYNCOPE,
        (
            "passed out",
            "pass out",
            "passing out",
            "fainted",
            "fainting",
            "lost consciousness",
            "loss of consciousness",
            "blackout",
            "blacked out",
            "black out",
            "blacking out",
        ),
        "passed out",
    ),
)


# ---------------------------------------------------------------------------
# Structural anchor-group matching: for phrasing variants that literal phrase
# matching cannot express -- connector words ("in", "with", "to") inserted
# between two concept-defining words, or those words in a different order --
# a concept can ALSO be declared as a set of "anchor groups": small sets of
# synonymous words, where a match requires at least one word from EVERY group
# to appear within a bounded token window of each other, regardless of order.
# This stays fully deterministic and auditable (presence/absence within a
# window, never a similarity score), and turns "add one more phrasing" into
# "add one more word to a group" instead of one more regex/phrase.
# ---------------------------------------------------------------------------
_ANCHOR_GROUP_WINDOW = 4  # max token span a match may cover

_ANCHOR_GROUP_PATTERNS: tuple[tuple[str, tuple[tuple[str, ...], ...]], ...] = (
    # "difficulty breathing" / "difficulty in breathing" / "having trouble breathing"
    (SHORTNESS_OF_BREATH, (("difficulty", "trouble"), ("breathe", "breathing", "breath"))),
    # "struggling to breathe" / "struggling with breathing"
    (SHORTNESS_OF_BREATH, (("struggling",), ("breathe", "breathing", "breath"))),
    # "I can barely breathe"
    (SHORTNESS_OF_BREATH, (("barely",), ("breathe", "breathing", "breath"))),
    # "unable to breathe"
    (SHORTNESS_OF_BREATH, (("unable",), ("breathe", "breathing", "breath"))),
    # "unbearable/severe/extreme breathing problem"
    (SHORTNESS_OF_BREATH, (("unbearable", "severe", "extreme", "intense"), ("breathing", "breath"), ("problem",))),

    # "something is wrong with my heart" / "my heart feels very wrong" --
    # genuine first-person concern about one's own heart (FIRST_PERSON_CONCERN
    # anchor "wrong" + CARDIAC_ANCHOR "heart"), order-independent, so hedging
    # ("I think", "seriously") never breaks the match.
    (CARDIAC_EMERGENCY_CONCERN, (("wrong",), ("heart",))),

    # "crushing pain in my chest" / "crushing feeling in my chest" (reordered
    # "crushing chest pain"). Bare co-occurrence, no severity gate, for the
    # same reason "chest pain"/"chest pressure" already require none: this
    # product's existing design treats any chest-pain-family mention as an
    # unconditional signal, not just severe ones.
    (CHEST_PAIN_PRESSURE, (("crushing",), ("chest",))),
    # "my chest feels extremely tight" (reordered/different word form of
    # "chest tightness").
    (CHEST_PAIN_PRESSURE, (("chest",), ("tight", "tightness", "tightening"))),
    # "chest hurts" / "my chest is hurting".
    (CHEST_PAIN_PRESSURE, (("chest",), ("hurt", "hurts", "hurting"))),

    # "my speech suddenly became slurred" (reordered "slurred speech").
    (STROKE_WARNING, (("speech",), ("slurred",))),
    # "one side of my face is drooping" (reordered "face drooping").
    (STROKE_WARNING, (("face", "facial"), ("drooping",))),
    # "weakness on one side of my body" (missing "sudden", extra words after
    # "side" that break the exact literal phrase "sudden weakness on one side").
    (STROKE_WARNING, (("weakness",), ("side",))),
    # "my face suddenly feels numb on one side" (adjective "numb", not the
    # literal alias's noun form "numbness").
    (STROKE_WARNING, (("numb", "numbness"), ("side",))),
    # "I suddenly can't move my left arm" -- sudden inability to move a limb
    # or side is a recognized FAST stroke warning sign distinct from the
    # existing "weakness"/"numbness" phrasing.
    (STROKE_WARNING, (("cant", "cannot", "unable"), ("move",), ("arm", "leg", "side", "face", "body"))),
)


def _tokens(text: str) -> list[str]:
    return text.split(" ") if text else []


def _anchor_groups_match(tokens: list[str], groups: tuple[tuple[str, ...], ...], window: int) -> bool:
    """True if every group has at least one matching word among ``tokens``,
    and some combination of those matches (one word per group) spans no more
    than ``window`` tokens, in any order."""
    positions_per_group: list[list[int]] = []
    for group in groups:
        positions = [index for index, token in enumerate(tokens) if token in group]
        if not positions:
            return False
        positions_per_group.append(positions)

    return any(
        max(combo) - min(combo) <= window
        for combo in itertools.product(*positions_per_group)
    )


# ---------------------------------------------------------------------------
# Reporting / third-party context guard: a small, explicit set of phrases
# indicating the text describes someone else's history, a past event, or
# general information-seeking rather than the patient's own current
# symptom. When present, detection is suppressed for that input entirely.
# Intentionally narrow (only unambiguous framings) -- a false suppression is
# worse than missing a structural variant, so this never guesses.
# ---------------------------------------------------------------------------
_REPORTING_CONTEXT_PHRASES: tuple[str, ...] = (
    "read about", "reading about", "studying", "learning about", "learned about",
    "want to learn about", "want to know about", "curious about", "researching",
    "an article about", "a book about", "watched a video about", "saw a video about",
)

_THIRD_PARTY_PATTERN = re.compile(
    r"\bmy (mother|father|mom|dad|sister|brother|friend|wife|husband|"
    r"grandmother|grandfather|grandma|grandpa|son|daughter|neighbor|colleague)\b"
    r".*\b(had|has had|experienced|went through|suffered)\b"
)


def _is_reporting_or_third_party_context(normalized_text: str) -> bool:
    if any(_phrase_in_text(normalized_text, phrase) for phrase in _REPORTING_CONTEXT_PHRASES):
        return True
    return bool(_THIRD_PARTY_PATTERN.search(normalized_text))


# ---------------------------------------------------------------------------
# Question/definitional framing guard: a narrow, separate guard for general
# questions about a concept ("What are the symptoms of a heart attack?")
# rather than a reported personal experience. Deliberately requires the
# ABSENCE of any first-person marker anywhere in the text, so a genuine
# complaint that happens to also contain a question ("Is this a heart
# attack? I have crushing chest pain") is never suppressed -- only text that
# is purely informational in framing.
# ---------------------------------------------------------------------------
_QUESTION_FRAMING_PHRASES: tuple[str, ...] = (
    "what are the symptoms of", "what is the symptom of", "what causes",
    "signs of a", "symptoms of a", "how do you know if",
)

_FIRST_PERSON_MARKERS: frozenset[str] = frozenset({"i", "im", "ive", "id", "my", "me", "myself"})


def _is_question_framing_without_first_person(normalized_text: str) -> bool:
    if not any(_phrase_in_text(normalized_text, phrase) for phrase in _QUESTION_FRAMING_PHRASES):
        return False
    return not (set(_tokens(normalized_text)) & _FIRST_PERSON_MARKERS)


def detect_safety_concepts(text: str) -> set[str]:
    """Return the set of controlled safety concepts detected in free text.

    Detection fires on the fixed phrase list (exact match after
    normalization, or a conservative typo-corrected match against that same
    fixed list) or the structural anchor-group patterns above -- never on
    generic single words like "heart" or "breathing" in isolation, so
    routine mentions ("heart health", "breathing is normal", "breathing
    exercise") do not trigger anything. Reporting/third-party context (e.g.
    "I read about difficulty breathing", "my mother had difficulty
    breathing") suppresses detection entirely.
    """
    normalized = _normalize(text)
    if not normalized:
        return set()

    if _is_reporting_or_third_party_context(normalized):
        return set()
    if _is_question_framing_without_first_person(normalized):
        return set()

    candidates = {normalized}
    corrected = _typo_corrected(normalized)
    if corrected != normalized:
        candidates.add(corrected)

    detected: set[str] = set()
    for concept, phrases, _alias in _CONCEPT_GROUPS:
        for phrase in phrases:
            if any(_phrase_in_text(candidate, phrase) for candidate in candidates):
                detected.add(concept)
                break

    for concept, groups in _ANCHOR_GROUP_PATTERNS:
        if concept in detected:
            continue
        if any(_anchor_groups_match(_tokens(candidate), groups, _ANCHOR_GROUP_WINDOW) for candidate in candidates):
            detected.add(concept)

    return detected


def extract_safety_concept_phrases(*texts: Iterable[str] | str) -> list[str]:
    """Scan one or more pieces of free text for controlled safety concepts
    and return the corresponding EXISTING backend/safety/rules.py alias
    phrases (deduplicated, order-preserving).

    The caller is expected to merge the returned phrases into the
    "associatedSymptoms" passed to the unmodified evaluate_safety() -- this
    function never decides is_emergency and never persists a diagnosis; it
    only surfaces "red-flag concept detected" signals for the existing
    deterministic rules to evaluate.
    """
    flat_texts: list[str] = []
    for item in texts:
        if isinstance(item, str):
            flat_texts.append(item)
        elif item is not None:
            flat_texts.extend(value for value in item if isinstance(value, str))

    detected_concepts: set[str] = set()
    for text in flat_texts:
        detected_concepts |= detect_safety_concepts(text)

    alias_by_concept = {concept: alias for concept, _phrases, alias in _CONCEPT_GROUPS}
    ordered_concepts = [concept for concept, _phrases, _alias in _CONCEPT_GROUPS if concept in detected_concepts]

    phrases: list[str] = []
    for concept in ordered_concepts:
        alias = alias_by_concept[concept]
        if alias not in phrases:
            phrases.append(alias)
    return phrases
