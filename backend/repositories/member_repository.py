"""Framework-independent member data access."""

from dataclasses import dataclass

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from backend.models.anomaly import UtilizationAnomalyResult
from backend.models.member import Member
from backend.models.model_run import ModelRun
from backend.models.prediction import XGBoostUtilizationPrediction
from backend.models.utilization import MemberUtilizationSnapshot


@dataclass(frozen=True)
class MemberAnalyticalResult:
    """Member aggregate data with historical utilization model outputs."""

    member: Member
    utilization_snapshot: MemberUtilizationSnapshot | None
    xgboost_prediction: XGBoostUtilizationPrediction | None
    anomaly_result: UtilizationAnomalyResult | None


class MemberRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_bene_id(self, bene_id: str) -> Member | None:
        return self._session.scalar(select(Member).where(Member.bene_id == bene_id))

    def add(self, member: Member) -> Member:
        self._session.add(member)
        return member

    def get_utilization(self, member_id: int) -> MemberUtilizationSnapshot | None:
        return self._session.scalar(
            select(MemberUtilizationSnapshot)
            .where(MemberUtilizationSnapshot.member_id == member_id)
            .order_by(desc(MemberUtilizationSnapshot.id))
        )

    def get_xgboost_result(
        self, member_id: int, model_run_id: int | None = None
    ) -> XGBoostUtilizationPrediction | None:
        statement = select(XGBoostUtilizationPrediction).where(
            XGBoostUtilizationPrediction.member_id == member_id
        )
        if model_run_id is not None:
            statement = statement.where(XGBoostUtilizationPrediction.model_run_id == model_run_id)
        return self._session.scalar(statement.order_by(desc(XGBoostUtilizationPrediction.id)))

    def get_anomaly_result(
        self, member_id: int, model_run_id: int | None = None
    ) -> UtilizationAnomalyResult | None:
        statement = select(UtilizationAnomalyResult).where(
            UtilizationAnomalyResult.member_id == member_id
        )
        if model_run_id is not None:
            statement = statement.where(UtilizationAnomalyResult.model_run_id == model_run_id)
        return self._session.scalar(statement.order_by(desc(UtilizationAnomalyResult.id)))

    def get_combined_result(self, bene_id: str) -> MemberAnalyticalResult | None:
        member = self.get_by_bene_id(bene_id)
        if member is None:
            return None
        return MemberAnalyticalResult(
            member=member,
            utilization_snapshot=self.get_utilization(member.id),
            xgboost_prediction=self.get_xgboost_result(member.id),
            anomaly_result=self.get_anomaly_result(member.id),
        )
