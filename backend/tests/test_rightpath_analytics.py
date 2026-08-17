"""Tests for the RightPath (patient-app) payer analytics feature:

- backend/services/analytics_service.py: get_rightpath_analytics()
- GET /api/payer/analytics/rightpath (backend/routes/analytics.py)

This is additive to, and completely separate from, the existing CMS/member
population analytics (get_population_analytics / GET /api/analytics), which
are covered by test_analytics_service.py and are not touched here.

Every field asserted has a traceable, real-database source:
  totalRightPathUsers       -> COUNT DISTINCT triage_encounters.user_id (non-null only)
  totalAssessments          -> COUNT triage_encounters
  emergencyAssessments      -> COUNT triage_encounters WHERE is_emergency = true
  nonEmergencyAssessments   -> COUNT triage_encounters WHERE is_emergency = false
  acuityDistribution        -> triage_encounters.recommended_acuity
  navigationActions         -> COUNT navigation_actions
  pathwayDistribution       -> navigation_actions.selected_acuity
  activityTrend             -> triage_encounters.created_at (grouped by day)

RightPath Impact metrics (added in this phase) are covered by
RightPathImpactAnalyticsServiceTests / RightPathImpactAnalyticsRouteTests
below:
  totalRightPathAssessments          -> COUNT triage_encounters
  nonEmergencyRecommendations        -> COUNT triage_encounters WHERE recommended_acuity != 'EMERGENCY'
  confirmedNonEdNavigationActions    -> COUNT navigation_actions WHERE selected_acuity != 'EMERGENCY'
  telehealthNavigations/primaryCareNavigations/urgentCareNavigations/emergencyNavigations
                                      -> navigation_actions.selected_acuity, per value
  potentialEdUtilizationOpportunities -> == confirmedNonEdNavigationActions (no causal claim)
  averageEdClaimCost                 -> SUM(member_utilization_snapshots.total_ed_related_cost)
                                         / SUM(member_utilization_snapshots.ed_visit_count)
  potentialEdCostOpportunity         -> potentialEdUtilizationOpportunities * averageEdClaimCost
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app import create_app
from backend.database import Base
from backend.models import Member, MemberUtilizationSnapshot, NavigationAction, TriageEncounter
from backend.services.analytics_service import get_rightpath_analytics
from backend.services.auth_service import register_user


def _encounter(
    *,
    user_id: int | None,
    is_emergency: bool,
    recommended_acuity: str,
    created_at: datetime,
    session_id: str = "s",
    chief_complaint: str = "test complaint text",
) -> TriageEncounter:
    return TriageEncounter(
        user_id=user_id,
        session_id=session_id,
        chief_complaint=chief_complaint,
        symptoms_duration="",
        has_red_flags=is_emergency,
        is_emergency=is_emergency,
        recommended_acuity=recommended_acuity,
        urgency_level="EMERGENCY" if is_emergency else "ROUTINE",
        recommended_setting_name=recommended_acuity.replace("_", " ").title(),
        clinical_rationale="test",
        safety_disclaimer="test",
        rule_set_version="1.0.0",
        created_at=created_at,
    )


class RightPathAnalyticsServiceTests(unittest.TestCase):
    """Direct service-layer tests against an isolated in-memory SQLite DB --
    never the live PostgreSQL database (per the standing database-safety
    rule: no test in this suite touches production/demo rows)."""

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

    def test_empty_database_returns_valid_zero_and_empty_response(self) -> None:
        data = get_rightpath_analytics(session=self.session)
        self.assertEqual(data["totalRightPathUsers"], 0)
        self.assertEqual(data["totalAssessments"], 0)
        self.assertEqual(data["emergencyAssessments"], 0)
        self.assertEqual(data["nonEmergencyAssessments"], 0)
        self.assertEqual(data["acuityDistribution"], [])
        self.assertEqual(data["navigationActions"], 0)
        self.assertEqual(data["pathwayDistribution"], [])
        self.assertEqual(data["activityTrend"], [])

    def test_aggregation_with_seeded_rows_returns_correct_counts(self) -> None:
        self.session.add_all([
            _encounter(user_id=1, is_emergency=True, recommended_acuity="EMERGENCY",
                       created_at=datetime(2026, 1, 5, tzinfo=timezone.utc), session_id="a"),
            _encounter(user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH",
                       created_at=datetime(2026, 1, 5, tzinfo=timezone.utc), session_id="b"),
            _encounter(user_id=2, is_emergency=False, recommended_acuity="URGENT_CARE",
                       created_at=datetime(2026, 1, 6, tzinfo=timezone.utc), session_id="c"),
            _encounter(user_id=None, is_emergency=False, recommended_acuity="TELEHEALTH",
                       created_at=datetime(2026, 1, 6, tzinfo=timezone.utc), session_id="d"),
        ])
        self.session.commit()
        self.session.add(NavigationAction(
            user_id=1, action_type="PROVIDER_SELECTED", selected_acuity="URGENT_CARE",
            recorded_at=datetime.now(timezone.utc),
        ))
        self.session.commit()

        data = get_rightpath_analytics(session=self.session)

        self.assertEqual(data["totalAssessments"], 4)
        self.assertEqual(data["emergencyAssessments"], 1)
        self.assertEqual(data["nonEmergencyAssessments"], 3)
        self.assertEqual(data["navigationActions"], 1)
        self.assertEqual(data["pathwayDistribution"], [{"pathway": "URGENT_CARE", "count": 1}])

        acuity_counts = {row["acuity"]: row["count"] for row in data["acuityDistribution"]}
        self.assertEqual(acuity_counts, {"EMERGENCY": 1, "TELEHEALTH": 2, "URGENT_CARE": 1})

        trend_counts = {row["date"]: row["count"] for row in data["activityTrend"]}
        self.assertEqual(trend_counts, {"2026-01-05": 2, "2026-01-06": 2})

    def test_multiple_assessments_from_same_user_counts_user_once(self) -> None:
        self.session.add_all([
            _encounter(user_id=7, is_emergency=False, recommended_acuity="TELEHEALTH",
                       created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), session_id="x"),
            _encounter(user_id=7, is_emergency=False, recommended_acuity="TELEHEALTH",
                       created_at=datetime(2026, 1, 2, tzinfo=timezone.utc), session_id="y"),
            _encounter(user_id=7, is_emergency=True, recommended_acuity="EMERGENCY",
                       created_at=datetime(2026, 1, 3, tzinfo=timezone.utc), session_id="z"),
        ])
        self.session.commit()

        data = get_rightpath_analytics(session=self.session)
        self.assertEqual(data["totalRightPathUsers"], 1)
        self.assertEqual(data["totalAssessments"], 3)

    def test_anonymous_encounters_do_not_inflate_user_count(self) -> None:
        self.session.add_all([
            _encounter(user_id=None, is_emergency=False, recommended_acuity="TELEHEALTH",
                       created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), session_id="anon-1"),
            _encounter(user_id=None, is_emergency=False, recommended_acuity="TELEHEALTH",
                       created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), session_id="anon-2"),
        ])
        self.session.commit()

        data = get_rightpath_analytics(session=self.session)
        self.assertEqual(data["totalRightPathUsers"], 0)
        self.assertEqual(data["totalAssessments"], 2)

    def test_response_contains_no_raw_patient_or_private_fields(self) -> None:
        self.session.add(_encounter(
            user_id=3, is_emergency=True, recommended_acuity="EMERGENCY",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), session_id="priv",
            chief_complaint="this is private free-text symptom detail",
        ))
        self.session.commit()

        data = get_rightpath_analytics(session=self.session)
        serialized = str(data)
        self.assertNotIn("chief_complaint", serialized)
        self.assertNotIn("private free-text symptom detail", serialized)
        self.assertNotIn("action_details", serialized)
        self.assertNotIn("session_id", serialized)
        for forbidden_key in ("name", "email", "zip", "contact", "user_id", "member_id"):
            self.assertNotIn(forbidden_key, data)


def _snapshot(member_id: int, *, ed_visits: int, ed_cost: float) -> tuple[Member, MemberUtilizationSnapshot]:
    member = Member(
        id=member_id, bene_id=f"CMS-IMPACT-{member_id}", age=60, gender="Female",
        dual_eligibility_months=0, chronic_condition_count=0,
    )
    snapshot = MemberUtilizationSnapshot(
        id=member_id, member_id=member_id,
        inpatient_visit_count=0, inpatient_total_cost=Decimal("0"),
        outpatient_visit_count=0, outpatient_total_cost=Decimal("0"),
        ed_visit_count=ed_visits, total_claim_payment_amount=Decimal("0"),
        total_ed_related_cost=Decimal(str(ed_cost)), average_claim_cost=Decimal("0"),
        provider_count=1,
    )
    return member, snapshot


class RightPathImpactAnalyticsServiceTests(unittest.TestCase):
    """Tests for the RightPath Impact fields added to get_rightpath_analytics()
    in this phase: confirmed non-ED navigation counts, per-pathway navigation
    counts, and the CMS-derived potential-ED-cost-opportunity estimate."""

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

    def _seed_encounters_and_actions(self) -> None:
        encounters = [
            _encounter(user_id=1, is_emergency=True, recommended_acuity="EMERGENCY",
                       created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), session_id="e1"),
            _encounter(user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH",
                       created_at=datetime(2026, 1, 2, tzinfo=timezone.utc), session_id="e2"),
            _encounter(user_id=2, is_emergency=False, recommended_acuity="URGENT_CARE",
                       created_at=datetime(2026, 1, 3, tzinfo=timezone.utc), session_id="e3"),
            _encounter(user_id=2, is_emergency=False, recommended_acuity="PRIMARY_CARE",
                       created_at=datetime(2026, 1, 4, tzinfo=timezone.utc), session_id="e4"),
            _encounter(user_id=3, is_emergency=False, recommended_acuity="TELEHEALTH",
                       created_at=datetime(2026, 1, 5, tzinfo=timezone.utc), session_id="e5"),
        ]
        self.session.add_all(encounters)
        self.session.commit()

        actions = [
            NavigationAction(user_id=1, action_type="APPOINTMENT_BOOKED", selected_acuity="TELEHEALTH",
                              recorded_at=datetime.now(timezone.utc)),
            NavigationAction(user_id=2, action_type="PROVIDER_SELECTED", selected_acuity="URGENT_CARE",
                              recorded_at=datetime.now(timezone.utc)),
            NavigationAction(user_id=2, action_type="PROVIDER_SELECTED", selected_acuity="PRIMARY_CARE",
                              recorded_at=datetime.now(timezone.utc)),
            # A navigation action explicitly recorded against EMERGENCY (schema
            # permits it even though the current UI never offers this choice)
            # must NOT be counted as a confirmed non-ED navigation.
            NavigationAction(user_id=1, action_type="PROVIDER_SELECTED", selected_acuity="EMERGENCY",
                              recorded_at=datetime.now(timezone.utc)),
        ]
        self.session.add_all(actions)
        self.session.commit()

    def test_totals_and_per_pathway_navigation_counts(self) -> None:
        self._seed_encounters_and_actions()
        data = get_rightpath_analytics(session=self.session)

        self.assertEqual(data["totalRightPathAssessments"], 5)
        self.assertEqual(data["nonEmergencyRecommendations"], 4)
        self.assertEqual(data["telehealthNavigations"], 1)
        self.assertEqual(data["primaryCareNavigations"], 1)
        self.assertEqual(data["urgentCareNavigations"], 1)
        self.assertEqual(data["emergencyNavigations"], 1)
        # 3 non-ED actions (telehealth + urgent care + primary care); the
        # EMERGENCY-selected action is excluded.
        self.assertEqual(data["confirmedNonEdNavigationActions"], 3)
        self.assertEqual(data["potentialEdUtilizationOpportunities"], 3)

    def test_potential_ed_cost_opportunity_equals_opportunities_times_cms_average(self) -> None:
        self._seed_encounters_and_actions()
        member, snapshot = _snapshot(1, ed_visits=10, ed_cost=29473.10)  # avg = 2947.31/visit
        self.session.add_all([member, snapshot])
        self.session.commit()

        data = get_rightpath_analytics(session=self.session)
        self.assertAlmostEqual(data["averageEdClaimCost"], 2947.31, places=2)
        self.assertEqual(data["potentialEdUtilizationOpportunities"], 3)
        self.assertAlmostEqual(data["potentialEdCostOpportunity"], 3 * 2947.31, places=2)

    def test_methodology_block_states_no_causal_claim(self) -> None:
        data = get_rightpath_analytics(session=self.session)
        self.assertEqual(data["costOpportunityMethodology"]["causalClaim"], False)
        self.assertIn("confirmed non-ED navigation actions", data["costOpportunityMethodology"]["formula"])

    def test_zero_cms_ed_visits_returns_none_not_fabricated_baseline(self) -> None:
        """No member_utilization_snapshots rows at all -> a per-visit average
        is undefined; must be None, never 0 or a hardcoded constant."""
        self._seed_encounters_and_actions()
        data = get_rightpath_analytics(session=self.session)
        self.assertIsNone(data["averageEdClaimCost"])
        self.assertIsNone(data["potentialEdCostOpportunity"])
        # The opportunity *count* is still real and reported even though the
        # dollar estimate cannot be computed.
        self.assertEqual(data["potentialEdUtilizationOpportunities"], 3)

    def test_zero_confirmed_navigation_actions_returns_honest_zero(self) -> None:
        self.session.add(_encounter(
            user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc), session_id="only",
        ))
        member, snapshot = _snapshot(1, ed_visits=10, ed_cost=29473.10)
        self.session.add_all([member, snapshot])
        self.session.commit()

        data = get_rightpath_analytics(session=self.session)
        self.assertEqual(data["confirmedNonEdNavigationActions"], 0)
        self.assertEqual(data["potentialEdUtilizationOpportunities"], 0)
        self.assertEqual(data["potentialEdCostOpportunity"], 0)  # 0 opportunities x a real baseline is a real zero
        self.assertIsNotNone(data["averageEdClaimCost"])

    def test_response_contains_no_raw_patient_data(self) -> None:
        self._seed_encounters_and_actions()
        data = get_rightpath_analytics(session=self.session)
        serialized = str(data)
        for forbidden in ("chief_complaint", "test complaint text", "action_details", "session_id"):
            self.assertNotIn(forbidden, serialized)
        for forbidden_key in ("name", "email", "zip", "contact", "user_id", "member_id"):
            self.assertNotIn(forbidden_key, data)


class RightPathAnalyticsRouteTests(unittest.TestCase):
    """HTTP-level authorization + response-shape tests via the Flask test
    client, following the existing route-test convention (see
    test_patients_pagination.py): a mocked session_scope binds every request
    to one isolated in-memory SQLite session, so nothing here ever touches
    the live PostgreSQL database."""

    def setUp(self) -> None:
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.session: Session = self.SessionLocal()

    def tearDown(self) -> None:
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @contextmanager
    def _mock_session_scope(self):
        yield self.session

    def _register_and_login(self, email: str, role: str) -> None:
        register_user(email=email, password="password123", role=role, session=self.session)
        response = self.client.post(
            "/api/auth/login", json={"email": email, "password": "password123"}
        )
        self.assertEqual(response.status_code, 200, response.get_json())

    def test_payer_can_access_endpoint(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.analytics_service.session_scope", self._mock_session_scope):
            self._register_and_login("rightpath-payer@example.com", "PAYER")
            response = self.client.get("/api/payer/analytics/rightpath")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIn("totalRightPathUsers", data)
            self.assertIn("acuityDistribution", data)
            self.assertIn("pathwayDistribution", data)
            self.assertIn("activityTrend", data)

    def test_payer_can_access_impact_metrics_on_the_same_endpoint(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.analytics_service.session_scope", self._mock_session_scope):
            self._register_and_login("rightpath-impact-payer@example.com", "PAYER")
            response = self.client.get("/api/payer/analytics/rightpath")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            for field in (
                "totalRightPathAssessments",
                "nonEmergencyRecommendations",
                "confirmedNonEdNavigationActions",
                "telehealthNavigations",
                "primaryCareNavigations",
                "urgentCareNavigations",
                "emergencyNavigations",
                "potentialEdUtilizationOpportunities",
                "averageEdClaimCost",
                "potentialEdCostOpportunity",
                "costOpportunityMethodology",
            ):
                self.assertIn(field, data)
            self.assertEqual(data["costOpportunityMethodology"]["causalClaim"], False)

    def test_patient_cannot_access_impact_endpoint(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._register_and_login("rightpath-impact-patient@example.com", "PATIENT")
            response = self.client.get("/api/payer/analytics/rightpath")
            self.assertEqual(response.status_code, 403)

    def test_patient_cannot_access_endpoint(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._register_and_login("rightpath-patient@example.com", "PATIENT")
            response = self.client.get("/api/payer/analytics/rightpath")
            self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_access_endpoint(self) -> None:
        response = self.client.get("/api/payer/analytics/rightpath")
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
