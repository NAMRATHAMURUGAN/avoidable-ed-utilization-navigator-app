"""Public API routes for symptom triage endpoints.

The anonymous/basic symptom-assessment path stays fully public and
unauthenticated, including when the deterministic Safety Engine detects an
emergency. Member-linked ML enrichment (patientContext / proactiveRecommendation)
comes from one of two independent, role-gated sources -- see
triage_service.process_triage_request:
  - An authenticated PAYER caller supplying a patientId (unchanged).
  - An authenticated PATIENT caller's own PatientMemberLink, resolved
    entirely server-side from their session identity -- a PATIENT-supplied
    patientId/member_id is never consulted for this.
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
        return jsonify({"error": "Malformed JSON request payload"}), 400

    current_user = get_current_user()
    allow_member_linkage = current_user is not None and current_user.role == "PAYER"
    # PATIENT-only self-link enrichment: resolved server-side from the
    # caller's own identity (see triage_service.process_triage_request /
    # patient_member_link_service), never from a client-supplied identifier.
    patient_self_link = current_user is not None and current_user.role == "PATIENT"
    # Identity/persistence linkage only, independent of the two gates above:
    # any authenticated caller's own encounter gets tagged with their user id
    # so it can later be retrieved via GET /api/navigation/my-history.
    user_id = current_user.id if current_user is not None else None

    try:
        response = triage_service.process_triage_request(
            data, allow_member_linkage=allow_member_linkage,
            user_id=user_id, patient_self_link=patient_self_link,
        )
    except triage_service.TriageValidationError as error:
        return jsonify({"error": str(error)}), 400
    except triage_service.TriageMemberNotFoundError as error:
        return jsonify({"error": str(error)}), 404
    except triage_service.TriagePersistenceError as error:
        return jsonify({"error": str(error)}), 503
    return jsonify(response)
