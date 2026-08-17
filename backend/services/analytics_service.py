"""Database-backed population analytics service layer.

Calculates aggregate population utilization metrics directly from PostgreSQL
`members`, `member_utilization_snapshots`, `xgboost_utilization_predictions`,
`utilization_anomaly_results`, `navigation_actions`, and `triage_encounters`
database tables.

Fields requiring line-item claims or survey data not present in the ingested
aggregate snapshots (e.g. claim-level visit timestamps, NYU ED-avoidability
classification) are explicitly returned as empty/None and documented as
DEFERRED data integrations -- never fabricated.

`utilizationTrend` is intentionally NOT a CMS-claims ED-visit trend (no
claim-level dates exist in the ingested dataset); it is the real, timestamped
volume of RightPath triage/navigation activity recorded through this
application, which is the genuine patient-portal-to-payer-analytics data
flow this service exposes. It is labeled as such wherever it is surfaced.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from backend.database import session_scope
from backend.models.encounter import NavigationAction, TriageEncounter
from backend.models.member import Member
from backend.models.prediction import XGBoostUtilizationPrediction
from backend.models.anomaly import UtilizationAnomalyResult
from backend.models.utilization import MemberUtilizationSnapshot
from backend.repositories.model_run_repository import ModelRunRepository
from backend.services import patient_service


# Canonical, ordered ED-utilization bands. Fixed order (not alphabetical) so
# chart X-axes are always presented low-to-high utilization.
_UTILIZATION_BANDS: tuple[str, ...] = ("0", "1", "2-3", "4-5", "6+")

_BAND_CASE = case(
    (MemberUtilizationSnapshot.ed_visit_count == 0, "0"),
    (MemberUtilizationSnapshot.ed_visit_count == 1, "1"),
    (MemberUtilizationSnapshot.ed_visit_count.between(2, 3), "2-3"),
    (MemberUtilizationSnapshot.ed_visit_count.between(4, 5), "4-5"),
    else_="6+",
).label("band")


def _utilization_distribution(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(_BAND_CASE, func.count(MemberUtilizationSnapshot.id)).group_by(_BAND_CASE)
    ).all()
    counts = {band: int(count) for band, count in rows}
    return [{"band": band, "memberCount": counts.get(band, 0)} for band in _UTILIZATION_BANDS]


def _cost_by_utilization_band(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        select(
            _BAND_CASE,
            func.count(MemberUtilizationSnapshot.id),
            func.coalesce(func.sum(MemberUtilizationSnapshot.total_ed_related_cost), 0),
            func.coalesce(func.avg(MemberUtilizationSnapshot.total_ed_related_cost), 0),
        ).group_by(_BAND_CASE)
    ).all()
    by_band = {
        band: {
            "memberCount": int(count),
            "totalEdSpend": float(total_cost),
            "averageEdSpend": float(avg_cost),
        }
        for band, count, total_cost, avg_cost in rows
    }
    return [
        {
            "band": band,
            "memberCount": by_band.get(band, {}).get("memberCount", 0),
            "totalEdSpend": by_band.get(band, {}).get("totalEdSpend", 0.0),
            "averageEdSpend": by_band.get(band, {}).get("averageEdSpend", 0.0),
        }
        for band in _UTILIZATION_BANDS
    ]


def _latest_model_run_ids(session: Session) -> tuple[int | None, int | None]:
    run_repo = ModelRunRepository(session)
    xgb_run = run_repo.get_latest("xgboost")
    anomaly_run = run_repo.get_latest("isolation_forest")
    return (
        xgb_run.model_run_id if xgb_run else None,
        anomaly_run.model_run_id if anomaly_run else None,
    )


def _high_utilization_and_anomalous_counts(
    session: Session, xgb_run_id: int | None, anomaly_run_id: int | None
) -> tuple[int, int]:
    high_utilization_count = 0
    if xgb_run_id is not None:
        high_utilization_count = int(
            session.scalar(
                select(func.count(XGBoostUtilizationPrediction.id)).where(
                    XGBoostUtilizationPrediction.model_run_id == xgb_run_id,
                    XGBoostUtilizationPrediction.predicted_high_utilization_pattern == 1,
                )
            )
            or 0
        )
    anomalous_count = 0
    if anomaly_run_id is not None:
        anomalous_count = int(
            session.scalar(
                select(func.count(UtilizationAnomalyResult.id)).where(
                    UtilizationAnomalyResult.model_run_id == anomaly_run_id,
                    UtilizationAnomalyResult.anomaly_flag == 1,
                )
            )
            or 0
        )
    return high_utilization_count, anomalous_count


def _anomaly_scatter_bins(
    session: Session, xgb_run_id: int | None, anomaly_run_id: int | None
) -> list[dict[str, Any]]:
    """Aggregated (never per-member) points for the anomaly-vs-utilization
    scatter view: one bin per (ED-visit count, anomaly status) combination,
    with the average Isolation Forest anomaly score for that bin. Binning
    avoids plotting 8,000+ individual points and avoids exposing any
    per-member identifier alongside a raw model score."""
    if anomaly_run_id is None:
        return []
    statement = (
        select(
            MemberUtilizationSnapshot.ed_visit_count,
            UtilizationAnomalyResult.anomaly_flag,
            func.count(Member.id),
            func.avg(UtilizationAnomalyResult.anomaly_score),
        )
        .select_from(Member)
        .join(MemberUtilizationSnapshot, MemberUtilizationSnapshot.member_id == Member.id)
        .join(
            UtilizationAnomalyResult,
            (UtilizationAnomalyResult.member_id == Member.id)
            & (UtilizationAnomalyResult.model_run_id == anomaly_run_id),
        )
        .group_by(MemberUtilizationSnapshot.ed_visit_count, UtilizationAnomalyResult.anomaly_flag)
        .order_by(MemberUtilizationSnapshot.ed_visit_count)
    )
    rows = session.execute(statement).all()
    return [
        {
            "edVisitCount": int(ed_visit_count),
            "isAnomalous": bool(anomaly_flag),
            "memberCount": int(member_count),
            "averageAnomalyScore": float(avg_score) if avg_score is not None else None,
        }
        for ed_visit_count, anomaly_flag, member_count, avg_score in rows
    ]


def _risk_distribution_and_priority_matrix(session: Session) -> tuple[dict[str, int], dict[str, int]]:
    """Reuses the exact same per-member risk formula already shown on
    /api/patients (patient_service.get_patients) so aggregate charts can
    never disagree with the per-member risk badges shown elsewhere."""
    patients = patient_service.get_patients(session=session)
    risk_distribution = {"high": 0, "moderate": 0, "low": 0}
    priority_matrix = {
        "highRiskHighAnomaly": 0,
        "highRiskLowAnomaly": 0,
        "lowerRiskHighAnomaly": 0,
        "lowerRiskLowAnomaly": 0,
    }
    for patient in patients:
        risk_level = str(patient.get("riskLevel", "LOW")).lower()
        if risk_level in risk_distribution:
            risk_distribution[risk_level] += 1
        is_high_risk = risk_level == "high"
        is_anomalous = bool(patient.get("isAnomalous"))
        if is_high_risk and is_anomalous:
            priority_matrix["highRiskHighAnomaly"] += 1
        elif is_high_risk and not is_anomalous:
            priority_matrix["highRiskLowAnomaly"] += 1
        elif not is_high_risk and is_anomalous:
            priority_matrix["lowerRiskHighAnomaly"] += 1
        else:
            priority_matrix["lowerRiskLowAnomaly"] += 1
    return risk_distribution, priority_matrix


def _care_management_opportunities(session: Session) -> list[dict[str, Any]]:
    """Real counts of recorded backend/routes/navigation.py NavigationAction
    rows, grouped by the acuity actually selected. Empty when no actions
    have been recorded yet -- never a fabricated distribution."""
    rows = session.execute(
        select(NavigationAction.selected_acuity, func.count(NavigationAction.id))
        .where(NavigationAction.selected_acuity.is_not(None))
        .group_by(NavigationAction.selected_acuity)
        .order_by(func.count(NavigationAction.id).desc())
    ).all()
    return [{"pathway": pathway, "actionCount": int(count)} for pathway, count in rows]


def _utilization_trend(session: Session) -> list[dict[str, Any]]:
    """Real, timestamped RightPath triage-encounter volume by day. This is
    application activity, not a CMS-claims ED-visit trend (no claim-level
    dates exist in the ingested dataset -- see module docstring)."""
    day = func.date(TriageEncounter.created_at).label("day")
    rows = session.execute(
        select(day, func.count(TriageEncounter.id)).group_by(day).order_by(day)
    ).all()
    return [{"date": str(day_value), "encounterCount": int(count)} for day_value, count in rows]


def _execute_get_population_analytics(session: Session) -> dict[str, Any]:
    total_patients = session.scalar(select(func.count(Member.id))) or 0
    total_ed_visits = (
        session.scalar(
            select(func.coalesce(func.sum(MemberUtilizationSnapshot.ed_visit_count), 0))
        )
        or 0
    )
    total_ed_spend = float(
        session.scalar(
            select(func.coalesce(func.sum(MemberUtilizationSnapshot.total_ed_related_cost), 0))
        )
        or 0.0
    )

    xgb_run_id, anomaly_run_id = _latest_model_run_ids(session)
    high_utilization_count, anomalous_count = _high_utilization_and_anomalous_counts(
        session, xgb_run_id, anomaly_run_id
    )
    risk_distribution, priority_matrix = _risk_distribution_and_priority_matrix(session)

    return {
        "totalPatients": int(total_patients),
        "totalEdVisits": int(total_ed_visits),
        "avoidableEdVisitsCount": None,  # DEFERRED: NYU ED classification requires claims detail
        "avoidableEdPercentage": None,  # DEFERRED: Requires claims-level NYU ED classification
        "totalEdSpend": total_ed_spend,
        "potentialSavings": None,  # DEFERRED: Savings calculation requires claims detail
        "nyuCategoryBreakdown": [],  # DEFERRED: NYU category breakdown requires claims detail
        "timeOfDayPattern": [],  # DEFERRED: Visit timestamp analysis requires claims detail
        "dayOfWeekPattern": [],  # DEFERRED: Day-of-week analysis requires claims detail
        "topAvoidableDiagnoses": [],  # DEFERRED: ICD-10 frequency requires claims detail
        "sdohBarrierDistribution": [],  # DEFERRED: SDOH survey data requires SDOH tables
        "highUtilizationMemberCount": high_utilization_count,
        "anomalousMemberCount": anomalous_count,
        "utilizationDistribution": _utilization_distribution(session),
        "costByUtilizationBand": _cost_by_utilization_band(session),
        "riskDistribution": risk_distribution,
        "priorityMatrix": priority_matrix,
        "anomalyScatterBins": _anomaly_scatter_bins(session, xgb_run_id, anomaly_run_id),
        "careManagementOpportunities": _care_management_opportunities(session),
        "utilizationTrend": _utilization_trend(session),
    }


def get_population_analytics(session: Session | None = None) -> dict[str, Any]:
    """Return database-backed population-level aggregate ED analytics."""
    if session is not None:
        return _execute_get_population_analytics(session)

    with session_scope() as sess:
        return _execute_get_population_analytics(sess)
