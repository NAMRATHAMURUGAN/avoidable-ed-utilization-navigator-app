"""Pure deterministic parity implementation of the TypeScript safety engine."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from backend.safety.rules import (
    EMERGENCY_WARNING_SIGN_RULES,
    SAFETY_RULE_SET_VERSION,
    UI_SELECTED_RED_FLAG_RULE_IDS,
)
from backend.safety.types import SafetyDecision, SafetyRule, TriggeredSafetyRule


_NON_ASCII_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_WHITESPACE = re.compile(r"\s+")


def normalize_safety_text(value: Any) -> str:
    """Match TypeScript normalization: lowercase, ASCII filtering, and trimming."""
    if not isinstance(value, str):
        return ""
    return _WHITESPACE.sub(" ", _NON_ASCII_ALPHANUMERIC.sub(" ", value.lower())).strip()


_NORMALIZED_SELECTED_RED_FLAG_RULE_IDS = {
    normalize_safety_text(label): rule_id
    for label, rule_id in UI_SELECTED_RED_FLAG_RULE_IDS.items()
}


def _value_for(record: Any, field: str) -> Any:
    """Read fields from mappings or ordinary objects; primitives act like an empty record."""
    if isinstance(record, Mapping):
        return record.get(field)
    if record is None or isinstance(record, (str, bytes, int, float, bool, complex)):
        return None
    return getattr(record, field, None)


def _string_values(value: Any) -> list[str]:
    """Match TypeScript's array-only, string-only field handling."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _phrase_matches(text: str, phrase: str) -> bool:
    """Match normalized phrases only at delimiter boundaries."""
    normalized_phrase = normalize_safety_text(phrase)
    if not text or not normalized_phrase:
        return False
    return f" {normalized_phrase} " in f" {text} "


def _triggered_rule(rule: SafetyRule) -> TriggeredSafetyRule:
    return {"id": rule.id, "category": rule.category, "description": rule.description}


def evaluate_safety(input_value: Any) -> SafetyDecision:
    """Return deterministic emergency warning-sign matches with TypeScript parity.

    This function intentionally has no clinical inference, ML, database, Flask,
    network, clock, or external-state dependency.
    """
    record = input_value if input_value is not None and not isinstance(
        input_value, (str, bytes, int, float, bool, complex)
    ) else {}
    text_to_screen = [
        normalized
        for normalized in (
            normalize_safety_text(value)
            for value in [
                _value_for(record, "chiefComplaint"),
                *_string_values(_value_for(record, "associatedSymptoms")),
            ]
        )
        if normalized
    ]
    selected_rule_ids = {
        rule_id
        for rule_id in (
            _NORMALIZED_SELECTED_RED_FLAG_RULE_IDS.get(normalize_safety_text(label))
            for label in _string_values(_value_for(record, "selectedRedFlags"))
        )
        if rule_id
    }

    triggered_rules = [
        _triggered_rule(rule)
        for rule in EMERGENCY_WARNING_SIGN_RULES
        if rule.enabled
        and (
            rule.id in selected_rule_ids
            or any(
                _phrase_matches(text, alias)
                for alias in rule.aliases
                for text in text_to_screen
            )
        )
    ]

    if _value_for(record, "hasRedFlags") is True and not any(
        rule["id"] == "RF-USER-REPORTED-001" for rule in triggered_rules
    ):
        user_reported_rule = next(
            (rule for rule in EMERGENCY_WARNING_SIGN_RULES if rule.id == "RF-USER-REPORTED-001"),
            None,
        )
        if user_reported_rule is not None and user_reported_rule.enabled:
            triggered_rules.append(_triggered_rule(user_reported_rule))

    return {
        "isEmergency": len(triggered_rules) > 0,
        "ruleSetVersion": SAFETY_RULE_SET_VERSION,
        "triggeredRules": triggered_rules,
    }
