"""Integration tests for the free-text safety fix: natural-language chief
complaints (including typos) must reach the SAME deterministic emergency
decision as the existing emergency checklist, via the unmodified
evaluate_safety() rule engine.

Regression coverage: the pre-existing checklist path, and the combination of
checklist + free text, must continue to behave exactly as before.
"""

from __future__ import annotations

from contextlib import contextmanager
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app import create_app
from backend.database import Base
from backend.services import triage_service


class TriageFreeTextSafetyServiceTests(unittest.TestCase):
    """Exercises backend.services.triage_service.process_triage_request
    directly against an isolated in-memory database."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.session: Session = self.SessionLocal()

    def tearDown(self) -> None:
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _triage(self, **payload) -> dict:
        return triage_service.process_triage_request(payload, session=self.session)

    # ------------------------------------------------------------------
    # EMERGENCY: free-text natural language, no checkboxes selected.
    # ------------------------------------------------------------------

    def test_free_text_emergency_cases(self) -> None:
        emergency_complaints = [
            "heart attack",
            "hert attack",
            "I think I'm having a heart attack",
            "crushing chest pain",
            "severe chest pressure",
            "can't breathe",
            "struggling to breathe",
            "severe shortness of breath",
            "I think I'm having a stroke",
            "face drooping and slurred speech",
            "I passed out",
            "lost consciousness",
        ]
        for complaint in emergency_complaints:
            with self.subTest(complaint=complaint):
                result = self._triage(chiefComplaint=complaint)
                self.assertEqual(result["recommendedAcuity"], "EMERGENCY")
                self.assertTrue(result["isEmergencyRedFlag"])
                self.assertTrue(result["triggeredRules"])

    # ------------------------------------------------------------------
    # NON-EMERGENCY: the new normalization layer must not introduce false
    # positives on routine complaints.
    # ------------------------------------------------------------------

    def test_free_text_non_emergency_cases_are_not_escalated(self) -> None:
        routine_complaints = [
            "mild cough",
            "fever for two days",
            "I want information about heart health",
            "my breathing is normal",
        ]
        for complaint in routine_complaints:
            with self.subTest(complaint=complaint):
                result = self._triage(chiefComplaint=complaint)
                self.assertNotEqual(result["recommendedAcuity"], "EMERGENCY")
                self.assertFalse(result["isEmergencyRedFlag"])

    # ------------------------------------------------------------------
    # BREATHING NATURAL-LANGUAGE VARIATIONS: connector words ("in", "with")
    # and phrasings the literal phrase list alone cannot express.
    # ------------------------------------------------------------------

    def test_breathing_natural_language_variations_reach_emergency(self) -> None:
        breathing_variations = [
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
        ]
        for complaint in breathing_variations:
            with self.subTest(complaint=complaint):
                result = self._triage(chiefComplaint=complaint)
                self.assertEqual(result["recommendedAcuity"], "EMERGENCY")
                self.assertTrue(result["isEmergencyRedFlag"])

    # ------------------------------------------------------------------
    # REPORTING / THIRD-PARTY CONTEXT: describing someone else's history or
    # general information-seeking must not become the patient's own
    # emergency, even when an otherwise-matching phrase is present.
    # ------------------------------------------------------------------

    def test_reporting_and_third_party_context_stays_non_emergency(self) -> None:
        non_active_complaints = [
            "breathing exercise",
            "breathing technique",
            "I want to learn about breathing exercises",
            "I read about difficulty breathing",
            "My mother had difficulty breathing last year",
            "I am studying heart attack symptoms",
            "How does breathing work?",
            "I watched a video about shortness of breath",
            "What are the symptoms of a heart attack?",
        ]
        for complaint in non_active_complaints:
            with self.subTest(complaint=complaint):
                result = self._triage(chiefComplaint=complaint)
                self.assertNotEqual(result["recommendedAcuity"], "EMERGENCY")
                self.assertFalse(result["isEmergencyRedFlag"])

    def test_question_framing_that_contains_a_literal_rules_alias_still_reaches_engine(self) -> None:
        """"What causes chest pain?" is correctly suppressed at the NLP
        layer (detect_safety_concepts returns no concept), but the
        deterministic engine.py independently re-scans the raw
        chiefComplaint against backend/safety/rules.py's own literal
        aliases -- which include bare "chest pain" -- so this still reaches
        EMERGENCY end-to-end. This is existing, untouched engine.py
        behavior (conservative-by-design, over-inclusive rather than
        under-inclusive) that the NLP layer has no mechanism to override,
        by architecture: the NLP layer can only ADD candidate concepts, it
        can never suppress what the engine's own independent scan finds."""
        result = self._triage(chiefComplaint="What causes chest pain?")
        self.assertEqual(result["recommendedAcuity"], "EMERGENCY")
        self.assertTrue(result["isEmergencyRedFlag"])

    # ------------------------------------------------------------------
    # CARDIAC/CHEST, STROKE, SYNCOPE NATURAL-LANGUAGE VARIATIONS (audit
    # follow-up): reordered/colloquial phrasings the literal phrase list
    # alone could not express.
    # ------------------------------------------------------------------

    def test_cardiac_chest_natural_language_variations_reach_emergency(self) -> None:
        complaints = [
            "in my chest there's crushing pain",
            "pain in my chest",
            "pressure in my chest",
            "severe pressure in my chest",
            "I feel intense pressure in my chest and I'm sweating",
            "my chest feels extremely tight",
            "chest hurts",
            "I've had this crushing feeling in my chest for the last 20 minutes and it's radiating to my arm",
        ]
        for complaint in complaints:
            with self.subTest(complaint=complaint):
                result = self._triage(chiefComplaint=complaint)
                self.assertEqual(result["recommendedAcuity"], "EMERGENCY")
                self.assertTrue(result["isEmergencyRedFlag"])

    def test_stroke_natural_language_variations_reach_emergency(self) -> None:
        complaints = [
            "my speech suddenly became slurred",
            "I suddenly can't move my left arm",
            "one side of my face is drooping",
            "weakness on one side of my body",
            "my face suddenly feels numb on one side",
            "About ten minutes ago my face started drooping on the right side and my words are coming out slurred",
        ]
        for complaint in complaints:
            with self.subTest(complaint=complaint):
                result = self._triage(chiefComplaint=complaint)
                self.assertEqual(result["recommendedAcuity"], "EMERGENCY")
                self.assertTrue(result["isEmergencyRedFlag"])

    def test_syncope_natural_language_variations_reach_emergency(self) -> None:
        complaints = ["I blacked out", "blacked out", "blackd out"]
        for complaint in complaints:
            with self.subTest(complaint=complaint):
                result = self._triage(chiefComplaint=complaint)
                self.assertEqual(result["recommendedAcuity"], "EMERGENCY")
                self.assertTrue(result["isEmergencyRedFlag"])

    def test_question_framing_guard_does_not_suppress_genuine_complaint(self) -> None:
        result = self._triage(chiefComplaint="Is this a heart attack? I have crushing chest pain")
        self.assertEqual(result["recommendedAcuity"], "EMERGENCY")
        self.assertTrue(result["isEmergencyRedFlag"])

    # ------------------------------------------------------------------
    # CHECKLIST REGRESSION: the pre-existing checkbox-driven path must
    # continue to work exactly as before, independent of free text.
    # ------------------------------------------------------------------

    def test_checklist_regression_each_flag_still_triggers_emergency(self) -> None:
        checklist_cases = [
            "Chest pain/pressure",
            "Severe trouble breathing",
            "Stroke symptoms",
            "Loss of consciousness",
        ]
        for label in checklist_cases:
            with self.subTest(label=label):
                result = self._triage(
                    chiefComplaint="routine follow-up",
                    selectedRedFlags=[label],
                    hasRedFlags=True,
                )
                self.assertEqual(result["recommendedAcuity"], "EMERGENCY")
                self.assertTrue(result["isEmergencyRedFlag"])

    # ------------------------------------------------------------------
    # PRIORITY REGRESSION: checklist and free text must combine safely,
    # and emergency signals always take precedence over routine wording.
    # ------------------------------------------------------------------

    def test_checklist_red_flag_with_normal_free_text_is_emergency(self) -> None:
        result = self._triage(
            chiefComplaint="mild cough",
            selectedRedFlags=["Chest pain/pressure"],
            hasRedFlags=True,
        )
        self.assertEqual(result["recommendedAcuity"], "EMERGENCY")

    def test_free_text_emergency_concept_with_empty_checklist_is_emergency(self) -> None:
        result = self._triage(chiefComplaint="heart attack", selectedRedFlags=[], hasRedFlags=False)
        self.assertEqual(result["recommendedAcuity"], "EMERGENCY")

    def test_free_text_emergency_concept_with_routine_wording_still_emergency(self) -> None:
        result = self._triage(chiefComplaint="mild chest pain but I think I'm having a heart attack")
        self.assertEqual(result["recommendedAcuity"], "EMERGENCY")
        self.assertTrue(result["isEmergencyRedFlag"])

    # ------------------------------------------------------------------
    # The extraction layer must never alter what is actually persisted as
    # the patient's own reported associated symptoms.
    # ------------------------------------------------------------------

    def test_persisted_associated_symptoms_are_not_fabricated(self) -> None:
        from backend.models import TriageEncounter

        result = self._triage(chiefComplaint="heart attack", associatedSymptoms=[])
        encounter = (
            self.session.query(TriageEncounter)
            .filter_by(id=result["encounterId"])
            .first()
        )
        self.assertIsNotNone(encounter)
        # The synthetic "chest pain" concept phrase used only to screen the
        # complaint must never be written back as if the patient selected it.
        self.assertEqual(encounter.associated_symptoms, [])
        self.assertEqual(encounter.chief_complaint, "heart attack")


class TriageFreeTextSafetyHttpTests(unittest.TestCase):
    """A smaller end-to-end check through the actual POST /api/triage route,
    confirming the frontend-facing response shape reaches EMERGENCY for a
    free-text-only complaint -- the exact bug reported in the browser."""

    def setUp(self) -> None:
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.session: Session = self.SessionLocal()

    def tearDown(self) -> None:
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @contextmanager
    def _mock_session_scope(self):
        yield self.session

    def test_heart_attack_free_text_reaches_emergency_via_http(self) -> None:
        from unittest.mock import patch

        with patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            response = self.client.post("/api/triage", json={"chiefComplaint": "heart attack"})
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["recommendedAcuity"], "EMERGENCY")
            self.assertTrue(data["isEmergencyRedFlag"])

    def test_typo_heart_attack_reaches_emergency_via_http(self) -> None:
        from unittest.mock import patch

        with patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            response = self.client.post("/api/triage", json={"chiefComplaint": "hert attack"})
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["recommendedAcuity"], "EMERGENCY")
            self.assertTrue(data["isEmergencyRedFlag"])

    def test_mild_cough_stays_non_emergency_via_http(self) -> None:
        from unittest.mock import patch

        with patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            response = self.client.post("/api/triage", json={"chiefComplaint": "mild cough"})
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertNotEqual(data["recommendedAcuity"], "EMERGENCY")
            self.assertFalse(data["isEmergencyRedFlag"])


if __name__ == "__main__":
    unittest.main()
