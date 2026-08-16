"""Endpoint coverage for database-backed navigation recommendations."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app import create_app
from backend.database import Base
from backend.models import (
    Member,
    MemberUtilizationSnapshot,
    ModelRun,
    UtilizationAnomalyResult,
    XGBoostUtilizationPrediction,
)


@pytest.fixture
def client_and_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session: Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()

    @contextmanager
    def session_scope():
        yield session

    app = create_app()
    app.config["TESTING"] = True
    with patch("backend.routes.navigation.session_scope", session_scope):
        yield app.test_client(), session

    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def add_member_with_snapshot(session: Session, member_id: int, bene_id: str) -> MemberUtilizationSnapshot:
    member = Member(
        id=member_id,
        bene_id=bene_id,
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
        ed_visit_count=1,
        total_claim_payment_amount=Decimal("0"),
        total_ed_related_cost=Decimal("0"),
        average_claim_cost=Decimal("0"),
        provider_count=1,
    )
    session.add_all([member, snapshot])
    return snapshot


def test_recommendations_use_latest_runs_and_resolve_beneficiary_id(client_and_session):
    client, session = client_and_session
    snapshot = add_member_with_snapshot(session, 10, "CMS-NAV-10")
    stale_xgb = ModelRun(
        model_run_id=1,
        model_type="xgboost",
        purpose="test",
        generated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    latest_xgb = ModelRun(
        model_run_id=2,
        model_type="xgboost",
        purpose="test",
        generated_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    stale_anomaly = ModelRun(
        model_run_id=3,
        model_type="isolation_forest",
        purpose="test",
        generated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    latest_anomaly = ModelRun(
        model_run_id=4,
        model_type="isolation_forest",
        purpose="test",
        generated_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    session.add_all([stale_xgb, latest_xgb, stale_anomaly, latest_anomaly])
    session.flush()
    session.add_all(
        [
            XGBoostUtilizationPrediction(
                id=10,
                member_id=10,
                utilization_snapshot_id=snapshot.id,
                model_run_id=stale_xgb.model_run_id,
                high_utilization_pattern=1,
                predicted_high_utilization_pattern=1,
                high_utilization_probability=0.95,
                dataset_split="test",
            ),
            XGBoostUtilizationPrediction(
                id=11,
                member_id=10,
                utilization_snapshot_id=snapshot.id,
                model_run_id=latest_xgb.model_run_id,
                high_utilization_pattern=0,
                predicted_high_utilization_pattern=0,
                high_utilization_probability=0.20,
                dataset_split="test",
            ),
            UtilizationAnomalyResult(
                id=10,
                member_id=10,
                utilization_snapshot_id=snapshot.id,
                model_run_id=stale_anomaly.model_run_id,
                anomaly_score=0.8,
                anomaly_flag=1,
                anomaly_rank=1,
                generated_at=datetime.now(timezone.utc),
            ),
            UtilizationAnomalyResult(
                id=11,
                member_id=10,
                utilization_snapshot_id=snapshot.id,
                model_run_id=latest_anomaly.model_run_id,
                anomaly_score=-0.1,
                anomaly_flag=0,
                anomaly_rank=2,
                generated_at=datetime.now(timezone.utc),
            ),
        ]
    )
    session.commit()

    response = client.get("/api/navigation/members/CMS-NAV-10/recommendations")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["member_id"] == "10"
    assert payload["navigation_priority"] == "LOW"


def test_recommendations_ignore_outputs_when_current_model_run_is_absent(client_and_session):
    client, session = client_and_session
    snapshot = add_member_with_snapshot(session, 11, "CMS-NAV-11")
    legacy_xgb = ModelRun(model_run_id=11, model_type="legacy_xgboost", purpose="test")
    legacy_anomaly = ModelRun(
        model_run_id=12, model_type="legacy_isolation_forest", purpose="test"
    )
    session.add_all([legacy_xgb, legacy_anomaly])
    session.flush()
    session.add_all(
        [
            XGBoostUtilizationPrediction(
                id=12,
                member_id=11,
                utilization_snapshot_id=snapshot.id,
                model_run_id=legacy_xgb.model_run_id,
                high_utilization_pattern=1,
                predicted_high_utilization_pattern=1,
                high_utilization_probability=0.99,
                dataset_split="test",
            ),
            UtilizationAnomalyResult(
                id=12,
                member_id=11,
                utilization_snapshot_id=snapshot.id,
                model_run_id=legacy_anomaly.model_run_id,
                anomaly_score=0.9,
                anomaly_flag=1,
                anomaly_rank=1,
                generated_at=datetime.now(timezone.utc),
            ),
        ]
    )
    session.commit()

    response = client.get("/api/navigation/members/11/recommendations")

    assert response.status_code == 200
    assert response.get_json()["navigation_priority"] == "LOW"


def test_recommendations_return_404_for_unknown_member(client_and_session):
    client, _ = client_and_session

    response = client.get("/api/navigation/members/does-not-exist/recommendations")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Member not found"}
