"""Reusable metadata for trained-model output runs."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, DateTime, Identity, Index, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

if TYPE_CHECKING:
    from backend.models.anomaly import UtilizationAnomalyResult
    from backend.models.prediction import XGBoostUtilizationPrediction


JSONType = JSONB().with_variant(JSON, "sqlite")


class ModelRun(Base):
    __tablename__ = "model_runs"
    __table_args__ = (
        Index(
            "ix_model_runs_type_version_generated_at",
            "model_type",
            "model_version",
            "generated_at",
        ),
    )

    model_run_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    model_type: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    target_definition: Mapped[str | None] = mapped_column(Text, nullable=True)
    leakage_exclusions: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    configuration: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    artifact_reference: Mapped[str | None] = mapped_column(Text, nullable=True)

    xgboost_predictions: Mapped[list["XGBoostUtilizationPrediction"]] = relationship(
        back_populates="model_run"
    )
    anomaly_results: Mapped[list["UtilizationAnomalyResult"]] = relationship(
        back_populates="model_run"
    )
