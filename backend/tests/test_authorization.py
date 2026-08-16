"""Authorization tests for the PAYER-only endpoint policy and the
member-linked-data boundaries on POST /api/triage and POST
/api/navigation/action.

There is no User<->Member mapping in this application: an authenticated
PATIENT does not correspond to any specific CMS Member record. These tests
therefore verify that an unauthenticated or PATIENT caller is denied
member-linked data/writes outright (401/403, or a triage/navigation call
that succeeds but omits member-linked enrichment) -- they do not, and must
not, test any notion of "the patient's own record," since no such concept
exists in the current data model.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app import create_app
from backend.database import Base
from backend.models import (
    Member,
    MemberUtilizationSnapshot,
    ModelRun,
    NavigationAction,
    Provider,
    TriageEncounter,
    UtilizationAnomalyResult,
    XGBoostUtilizationPrediction,
)
from backend.services.auth_service import register_user

PAYER_ONLY_GET_ENDPOINTS = [
    "/api/patients",
    "/api/patients/1",
    "/api/patients/1/history",
    "/api/ml/risk-predictions",
    "/api/ml/anomalies",
    "/api/ml/anomalies/summary",
    "/api/ml/overlap",
    "/api/ml/models",
    "/api/analytics",
]

# Every session_scope import used anywhere in the request paths exercised by
# the "PAYER succeeds" test below.
ALL_SESSION_SCOPE_TARGETS = (
    "backend.services.auth_service.session_scope",
    "backend.services.patient_service.session_scope",
    "backend.services.analytics_service.session_scope",
    "backend.routes.navigation.session_scope",
    "backend.services.triage_service.session_scope",
)


class AuthorizationMatrixTestCase(unittest.TestCase):
    """401 (unauthenticated) / 403 (PATIENT) / 200 (PAYER) matrix for every
    PAYER-only endpoint, plus the member-linked-data boundaries on
    POST /api/triage and POST /api/navigation/action."""

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
        """One HIGH-risk member (id=1), used both for the PAYER-only matrix
        and to prove member-linked risk data never leaks -- directly (via
        patientContext) or indirectly (via the PRIMARY_CARE acuity nudge) --
        to an unauthenticated or PATIENT caller."""
        xgb_run = ModelRun(
            model_run_id=1,
            model_type="xgboost",
            purpose="utilization prediction",
            artifact_reference="ml_models/xgboost_risk_model.pkl",
            generated_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        anomaly_run = ModelRun(
            model_run_id=2,
            model_type="isolation_forest",
            purpose="utilization anomaly",
            artifact_reference="ml_models/isolation_forest.pkl",
            generated_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
        self.db_session.add_all([xgb_run, anomaly_run])
        self.db_session.flush()

        member = Member(
            id=1,
            bene_id="CMS-AUTHZ-001",
            age=70,
            gender="Female",
            dual_eligibility_months=0,
            chronic_condition_count=2,
        )
        self.db_session.add(member)
        self.db_session.flush()

        snapshot = MemberUtilizationSnapshot(
            id=1,
            member_id=member.id,
            inpatient_visit_count=1,
            inpatient_total_cost=Decimal("500.00"),
            outpatient_visit_count=2,
            outpatient_total_cost=Decimal("300.00"),
            ed_visit_count=5,
            total_claim_payment_amount=Decimal("2000.00"),
            total_ed_related_cost=Decimal("1500.00"),
            average_claim_cost=Decimal("400.00"),
            provider_count=3,
        )
        self.db_session.add(snapshot)
        self.db_session.flush()

        prediction = XGBoostUtilizationPrediction(
            id=1,
            member_id=member.id,
            utilization_snapshot_id=snapshot.id,
            model_run_id=xgb_run.model_run_id,
            high_utilization_pattern=1,
            predicted_high_utilization_pattern=1,
            high_utilization_probability=0.91,
            dataset_split="test",
        )
        anomaly = UtilizationAnomalyResult(
            id=1,
            member_id=member.id,
            utilization_snapshot_id=snapshot.id,
            model_run_id=anomaly_run.model_run_id,
            anomaly_score=0.5,
            anomaly_flag=1,
            anomaly_rank=1,
            generated_at=datetime.now(timezone.utc),
        )
        self.db_session.add_all([prediction, anomaly])

        # A real, explicitly-seeded provider referenced by selectedProviderId
        # in the navigation-action tests below. Production routes no longer
        # auto-seed MOCK_PROVIDERS, so tests must seed what they need.
        self.db_session.add(Provider(
            id="prov-01",
            name="Test Provider prov-01",
            type="URGENT_CARE",
            address="123 Test St",
            city_state_zip="Testville, TS 00000",
            distance_miles=1.0,
            operating_hours="24/7",
            phone="555-0100",
            services=[],
            is_demo=False,
        ))
        self.db_session.commit()

    def _seed_anonymous_encounter(self, session_id: str) -> None:
        """Seed a TriageEncounter with no member link, giving navigation-action
        tests a valid sessionId anchor without requiring a real /api/triage call."""
        self.db_session.add(TriageEncounter(
            session_id=session_id,
            chief_complaint="Test complaint",
            is_emergency=False,
            recommended_acuity="TELEHEALTH",
            urgency_level="SAME_DAY_TELEHEALTH",
            recommended_setting_name="Telehealth",
            clinical_rationale="Test",
            safety_disclaimer="Test",
            rule_set_version="1.0.0",
            created_at=datetime.now(timezone.utc),
        ))
        self.db_session.commit()

    def _register_and_login(self, email: str, role: str) -> None:
        """Caller must already be inside a patch of auth_service.session_scope.

        PATIENT accounts go through the public registration endpoint (the
        flow it actually serves). PAYER accounts are provisioned directly via
        the service layer, since POST /api/auth/register only ever creates a
        PATIENT account.
        """
        if role == "PAYER":
            register_user(email=email, password="password123", role="PAYER", session=self.db_session)
        else:
            self.client.post(
                "/api/auth/register",
                json={"email": email, "password": "password123", "role": role},
            )
        login_response = self.client.post(
            "/api/auth/login", json={"email": email, "password": "password123"}
        )
        assert login_response.status_code == 200, login_response.get_json()

    # ------------------------------------------------------------------
    # PAYER-only endpoint matrix
    # ------------------------------------------------------------------

    def test_payer_only_endpoints_reject_unauthenticated(self) -> None:
        for path in PAYER_ONLY_GET_ENDPOINTS:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 401)
                self.assertIn("error", response.get_json())

    def test_payer_only_endpoints_reject_patient_role(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._register_and_login("patient-authz@example.com", "PATIENT")
            for path in PAYER_ONLY_GET_ENDPOINTS:
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 403)
                    self.assertIn("error", response.get_json())

    def test_payer_only_endpoints_permit_payer_role(self) -> None:
        with ExitStack() as stack:
            for target in ALL_SESSION_SCOPE_TARGETS:
                stack.enter_context(patch(target, self._mock_session_scope))
            self._register_and_login("payer-authz@example.com", "PAYER")
            for path in PAYER_ONLY_GET_ENDPOINTS:
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200, response.get_json())

    def test_idor_regression_unauthorized_caller_cannot_probe_member_existence(self) -> None:
        """An unauthorized caller must get 401 for both a real and a
        nonexistent member id -- proving authorization runs before any
        existence check, so response codes cannot be used to enumerate
        valid CMS member ids."""
        for patient_id in ("1", "999999"):
            with self.subTest(patient_id=patient_id):
                response = self.client.get(f"/api/patients/{patient_id}")
                self.assertEqual(response.status_code, 401)

    # ------------------------------------------------------------------
    # POST /api/triage member-linkage boundary
    # ------------------------------------------------------------------

    def test_triage_anonymous_without_patient_id_succeeds(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            response = self.client.post(
                "/api/triage", json={"chiefComplaint": "Mild seasonal cold symptoms"}
            )
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertFalse(data["isEmergencyRedFlag"])
            self.assertIsNone(data.get("patientContext"))

    def test_triage_patient_role_without_patient_id_succeeds(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            self._register_and_login("patient-triage@example.com", "PATIENT")
            response = self.client.post(
                "/api/triage", json={"chiefComplaint": "Mild seasonal cold symptoms"}
            )
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertFalse(data["isEmergencyRedFlag"])
            self.assertIsNone(data.get("patientContext"))

    def test_triage_unauthenticated_with_patient_id_omits_member_linked_data(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            response = self.client.post(
                "/api/triage",
                json={"chiefComplaint": "Feeling generally tired and low energy", "patientId": "1"},
            )
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIsNone(data.get("patientContext"))
            self.assertIsNone(data.get("proactiveRecommendation"))
            # Member 1 is HIGH risk; if that leaked through even indirectly,
            # the acuity would be nudged to PRIMARY_CARE instead of the
            # generic (non-risk-adjusted) default for this chief complaint.
            self.assertNotEqual(data["recommendedAcuity"], "PRIMARY_CARE")

    def test_triage_patient_role_with_patient_id_omits_member_linked_data(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            self._register_and_login("patient-linked@example.com", "PATIENT")
            response = self.client.post(
                "/api/triage",
                json={"chiefComplaint": "Feeling generally tired and low energy", "patientId": "1"},
            )
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIsNone(data.get("patientContext"))
            self.assertIsNone(data.get("proactiveRecommendation"))
            self.assertNotEqual(data["recommendedAcuity"], "PRIMARY_CARE")

    def test_triage_payer_role_with_patient_id_returns_member_linked_data(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            self._register_and_login("payer-linked@example.com", "PAYER")
            response = self.client.post(
                "/api/triage",
                json={"chiefComplaint": "Feeling generally tired and low energy", "patientId": "1"},
            )
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIsNotNone(data.get("patientContext"))
            self.assertEqual(data["patientContext"]["beneficiaryId"], "CMS-AUTHZ-001")
            self.assertEqual(data["patientContext"]["riskLevel"], "HIGH")
            self.assertIsNotNone(data.get("proactiveRecommendation"))
            # The high-risk linkage MAY influence acuity for an authorized PAYER.
            self.assertEqual(data["recommendedAcuity"], "PRIMARY_CARE")

    def test_triage_safety_engine_behavior_unaffected_by_authorization(self) -> None:
        """The emergency safety-engine decision must be identical across
        unauthenticated, PATIENT, and PAYER (member-linked) callers."""
        emergency_payload = {
            "chiefComplaint": "Crushing chest pain and severe shortness of breath",
            "hasRedFlags": True,
        }

        with patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            anon_data = self.client.post("/api/triage", json=emergency_payload).get_json()

        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            self._register_and_login("patient-safety@example.com", "PATIENT")
            patient_data = self.client.post("/api/triage", json=emergency_payload).get_json()

        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            self._register_and_login("payer-safety@example.com", "PAYER")
            payer_data = self.client.post(
                "/api/triage", json={**emergency_payload, "patientId": "1"}
            ).get_json()

        for data in (anon_data, patient_data, payer_data):
            self.assertTrue(data["isEmergencyRedFlag"])
            self.assertEqual(data["recommendedAcuity"], "EMERGENCY")
            self.assertEqual(data["urgencyLevel"], "IMMEDIATE_911_ER")
            self.assertEqual(data["triggeredRules"], anon_data["triggeredRules"])
            self.assertEqual(data["suitableProviders"], [])

    # ------------------------------------------------------------------
    # POST /api/navigation/action member-linkage boundary
    # ------------------------------------------------------------------

    def test_navigation_action_anonymous_without_patient_id_succeeds(self) -> None:
        with patch("backend.routes.navigation.session_scope", self._mock_session_scope):
            self._seed_anonymous_encounter("anon-session-no-patient")
            response = self.client.post(
                "/api/navigation/action",
                json={
                    "actionType": "PROVIDER_SELECTED",
                    "sessionId": "anon-session-no-patient",
                    "selectedProviderId": "prov-01",
                    "selectedAcuity": "TELEHEALTH",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["status"], "recorded")

    def test_navigation_action_anonymous_with_patient_id_does_not_link_member(self) -> None:
        with patch("backend.routes.navigation.session_scope", self._mock_session_scope):
            self._seed_anonymous_encounter("anon-session-with-patient")
            response = self.client.post(
                "/api/navigation/action",
                json={
                    "actionType": "PROVIDER_SELECTED",
                    "sessionId": "anon-session-with-patient",
                    "patientId": "1",
                    "selectedProviderId": "prov-01",
                    "selectedAcuity": "TELEHEALTH",
                },
            )
            self.assertEqual(response.status_code, 200)
            action_id = response.get_json()["actionId"]

        action = self.db_session.query(NavigationAction).filter_by(id=action_id).first()
        self.assertIsNotNone(action)
        self.assertIsNone(action.member_id)

    def test_navigation_action_patient_role_with_patient_id_does_not_link_member(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.navigation.session_scope", self._mock_session_scope):
            self._seed_anonymous_encounter("patient-session-with-patient")
            self._register_and_login("patient-nav@example.com", "PATIENT")
            response = self.client.post(
                "/api/navigation/action",
                json={
                    "actionType": "PROVIDER_SELECTED",
                    "sessionId": "patient-session-with-patient",
                    "patientId": "1",
                    "selectedProviderId": "prov-01",
                    "selectedAcuity": "TELEHEALTH",
                },
            )
            self.assertEqual(response.status_code, 200)
            action_id = response.get_json()["actionId"]

        action = self.db_session.query(NavigationAction).filter_by(id=action_id).first()
        self.assertIsNotNone(action)
        self.assertIsNone(action.member_id)

    def test_navigation_action_payer_role_with_patient_id_links_member(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.navigation.session_scope", self._mock_session_scope):
            self._register_and_login("payer-nav@example.com", "PAYER")
            response = self.client.post(
                "/api/navigation/action",
                json={
                    "actionType": "PROVIDER_SELECTED",
                    "patientId": "1",
                    "selectedProviderId": "prov-01",
                    "selectedAcuity": "TELEHEALTH",
                },
            )
            self.assertEqual(response.status_code, 200)
            action_id = response.get_json()["actionId"]

        action = self.db_session.query(NavigationAction).filter_by(id=action_id).first()
        self.assertIsNotNone(action)
        self.assertEqual(action.member_id, 1)


if __name__ == "__main__":
    unittest.main()
