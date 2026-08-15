"""Public API routes for symptom triage endpoints."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.services import triage_service

triage_blueprint = Blueprint("triage", __name__, url_prefix="/api/triage")


@triage_blueprint.post("", strict_slashes=False)
def triage():
    """POST /api/triage - Screen symptoms via deterministic Safety Engine."""
    if request.content_length and not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400

    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Malformed JSON request payload"}), 400

    try:
        response = triage_service.process_triage_request(data)
    except triage_service.TriageValidationError as error:
        return jsonify({"error": str(error)}), 400
    except triage_service.TriageMemberNotFoundError as error:
        return jsonify({"error": str(error)}), 404
    return jsonify(response)
