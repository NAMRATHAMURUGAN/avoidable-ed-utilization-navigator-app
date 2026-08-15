"""Public API routes for patient cohort and detail endpoints."""

from __future__ import annotations

import re

from flask import Blueprint, jsonify, request

from backend.services import patient_service

patients_blueprint = Blueprint("patients", __name__, url_prefix="/api/patients")
VALID_RISK_LEVELS = {"HIGH", "MODERATE", "LOW"}
MAX_SEARCH_LENGTH = 100
MAX_PATIENT_ID_LENGTH = 64
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")


def validate_patient_identifier(patient_id: str) -> str | None:
    if not patient_id or len(patient_id) > MAX_PATIENT_ID_LENGTH:
        return "patient ID must be between 1 and 64 characters"
    if not _IDENTIFIER_PATTERN.fullmatch(patient_id):
        return "patient ID contains invalid characters"
    return None


@patients_blueprint.get("", strict_slashes=False)
def get_patients():
    """GET /api/patients - Return synthetic/CMS patient cohort with optional filters."""
    risk = request.args.get("risk")
    search = request.args.get("search")
    if risk is not None and risk.upper() not in VALID_RISK_LEVELS:
        return jsonify({"error": "risk must be one of: HIGH, MODERATE, LOW"}), 400
    if search is not None and len(search) > MAX_SEARCH_LENGTH:
        return jsonify({"error": f"search must not exceed {MAX_SEARCH_LENGTH} characters"}), 400
    results = patient_service.get_patients(risk=risk, search=search)
    return jsonify(results)


@patients_blueprint.get("/<patient_id>", strict_slashes=False)
def get_patient_by_id(patient_id: str):
    """GET /api/patients/<id> - Return single patient detail or 404."""
    error = validate_patient_identifier(patient_id)
    if error:
        return jsonify({"error": error}), 400
    patient = patient_service.get_patient_by_id(patient_id)
    if patient is None:
        return jsonify({"error": "Patient not found"}), 404
    return jsonify(patient)
