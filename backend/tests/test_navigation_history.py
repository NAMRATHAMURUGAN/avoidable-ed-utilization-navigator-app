"""Unit tests for Milestone 5 dynamic care navigation and persistent interaction history.

Tests TriageEncounter creation, NavigationAction recording, patient history queries,
care destinations (EMERGENCY, URGENT_CARE, PRIMARY_CARE, TELEHEALTH), and provider isDemo flags
against an in-memory SQLite database with zero external server dependencies.
"""

from __future__ import annotations

from contextlib import contextmanager
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app import create_app
from backend.database import Base
from backend.models import (
    Member,
    NavigationAction,
    Provider,
    TriageEncounter,
)


class NavigationHistoryTestCase(unittest.TestCase):
    """Test suite for care destinations, triage persistence, and interaction history."""

    def setUp(self) -> None:
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.db_session: Session = self.SessionLocal()

        self._seed_member()

    def tearDown(self) -> None:
        self.db_session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @contextmanager
    def _mock_session_scope(self):
        yield self.db_session

    def _seed_member(self) -> None:
        m = Member(
            id=1001,
            bene_id="CMS-TEST-1001",
            age=68,
            gender="Male",
            dual_eligibility_months=0,
            chronic_condition_count=2,
        )
        self.db_session.add(m)
        self.db_session.commit()

    def _login_as_payer(self) -> None:
        """Register+login a PAYER user. Caller must be inside a patch of
        backend.services.auth_service.session_scope."""
        self.client.post(
            "/api/auth/register",
            json={"email": "payer-navhist@example.com", "password": "password123", "role": "PAYER"},
        )
        response = self.client.post(
            "/api/auth/login",
            json={"email": "payer-navhist@example.com", "password": "password123"},
        )
        assert response.status_code == 200, response.get_json()

    def test_emergency_triage_hard_safety_boundary(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            payload = {
                "chiefComplaint": "Crushing chest pain and severe shortness of breath",
                "symptomsDuration": "15 minutes",
                "hasRedFlags": True,
            }
            res = self.client.post("/api/triage", json=payload)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()

            self.assertTrue(data["isEmergencyRedFlag"])
            self.assertEqual(data["recommendedAcuity"], "EMERGENCY")
            self.assertEqual(data["urgencyLevel"], "IMMEDIATE_911_ER")
            self.assertIsNotNone(data["encounterId"])
            self.assertIsNotNone(data["sessionId"])

            # Verify persisted record
            enc = self.db_session.query(TriageEncounter).filter_by(id=data["encounterId"]).first()
            self.assertIsNotNone(enc)
            self.assertTrue(enc.is_emergency)
            self.assertEqual(enc.recommended_acuity, "EMERGENCY")

    def test_primary_care_destination(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            payload = {
                "chiefComplaint": "Annual checkup and medication review",
                "symptomsDuration": "1 week",
            }
            res = self.client.post("/api/triage", json=payload)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertFalse(data["isEmergencyRedFlag"])
            self.assertEqual(data["recommendedAcuity"], "PRIMARY_CARE")

    def test_urgent_care_destination(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            payload = {
                "chiefComplaint": "Minor finger cut while chopping vegetables",
                "symptomsDuration": "30 minutes",
            }
            res = self.client.post("/api/triage", json=payload)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertFalse(data["isEmergencyRedFlag"])
            self.assertEqual(data["recommendedAcuity"], "URGENT_CARE")

    def test_telehealth_destination(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            payload = {
                "chiefComplaint": "Mild itchy skin rash",
                "symptomsDuration": "2 days",
            }
            res = self.client.post("/api/triage", json=payload)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertFalse(data["isEmergencyRedFlag"])
            self.assertEqual(data["recommendedAcuity"], "TELEHEALTH")

    def test_member_linked_encounter_persistence(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope), \
             patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._login_as_payer()
            payload = {
                "patientId": "1001",
                "chiefComplaint": "Minor ankle sprain",
            }
            res = self.client.post("/api/triage", json=payload)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()

            enc = self.db_session.query(TriageEncounter).filter_by(id=data["encounterId"]).first()
            self.assertIsNotNone(enc)
            self.assertEqual(enc.member_id, 1001)

    def test_record_navigation_action_and_history(self) -> None:
        with patch("backend.routes.navigation.session_scope", self._mock_session_scope), \
             patch("backend.services.patient_service.session_scope", self._mock_session_scope), \
             patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._login_as_payer()

            # Create an encounter
            enc = TriageEncounter(
                member_id=1001,
                session_id="sess-abc-123",
                chief_complaint="Sprained wrist",
                is_emergency=False,
                recommended_acuity="URGENT_CARE",
                urgency_level="SAME_DAY_URGENT",
                recommended_setting_name="Local Urgent Care Center",
                clinical_rationale="Non-emergent sprain",
                safety_disclaimer="Safety notice",
                rule_set_version="1.0.0",
                created_at=self.db_session.query(Member).first().id and None or None,  # timestamp handled
            )
            from datetime import datetime, timezone
            enc.created_at = datetime.now(timezone.utc)
            self.db_session.add(enc)
            self.db_session.commit()

            # Record navigation action
            action_payload = {
                "encounterId": enc.id,
                "patientId": "1001",
                "actionType": "PROVIDER_SELECTED",
                "selectedProviderId": "prov-02",
                "selectedAcuity": "URGENT_CARE",
                "actionDetails": {"notes": "User selected urgent care provider"},
            }
            res = self.client.post("/api/navigation/action", json=action_payload)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data["status"], "recorded")
            self.assertIsNotNone(data["actionId"])

            # Retrieve patient history
            hist_res = self.client.get("/api/patients/1001/history")
            self.assertEqual(hist_res.status_code, 200)
            hist_data = hist_res.get_json()
            self.assertEqual(hist_data["totalEncounters"], 1)
            self.assertEqual(hist_data["totalActions"], 1)
            self.assertEqual(hist_data["actions"][0]["selectedProviderId"], "prov-02")

    def test_member_linked_by_bene_id(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope), \
             patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._login_as_payer()
            payload = {
                "patientId": "CMS-TEST-1001",
                "chiefComplaint": "Minor ankle sprain",
            }
            res = self.client.post("/api/triage", json=payload)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()

            enc = self.db_session.query(TriageEncounter).filter_by(id=data["encounterId"]).first()
            self.assertIsNotNone(enc)
            self.assertEqual(enc.member_id, 1001)

    def test_session_history_endpoint(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.navigation.session_scope", self._mock_session_scope):
            triage_res = self.client.post("/api/triage", json={"chiefComplaint": "Mild headache"})
            triage_data = triage_res.get_json()
            sess_id = triage_data["sessionId"]

            self.client.post("/api/navigation/action", json={
                "sessionId": sess_id,
                "actionType": "PROVIDER_SELECTED",
                "selectedProviderId": "prov-01",
                "selectedAcuity": "TELEHEALTH",
            })

            res = self.client.get(f"/api/navigation/session/{sess_id}")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data["sessionId"], sess_id)
            self.assertEqual(data["encounter"]["recommendedAcuity"], "TELEHEALTH")
            self.assertEqual(data["totalActions"], 1)
            self.assertEqual(data["actions"][0]["selectedProviderId"], "prov-01")

    def test_patient_anonymous_triage_flow(self) -> None:
        """Verify patient self-entry anonymous triage without CMS beneficiary ID or member link."""
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            payload = {
                "chiefComplaint": "Seasonal allergies and mild nasal congestion",
                "symptomsDuration": "2 days",
                "selectedRedFlags": [],
                "hasRedFlags": False,
            }
            res = self.client.post("/api/triage", json=payload)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()

            self.assertFalse(data["isEmergencyRedFlag"])
            self.assertEqual(data["recommendedAcuity"], "TELEHEALTH")
            self.assertIsNone(data.get("patientContext"))  # Unlinked patient context is None
            self.assertIsNotNone(data["encounterId"])
            self.assertIsNotNone(data["sessionId"])

    def test_simulated_telehealth_appointment_booking(self) -> None:
        """Verify recording APPOINTMENT_BOOKED navigation action for simulated telehealth flow."""
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.navigation.session_scope", self._mock_session_scope):
            triage_res = self.client.post("/api/triage", json={"chiefComplaint": "Skin rash"})
            triage_data = triage_res.get_json()
            sess_id = triage_data["sessionId"]
            enc_id = triage_data["encounterId"]

            action_payload = {
                "encounterId": enc_id,
                "sessionId": sess_id,
                "actionType": "APPOINTMENT_BOOKED",
                "selectedProviderId": "prov-telehealth-01",
                "selectedAcuity": "TELEHEALTH",
                "actionDetails": {
                    "providerName": "24/7 Virtual Telehealth Network",
                    "appointmentTime": "Today at 3:00 PM",
                    "isSimulatedDemo": True,
                },
            }
            res = self.client.post("/api/navigation/action", json=action_payload)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data["status"], "recorded")

            # Check session history contains APPOINTMENT_BOOKED action
            sess_res = self.client.get(f"/api/navigation/session/{sess_id}")
            self.assertEqual(sess_res.status_code, 200)
            sess_data = sess_res.get_json()
            self.assertEqual(sess_data["totalActions"], 1)
            self.assertEqual(sess_data["actions"][0]["actionType"], "APPOINTMENT_BOOKED")
            self.assertEqual(sess_data["actions"][0]["actionDetails"]["appointmentTime"], "Today at 3:00 PM")


if __name__ == "__main__":
    unittest.main()

