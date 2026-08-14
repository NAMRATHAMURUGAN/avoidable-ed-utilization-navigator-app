"""Member-level aggregate utilization snapshots from the processed feature file."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

if TYPE_CHECKING:
    from backend.models.anomaly import UtilizationAnomalyResult
    from backend.models.member import Member
    from backend.models.prediction import XGBoostUtilizationPrediction


class MemberUtilizationSnapshot(Base):
    """A source-derived aggregate snapshot with no invented observation period."""

    __tablename__ = "member_utilization_snapshots"
    __table_args__ = (
        UniqueConstraint("id", "member_id", name="uq_utilization_snapshot_id_member"),
        CheckConstraint("inpatient_visit_count >= 0", name="ck_utilization_inpatient_visits_nonnegative"),
        CheckConstraint("outpatient_visit_count >= 0", name="ck_utilization_outpatient_visits_nonnegative"),
        CheckConstraint("ed_visit_count >= 0", name="ck_utilization_ed_visits_nonnegative"),
        CheckConstraint("provider_count >= 0", name="ck_utilization_provider_count_nonnegative"),
        CheckConstraint("inpatient_total_cost >= 0", name="ck_utilization_inpatient_cost_nonnegative"),
        CheckConstraint("outpatient_total_cost >= 0", name="ck_utilization_outpatient_cost_nonnegative"),
        CheckConstraint("total_claim_payment_amount >= 0", name="ck_utilization_total_payment_nonnegative"),
        CheckConstraint("total_ed_related_cost >= 0", name="ck_utilization_ed_cost_nonnegative"),
        CheckConstraint("average_claim_cost >= 0", name="ck_utilization_average_cost_nonnegative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    member_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("members.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    inpatient_visit_count: Mapped[int] = mapped_column(Integer, nullable=False)
    inpatient_total_cost: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    outpatient_visit_count: Mapped[int] = mapped_column(Integer, nullable=False)
    outpatient_total_cost: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    ed_visit_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_claim_payment_amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    total_ed_related_cost: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    average_claim_cost: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    provider_count: Mapped[int] = mapped_column(Integer, nullable=False)

    member: Mapped["Member"] = relationship(back_populates="utilization_snapshots")
    xgboost_predictions: Mapped[list["XGBoostUtilizationPrediction"]] = relationship(
        back_populates="utilization_snapshot",
        # ``member_id`` is intentionally part of the composite FK below, but
        # is owned for writes by XGBoostUtilizationPrediction.member.  This
        # relationship is a consistency-checked navigation path only.
        viewonly=True,
    )
    anomaly_results: Mapped[list["UtilizationAnomalyResult"]] = relationship(
        back_populates="utilization_snapshot",
        # See the corresponding XGBoost relationship above.
        viewonly=True,
    )
