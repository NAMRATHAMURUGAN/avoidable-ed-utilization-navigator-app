"""Service for the demo-safe PATIENT <-> synthetic CMS Member association.

This is the ONLY place a PATIENT's synthetic CMS member is resolved. It
never accepts a caller-supplied member/beneficiary identifier -- the link is
either an existing persisted PatientMemberLink for this user_id, or a fresh
deterministic assignment of the lowest not-yet-claimed Member, created once
and reused forever after. A PATIENT can never choose, change, or probe which
member they are linked to through this function.

Deliberately mirrors the exact member-resolution shape used by the existing
PAYER-supplied-patientId path in backend/services/triage_service.py (latest
xgboost/isolation_forest model run, then the combined analytical result), so
downstream consumers (CareNavigationService, member_analytical_result_to_dict)
treat a self-linked member identically to a payer-selected one.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.models.patient_member_link import PatientMemberLink
from backend.repositories.member_repository import MemberAnalyticalResult, MemberRepository
from backend.repositories.model_run_repository import ModelRunRepository
from backend.repositories.patient_member_link_repository import PatientMemberLinkRepository


def get_or_create_linked_member(session: Session, user_id: int) -> MemberAnalyticalResult | None:
    """Return this PATIENT's linked synthetic CMS member's full analytical
    result (member + latest utilization/ML), creating the link on first use
    if one does not exist yet.

    Returns None only if the synthetic member pool is exhausted or empty --
    callers must treat that as "no enrichment available for this request"
    and continue triage normally (the existing anonymous-equivalent
    behavior), never as an error that blocks a real patient's assessment.
    """
    link_repo = PatientMemberLinkRepository(session)
    existing = link_repo.get_by_user_id(user_id)

    if existing is not None:
        member_id = existing.member_id
    else:
        candidate_member_id = link_repo.get_next_unclaimed_member_id()
        if candidate_member_id is None:
            return None
        link_repo.create(
            PatientMemberLink(
                user_id=user_id,
                member_id=candidate_member_id,
                created_at=datetime.now(timezone.utc),
            )
        )
        member_id = candidate_member_id

    member_repo = MemberRepository(session)
    run_repo = ModelRunRepository(session)
    xgb_run = run_repo.get_latest("xgboost")
    anomaly_run = run_repo.get_latest("isolation_forest")
    return member_repo.get_combined_result_by_id_or_bene(
        str(member_id),
        xgb_model_run_id=xgb_run.model_run_id if xgb_run else None,
        anomaly_model_run_id=anomaly_run.model_run_id if anomaly_run else None,
    )
