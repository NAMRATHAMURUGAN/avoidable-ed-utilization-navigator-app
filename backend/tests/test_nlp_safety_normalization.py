"""Tests for the deterministic natural-language safety-concept extraction
layer (backend/safety/nlp_normalization.py).

This layer sits in front of the existing, unmodified deterministic Safety
Engine (backend/safety/engine.py, backend/safety/rules.py): it only ever
detects controlled, high-confidence emergency-concept phrases in free text
and maps them to EXISTING rule-engine alias phrases. These tests verify the
extraction layer in isolation; test_triage_free_text_safety.py verifies the
full pipeline (free text -> extraction -> existing evaluate_safety()).
"""

from __future__ import annotations

import unittest

from backend.safety.nlp_normalization import (
    CARDIAC_EMERGENCY_CONCERN,
    CHEST_PAIN_PRESSURE,
    SHORTNESS_OF_BREATH,
    STROKE_WARNING,
    SYNCOPE,
    detect_safety_concepts,
    extract_safety_concept_phrases,
)


class DetectSafetyConceptsTests(unittest.TestCase):
    """Direct concept-detection tests, including the required typo cases."""

    def test_cardiac_emergency_phrases(self) -> None:
        for text in (
            "heart attack",
            "possible heart attack",
            "cardiac arrest",
            "I think I'm having a heart attack",
            "mild chest pain but I think I'm having a heart attack",
        ):
            with self.subTest(text=text):
                self.assertIn(CARDIAC_EMERGENCY_CONCERN, detect_safety_concepts(text))

    def test_chest_pain_pressure_phrases(self) -> None:
        for text in (
            "crushing chest pain",
            "severe chest pain",
            "severe chest pressure",
            "chest tightness",
            "my chest feels crushed",
        ):
            with self.subTest(text=text):
                self.assertIn(CHEST_PAIN_PRESSURE, detect_safety_concepts(text))

    def test_breathing_emergency_phrases(self) -> None:
        for text in (
            "can't breathe",
            "cannot breathe",
            "difficulty breathing",
            "severe difficulty breathing",
            "struggling to breathe",
            "shortness of breath",
            "severe shortness of breath",
            "can't catch my breath",
        ):
            with self.subTest(text=text):
                self.assertIn(SHORTNESS_OF_BREATH, detect_safety_concepts(text))

    def test_stroke_warning_phrases(self) -> None:
        for text in (
            "stroke",
            "possible stroke",
            "I think I'm having a stroke",
            "face drooping",
            "facial drooping",
            "slurred speech",
            "sudden trouble speaking",
            "sudden weakness on one side",
            "numbness on one side",
            "I suddenly can't speak properly",
        ):
            with self.subTest(text=text):
                self.assertIn(STROKE_WARNING, detect_safety_concepts(text))

    def test_syncope_phrases(self) -> None:
        for text in (
            "passed out",
            "passing out",
            "fainted",
            "fainting",
            "lost consciousness",
            "blackout",
            "I just passed out",
        ):
            with self.subTest(text=text):
                self.assertIn(SYNCOPE, detect_safety_concepts(text))

    def test_typo_tolerance_required_examples(self) -> None:
        self.assertIn(CARDIAC_EMERGENCY_CONCERN, detect_safety_concepts("hert attack"))
        self.assertIn(CHEST_PAIN_PRESSURE, detect_safety_concepts("chest presure"))
        self.assertIn(SHORTNESS_OF_BREATH, detect_safety_concepts("shortnes of breath"))
        self.assertIn(SYNCOPE, detect_safety_concepts("faintingg"))

    def test_no_false_positive_on_routine_text(self) -> None:
        for text in (
            "mild cough",
            "fever for two days",
            "I want information about heart health",
            "I want to learn about heart health",
            "my breathing is normal",
            "I had a headache yesterday",
            "My chest feels slightly uncomfortable after exercise",
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_safety_concepts(text), set())

    def test_no_false_positive_on_blank_or_non_string_input(self) -> None:
        self.assertEqual(detect_safety_concepts(""), set())
        self.assertEqual(detect_safety_concepts("   "), set())
        self.assertEqual(detect_safety_concepts(None), set())  # type: ignore[arg-type]

    def test_typo_correction_does_not_match_unrelated_words(self) -> None:
        # "check" is close in spelling to "chest" but must not be treated as
        # a chest-related concept -- fuzzy matching is conservative.
        self.assertEqual(detect_safety_concepts("please check my account balance"), set())
        # "cough" must not fuzzy-match into any critical vocabulary word.
        self.assertEqual(detect_safety_concepts("mild cough and a runny nose"), set())

    def test_breathing_natural_language_variations(self) -> None:
        """Structural anchor-group matching: connector words ("in", "with",
        "to") and reordering that defeat literal phrase matching."""
        for text in (
            "difficulty breathing",
            "difficulty in breathing",
            "severe difficulty breathing",
            "severe difficulty in breathing",
            "unbearable difficulty breathing",
            "unbearable difficulty in breathing",
            "extreme difficulty breathing",
            "struggling to breathe",
            "struggling with breathing",
            "having trouble breathing",
            "I can barely breathe",
            "I cannot catch my breath",
            "can't breathe",
            "unable to breathe",
            "severe shortness of breath",
            "unbearable breathing problem",
        ):
            with self.subTest(text=text):
                self.assertIn(SHORTNESS_OF_BREATH, detect_safety_concepts(text))

    def test_reporting_and_third_party_context_is_not_flagged(self) -> None:
        """Text describing someone else's history or general
        information-seeking must not be treated as the patient's own
        active symptom, even when it contains an otherwise-matching phrase."""
        for text in (
            "breathing exercise",
            "breathing technique",
            "I want to learn about breathing exercises",
            "I read about difficulty breathing",
            "My mother had difficulty breathing last year",
            "I am studying heart attack symptoms",
            "How does breathing work?",
            "I watched a video about shortness of breath",
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_safety_concepts(text), set())

    def test_cardiac_chest_natural_language_variations(self) -> None:
        """Reordered/colloquial cardiac-chest phrasings the literal phrase
        list alone cannot express (audit follow-up)."""
        for text in (
            "in my chest there's crushing pain",
            "pain in my chest",
            "pressure in my chest",
            "severe pressure in my chest",
            "I feel intense pressure in my chest and I'm sweating",
            "my chest feels extremely tight",
            "chest hurts",
            "I've had this crushing feeling in my chest for the last 20 minutes and it's radiating to my arm",
        ):
            with self.subTest(text=text):
                self.assertIn(CHEST_PAIN_PRESSURE, detect_safety_concepts(text))

    def test_vague_cardiac_references_intentionally_not_flagged(self) -> None:
        """Generic "something's wrong with my heart" framing is not a
        specific recognized warning sign -- intentionally excluded per
        product direction, not a bug."""
        for text in (
            "I think something is wrong with my heart",
            "my heart feels like it's going to explode",
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_safety_concepts(text), set())

    def test_stroke_natural_language_variations(self) -> None:
        """Reordered/colloquial stroke phrasings (audit follow-up)."""
        for text in (
            "my speech suddenly became slurred",
            "I suddenly can't move my left arm",
            "one side of my face is drooping",
            "weakness on one side of my body",
            "my face suddenly feels numb on one side",
            "About ten minutes ago my face started drooping on the right side and my words are coming out slurred",
        ):
            with self.subTest(text=text):
                self.assertIn(STROKE_WARNING, detect_safety_concepts(text))

    def test_ambiguous_stroke_phrasing_intentionally_not_flagged(self) -> None:
        """"can't talk right now" is an extremely common, unrelated phrase
        ("I can't talk right now, I'm busy") -- too high a false-positive
        risk to treat as a stroke warning sign."""
        self.assertEqual(detect_safety_concepts("can't talk right"), set())

    def test_syncope_natural_language_variations(self) -> None:
        """"Blacked out" word-form variant and its typo (audit follow-up)."""
        for text in ("I blacked out", "blacked out", "blackd out"):
            with self.subTest(text=text):
                self.assertIn(SYNCOPE, detect_safety_concepts(text))

    def test_narrative_syncope_descriptions_are_a_known_gap(self) -> None:
        """Narrative descriptions that imply unconsciousness without using
        any recognized trigger word ("woke up on the floor", "everything
        went black") are outside what a controlled, deterministic phrase/
        anchor-group matcher can safely generalize to without materially
        raising false-positive risk. Documented gap, not silently dropped."""
        for text in (
            "I just woke up on the floor and don't remember what happened",
            "I was standing in line and then everything went black and I woke up on the ground a minute later",
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_safety_concepts(text), set())

    def test_question_framing_without_first_person_is_not_flagged(self) -> None:
        """General informational questions ("What are the symptoms of...")
        with no first-person marker anywhere are not a personal complaint."""
        for text in (
            "What are the symptoms of a heart attack?",
            "What causes chest pain?",
        ):
            with self.subTest(text=text):
                self.assertEqual(detect_safety_concepts(text), set())

    def test_question_framing_guard_does_not_suppress_genuine_complaints(self) -> None:
        """A genuine first-person complaint must never be suppressed just
        because it also contains a question -- safety recall takes priority
        over the question-framing guard."""
        concepts = detect_safety_concepts("Is this a heart attack? I have crushing chest pain")
        self.assertIn(CARDIAC_EMERGENCY_CONCERN, concepts)
        self.assertIn(CHEST_PAIN_PRESSURE, concepts)


class ExtractSafetyConceptPhrasesTests(unittest.TestCase):
    """Tests for the function triage_service.py actually calls."""

    def test_returns_existing_rules_py_alias_phrases_only(self) -> None:
        # Every phrase this function can return must already be a real,
        # unmodified alias on EMERGENCY_WARNING_SIGN_RULES.
        from backend.safety.rules import EMERGENCY_WARNING_SIGN_RULES

        all_existing_aliases = {
            alias for rule in EMERGENCY_WARNING_SIGN_RULES for alias in rule.aliases
        }
        for text in (
            "heart attack",
            "hert attack",
            "can't breathe",
            "I think I'm having a stroke",
            "I passed out",
        ):
            with self.subTest(text=text):
                for phrase in extract_safety_concept_phrases(text):
                    self.assertIn(phrase, all_existing_aliases)

    def test_scans_multiple_text_sources_and_deduplicates(self) -> None:
        phrases = extract_safety_concept_phrases(
            "heart attack", ["chest pain", "heart attack"]
        )
        self.assertEqual(phrases, ["chest pain"])

    def test_empty_for_routine_complaint(self) -> None:
        self.assertEqual(extract_safety_concept_phrases("mild cough"), [])

    def test_ignores_non_string_and_none_inputs(self) -> None:
        self.assertEqual(extract_safety_concept_phrases(None, "mild cough", [None, 7]), [])


if __name__ == "__main__":
    unittest.main()
