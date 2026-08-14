"""Versioned, ordered emergency warning-sign rule configuration."""

from __future__ import annotations

from backend.safety.types import SafetyRule


SAFETY_RULE_SET_VERSION = "1.0.0"


EMERGENCY_WARNING_SIGN_RULES: tuple[SafetyRule, ...] = (
    SafetyRule(
        id="RF-CARDIO-001",
        category="cardiac_warning_sign",
        description="Chest pain or chest pressure",
        aliases=(
            "chest pain",
            "chest pressure",
            "pain in my chest",
            "pressure in my chest",
        ),
        enabled=True,
    ),
    SafetyRule(
        id="RF-RESP-001",
        category="breathing_warning_sign",
        description="Severe trouble breathing",
        aliases=(
            "severe trouble breathing",
            "severe difficulty breathing",
            "severe shortness of breath",
            "cannot breathe",
            "cant breathe",
        ),
        enabled=True,
    ),
    SafetyRule(
        id="RF-NEURO-001",
        category="stroke_like_warning_sign",
        description="Stroke-like symptoms",
        aliases=(
            "stroke symptoms",
            "stroke signs",
            "sudden weakness on one side",
            "difficulty speaking",
            "trouble speaking",
        ),
        enabled=True,
    ),
    SafetyRule(
        id="RF-CONSCIOUSNESS-001",
        category="consciousness_warning_sign",
        description="Loss of consciousness",
        aliases=("loss of consciousness", "lost consciousness", "passed out", "pass out"),
        enabled=True,
    ),
    SafetyRule(
        id="RF-USER-REPORTED-001",
        category="user_reported_warning_sign",
        description="User reported an emergency warning sign",
        aliases=(),
        enabled=True,
    ),
)


UI_SELECTED_RED_FLAG_RULE_IDS: dict[str, str] = {
    "Chest pain/pressure": "RF-CARDIO-001",
    "Chest pain or pressure": "RF-CARDIO-001",
    "Severe trouble breathing": "RF-RESP-001",
    "Stroke symptoms": "RF-NEURO-001",
    "Loss of consciousness": "RF-CONSCIOUSNESS-001",
}
