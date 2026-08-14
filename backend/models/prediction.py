"""XGBoost historical utilization-pattern outputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

if TYPE_CHECKING:
    from backend.models.member import Member
    from backend.models.model_run import ModelRun
    from backend.models.utilization import MemberUtilizationSnapshot


class XGBoostUtilizationPrediction(Base):
    __tablename__ = "xgboost_utilization_predictions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["utilization_snapshot_id", "member_id"],
            ["member_utilization_snapshots.id", "member_utilization_snapshots.member_id"],
            name="fk_xgboost_prediction_snapshot_member",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "model_run_id",
            "utilization_snapshot_id",
            name="uq_xgboost_prediction_run_snapshot",
        ),
        CheckConstraint(
            "high_utilization_pattern IN (0, 1)",
            name="ck_xgboost_prediction_actual_binary",
        ),
        CheckConstraint(
            "predicted_high_utilization_pattern IN (0, 1)",
            name="ck_xgboost_prediction_predicted_binary",
        ),
        CheckConstraint(
            "high_utilization_probability >= 0 AND high_utilization_probability <= 1",
            name="ck_xgboost_prediction_probability_range",
        ),
        Index(
            "ix_xgboost_prediction_predicted_probability",
            "predicted_high_utilization_pattern",
            "high_utilization_probability",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    member_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("members.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    utilization_snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    model_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("model_runs.model_run_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    high_utilization_pattern: Mapped[int] = mapped_column(Integer, nullable=False)
    predicted_high_utilization_pattern: Mapped[int] = mapped_column(Integer, nullable=False)
    high_utilization_probability: Mapped[float] = mapped_column(Float, nullable=False)
    dataset_split: Mapped[str] = mapped_column(String(32), nullable=False)

    member: Mapped["Member"] = relationship(back_populates="xgboost_predictions")
    utilization_snapshot: Mapped["MemberUtilizationSnapshot"] = relationship(
        back_populates="xgboost_predictions",
        # The composite FK also includes member_id to enforce that this
        # snapshot belongs to ``member``.  Keep this relationship read-only so
        # SQLAlchemy has one unambiguous writer for member_id.
        viewonly=True,
    )
    model_run: Mapped["ModelRun"] = relationship(back_populates="xgboost_predictions")
