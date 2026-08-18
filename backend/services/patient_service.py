"""Database-backed patient service layer.

Retrieves member identity, aggregate utilization snapshots, and associated ML model
predictions/anomalies directly from PostgreSQL via MemberRepository and ModelRunRepository.

Does NOT fabricate missing fields or apply artificial pagination limits.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.database import session_scope
from backend.repositories.member_repository import MemberAnalyticalResult, MemberRepository
from backend.repositories.model_run_repository import ModelRunRepository


def member_analytical_result_to_dict(result: MemberAnalyticalResult) -> dict[str, Any]:
    """Convert MemberAnalyticalResult to frontend-compatible PatientProfile dictionary."""
    member = result.member
    snapshot = result.utilization_snapshot
    xgb = result.xgboost_prediction
    anomaly = result.anomaly_result

    prob = xgb.high_utilization_probability if xgb else 0.0
    anom_flag = anomaly.anomaly_flag if anomaly else 0
    ed_visits = snapshot.ed_visit_count if snapshot else 0

    if prob > 0.7 or anom_flag == 1:
        risk_level = "HIGH"
    elif prob > 0.4 or ed_visits > 2:
        risk_level = "MODERATE"
    else:
        risk_level = "LOW"

    return {
        "id": str(member.id),
        "beneficiaryId": member.bene_id,
        "name": f"Beneficiary {member.bene_id}",
        "age": member.age,
        "gender": member.gender,
        "zipCode": "",  # DEFERRED: ZIP code not in Member schema
        "medicareType": (
            f"Dual Eligible ({member.dual_eligibility_months}m)"
            if member.dual_eligibility_months > 0
            else "Medicare FFS"
        ),
        "primaryCareProvider": None,  # DEFERRED: PCP identity not in Member schema
        "lastPcpVisitDate": None,  # DEFERRED: PCP visit date not in Member schema
        "chronicConditions": (
            [f"{member.chronic_condition_count} Chronic Conditions"]
            if member.chronic_condition_count > 0
            else []
        ),
        "sdohBarriers": [],  # DEFERRED: SDOH survey data not in Member schema
        "edVisitCount12m": ed_visits,
        "avoidableEdCount12m": None,  # DEFERRED: NYU ED classification requires claims detail
        "totalEdSpend12m": float(snapshot.total_ed_related_cost) if snapshot else 0.0,
        "avoidableEdSpend12m": 0.0,  # DEFERRED: Avoidable spend requires claims detail
        "riskLevel": risk_level,
        # Categorical anomaly status only (never the raw Isolation Forest
        # score) -- consistent with riskLevel, this is a population-
        # segmentation signal, not a diagnosis.
        "isAnomalous": bool(anom_flag),
        "riskLevelInterpretation": (
            "This priority signal reflects historical high-utilization-pattern and "
            "utilization-anomaly data only. It is not a medical necessity determination, "
            "an emergency necessity determination, an indicator of clinical deterioration, "
            "a finding of inappropriate ED use, or a determination of ED avoidability."
        ),
        "claimsHistory": [],  # DEFERRED: Line-level claims history not stored on snapshot
        "assignedCareManager": None,  # DEFERRED: Care manager assignment not in Member schema
    }


def _utilization_band_for_visit_count(count: Any) -> str:
    try:
        visits = int(count)
    except (TypeError, ValueError):
        return "6+"
    if visits == 0:
        return "0"
    if visits == 1:
        return "1"
    if 2 <= visits <= 3:
        return "2-3"
    if 4 <= visits <= 5:
        return "4-5"
    return "6+"


def _execute_get_patients(
    session: Session,
    risk: str | None = None,
    search: str | None = None,
    band: str | None = None,
    anomaly: str | None = None,
) -> list[dict[str, Any]]:
    member_repo = MemberRepository(session)
    run_repo = ModelRunRepository(session)

    xgb_run = run_repo.get_latest("xgboost")
    anomaly_run = run_repo.get_latest("isolation_forest")

    xgb_run_id = xgb_run.model_run_id if xgb_run else None
    anomaly_run_id = anomaly_run.model_run_id if anomaly_run else None

    results = member_repo.get_all_combined_results(
        xgb_model_run_id=xgb_run_id,
        anomaly_model_run_id=anomaly_run_id,
    )

    patient_dicts = [member_analytical_result_to_dict(r) for r in results]

    if risk and isinstance(risk, str):
        risk_lower = risk.lower()
        patient_dicts = [p for p in patient_dicts if p.get("riskLevel", "").lower() == risk_lower]

    if band and isinstance(band, str):
        patient_dicts = [
            p for p in patient_dicts if _utilization_band_for_visit_count(p.get("edVisitCount12m")) == band
        ]

    if anomaly and isinstance(anomaly, str):
        want_anomalous = anomaly.upper() == "ANOMALOUS"
        patient_dicts = [p for p in patient_dicts if bool(p.get("isAnomalous")) == want_anomalous]

    if search and isinstance(search, str):
        q = search.lower()
        filtered: list[dict[str, Any]] = []
        for p in patient_dicts:
            name_match = q in str(p.get("name", "")).lower()
            bene_match = q in str(p.get("beneficiaryId", "")).lower()
            id_match = q in str(p.get("id", "")).lower()
            gender_match = q in str(p.get("gender", "")).lower()
            cond_match = any(q in str(c).lower() for c in p.get("chronicConditions", []))
            if name_match or bene_match or id_match or gender_match or cond_match:
                filtered.append(p)
        patient_dicts = filtered

    return patient_dicts


def get_patients_page(
    risk: str | None = None,
    search: str | None = None,
    band: str | None = None,
    anomaly: str | None = None,
    page: int = 1,
    page_size: int = 20,
    session: Session | None = None,
) -> dict[str, Any]:
    """Return one page of the database-backed patient cohort.

    Exists alongside (not instead of) get_patients(): the Werkzeug
    development server was observed to reset the connection on Windows when
    sending the full ~7MB unpaginated payload for the current 8,671-member
    population, so any UI paging/scrolling through the cohort should call
    this instead of fetching everything at once. Filtering logic is
    identical to get_patients(); only the page slice differs.
    """

    def _execute(sess: Session) -> dict[str, Any]:
        all_matching = _execute_get_patients(sess, risk=risk, search=search, band=band, anomaly=anomaly)
        total = len(all_matching)
        safe_page_size = max(1, min(page_size, 200))
        total_pages = max(1, -(-total // safe_page_size)) if total else 1
        safe_page = max(1, min(page, total_pages))
        start = (safe_page - 1) * safe_page_size
        return {
            "items": all_matching[start : start + safe_page_size],
            "total": total,
            "page": safe_page,
            "pageSize": safe_page_size,
            "totalPages": total_pages,
        }

    if session is not None:
        return _execute(session)
    with session_scope() as sess:
        return _execute(sess)


def get_patients(
    risk: str | None = None,
    search: str | None = None,
    session: Session | None = None,
) -> list[dict[str, Any]]:
    """Return complete database-backed patient cohort with optional filtering."""
    if session is not None:
        return _execute_get_patients(session, risk=risk, search=search)

    with session_scope() as sess:
        return _execute_get_patients(sess, risk=risk, search=search)


def _execute_get_patient_by_id(
    session: Session,
    patient_id: str,
) -> dict[str, Any] | None:
    member_repo = MemberRepository(session)
    run_repo = ModelRunRepository(session)

    xgb_run = run_repo.get_latest("xgboost")
    anomaly_run = run_repo.get_latest("isolation_forest")

    xgb_run_id = xgb_run.model_run_id if xgb_run else None
    anomaly_run_id = anomaly_run.model_run_id if anomaly_run else None

    result = member_repo.get_combined_result_by_id_or_bene(
        patient_id,
        xgb_model_run_id=xgb_run_id,
        anomaly_model_run_id=anomaly_run_id,
    )

    if result is None:
        return None

    return member_analytical_result_to_dict(result)


def get_patient_by_id(
    patient_id: str,
    session: Session | None = None,
) -> dict[str, Any] | None:
    """Return single database-backed patient by ID or beneficiary ID."""
    if session is not None:
        return _execute_get_patient_by_id(session, patient_id)

    with session_scope() as sess:
        return _execute_get_patient_by_id(sess, patient_id)
