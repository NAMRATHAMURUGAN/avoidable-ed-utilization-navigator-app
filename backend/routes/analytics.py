"""PAYER-only API routes for population analytics endpoints.

Population-level utilization and spend analytics are treated as payer-side
information per current product direction.
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from backend.auth.decorators import role_required
from backend.services import analytics_service

analytics_blueprint = Blueprint("analytics", __name__, url_prefix="/api/analytics")

# Separate blueprint/prefix: RightPath (patient-app) activity intelligence is
# additive to, and never merged with, the CMS/member population analytics
# served from analytics_blueprint above.
payer_analytics_blueprint = Blueprint(
    "payer_analytics", __name__, url_prefix="/api/payer/analytics"
)


@analytics_blueprint.get("", strict_slashes=False)
@role_required("PAYER")
def get_analytics():
    """GET /api/analytics - Return aggregate population ED analytics."""
    data = analytics_service.get_population_analytics()
    return jsonify(data)


@payer_analytics_blueprint.get("/rightpath", strict_slashes=False)
@role_required("PAYER")
def get_rightpath_analytics():
    """GET /api/payer/analytics/rightpath - Return aggregate RightPath
    (patient-app) activity analytics: assessment/acuity/pathway counts and
    a daily activity trend, derived from triage_encounters and
    navigation_actions. Aggregate counts only -- never raw complaint text,
    action details, or patient-identifying fields."""
    data = analytics_service.get_rightpath_analytics()
    return jsonify(data)
