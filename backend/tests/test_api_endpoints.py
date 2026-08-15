"""Unit tests for Flask core public API endpoints using in-memory SQLite database.

Tests database-backed MemberRepository, patient_service, analytics_service,
and triage_service against an isolated in-memory SQLite database.
"""

from __future__ import annotations

from contextlib import contextmanager
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
    UtilizationAnomalyResult,
    XGBoostUtilizationPrediction,
)
from backend.repositories.member_repository import MemberRepository, MemberAnalyticalResult
from backend.services.patient_service import member_analytical_result_to_dict


class ApiEndpointsTestCase(unittest.TestCase):
    """Test suite for Flask core API endpoints and risk/triage behavior."""

    def setUp(self) -> None:
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

        # In-memory SQLite database engine for isolated unit testing
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.db_session: Session = self.SessionLocal()

        self._seed_database()
        self.triage_scope_patcher = patch(
            "backend.services.triage_service.session_scope", self._mock_session_scope
        )
        self.provider_scope_patcher = patch(
            "backend.services.provider_service.session_scope", self._mock_session_scope
        )
        self.triage_scope_patcher.start()
        self.provider_scope_patcher.start()

    def tearDown(self) -> None:
        self.triage_scope_patcher.stop()
        self.provider_scope_patcher.stop()
        self.db_session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @contextmanager
    def _mock_session_scope(self):
        yield self.db_session

    def _seed_database(self) -> None:
        xgb_run = ModelRun(
            model_run_id=1,
            model_type="xgboost",
            purpose="utilization prediction",
            artifact_reference="ml_models/xgboost_risk_model.pkl",
            configuration={"ingestion_context": {"dataset_identifier": "test-dataset-sha256"}},
        )
        anomaly_run = ModelRun(
            model_run_id=2,
            model_type="isolation_forest",
            purpose="utilization anomaly",
            artifact_reference="ml_models/isolation_forest.pkl",
            configuration={"ingestion_context": {"dataset_identifier": "test-dataset-sha256"}},
        )
        self.db_session.add_all([xgb_run, anomaly_run])
        self.db_session.flush()

        # Member 1 (High risk via high probability & anomaly)
        m1 = Member(
            id=1,
            bene_id="CMS-A894-201",
            age=72,
            gender="Female",
            dual_eligibility_months=0,
            chronic_condition_count=3,
        )
        self.db_session.add(m1)
        self.db_session.flush()

        s1 = MemberUtilizationSnapshot(
            id=1,
            member_id=m1.id,
            inpatient_visit_count=1,
            inpatient_total_cost=Decimal("1850.00"),
            outpatient_visit_count=3,
            outpatient_total_cost=Decimal("450.00"),
            ed_visit_count=5,
            total_claim_payment_amount=Decimal("11250.00"),
            total_ed_related_cost=Decimal("8950.00"),
            average_claim_cost=Decimal("1250.00"),
            provider_count=4,
        )
        self.db_session.add(s1)
        self.db_session.flush()

        p1 = XGBoostUtilizationPrediction(
            id=1,
            member_id=m1.id,
            utilization_snapshot_id=s1.id,
            model_run_id=xgb_run.model_run_id,
            high_utilization_pattern=1,
            predicted_high_utilization_pattern=1,
            high_utilization_probability=0.85,
            dataset_split="test",
        )
        a1 = UtilizationAnomalyResult(
            id=1,
            member_id=m1.id,
            utilization_snapshot_id=s1.id,
            model_run_id=anomaly_run.model_run_id,
            anomaly_score=0.45,
            anomaly_flag=1,
            anomaly_rank=1,
            generated_at=datetime.now(timezone.utc),
        )
        self.db_session.add_all([p1, a1])

        # Member 2 (Low risk)
        m2 = Member(
            id=2,
            bene_id="CMS-B412-990",
            age=68,
            gender="Male",
            dual_eligibility_months=6,
            chronic_condition_count=1,
        )
        self.db_session.add(m2)
        self.db_session.flush()

        s2 = MemberUtilizationSnapshot(
            id=2,
            member_id=m2.id,
            inpatient_visit_count=0,
            inpatient_total_cost=Decimal("0.00"),
            outpatient_visit_count=1,
            outpatient_total_cost=Decimal("120.00"),
            ed_visit_count=1,
            total_claim_payment_amount=Decimal("450.00"),
            total_ed_related_cost=Decimal("330.00"),
            average_claim_cost=Decimal("225.00"),
            provider_count=1,
        )
        self.db_session.add(s2)
        self.db_session.flush()

        p2 = XGBoostUtilizationPrediction(
            id=2,
            member_id=m2.id,
            utilization_snapshot_id=s2.id,
            model_run_id=xgb_run.model_run_id,
            high_utilization_pattern=0,
            predicted_high_utilization_pattern=0,
            high_utilization_probability=0.15,
            dataset_split="test",
        )
        a2 = UtilizationAnomalyResult(
            id=2,
            member_id=m2.id,
            utilization_snapshot_id=s2.id,
            model_run_id=anomaly_run.model_run_id,
            anomaly_score=-0.12,
            anomaly_flag=0,
            anomaly_rank=50,
            generated_at=datetime.now(timezone.utc),
        )
        self.db_session.add_all([p2, a2])

        # Member 3 (Member without snapshot or ML predictions - verifying NO MEMBER LOSS)
        m3 = Member(
            id=3,
            bene_id="CMS-C902-114",
            age=81,
            gender="Female",
            dual_eligibility_months=12,
            chronic_condition_count=0,
        )
        self.db_session.add(m3)

        self.db_session.commit()

    def test_member_repository_bulk_query_no_member_loss(self) -> None:
        repo = MemberRepository(self.db_session)
        results = repo.get_all_combined_results(xgb_model_run_id=1, anomaly_model_run_id=2)
        self.assertEqual(len(results), 3, "All 3 members must be returned regardless of missing ML/snapshot data.")
        self.assertEqual(results[2].member.bene_id, "CMS-C902-114")
        self.assertIsNone(results[2].utilization_snapshot)
        self.assertIsNone(results[2].xgboost_prediction)
        self.assertIsNone(results[2].anomaly_result)

    def test_risk_derivation_boundary_cases(self) -> None:
        m = Member(id=99, bene_id="TEST-MEM", age=70, gender="F", dual_eligibility_months=0, chronic_condition_count=1)

        def make_res(prob=None, flag=0, visits=0):
            xgb = XGBoostUtilizationPrediction(high_utilization_probability=prob) if prob is not None else None
            anom = UtilizationAnomalyResult(anomaly_flag=flag)
            snap = MemberUtilizationSnapshot(ed_visit_count=visits, total_ed_related_cost=Decimal("0"))
            return member_analytical_result_to_dict(MemberAnalyticalResult(m, snap, xgb, anom))

        # prob > 0.7 -> HIGH
        self.assertEqual(make_res(prob=0.71)["riskLevel"], "HIGH")

        # prob == 0.7 -> MODERATE
        self.assertEqual(make_res(prob=0.70)["riskLevel"], "MODERATE")

        # prob > 0.4 -> MODERATE
        self.assertEqual(make_res(prob=0.41)["riskLevel"], "MODERATE")

        # prob == 0.4 -> LOW
        self.assertEqual(make_res(prob=0.40)["riskLevel"], "LOW")

        # anomaly_flag == 1 -> HIGH
        self.assertEqual(make_res(prob=0.10, flag=1)["riskLevel"], "HIGH")

        # High ED visits (> 2) without ML prediction -> MODERATE
        self.assertEqual(make_res(prob=None, flag=0, visits=3)["riskLevel"], "MODERATE")

        # Missing snapshot & prediction -> LOW
        missing_res = member_analytical_result_to_dict(MemberAnalyticalResult(m, None, None, None))
        self.assertEqual(missing_res["riskLevel"], "LOW")
        self.assertEqual(missing_res["edVisitCount12m"], 0)

    def test_get_patients_returns_all_from_database(self) -> None:
        with patch("backend.services.patient_service.session_scope", self._mock_session_scope):
            response = self.client.get("/api/patients")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIsInstance(data, list)
            self.assertEqual(len(data), 3, "All 3 members must be visible in API response.")
            self.assertEqual(data[0]["beneficiaryId"], "CMS-A894-201")
            self.assertEqual(data[0]["riskLevel"], "HIGH")
            self.assertEqual(data[2]["beneficiaryId"], "CMS-C902-114")
            self.assertEqual(data[2]["riskLevel"], "LOW")

    def test_get_patient_by_id_found(self) -> None:
        with patch("backend.services.patient_service.session_scope", self._mock_session_scope):
            response = self.client.get("/api/patients/1")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["beneficiaryId"], "CMS-A894-201")

            response_bene = self.client.get("/api/patients/CMS-B412-990")
            self.assertEqual(response_bene.status_code, 200)
            data_bene = response_bene.get_json()
            self.assertEqual(data_bene["beneficiaryId"], "CMS-B412-990")

    def test_get_patient_by_id_not_found(self) -> None:
        with patch("backend.services.patient_service.session_scope", self._mock_session_scope):
            response = self.client.get("/api/patients/nonexistent-id")
            self.assertEqual(response.status_code, 404)
            data = response.get_json()
            self.assertEqual(data, {"error": "Patient not found"})

    def test_get_analytics_returns_database_aggregations(self) -> None:
        with patch("backend.services.analytics_service.session_scope", self._mock_session_scope):
            response = self.client.get("/api/analytics")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["totalPatients"], 3)
            self.assertEqual(data["totalEdVisits"], 6)  # 5 + 1 + 0
            self.assertEqual(data["totalEdSpend"], 9280.0)  # 8950.0 + 330.0
            self.assertIsNone(data["avoidableEdVisitsCount"])  # Documented DEFERRED
            self.assertEqual(data["nyuCategoryBreakdown"], [])  # Documented DEFERRED

    def test_post_triage_happy_path_non_emergency(self) -> None:
        payload = {
            "chiefComplaint": "Minor finger cut while cooking",
            "symptomsDuration": "1 hour",
            "associatedSymptoms": [],
            "selectedRedFlags": [],
            "hasRedFlags": False,
        }
        response = self.client.post("/api/triage", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertFalse(data["isEmergencyRedFlag"])
        self.assertEqual(data["recommendedAcuity"], "URGENT_CARE")
        self.assertEqual(data["urgencyLevel"], "SAME_DAY_URGENT")
        self.assertGreater(len(data["suitableProviders"]), 0)

    def test_post_triage_emergency_red_flag(self) -> None:
        payload = {
            "chiefComplaint": "Severe chest tightness and shortness of breath",
            "symptomsDuration": "20 minutes",
            "associatedSymptoms": ["dizziness"],
            "selectedRedFlags": [],
            "hasRedFlags": True,
        }
        response = self.client.post("/api/triage", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["isEmergencyRedFlag"])
        self.assertEqual(data["recommendedAcuity"], "EMERGENCY")
        self.assertEqual(data["urgencyLevel"], "IMMEDIATE_911_ER")
        self.assertEqual(len(data["suitableProviders"]), 0)

    def test_post_triage_malformed_input_handling(self) -> None:
        response = self.client.post("/api/triage", data="invalid json string", content_type="text/plain")
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(data, {"error": "Content-Type must be application/json"})

        # Empty JSON object is invalid; a clinical recommendation cannot be inferred.
        empty_res = self.client.post("/api/triage", json={})
        self.assertEqual(empty_res.status_code, 400)
        self.assertIn("chiefComplaint", empty_res.get_json()["error"])

        array_res = self.client.post("/api/triage", json=[])
        self.assertEqual(array_res.status_code, 400)
        self.assertEqual(array_res.content_type, "application/json")

    def test_post_triage_valid_member_and_unknown_member_validation(self) -> None:
        valid = self.client.post("/api/triage", json={
            "patientId": "CMS-A894-201",
            "chiefComplaint": "Medication review follow-up",
            "associatedSymptoms": [],
            "selectedRedFlags": [],
            "hasRedFlags": False,
        })
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(valid.get_json()["patientContext"]["beneficiaryId"], "CMS-A894-201")

        unknown = self.client.post("/api/triage", json={
            "patientId": "CMS-UNKNOWN-1",
            "chiefComplaint": "Medication review",
        })
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.get_json(), {"error": "Patient not found"})

    def test_post_triage_rejects_invalid_field_types_and_lengths(self) -> None:
        invalid_payloads = [
            {"chiefComplaint": 4},
            {"chiefComplaint": "Rash", "symptomsDuration": 1},
            {"chiefComplaint": "Rash", "associatedSymptoms": "fever"},
            {"chiefComplaint": "Rash", "selectedRedFlags": ["Chest pain", 1]},
            {"chiefComplaint": "Rash", "hasRedFlags": "false"},
            {"chiefComplaint": "x" * 1001},
            {"chiefComplaint": "Rash", "sessionId": "bad session"},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.client.post("/api/triage", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertIsInstance(response.get_json(), dict)

    def test_get_providers_returns_compatibility_providers(self) -> None:
        response = self.client.get("/api/providers")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 5)

    def test_provider_query_validation(self) -> None:
        for query in ("?maxDistance=bad", "?maxDistance=-1", "?maxDistance=nan", "?maxDistance=501", "?type=HOSPITAL"):
            with self.subTest(query=query):
                response = self.client.get("/api/providers" + query)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.content_type, "application/json")

    def test_patient_query_and_identifier_validation(self) -> None:
        self.assertEqual(self.client.get("/api/patients?risk=unknown").status_code, 400)
        self.assertEqual(self.client.get("/api/patients?search=" + ("x" * 101)).status_code, 400)
        self.assertEqual(self.client.get("/api/patients/" + ("x" * 65)).status_code, 400)

    def test_flask_errors_are_json(self) -> None:
        response = self.client.get("/api/not-a-route")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content_type, "application/json")
        self.assertIn("error", response.get_json())

    def test_existing_ml_results_health(self) -> None:
        response = self.client.get("/api/ml/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data, {"status": "ok", "service": "ml-results"})


if __name__ == "__main__":
    unittest.main()
