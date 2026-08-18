"""Tests for the extended payer analytics aggregations added to
analytics_service.get_population_analytics(): utilization distribution,
cost-by-band, risk distribution, the risk x anomaly priority matrix,
anomaly scatter bins, care-management opportunities, and the real
triage-activity trend.

Every field asserted here has a traceable source:
  utilizationDistribution / costByUtilizationBand -> member_utilization_snapshots.ed_visit_count/total_ed_related_cost
  highUtilizationMemberCount                      -> xgboost_utilization_predictions.predicted_high_utilization_pattern (latest model_run)
  anomalousMemberCount / anomalyScatterBins        -> utilization_anomaly_results.anomaly_flag/anomaly_score (latest model_run)
  riskDistribution / priorityMatrix                -> patient_service.get_patients() (same formula as /api/patients)
  careManagementOpportunities                      -> navigation_actions.selected_acuity
  utilizationTrend                                 -> triage_encounters.created_at
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.database import Base
from backend.models import (
    Member,
    MemberUtilizationSnapshot,
    ModelRun,
    NavigationAction,
    TriageEncounter,
    UtilizationAnomalyResult,
    XGBoostUtilizationPrediction,
)
from backend.services.analytics_service import get_population_analytics


class AnalyticsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session: Session = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )()

    def tearDown(self) -> None:
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _seed_member(
        self,
        member_id: int,
        *,
        ed_visits: int,
        ed_cost: float,
        xgb_run_id: int,
        anomaly_run_id: int,
        predicted_high_utilization: int,
        probability: float,
        anomaly_flag: int,
        anomaly_score: float,
    ) -> None:
        member = Member(
            id=member_id,
            bene_id=f"CMS-AN-{member_id}",
            age=50,
            gender="Female",
            dual_eligibility_months=0,
            chronic_condition_count=0,
        )
        snapshot = MemberUtilizationSnapshot(
            id=member_id,
            member_id=member_id,
            inpatient_visit_count=0,
            inpatient_total_cost=Decimal("0"),
            outpatient_visit_count=0,
            outpatient_total_cost=Decimal("0"),
            ed_visit_count=ed_visits,
            total_claim_payment_amount=Decimal("0"),
            total_ed_related_cost=Decimal(str(ed_cost)),
            average_claim_cost=Decimal("0"),
            provider_count=1,
        )
        xgb = XGBoostUtilizationPrediction(
            id=member_id,
            member_id=member_id,
            utilization_snapshot_id=member_id,
            model_run_id=xgb_run_id,
            high_utilization_pattern=predicted_high_utilization,
            predicted_high_utilization_pattern=predicted_high_utilization,
            high_utilization_probability=probability,
            dataset_split="test",
        )
        anomaly = UtilizationAnomalyResult(
            id=member_id,
            member_id=member_id,
            utilization_snapshot_id=member_id,
            model_run_id=anomaly_run_id,
            anomaly_score=anomaly_score,
            anomaly_flag=anomaly_flag,
            anomaly_rank=member_id,
            generated_at=datetime.now(timezone.utc),
        )
        self.session.add_all([member, snapshot, xgb, anomaly])

    def test_full_population_aggregations(self) -> None:
        xgb_run = ModelRun(model_run_id=1, model_type="xgboost", purpose="test")
        anomaly_run = ModelRun(model_run_id=2, model_type="isolation_forest", purpose="test")
        self.session.add_all([xgb_run, anomaly_run])
        self.session.flush()

        # Member 1: 0 ED visits, low risk, not anomalous.
        self._seed_member(
            1, ed_visits=0, ed_cost=0, xgb_run_id=1, anomaly_run_id=2,
            predicted_high_utilization=0, probability=0.1, anomaly_flag=0, anomaly_score=0.1,
        )
        # Member 2: 1 ED visit, moderate-adjacent low risk, not anomalous.
        self._seed_member(
            2, ed_visits=1, ed_cost=1000, xgb_run_id=1, anomaly_run_id=2,
            predicted_high_utilization=0, probability=0.2, anomaly_flag=0, anomaly_score=0.2,
        )
        # Member 3: 6+ ED visits, high risk AND anomalous.
        self._seed_member(
            3, ed_visits=8, ed_cost=50000, xgb_run_id=1, anomaly_run_id=2,
            predicted_high_utilization=1, probability=0.9, anomaly_flag=1, anomaly_score=0.8,
        )
        # Member 4: 4-5 ED visits, high utilization prediction, not anomalous.
        self._seed_member(
            4, ed_visits=4, ed_cost=20000, xgb_run_id=1, anomaly_run_id=2,
            predicted_high_utilization=1, probability=0.75, anomaly_flag=0, anomaly_score=0.3,
        )
        self.session.commit()

        # A real recorded care-navigation action.
        self.session.add(
            NavigationAction(
                selected_acuity="URGENT_CARE",
                action_type="PROVIDER_SELECTED",
                recorded_at=datetime.now(timezone.utc),
            )
        )
        # A real triage encounter with a known date, for the trend.
        self.session.add(
            TriageEncounter(
                session_id="s1",
                chief_complaint="test",
                symptoms_duration="",
                has_red_flags=False,
                is_emergency=False,
                recommended_acuity="TELEHEALTH",
                urgency_level="ROUTINE",
                recommended_setting_name="Telehealth",
                clinical_rationale="test",
                safety_disclaimer="test",
                rule_set_version="1.0.0",
                created_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
            )
        )
        self.session.commit()

        data = get_population_analytics(session=self.session)

        self.assertEqual(data["totalPatients"], 4)
        self.assertEqual(data["totalEdVisits"], 0 + 1 + 8 + 4)
        self.assertEqual(data["totalEdSpend"], 0 + 1000 + 50000 + 20000)

        self.assertEqual(data["highUtilizationMemberCount"], 2)  # members 3, 4
        self.assertEqual(data["anomalousMemberCount"], 1)  # member 3

        band_counts = {row["band"]: row["memberCount"] for row in data["utilizationDistribution"]}
        self.assertEqual(band_counts, {"0": 1, "1": 1, "2-3": 0, "4-5": 1, "6+": 1})
        # Fixed, low-to-high band ordering for chart X-axes.
        self.assertEqual([row["band"] for row in data["utilizationDistribution"]], ["0", "1", "2-3", "4-5", "6+"])

        cost_by_band = {row["band"]: row["totalEdSpend"] for row in data["costByUtilizationBand"]}
        self.assertEqual(cost_by_band["6+"], 50000)
        self.assertEqual(cost_by_band["4-5"], 20000)
        self.assertEqual(cost_by_band["2-3"], 0)

        self.assertEqual(data["riskDistribution"]["high"], 2)  # members 3, 4 (prob>0.7 or anomaly)
        self.assertEqual(
            data["riskDistribution"]["high"]
            + data["riskDistribution"]["moderate"]
            + data["riskDistribution"]["low"],
            4,
        )

        self.assertEqual(data["priorityMatrix"]["highRiskHighAnomaly"], 1)  # member 3
        self.assertEqual(data["priorityMatrix"]["highRiskLowAnomaly"], 1)  # member 4
        self.assertEqual(data["priorityMatrix"]["lowerRiskHighAnomaly"], 0)
        self.assertEqual(data["priorityMatrix"]["lowerRiskLowAnomaly"], 2)  # members 1, 2

        self.assertEqual(data["careManagementOpportunities"], [{"pathway": "URGENT_CARE", "actionCount": 1}])
        self.assertEqual(data["utilizationTrend"], [{"date": "2026-01-05", "encounterCount": 1}])

        scatter_member_3 = next(
            b for b in data["anomalyScatterBins"] if b["edVisitCount"] == 8 and b["isAnomalous"]
        )
        self.assertEqual(scatter_member_3["memberCount"], 1)
        self.assertAlmostEqual(scatter_member_3["averageAnomalyScore"], 0.8)

    def test_empty_database_returns_honest_zero_and_empty_values(self) -> None:
        data = get_population_analytics(session=self.session)
        self.assertEqual(data["totalPatients"], 0)
        self.assertEqual(data["highUtilizationMemberCount"], 0)
        self.assertEqual(data["anomalousMemberCount"], 0)
        self.assertEqual(
            [row["memberCount"] for row in data["utilizationDistribution"]], [0, 0, 0, 0, 0]
        )
        self.assertEqual(data["careManagementOpportunities"], [])
        self.assertEqual(data["utilizationTrend"], [])
        self.assertEqual(data["anomalyScatterBins"], [])
        self.assertEqual(
            data["priorityMatrix"],
            {
                "highRiskHighAnomaly": 0,
                "highRiskLowAnomaly": 0,
                "lowerRiskHighAnomaly": 0,
                "lowerRiskLowAnomaly": 0,
            },
        )
        # Fields requiring claims-level detail that genuinely does not exist
        # remain explicitly deferred, never fabricated.
        self.assertIsNone(data["avoidableEdVisitsCount"])
        self.assertIsNone(data["potentialSavings"])


if __name__ == "__main__":
    unittest.main()
