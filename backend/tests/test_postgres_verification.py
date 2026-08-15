"""Opt-in PostgreSQL read-only integration verification test suite.

This module executes strictly read-only verification queries against the configured
PostgreSQL database when `DATABASE_URL` is set in the process environment.

If `DATABASE_URL` is absent, the suite skips cleanly without failing normal unit tests.
"""

from __future__ import annotations

import os
import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.models import (
    Member,
    MemberUtilizationSnapshot,
    ModelRun,
    UtilizationAnomalyResult,
    XGBoostUtilizationPrediction,
)
from backend.repositories.member_repository import MemberRepository


DATABASE_URL = os.getenv("DATABASE_URL")


@unittest.skipUnless(DATABASE_URL, "Opt-in PostgreSQL verification skipped: DATABASE_URL not set.")
class PostgresIntegrationVerificationTestCase(unittest.TestCase):
    """Read-only PostgreSQL integration verification test suite."""

    @classmethod
    def setUpClass(cls) -> None:
        if not DATABASE_URL:
            return
        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        cls.SessionLocal = sessionmaker(bind=cls.engine, autoflush=False, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "engine"):
            cls.engine.dispose()

    def setUp(self) -> None:
        self.session: Session = self.SessionLocal()

    def tearDown(self) -> None:
        self.session.close()

    def test_verify_postgresql_row_counts_and_models(self) -> None:
        member_count = self.session.scalar(select(func.count(Member.id))) or 0
        snapshot_count = self.session.scalar(select(func.count(MemberUtilizationSnapshot.id))) or 0
        xgb_count = self.session.scalar(select(func.count(XGBoostUtilizationPrediction.id))) or 0
        anomaly_count = self.session.scalar(select(func.count(UtilizationAnomalyResult.id))) or 0
        model_run_count = self.session.scalar(select(func.count(ModelRun.model_run_id))) or 0

        self.assertGreater(member_count, 0, "PostgreSQL members table should contain records.")
        self.assertEqual(
            member_count, snapshot_count, "Member count and snapshot count should match."
        )
        self.assertEqual(
            member_count, xgb_count, "Member count and XGBoost prediction count should match."
        )
        self.assertEqual(
            member_count, anomaly_count, "Member count and anomaly result count should match."
        )
        self.assertGreaterEqual(model_run_count, 2, "ModelRuns table should record model runs.")

    def test_verify_no_member_loss_via_repository(self) -> None:
        repo = MemberRepository(self.session)
        results = repo.get_all_combined_results()
        member_count = self.session.scalar(select(func.count(Member.id))) or 0
        self.assertEqual(
            len(results),
            member_count,
            "MemberRepository bulk query must return 100% of members without silent dropping.",
        )


if __name__ == "__main__":
    unittest.main()
