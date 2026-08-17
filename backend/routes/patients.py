"""PAYER-only API routes for patient cohort and detail endpoints.

CMS Member/beneficiary records are payer-side analytical data; there is no
User<->Member mapping, so these routes are restricted to the PAYER role
rather than being scoped to an individual caller.
"""

from __future__ import annotations

import re

from flask import Blueprint, jsonify, request

from backend.auth.decorators import role_required
from backend.services import patient_service

patients_blueprint = Blueprint("patients", __name__, url_prefix="/api/patients")
VALID_RISK_LEVELS = {"HIGH", "MODERATE", "LOW"}
VALID_UTILIZATION_BANDS = {"0", "1", "2-3", "4-5", "6+"}
VALID_ANOMALY_STATUSES = {"ANOMALOUS", "NORMAL"}
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
@role_required("PAYER")
def get_patients():
    """GET /api/patients - Return synthetic/CMS patient cohort with optional filters.

    When ``page`` and/or ``pageSize`` are supplied, returns a paginated
    envelope ({items, total, page, pageSize, totalPages}) instead of the
    full unpaginated array, to avoid transferring the entire ~7MB
    population in one response. Omitting both preserves the exact
    historical response shape (a plain array) for existing callers.
    """
    risk = request.args.get("risk")
    search = request.args.get("search")
    band = request.args.get("band")
    anomaly = request.args.get("anomaly")
    if risk is not None and risk.upper() not in VALID_RISK_LEVELS:
        return jsonify({"error": "risk must be one of: HIGH, MODERATE, LOW"}), 400
    if search is not None and len(search) > MAX_SEARCH_LENGTH:
        return jsonify({"error": f"search must not exceed {MAX_SEARCH_LENGTH} characters"}), 400
    if band is not None and band not in VALID_UTILIZATION_BANDS:
        return jsonify({"error": "band must be one of: " + ", ".join(sorted(VALID_UTILIZATION_BANDS))}), 400
    if anomaly is not None and anomaly.upper() not in VALID_ANOMALY_STATUSES:
        return jsonify({"error": "anomaly must be one of: ANOMALOUS, NORMAL"}), 400

    page_raw = request.args.get("page")
    page_size_raw = request.args.get("pageSize")
    if page_raw is None and page_size_raw is None:
        results = patient_service.get_patients(risk=risk, search=search)
        return jsonify(results)

    try:
        page = int(page_raw) if page_raw is not None else 1
        page_size = int(page_size_raw) if page_size_raw is not None else 20
    except (TypeError, ValueError):
        return jsonify({"error": "page and pageSize must be integers"}), 400
    if page < 1 or page_size < 1:
        return jsonify({"error": "page and pageSize must be positive integers"}), 400

    result = patient_service.get_patients_page(
        risk=risk, search=search, band=band, anomaly=anomaly, page=page, page_size=page_size
    )
    return jsonify(result)


@patients_blueprint.get("/<patient_id>", strict_slashes=False)
@role_required("PAYER")
def get_patient_by_id(patient_id: str):
    """GET /api/patients/<id> - Return single patient detail or 404."""
    error = validate_patient_identifier(patient_id)
    if error:
        return jsonify({"error": error}), 400
    patient = patient_service.get_patient_by_id(patient_id)
    if patient is None:
        return jsonify({"error": "Patient not found"}), 404
    return jsonify(patient)
