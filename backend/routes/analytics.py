"""Public API routes for population analytics endpoints."""

from __future__ import annotations

from flask import Blueprint, jsonify

from backend.services import analytics_service

analytics_blueprint = Blueprint("analytics", __name__, url_prefix="/api/analytics")


@analytics_blueprint.get("", strict_slashes=False)
def get_analytics():
    """GET /api/analytics - Return aggregate population ED analytics."""
    data = analytics_service.get_population_analytics()
    return jsonify(data)
