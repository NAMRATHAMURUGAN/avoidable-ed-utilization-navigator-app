"""Isolation Forest utilization-anomaly outputs."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

if TYPE_CHECKING:
    from backend.models.member import Member
    from backend.models.model_run import ModelRun
    from backend.models.utilization import MemberUtilizationSnapshot


class UtilizationAnomalyResult(Base):
    __tablename__ = "utilization_anomaly_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["utilization_snapshot_id", "member_id"],
            ["member_utilization_snapshots.id", "member_utilization_snapshots.member_id"],
            name="fk_anomaly_result_snapshot_member",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "model_run_id",
            "utilization_snapshot_id",
            name="uq_anomaly_result_run_snapshot",
        ),
        UniqueConstraint("model_run_id", "anomaly_rank", name="uq_anomaly_result_run_rank"),
        CheckConstraint("anomaly_flag IN (0, 1)", name="ck_anomaly_result_flag_binary"),
        CheckConstraint("anomaly_rank > 0", name="ck_anomaly_result_rank_positive"),
        Index("ix_anomaly_result_flag_rank", "anomaly_flag", "anomaly_rank"),
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
    anomaly_score: Mapped[float] = mapped_column(Float, nullable=False)
    anomaly_flag: Mapped[int] = mapped_column(Integer, nullable=False)
    anomaly_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    member: Mapped["Member"] = relationship(back_populates="anomaly_results")
    utilization_snapshot: Mapped["MemberUtilizationSnapshot"] = relationship(
        back_populates="anomaly_results"
    )
    model_run: Mapped["ModelRun"] = relationship(back_populates="anomaly_results")
