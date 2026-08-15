"""Database-backed population analytics service layer.

Calculates aggregate population utilization metrics directly from PostgreSQL
`members` and `member_utilization_snapshots` database tables.

Fields requiring line-item claims or survey data not present in aggregate snapshots
are explicitly returned as empty/None and documented as DEFERRED data integrations.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.database import session_scope
from backend.models.member import Member
from backend.models.utilization import MemberUtilizationSnapshot


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
    }


def get_population_analytics(session: Session | None = None) -> dict[str, Any]:
    """Return database-backed population-level aggregate ED analytics."""
    if session is not None:
        return _execute_get_population_analytics(session)

    with session_scope() as sess:
        return _execute_get_population_analytics(sess)
