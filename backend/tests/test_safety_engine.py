"""Behavioral parity tests for the deterministic Python safety engine."""

from __future__ import annotations

import unittest

from backend.safety.engine import evaluate_safety, normalize_safety_text


def base_request(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "chiefComplaint": "",
        "symptomsDuration": "Today",
        "associatedSymptoms": [],
        "hasRedFlags": False,
        "selectedRedFlags": [],
    }
    request.update(overrides)
    return request


def decision(*rule_ids: tuple[str, str, str]) -> dict[str, object]:
    return {
        "isEmergency": bool(rule_ids),
        "ruleSetVersion": "1.0.0",
        "triggeredRules": [
            {"id": rule_id, "category": category, "description": description}
            for rule_id, category, description in rule_ids
        ],
    }


CARDIO = ("RF-CARDIO-001", "cardiac_warning_sign", "Chest pain or chest pressure")
RESP = ("RF-RESP-001", "breathing_warning_sign", "Severe trouble breathing")
NEURO = ("RF-NEURO-001", "stroke_like_warning_sign", "Stroke-like symptoms")
CONSCIOUSNESS = ("RF-CONSCIOUSNESS-001", "consciousness_warning_sign", "Loss of consciousness")
USER_REPORTED = (
    "RF-USER-REPORTED-001",
    "user_reported_warning_sign",
    "User reported an emergency warning sign",
)


class SafetyEngineParityTests(unittest.TestCase):
    def test_detects_configured_warning_signs_from_symptom_text(self) -> None:
        self.assertEqual(evaluate_safety(base_request(chiefComplaint="chest pain and pressure")), decision(CARDIO))
        self.assertEqual(evaluate_safety(base_request(chiefComplaint="severe trouble breathing")), decision(RESP))
        self.assertEqual(
            evaluate_safety(base_request(chiefComplaint="sudden weakness on one side and difficulty speaking")),
            decision(NEURO),
        )
        self.assertEqual(evaluate_safety(base_request(chiefComplaint="I passed out and lost consciousness")), decision(CONSCIOUSNESS))

    def test_normalizes_case_punctuation_whitespace_and_ascii_only_text(self) -> None:
        self.assertEqual(normalize_safety_text("  CHEST-pain!  "), "chest pain")
        self.assertEqual(normalize_safety_text("Caf\u00e9\u2014chest_pain"), "caf chest pain")
        self.assertEqual(evaluate_safety(base_request(chiefComplaint="CHEST PAIN")), decision(CARDIO))
        self.assertEqual(evaluate_safety(base_request(chiefComplaint="chest-pain!")), decision(CARDIO))
        self.assertEqual(evaluate_safety(base_request(chiefComplaint=" chest    pain ")), decision(CARDIO))

    def test_selected_flags_and_unknown_labels(self) -> None:
        expected = {
            "Chest pain/pressure": CARDIO,
            "Chest pain or pressure": CARDIO,
            "Severe trouble breathing": RESP,
            "Stroke symptoms": NEURO,
            "Loss of consciousness": CONSCIOUSNESS,
        }
        for label, expected_rule in expected.items():
            with self.subTest(label=label):
                self.assertEqual(evaluate_safety(base_request(selectedRedFlags=[label])), decision(expected_rule))
        self.assertEqual(evaluate_safety(base_request(selectedRedFlags=["Unrecognized warning"])), decision())

    def test_explicit_has_red_flags_requires_literal_true(self) -> None:
        self.assertEqual(evaluate_safety(base_request(hasRedFlags=True)), decision(USER_REPORTED))
        self.assertEqual(evaluate_safety(base_request(hasRedFlags="true")), decision())
        self.assertEqual(evaluate_safety(base_request(hasRedFlags=1)), decision())

    def test_non_emergency_phrase_boundaries_and_blank_input(self) -> None:
        self.assertEqual(evaluate_safety(base_request(chiefComplaint="mild sore throat for two days")), decision())
        self.assertEqual(evaluate_safety(base_request(chiefComplaint="mychestpaintracker is broken")), decision())
        self.assertEqual(evaluate_safety(base_request()), decision())
        self.assertEqual(evaluate_safety(None), decision())

    def test_malformed_fields_and_arrays_are_ignored_without_error(self) -> None:
        self.assertEqual(
            evaluate_safety(
                {
                    "chiefComplaint": 42,
                    "associatedSymptoms": [None, 7],
                    "selectedRedFlags": "chest pain",
                    "hasRedFlags": "true",
                }
            ),
            decision(),
        )
        malformed = {
            "chiefComplaint": 42,
            "associatedSymptoms": [None, 7, True, {}, "chest pain"],
            "selectedRedFlags": "chest pain",
            "hasRedFlags": "true",
        }
        self.assertEqual(evaluate_safety(malformed), decision(CARDIO))
        self.assertEqual(
            evaluate_safety({"associatedSymptoms": "chest pain", "selectedRedFlags": [None, 7, {}, True]}),
            decision(),
        )

    def test_multiple_rules_follow_configured_order_and_deduplicate(self) -> None:
        result = evaluate_safety(
            base_request(
                chiefComplaint="severe trouble breathing and chest pain",
                selectedRedFlags=["Chest pain or pressure", "Chest pain/pressure"],
            )
        )
        self.assertEqual(result, decision(CARDIO, RESP))

    def test_user_reported_rule_is_appended_last(self) -> None:
        self.assertEqual(
            evaluate_safety(base_request(chiefComplaint="chest pain", hasRedFlags=True)),
            decision(CARDIO, USER_REPORTED),
        )

    def test_ml_and_unrelated_fields_do_not_affect_the_result(self) -> None:
        self.assertEqual(
            evaluate_safety(base_request(riskScore=999, anomalyScore=999, modelOutput={"emergency": True})),
            decision(),
        )
        self.assertEqual(
            evaluate_safety(base_request(chiefComplaint="chest pain", riskScore=0, anomalyScore=1)),
            decision(CARDIO),
        )

    def test_associated_symptoms_are_scanned_after_chief_complaint(self) -> None:
        self.assertEqual(
            evaluate_safety(base_request(chiefComplaint="mild sore throat", associatedSymptoms=["severe shortness of breath"])),
            decision(RESP),
        )


if __name__ == "__main__":
    unittest.main()
