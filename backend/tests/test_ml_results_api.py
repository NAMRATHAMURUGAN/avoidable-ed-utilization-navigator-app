"""Unit tests for the read-only /api/ml/* generated ML result endpoints.

These endpoints read already-generated artifacts (processed_data/*.csv,
ml_models/*.json) directly from disk; they never touch PostgreSQL and never
load a trained model (.pkl) file. Tests run against the real generated
artifacts already present in this repository and do not modify them. The
artifact-unavailable test safely substitutes the artifact directory via
mocking so it exercises the not-found error path without touching, moving,
or deleting any real project artifact.
"""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from backend.app import create_app


class MlResultsApiTestCase(unittest.TestCase):
    """Test suite for GET /api/ml/* generated ML result endpoints."""

    def setUp(self) -> None:
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_health(self) -> None:
        response = self.client.get("/api/ml/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok", "service": "ml-results"})

    def test_risk_predictions_structure_and_fields(self) -> None:
        response = self.client.get("/api/ml/risk-predictions")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

        record = data[0]
        for field in (
            "BENE_ID",
            "high_utilization_pattern",
            "predicted_high_utilization_pattern",
            "high_utilization_probability",
            "dataset_split",
        ):
            self.assertIn(field, record)

        # high_utilization_probability is a historical-pattern probability in [0, 1];
        # it must not be conflated with a clinical or emergency-necessity score.
        self.assertIsInstance(record["high_utilization_probability"], float)
        self.assertGreaterEqual(record["high_utilization_probability"], 0.0)
        self.assertLessEqual(record["high_utilization_probability"], 1.0)
        self.assertIn(record["high_utilization_pattern"], (0, 1))
        self.assertIn(record["predicted_high_utilization_pattern"], (0, 1))

    def test_anomalies_structure_and_fields(self) -> None:
        response = self.client.get("/api/ml/anomalies")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

        record = data[0]
        for field in (
            "BENE_ID",
            "anomaly_score",
            "anomaly_flag",
            "anomaly_rank",
            "model_version",
            "generated_at",
        ):
            self.assertIn(field, record)

        self.assertIsInstance(record["anomaly_score"], float)
        self.assertIn(record["anomaly_flag"], (0, 1))
        self.assertIsInstance(record["anomaly_rank"], int)
        self.assertGreater(record["anomaly_rank"], 0)
        self.assertIsInstance(record["model_version"], str)
        self.assertTrue(record["model_version"])
        self.assertIsInstance(record["generated_at"], str)
        self.assertTrue(record["generated_at"])

    def test_anomaly_summary_structure_and_fields(self) -> None:
        response = self.client.get("/api/ml/anomalies/summary")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        for field in (
            "total_members",
            "number_of_anomalies",
            "anomaly_percentage",
            "contamination_experiments",
            "selected_configuration",
            "xgboost_post_hoc_overlap_counts",
        ):
            self.assertIn(field, data)

        self.assertIsInstance(data["total_members"], int)
        self.assertGreater(data["total_members"], 0)
        self.assertIsInstance(data["number_of_anomalies"], int)
        self.assertIsInstance(data["contamination_experiments"], list)
        self.assertGreater(len(data["contamination_experiments"]), 0)
        self.assertIsInstance(data["selected_configuration"], dict)
        self.assertIsInstance(data["xgboost_post_hoc_overlap_counts"], dict)

        # The anomaly score must never be described as a probability.
        self.assertIn("limitation", data)
        self.assertNotIn("probability", data["limitation"].lower())

    def test_overlap_structure_and_fields(self) -> None:
        response = self.client.get("/api/ml/overlap")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

        record = data[0]
        for field in (
            "BENE_ID",
            "anomaly_score",
            "anomaly_flag",
            "high_utilization_pattern",
            "comparison_group",
        ):
            self.assertIn(field, record)

    def test_models_metadata_and_non_clinical_limitations(self) -> None:
        response = self.client.get("/api/ml/models")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertIn("xgboost", data)
        self.assertIn("baseline_metrics", data["xgboost"])
        self.assertIn("tuned_metrics", data["xgboost"])
        self.assertIn("test_metrics", data["xgboost"]["baseline_metrics"])

        self.assertIn("isolation_forest", data)
        self.assertIn("metrics", data["isolation_forest"])
        self.assertIn("feature_metadata", data["isolation_forest"])

        # The explicit non-clinical limitations must be present and must not
        # imply medical necessity, emergency necessity, or ED-avoidability.
        self.assertIn("limitations", data)
        xgboost_limitation = data["limitations"]["xgboost"].lower()
        isolation_limitation = data["limitations"]["isolation_forest"].lower()

        self.assertIn("not an emergency-necessity determination", xgboost_limitation)
        self.assertIn("medical necessity", isolation_limitation)
        self.assertIn("ed avoidability", isolation_limitation)

    def test_risk_predictions_returns_503_when_artifact_unavailable(self) -> None:
        """Safely substitute the artifact directory to exercise the not-found
        error path. No real project artifact is modified, moved, or deleted."""
        with patch(
            "backend.services.ml_results_service.PROCESSED_DATA_DIR",
            Path("/nonexistent-ml-results-directory-for-testing"),
        ):
            response = self.client.get("/api/ml/risk-predictions")
            self.assertEqual(response.status_code, 503)
            data = response.get_json()
            self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main()
