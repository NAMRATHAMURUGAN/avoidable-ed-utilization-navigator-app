"""Public API routes for patient cohort and detail endpoints."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.services import patient_service

patients_blueprint = Blueprint("patients", __name__, url_prefix="/api/patients")


@patients_blueprint.get("", strict_slashes=False)
def get_patients():
    """GET /api/patients - Return synthetic/CMS patient cohort with optional filters."""
    risk = request.args.get("risk")
    search = request.args.get("search")
    results = patient_service.get_patients(risk=risk, search=search)
    return jsonify(results)


@patients_blueprint.get("/<patient_id>", strict_slashes=False)
def get_patient_by_id(patient_id: str):
    """GET /api/patients/<id> - Return single patient detail or 404."""
    patient = patient_service.get_patient_by_id(patient_id)
    if patient is None:
        return jsonify({"error": "Patient not found"}), 404
    return jsonify(patient)
