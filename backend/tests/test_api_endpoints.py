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

    def tearDown(self) -> None:
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

    def test_risk_level_interpretation_is_non_clinical(self) -> None:
        m = Member(id=98, bene_id="TEST-INTERP", age=60, gender="F", dual_eligibility_months=0, chronic_condition_count=0)
        xgb = XGBoostUtilizationPrediction(high_utilization_probability=0.9)
        anom = UtilizationAnomalyResult(anomaly_flag=1)
        snap = MemberUtilizationSnapshot(ed_visit_count=5, total_ed_related_cost=Decimal("0"))
        result = member_analytical_result_to_dict(MemberAnalyticalResult(m, snap, xgb, anom))

        self.assertEqual(result["riskLevel"], "HIGH")
        self.assertIn("riskLevelInterpretation", result)
        interpretation = result["riskLevelInterpretation"].lower()
        self.assertIn("historical high-utilization-pattern", interpretation)
        self.assertIn("utilization-anomaly", interpretation)
        self.assertIn("not a medical necessity determination", interpretation)
        self.assertIn("emergency necessity determination", interpretation)
        self.assertIn("clinical deterioration", interpretation)
        self.assertIn("inappropriate ed use", interpretation)
        self.assertIn("ed avoidability", interpretation)

    def test_get_patients_returns_all_from_database(self) -> None:
        with patch("backend.services.patient_service.session_scope", self._mock_session_scope):
            response = self.client.get("/api/patients")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIsInstance(data, list)
            self.assertEqual(len(data), 3, "All 3 members must be visible in API response.")
            self.assertEqual(data[0]["beneficiaryId"], "CMS-A894-201")
            self.assertEqual(data[0]["riskLevel"], "HIGH")
            self.assertIn("riskLevelInterpretation", data[0])
            self.assertIn("not a medical necessity determination", data[0]["riskLevelInterpretation"])
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

    def test_triage_uses_latest_xgboost_and_anomaly_model_run(self) -> None:
        """Prove /api/triage links a member's XGBoost prediction and Isolation Forest
        anomaly via the explicitly resolved *latest* model run, not by falling back to
        whichever row happens to have the highest database row ID.

        A stale model run's prediction/anomaly rows are given lower row IDs than the
        latest model run's rows (by inserting the latest rows first). A naive
        "order by row ID descending, ignore model_run_id" lookup would therefore
        return the stale (higher-ID) rows here; only explicit model_run_id scoping
        via ModelRunRepository.get_latest(...) returns the correct latest values.
        """
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            member = Member(
                id=501,
                bene_id="CMS-SCOPE-501",
                age=59,
                gender="Female",
                dual_eligibility_months=0,
                chronic_condition_count=1,
            )
            self.db_session.add(member)
            self.db_session.flush()

            snapshot = MemberUtilizationSnapshot(
                id=501,
                member_id=member.id,
                inpatient_visit_count=0,
                inpatient_total_cost=Decimal("0.00"),
                outpatient_visit_count=1,
                outpatient_total_cost=Decimal("100.00"),
                ed_visit_count=1,
                total_claim_payment_amount=Decimal("100.00"),
                total_ed_related_cost=Decimal("50.00"),
                average_claim_cost=Decimal("100.00"),
                provider_count=1,
            )
            self.db_session.add(snapshot)
            self.db_session.flush()

            stale_xgb_run = ModelRun(
                model_run_id=101,
                model_type="xgboost",
                purpose="utilization prediction",
                artifact_reference="ml_models/xgboost_risk_model_stale_test.pkl",
                generated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
            latest_xgb_run = ModelRun(
                model_run_id=102,
                model_type="xgboost",
                purpose="utilization prediction",
                artifact_reference="ml_models/xgboost_risk_model_latest_test.pkl",
                generated_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
            )
            stale_anomaly_run = ModelRun(
                model_run_id=103,
                model_type="isolation_forest",
                purpose="utilization anomaly",
                artifact_reference="ml_models/isolation_forest_stale_test.pkl",
                generated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
            latest_anomaly_run = ModelRun(
                model_run_id=104,
                model_type="isolation_forest",
                purpose="utilization anomaly",
                artifact_reference="ml_models/isolation_forest_latest_test.pkl",
                generated_at=datetime(2025, 6, 1, tzinfo=timezone.utc),
            )
            self.db_session.add_all(
                [stale_xgb_run, latest_xgb_run, stale_anomaly_run, latest_anomaly_run]
            )
            self.db_session.flush()

            # Insert the LATEST run's rows first (lower row IDs) and the STALE run's
            # rows second (higher row IDs) so a row-ID fallback would pick the wrong one.
            latest_prediction = XGBoostUtilizationPrediction(
                id=501,
                member_id=member.id,
                utilization_snapshot_id=snapshot.id,
                model_run_id=latest_xgb_run.model_run_id,
                high_utilization_pattern=0,
                predicted_high_utilization_pattern=0,
                high_utilization_probability=0.20,
                dataset_split="test",
            )
            stale_prediction = XGBoostUtilizationPrediction(
                id=502,
                member_id=member.id,
                utilization_snapshot_id=snapshot.id,
                model_run_id=stale_xgb_run.model_run_id,
                high_utilization_pattern=1,
                predicted_high_utilization_pattern=1,
                high_utilization_probability=0.95,
                dataset_split="test",
            )
            latest_anomaly = UtilizationAnomalyResult(
                id=501,
                member_id=member.id,
                utilization_snapshot_id=snapshot.id,
                model_run_id=latest_anomaly_run.model_run_id,
                anomaly_score=-0.10,
                anomaly_flag=0,
                anomaly_rank=99,
                generated_at=datetime.now(timezone.utc),
            )
            stale_anomaly = UtilizationAnomalyResult(
                id=502,
                member_id=member.id,
                utilization_snapshot_id=snapshot.id,
                model_run_id=stale_anomaly_run.model_run_id,
                anomaly_score=0.80,
                anomaly_flag=1,
                anomaly_rank=1,
                generated_at=datetime.now(timezone.utc),
            )
            self.db_session.add_all(
                [latest_prediction, stale_prediction, latest_anomaly, stale_anomaly]
            )
            self.db_session.commit()

            payload = {
                "patientId": "501",
                "chiefComplaint": "Annual checkup and medication review",
            }
            response = self.client.post("/api/triage", json=payload)
            self.assertEqual(response.status_code, 200)
            data = response.get_json()

            self.assertIsNotNone(data.get("patientContext"))
            # Must reflect the LATEST model run (0.20 probability, no anomaly flag),
            # not the stale run (0.95 probability, anomaly flag set) that a row-ID
            # fallback lookup would incorrectly return.
            self.assertAlmostEqual(data["patientContext"]["highUtilizationProbability"], 0.20)
            self.assertFalse(data["patientContext"]["anomalyFlag"])
            self.assertEqual(data["patientContext"]["riskLevel"], "LOW")
            self.assertIn("riskLevelInterpretation", data["patientContext"])

    def test_post_triage_malformed_input_handling(self) -> None:
        response = self.client.post("/api/triage", data="invalid json string", content_type="text/plain")
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertEqual(data, {"error": "Content-Type must be application/json"})

        # Empty JSON object should process safely without crashing
        empty_res = self.client.post("/api/triage", json={})
        self.assertEqual(empty_res.status_code, 200)
        empty_data = empty_res.get_json()
        self.assertFalse(empty_data["isEmergencyRedFlag"])

    def test_get_providers_returns_compatibility_providers(self) -> None:
        response = self.client.get("/api/providers")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 5)

    def test_existing_ml_results_health(self) -> None:
        response = self.client.get("/api/ml/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data, {"status": "ok", "service": "ml-results"})


if __name__ == "__main__":
    unittest.main()
