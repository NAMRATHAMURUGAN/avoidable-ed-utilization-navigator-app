"""Read-only routes for generated ML outputs."""

from __future__ import annotations

from flask import Blueprint, jsonify

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
def risk_predictions():
    return _service_response(ml_results_service.read_risk_predictions)


@ml_results_blueprint.get("/anomalies")
def anomalies():
    return _service_response(ml_results_service.read_anomalies)


@ml_results_blueprint.get("/anomalies/summary")
def anomaly_summary():
    return _service_response(ml_results_service.read_anomaly_summary)


@ml_results_blueprint.get("/overlap")
def overlap():
    return _service_response(ml_results_service.read_overlap)


@ml_results_blueprint.get("/models")
def models():
    return _service_response(ml_results_service.read_model_metadata)
