"""Tests for the PATIENT <-> synthetic CMS Member demo association:

- backend/models/patient_member_link.py (PatientMemberLink)
- backend/repositories/patient_member_link_repository.py
- backend/services/patient_member_link_service.py (get_or_create_linked_member)
- the PATIENT self-link branch in backend/services/triage_service.py /
  backend/routes/triage.py

Covers: first-time link creation, persistence/idempotency across repeated
calls, that a PATIENT can never choose a different member via a
client-supplied identifier, that an emergency determination is never
influenced by utilization/ML data, that a non-emergency high-utilization
patient's acuity IS appropriately nudged, and that existing PAYER flows are
unaffected. Never touches backend/safety/engine.py or backend/safety/rules.py.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app import create_app
from backend.database import Base
from backend.models import (
    Member,
    MemberUtilizationSnapshot,
    ModelRun,
    PatientMemberLink,
    UtilizationAnomalyResult,
    XGBoostUtilizationPrediction,
)
from backend.services.auth_service import register_user


def _make_member(
    *, member_id: int, bene_id: str, high_utilization_probability: float, anomaly_flag: int, ed_visit_count: int
) -> tuple[Member, MemberUtilizationSnapshot, XGBoostUtilizationPrediction, UtilizationAnomalyResult]:
    member = Member(
        id=member_id, bene_id=bene_id, age=68, gender="Female",
        dual_eligibility_months=0, chronic_condition_count=1,
    )
    snapshot = MemberUtilizationSnapshot(
        id=member_id, member_id=member_id, inpatient_visit_count=0,
        inpatient_total_cost=Decimal("0.00"), outpatient_visit_count=1,
        outpatient_total_cost=Decimal("100.00"), ed_visit_count=ed_visit_count,
        total_claim_payment_amount=Decimal("500.00"), total_ed_related_cost=Decimal("300.00"),
        average_claim_cost=Decimal("200.00"), provider_count=1,
    )
    prediction = XGBoostUtilizationPrediction(
        id=member_id, member_id=member_id, utilization_snapshot_id=member_id,
        model_run_id=1, high_utilization_pattern=anomaly_flag,
        predicted_high_utilization_pattern=anomaly_flag,
        high_utilization_probability=high_utilization_probability, dataset_split="test",
    )
    anomaly = UtilizationAnomalyResult(
        id=member_id, member_id=member_id, utilization_snapshot_id=member_id,
        model_run_id=2, anomaly_score=0.5, anomaly_flag=anomaly_flag,
        anomaly_rank=member_id, generated_at=datetime.now(timezone.utc),
    )
    return member, snapshot, prediction, anomaly


class PatientMemberLinkTestCase(unittest.TestCase):
    """HTTP-level tests via the Flask test client against an isolated
    in-memory SQLite database -- never the live PostgreSQL database."""

    def setUp(self) -> None:
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.session: Session = self.SessionLocal()

        self._seed_model_runs()

    def tearDown(self) -> None:
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @contextmanager
    def _mock_session_scope(self):
        yield self.session

    def _seed_model_runs(self) -> None:
        self.session.add_all([
            ModelRun(model_run_id=1, model_type="xgboost", purpose="utilization prediction",
                      artifact_reference="ml_models/xgboost_risk_model.pkl",
                      generated_at=datetime(2025, 1, 1, tzinfo=timezone.utc)),
            ModelRun(model_run_id=2, model_type="isolation_forest", purpose="utilization anomaly",
                      artifact_reference="ml_models/isolation_forest.pkl",
                      generated_at=datetime(2025, 1, 1, tzinfo=timezone.utc)),
        ])
        self.session.commit()

    def _seed_members(self, *members: tuple) -> None:
        for member, snapshot, prediction, anomaly in members:
            self.session.add(member)
        self.session.flush()
        for member, snapshot, prediction, anomaly in members:
            self.session.add_all([snapshot, prediction, anomaly])
        self.session.commit()

    def _register_and_login(self, email: str) -> None:
        self.client.post(
            "/api/auth/register", json={"email": email, "password": "password123", "role": "PATIENT"}
        )
        response = self.client.post("/api/auth/login", json={"email": email, "password": "password123"})
        assert response.status_code == 200, response.get_json()

    def _patches(self):
        return (
            patch("backend.services.auth_service.session_scope", self._mock_session_scope),
            patch("backend.services.triage_service.session_scope", self._mock_session_scope),
        )

    # ------------------------------------------------------------------
    # First-time link creation
    # ------------------------------------------------------------------

    def test_first_triage_creates_a_link_to_the_lowest_unclaimed_member(self) -> None:
        low_risk = _make_member(member_id=5, bene_id="CMS-LOW-005", high_utilization_probability=0.1,
                                 anomaly_flag=0, ed_visit_count=0)
        self._seed_members(low_risk)
        self.assertEqual(self.session.query(PatientMemberLink).count(), 0)

        p1, p2 = self._patches()
        with p1, p2:
            self._register_and_login("first-link@example.com")
            response = self.client.post("/api/triage", json={"chiefComplaint": "Mild seasonal cold symptoms"})
            self.assertEqual(response.status_code, 200, response.get_json())
            data = response.get_json()
            self.assertEqual(data["patientContext"]["beneficiaryId"], "CMS-LOW-005")

        links = self.session.query(PatientMemberLink).all()
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].member_id, 5)

    # ------------------------------------------------------------------
    # Persistent / idempotent link
    # ------------------------------------------------------------------

    def test_link_persists_across_repeated_triage_calls_not_recreated(self) -> None:
        low_risk = _make_member(member_id=5, bene_id="CMS-LOW-005", high_utilization_probability=0.1,
                                 anomaly_flag=0, ed_visit_count=0)
        another = _make_member(member_id=6, bene_id="CMS-LOW-006", high_utilization_probability=0.1,
                                anomaly_flag=0, ed_visit_count=0)
        self._seed_members(low_risk, another)

        p1, p2 = self._patches()
        with p1, p2:
            self._register_and_login("repeat-link@example.com")
            first = self.client.post("/api/triage", json={"chiefComplaint": "Mild seasonal cold symptoms"}).get_json()
            second = self.client.post("/api/triage", json={"chiefComplaint": "Sore throat for two days"}).get_json()

        self.assertEqual(first["patientContext"]["beneficiaryId"], second["patientContext"]["beneficiaryId"])
        # Member 5 (lowest id) was claimed first and reused -- member 6 was
        # never touched by this same patient across either call.
        self.assertEqual(first["patientContext"]["beneficiaryId"], "CMS-LOW-005")
        links = self.session.query(PatientMemberLink).all()
        self.assertEqual(len(links), 1)

    def test_get_or_create_linked_member_is_idempotent_at_the_service_layer(self) -> None:
        """Direct service-level check, independent of the HTTP layer."""
        from backend.services.patient_member_link_service import get_or_create_linked_member

        low_risk = _make_member(member_id=5, bene_id="CMS-LOW-005", high_utilization_probability=0.1,
                                 anomaly_flag=0, ed_visit_count=0)
        self._seed_members(low_risk)

        user = register_user(email="svc-link@example.com", password="password123", role="PATIENT", session=self.session)
        self.session.commit()

        first = get_or_create_linked_member(self.session, user.id)
        second = get_or_create_linked_member(self.session, user.id)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first.member.id, second.member.id)
        self.assertEqual(self.session.query(PatientMemberLink).count(), 1)

    # ------------------------------------------------------------------
    # Patient cannot choose another member
    # ------------------------------------------------------------------

    def test_patient_supplied_patient_id_is_ignored_next_unclaimed_member_used_instead(self) -> None:
        """Two members exist; the patient explicitly asks for the SECOND
        (higher-id) one. They must still be linked to the lowest unclaimed
        member (deterministic, server-side), never the one they requested."""
        first_unclaimed = _make_member(member_id=10, bene_id="CMS-A-010", high_utilization_probability=0.1,
                                        anomaly_flag=0, ed_visit_count=0)
        requested_by_patient = _make_member(member_id=20, bene_id="CMS-B-020", high_utilization_probability=0.95,
                                             anomaly_flag=1, ed_visit_count=6)
        self._seed_members(first_unclaimed, requested_by_patient)

        p1, p2 = self._patches()
        with p1, p2:
            self._register_and_login("cannot-choose@example.com")
            response = self.client.post(
                "/api/triage",
                json={"chiefComplaint": "Feeling tired and low energy", "patientId": "20"},
            )
            self.assertEqual(response.status_code, 200, response.get_json())
            data = response.get_json()

        self.assertEqual(data["patientContext"]["beneficiaryId"], "CMS-A-010")
        self.assertNotEqual(data["patientContext"]["beneficiaryId"], "CMS-B-020")
        links = self.session.query(PatientMemberLink).all()
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].member_id, 10)

    def test_two_different_patients_get_two_different_members(self) -> None:
        member_a = _make_member(member_id=1, bene_id="CMS-A-001", high_utilization_probability=0.1,
                                 anomaly_flag=0, ed_visit_count=0)
        member_b = _make_member(member_id=2, bene_id="CMS-B-002", high_utilization_probability=0.1,
                                 anomaly_flag=0, ed_visit_count=0)
        self._seed_members(member_a, member_b)

        p1, p2 = self._patches()
        with p1, p2:
            self._register_and_login("patient-one@example.com")
            first = self.client.post("/api/triage", json={"chiefComplaint": "Mild cold"}).get_json()
            self._register_and_login("patient-two@example.com")
            second = self.client.post("/api/triage", json={"chiefComplaint": "Mild cold"}).get_json()

        self.assertNotEqual(first["patientContext"]["beneficiaryId"], second["patientContext"]["beneficiaryId"])
        self.assertEqual(self.session.query(PatientMemberLink).count(), 2)

    # ------------------------------------------------------------------
    # Emergency is never influenced by utilization
    # ------------------------------------------------------------------

    def test_emergency_complaint_still_routes_to_ed_for_high_utilization_self_linked_patient(self) -> None:
        high_risk = _make_member(member_id=1, bene_id="CMS-HIGH-001", high_utilization_probability=0.95,
                                  anomaly_flag=1, ed_visit_count=6)
        self._seed_members(high_risk)

        p1, p2 = self._patches()
        with p1, p2:
            self._register_and_login("emergency-high-risk@example.com")
            response = self.client.post(
                "/api/triage",
                json={"chiefComplaint": "Crushing chest pain and severe shortness of breath", "hasRedFlags": True},
            )
            self.assertEqual(response.status_code, 200, response.get_json())
            data = response.get_json()

        self.assertTrue(data["isEmergencyRedFlag"])
        self.assertEqual(data["recommendedAcuity"], "EMERGENCY")
        self.assertEqual(data["urgencyLevel"], "IMMEDIATE_911_ER")
        # Member resolution happens before the emergency/non-emergency
        # branch (unchanged, pre-existing structure also used by the PAYER
        # path) so patientContext MAY still be present here -- what matters,
        # and what this test actually proves, is that recommendedAcuity is
        # never anything other than EMERGENCY regardless of the linked
        # member's risk level: the emergency branch never reads
        # patient_context at all. The frontend's emergency renderer also
        # never displays these fields (see renderEmergencyResult).

    # ------------------------------------------------------------------
    # Non-emergency high-utilization patient IS nudged appropriately
    # ------------------------------------------------------------------

    def test_non_emergency_high_utilization_patient_receives_primary_care_not_telehealth(self) -> None:
        high_risk = _make_member(member_id=1, bene_id="CMS-HIGH-001", high_utilization_probability=0.95,
                                  anomaly_flag=1, ed_visit_count=6)
        self._seed_members(high_risk)

        p1, p2 = self._patches()
        with p1, p2:
            self._register_and_login("nonemergency-high-risk@example.com")
            response = self.client.post(
                "/api/triage", json={"chiefComplaint": "Mild seasonal cold symptoms"}
            )
            self.assertEqual(response.status_code, 200, response.get_json())
            data = response.get_json()

        self.assertFalse(data["isEmergencyRedFlag"])
        self.assertEqual(data["recommendedAcuity"], "PRIMARY_CARE")
        self.assertIsNotNone(data.get("proactiveRecommendation"))
        self.assertEqual(data["proactiveRecommendation"]["navigation_priority"], "HIGH")

    def test_non_emergency_low_utilization_patient_still_gets_telehealth(self) -> None:
        """Contrast case: the acuity nudge is conditional on the linked
        member's actual risk level, not applied unconditionally to every
        self-linked patient."""
        low_risk = _make_member(member_id=1, bene_id="CMS-LOW-001", high_utilization_probability=0.05,
                                 anomaly_flag=0, ed_visit_count=0)
        self._seed_members(low_risk)

        p1, p2 = self._patches()
        with p1, p2:
            self._register_and_login("nonemergency-low-risk@example.com")
            response = self.client.post(
                "/api/triage", json={"chiefComplaint": "Mild seasonal cold symptoms"}
            )
            self.assertEqual(response.status_code, 200, response.get_json())
            data = response.get_json()

        self.assertFalse(data["isEmergencyRedFlag"])
        self.assertEqual(data["recommendedAcuity"], "TELEHEALTH")

    # ------------------------------------------------------------------
    # Existing PAYER flows unaffected
    # ------------------------------------------------------------------

    def test_payer_can_still_select_any_member_including_one_already_patient_linked(self) -> None:
        member = _make_member(member_id=1, bene_id="CMS-SHARED-001", high_utilization_probability=0.95,
                               anomaly_flag=1, ed_visit_count=6)
        self._seed_members(member)

        p1, p2 = self._patches()
        with p1, p2:
            self._register_and_login("payer-flow-patient@example.com")
            self.client.post("/api/triage", json={"chiefComplaint": "Mild cold"})  # creates the PATIENT's link
            self.assertEqual(self.session.query(PatientMemberLink).count(), 1)

            register_user(email="payer-flow-payer@example.com", password="password123", role="PAYER", session=self.session)
            login = self.client.post(
                "/api/auth/login", json={"email": "payer-flow-payer@example.com", "password": "password123"}
            )
            self.assertEqual(login.status_code, 200)

            response = self.client.post(
                "/api/triage",
                json={"chiefComplaint": "Feeling tired and low energy", "patientId": "1"},
            )
            self.assertEqual(response.status_code, 200, response.get_json())
            data = response.get_json()

        self.assertEqual(data["patientContext"]["beneficiaryId"], "CMS-SHARED-001")
        self.assertEqual(data["patientContext"]["riskLevel"], "HIGH")
        self.assertEqual(data["recommendedAcuity"], "PRIMARY_CARE")
        # The PAYER's explicit selection never created or altered any
        # PatientMemberLink row -- that mechanism is PATIENT-only.
        self.assertEqual(self.session.query(PatientMemberLink).count(), 1)

    # ------------------------------------------------------------------
    # Graceful no-op when the synthetic member pool is empty
    # ------------------------------------------------------------------

    def test_empty_member_pool_does_not_block_patient_triage(self) -> None:
        p1, p2 = self._patches()
        with p1, p2:
            self._register_and_login("no-members-available@example.com")
            response = self.client.post("/api/triage", json={"chiefComplaint": "Mild seasonal cold symptoms"})
            self.assertEqual(response.status_code, 200, response.get_json())
            data = response.get_json()

        self.assertFalse(data["isEmergencyRedFlag"])
        self.assertIsNone(data.get("patientContext"))
        self.assertIsNone(data.get("proactiveRecommendation"))
        self.assertEqual(self.session.query(PatientMemberLink).count(), 0)

    # ------------------------------------------------------------------
    # Repository-level determinism
    # ------------------------------------------------------------------

    def test_repository_next_unclaimed_member_id_is_lowest_id_not_random(self) -> None:
        from backend.repositories.patient_member_link_repository import PatientMemberLinkRepository

        m1 = _make_member(member_id=3, bene_id="CMS-003", high_utilization_probability=0.1, anomaly_flag=0, ed_visit_count=0)
        m2 = _make_member(member_id=1, bene_id="CMS-001", high_utilization_probability=0.1, anomaly_flag=0, ed_visit_count=0)
        m3 = _make_member(member_id=2, bene_id="CMS-002", high_utilization_probability=0.1, anomaly_flag=0, ed_visit_count=0)
        self._seed_members(m1, m2, m3)

        repo = PatientMemberLinkRepository(self.session)
        self.assertEqual(repo.get_next_unclaimed_member_id(), 1)

        repo.create(PatientMemberLink(user_id=999, member_id=1, created_at=datetime.now(timezone.utc)))
        self.session.commit()
        self.assertEqual(repo.get_next_unclaimed_member_id(), 2)


if __name__ == "__main__":
    unittest.main()
