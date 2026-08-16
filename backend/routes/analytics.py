"""PAYER-only API routes for population analytics endpoints.

Population-level utilization and spend analytics are treated as payer-side
information per current product direction.
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from backend.auth.decorators import role_required
from backend.services import analytics_service

analytics_blueprint = Blueprint("analytics", __name__, url_prefix="/api/analytics")


@analytics_blueprint.get("", strict_slashes=False)
@role_required("PAYER")
def get_analytics():
    """GET /api/analytics - Return aggregate population ED analytics."""
    data = analytics_service.get_population_analytics()
    return jsonify(data)
