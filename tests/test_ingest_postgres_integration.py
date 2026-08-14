"""Opt-in non-destructive PostgreSQL ingestion checks."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import func, select

from backend.database import session_scope
from backend.ingest_ml_data import ingest_ml_data, load_and_validate_sources
from backend.models import (
    Member,
    MemberUtilizationSnapshot,
    ModelRun,
    UtilizationAnomalyResult,
    XGBoostUtilizationPrediction,
)


pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INGESTION_TESTS") != "1",
    reason="Set RUN_POSTGRES_INGESTION_TESTS=1 to run against the configured PostgreSQL database.",
)
def test_reingestion_is_idempotent_for_configured_database() -> None:
    """Run the approved importer twice without deleting or recreating database data."""
    sources = load_and_validate_sources()
    first = ingest_ml_data()
    second = ingest_ml_data()
    assert first == second
    assert first["members"] == sources.member_count

    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(Member)) == sources.member_count
        assert session.scalar(select(func.count()).select_from(MemberUtilizationSnapshot)) == sources.member_count
        assert session.scalar(select(func.count()).select_from(XGBoostUtilizationPrediction)) == sources.member_count
        assert session.scalar(select(func.count()).select_from(UtilizationAnomalyResult)) == sources.member_count
        assert session.scalar(select(func.count()).select_from(ModelRun)) == 2
        assert session.scalar(
            select(func.count()).select_from(XGBoostUtilizationPrediction).where(
                XGBoostUtilizationPrediction.high_utilization_pattern == 1
            )
        ) == 882
        assert session.scalar(
            select(func.count()).select_from(UtilizationAnomalyResult).where(
                UtilizationAnomalyResult.anomaly_flag == 1
            )
        ) == 174
