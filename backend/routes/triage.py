"""Public API routes for symptom triage endpoints.

The anonymous/basic symptom-assessment path stays fully public and
unauthenticated, including when the deterministic Safety Engine detects an
emergency. If the request additionally supplies a patientId, member-linked
ML enrichment (patientContext / proactiveRecommendation) is only resolved
for an authenticated PAYER caller -- see triage_service.process_triage_request.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.services import triage_service
from backend.services.auth_service import get_current_user

triage_blueprint = Blueprint("triage", __name__, url_prefix="/api/triage")


@triage_blueprint.post("", strict_slashes=False)
def triage():
    """POST /api/triage - Screen symptoms via deterministic Safety Engine."""
    if request.content_length and not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400

    data = request.get_json(silent=True)
    if data is None:
        data = {}

    current_user = get_current_user()
    allow_member_linkage = current_user is not None and current_user.role == "PAYER"

    response = triage_service.process_triage_request(
        data, allow_member_linkage=allow_member_linkage
    )
    return jsonify(response)
