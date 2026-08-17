"""Tests for optional server-side pagination on GET /api/patients.

Added because the Werkzeug development server was observed (via real
browser/HTTP testing) to reset the connection on Windows when sending the
full ~7MB unpaginated response for the current 8,671-member population.
Omitting page/pageSize must preserve the exact historical (plain array)
response shape for any existing caller.
"""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app import create_app
from backend.database import Base
from backend.models import Member, MemberUtilizationSnapshot
from backend.services.auth_service import register_user


class PatientsPaginationTestCase(unittest.TestCase):
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

    def _login_as_payer(self) -> None:
        register_user(email="patients-page@example.com", password="password123", role="PAYER", session=self.session)
        response = self.client.post(
            "/api/auth/login", json={"email": "patients-page@example.com", "password": "password123"}
        )
        self.assertEqual(response.status_code, 200, response.get_json())

    def _seed_members(self, count: int) -> None:
        for i in range(1, count + 1):
            self.session.add(Member(
                id=i, bene_id=f"CMS-PAGE-{i}", age=50, gender="Female",
                dual_eligibility_months=0, chronic_condition_count=0,
            ))
            self.session.add(MemberUtilizationSnapshot(
                id=i, member_id=i, inpatient_visit_count=0, inpatient_total_cost=Decimal("0"),
                outpatient_visit_count=0, outpatient_total_cost=Decimal("0"), ed_visit_count=0,
                total_claim_payment_amount=Decimal("0"), total_ed_related_cost=Decimal("0"),
                average_claim_cost=Decimal("0"), provider_count=1,
            ))
        self.session.commit()

    def test_no_page_params_returns_plain_array_unchanged(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.patient_service.session_scope", self._mock_session_scope):
            self._login_as_payer()
            self._seed_members(5)
            response = self.client.get("/api/patients")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertIsInstance(data, list)
            self.assertEqual(len(data), 5)

    def test_page_and_page_size_return_paginated_envelope(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.patient_service.session_scope", self._mock_session_scope):
            self._login_as_payer()
            self._seed_members(25)
            response = self.client.get("/api/patients?page=1&pageSize=10")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["total"], 25)
            self.assertEqual(data["page"], 1)
            self.assertEqual(data["pageSize"], 10)
            self.assertEqual(data["totalPages"], 3)
            self.assertEqual(len(data["items"]), 10)

            page3 = self.client.get("/api/patients?page=3&pageSize=10").get_json()
            self.assertEqual(len(page3["items"]), 5)

    def test_page_beyond_range_clamps_to_last_page(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.patient_service.session_scope", self._mock_session_scope):
            self._login_as_payer()
            self._seed_members(3)
            response = self.client.get("/api/patients?page=999&pageSize=10")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["page"], 1)
            self.assertEqual(len(data["items"]), 3)

    def test_invalid_page_is_rejected(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._login_as_payer()
            response = self.client.get("/api/patients?page=abc")
            self.assertEqual(response.status_code, 400)
            response2 = self.client.get("/api/patients?page=0")
            self.assertEqual(response2.status_code, 400)

    def test_invalid_band_and_anomaly_are_rejected(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._login_as_payer()
            self.assertEqual(self.client.get("/api/patients?band=weird").status_code, 400)
            self.assertEqual(self.client.get("/api/patients?anomaly=weird").status_code, 400)

    def test_anomaly_filter_with_pagination(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.patient_service.session_scope", self._mock_session_scope):
            self._login_as_payer()
            self._seed_members(4)
            # No ML runs/predictions seeded -> every member has isAnomalous=False (no anomaly_flag).
            response = self.client.get("/api/patients?anomaly=NORMAL&page=1&pageSize=10")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["total"], 4)
            response2 = self.client.get("/api/patients?anomaly=ANOMALOUS&page=1&pageSize=10")
            self.assertEqual(response2.get_json()["total"], 0)

    def test_patient_role_rejected_for_paginated_request(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            register_user(email="patients-page-patient@example.com", password="password123", role="PATIENT", session=self.session)
            self.client.post(
                "/api/auth/login", json={"email": "patients-page-patient@example.com", "password": "password123"}
            )
            response = self.client.get("/api/patients?page=1&pageSize=10")
            self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
