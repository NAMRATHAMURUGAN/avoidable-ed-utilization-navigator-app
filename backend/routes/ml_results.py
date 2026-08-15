"""Read-only routes for generated ML outputs.

All routes except the health check are PAYER-only: they expose raw,
per-member historical-utilization-pattern and utilization-anomaly output
across the full population, which must never be reachable by an
unauthenticated caller or a PATIENT.
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from backend.auth.decorators import role_required
from backend.models.ml_results import ArtifactNotFoundError, MLResultsServiceError
from backend.services import ml_results_service


ml_results_blueprint = Blueprint("ml_results", __name__, url_prefix="/api/ml")


def _service_response(loader):
    """Convert expected artifact errors into safe JSON API responses."""
    try:
        return jsonify(loader())
    except ArtifactNotFoundError as error:
        return jsonify({"error": str(error)}), 503
    except MLResultsServiceError as error:
        return jsonify({"error": str(error)}), 500


@ml_results_blueprint.get("/health")
def health():
    return jsonify({"status": "ok", "service": "ml-results"})


@ml_results_blueprint.get("/risk-predictions")
@role_required("PAYER")
def risk_predictions():
    return _service_response(ml_results_service.read_risk_predictions)


@ml_results_blueprint.get("/anomalies")
@role_required("PAYER")
def anomalies():
    return _service_response(ml_results_service.read_anomalies)


@ml_results_blueprint.get("/anomalies/summary")
@role_required("PAYER")
def anomaly_summary():
    return _service_response(ml_results_service.read_anomaly_summary)


@ml_results_blueprint.get("/overlap")
@role_required("PAYER")
def overlap():
    return _service_response(ml_results_service.read_overlap)


@ml_results_blueprint.get("/models")
@role_required("PAYER")
def models():
    return _service_response(ml_results_service.read_model_metadata)
