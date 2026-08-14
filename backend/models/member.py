"""Member identity and source-derived demographic fields."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, Identity, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

if TYPE_CHECKING:
    from backend.models.anomaly import UtilizationAnomalyResult
    from backend.models.prediction import XGBoostUtilizationPrediction
    from backend.models.utilization import MemberUtilizationSnapshot


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (
        CheckConstraint("age >= 0", name="ck_members_age_nonnegative"),
        CheckConstraint(
            "dual_eligibility_months >= 0",
            name="ck_members_dual_eligibility_months_nonnegative",
        ),
        CheckConstraint(
            "chronic_condition_count >= 0",
            name="ck_members_chronic_condition_count_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    bene_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    gender: Mapped[str] = mapped_column(String(32), nullable=False)
    dual_eligibility_months: Mapped[int] = mapped_column(Integer, nullable=False)
    chronic_condition_count: Mapped[int] = mapped_column(Integer, nullable=False)

    utilization_snapshots: Mapped[list["MemberUtilizationSnapshot"]] = relationship(
        back_populates="member"
    )
    xgboost_predictions: Mapped[list["XGBoostUtilizationPrediction"]] = relationship(
        back_populates="member"
    )
    anomaly_results: Mapped[list["UtilizationAnomalyResult"]] = relationship(
        back_populates="member"
    )
