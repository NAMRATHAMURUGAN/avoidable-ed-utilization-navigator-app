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

    def get_by_id(self, member_id: int) -> Member | None:
        return self._session.scalar(select(Member).where(Member.id == member_id))

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

    def get_combined_result(
        self,
        bene_id: str,
        xgb_model_run_id: int | None = None,
        anomaly_model_run_id: int | None = None,
    ) -> MemberAnalyticalResult | None:
        member = self.get_by_bene_id(bene_id)
        if member is None:
            return None
        return MemberAnalyticalResult(
            member=member,
            utilization_snapshot=self.get_utilization(member.id),
            xgboost_prediction=self.get_xgboost_result(member.id, xgb_model_run_id),
            anomaly_result=self.get_anomaly_result(member.id, anomaly_model_run_id),
        )

    def get_combined_result_by_id_or_bene(
        self,
        identifier: str,
        xgb_model_run_id: int | None = None,
        anomaly_model_run_id: int | None = None,
    ) -> MemberAnalyticalResult | None:
        member = self.get_by_bene_id(identifier)
        if member is None:
            try:
                member_id = int(identifier)
                member = self.get_by_id(member_id)
            except ValueError:
                member = None
        if member is None:
            return None
        return MemberAnalyticalResult(
            member=member,
            utilization_snapshot=self.get_utilization(member.id),
            xgboost_prediction=self.get_xgboost_result(member.id, xgb_model_run_id),
            anomaly_result=self.get_anomaly_result(member.id, anomaly_model_run_id),
        )

    def get_all_combined_results(
        self,
        xgb_model_run_id: int | None = None,
        anomaly_model_run_id: int | None = None,
    ) -> list[MemberAnalyticalResult]:
        statement = select(
            Member,
            MemberUtilizationSnapshot,
            XGBoostUtilizationPrediction,
            UtilizationAnomalyResult,
        ).outerjoin(
            MemberUtilizationSnapshot, MemberUtilizationSnapshot.member_id == Member.id
        )

        xgb_condition = XGBoostUtilizationPrediction.member_id == Member.id
        if xgb_model_run_id is not None:
            xgb_condition = xgb_condition & (
                XGBoostUtilizationPrediction.model_run_id == xgb_model_run_id
            )
        statement = statement.outerjoin(XGBoostUtilizationPrediction, xgb_condition)

        anomaly_condition = UtilizationAnomalyResult.member_id == Member.id
        if anomaly_model_run_id is not None:
            anomaly_condition = anomaly_condition & (
                UtilizationAnomalyResult.model_run_id == anomaly_model_run_id
            )
        statement = statement.outerjoin(UtilizationAnomalyResult, anomaly_condition)

        statement = statement.order_by(Member.id)

        rows = self._session.execute(statement).all()
        return [
            MemberAnalyticalResult(
                member=m,
                utilization_snapshot=snap,
                xgboost_prediction=xgb,
                anomaly_result=anom,
            )
            for m, snap, xgb, anom in rows
        ]
