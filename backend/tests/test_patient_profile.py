"""Regression tests for self-entered patient profile persistence
(GET/PUT /api/profile), scoped strictly to the authenticated caller's own
user_id.

This is independent of, and does not exercise or alter, CMS-Member-based
PAYER-only data (test_authorization.py, test_navigation_history.py) or
user_id-linked triage/navigation-action history (test_account_history.py):
a PatientProfile is self-entered identity data, never clinical/ML data.
"""

from __future__ import annotations

from contextlib import contextmanager
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app import create_app
from backend.database import Base
from backend.services.auth_service import register_user


class PatientProfileTestCase(unittest.TestCase):
    """Test suite for GET/PUT /api/profile."""

    def setUp(self) -> None:
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.db_session: Session = self.SessionLocal()

    def tearDown(self) -> None:
        self.db_session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @contextmanager
    def _mock_session_scope(self):
        yield self.db_session

    def _patches(self):
        return (
            patch("backend.services.auth_service.session_scope", self._mock_session_scope),
            patch("backend.services.patient_profile_service.session_scope", self._mock_session_scope),
        )

    def _register_and_login_patient(self, email: str) -> None:
        register_response = self.client.post(
            "/api/auth/register",
            json={"email": email, "password": "password123", "role": "PATIENT"},
        )
        self.assertEqual(register_response.status_code, 201, register_response.get_json())
        login_response = self.client.post(
            "/api/auth/login", json={"email": email, "password": "password123"}
        )
        self.assertEqual(login_response.status_code, 200, login_response.get_json())

    def _login_as_payer(self, email: str) -> None:
        register_user(email=email, password="password123", role="PAYER", session=self.db_session)
        response = self.client.post(
            "/api/auth/login", json={"email": email, "password": "password123"}
        )
        self.assertEqual(response.status_code, 200, response.get_json())

    # ------------------------------------------------------------------
    # A. Authenticated PATIENT can GET their profile (honest empty state
    #    for a caller who has never saved one -- no Jane-Doe substitution).
    # ------------------------------------------------------------------

    def test_get_profile_returns_honest_empty_state_when_unsaved(self) -> None:
        p1, p2 = self._patches()
        with p1, p2:
            self._register_and_login_patient("profile-empty@example.com")
            response = self.client.get("/api/profile")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertFalse(data["exists"])
            self.assertIsNone(data["fullName"])
            self.assertIsNone(data["age"])

    # ------------------------------------------------------------------
    # B. Authenticated PATIENT can update their profile.
    # ------------------------------------------------------------------

    def test_put_profile_creates_and_returns_saved_values(self) -> None:
        p1, p2 = self._patches()
        with p1, p2:
            self._register_and_login_patient("profile-save@example.com")
            response = self.client.put("/api/profile", json={
                "fullName": "Alex Rivera",
                "age": 37,
                "zipCode": "10001",
                "contactInfo": "alex.rivera@example.com",
                "insuranceStatus": "Medicaid",
                "preferredCareSetting": "Primary Care Clinic",
                "communicationPreference": "SMS Text Messages",
            })
            self.assertEqual(response.status_code, 200, response.get_json())
            data = response.get_json()
            self.assertTrue(data["exists"])
            self.assertEqual(data["fullName"], "Alex Rivera")
            self.assertEqual(data["age"], 37)
            self.assertEqual(data["zipCode"], "10001")

    # ------------------------------------------------------------------
    # C. Updated profile survives a fresh GET (independent request).
    # ------------------------------------------------------------------

    def test_updated_profile_survives_fresh_get(self) -> None:
        p1, p2 = self._patches()
        with p1, p2:
            self._register_and_login_patient("profile-refresh@example.com")
            self.client.put("/api/profile", json={"fullName": "Casey Nguyen", "age": 29})

            response = self.client.get("/api/profile")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertTrue(data["exists"])
            self.assertEqual(data["fullName"], "Casey Nguyen")
            self.assertEqual(data["age"], 29)

    # ------------------------------------------------------------------
    # D. Updated profile survives logout/login with the same account.
    # ------------------------------------------------------------------

    def test_updated_profile_survives_logout_and_login(self) -> None:
        p1, p2 = self._patches()
        with p1, p2:
            self._register_and_login_patient("profile-relogin@example.com")
            self.client.put("/api/profile", json={"fullName": "Morgan Lee", "zipCode": "94110"})

            logout = self.client.post("/api/auth/logout")
            self.assertEqual(logout.status_code, 200)

            login = self.client.post(
                "/api/auth/login",
                json={"email": "profile-relogin@example.com", "password": "password123"},
            )
            self.assertEqual(login.status_code, 200)

            response = self.client.get("/api/profile")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["fullName"], "Morgan Lee")
            self.assertEqual(data["zipCode"], "94110")

    # ------------------------------------------------------------------
    # E. One authenticated user cannot read or update another user's
    #    profile. There is no user id request parameter at all.
    # ------------------------------------------------------------------

    def test_another_patient_cannot_read_or_update_profile(self) -> None:
        p1, p2 = self._patches()
        with p1, p2:
            self._register_and_login_patient("profile-a@example.com")
            self.client.put("/api/profile", json={"fullName": "Patient A"})

            logout = self.client.post("/api/auth/logout")
            self.assertEqual(logout.status_code, 200)

            self._register_and_login_patient("profile-b@example.com")
            response = self.client.get("/api/profile")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            # Patient B has never saved a profile -- must not see A's data.
            self.assertFalse(data["exists"])
            self.assertNotEqual(data.get("fullName"), "Patient A")

            update = self.client.put("/api/profile", json={"fullName": "Patient B"})
            self.assertEqual(update.status_code, 200)

            check_a = self.client.get("/api/profile")
            # Still scoped to B's own session -- cannot see or have altered A's row.
            self.assertEqual(check_a.get_json()["fullName"], "Patient B")

    # ------------------------------------------------------------------
    # F. Anonymous requests are rejected.
    # ------------------------------------------------------------------

    def test_anonymous_get_and_put_are_rejected(self) -> None:
        get_response = self.client.get("/api/profile")
        self.assertEqual(get_response.status_code, 401)

        put_response = self.client.put("/api/profile", json={"fullName": "Nobody"})
        self.assertEqual(put_response.status_code, 401)

    # ------------------------------------------------------------------
    # G. Existing triage flow is unaffected by the new profile table/routes.
    # ------------------------------------------------------------------

    def test_existing_triage_still_works(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            response = self.client.post(
                "/api/triage", json={"chiefComplaint": "Mild seasonal allergies"}
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("recommendedAcuity", response.get_json())

    # ------------------------------------------------------------------
    # H. Existing my-history persistence is unaffected by profile saves.
    # ------------------------------------------------------------------

    def test_existing_history_persistence_still_works_alongside_profile(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope), \
             patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.patient_profile_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.navigation.session_scope", self._mock_session_scope):
            self._register_and_login_patient("profile-history@example.com")
            self.client.put("/api/profile", json={"fullName": "History Tester"})
            triage = self.client.post(
                "/api/triage", json={"chiefComplaint": "Mild headache"}
            ).get_json()

            history = self.client.get("/api/navigation/my-history")
            self.assertEqual(history.status_code, 200)
            data = history.get_json()
            self.assertEqual(data["totalEncounters"], 1)
            self.assertEqual(data["encounters"][0]["encounterId"], triage["encounterId"])

    # ------------------------------------------------------------------
    # I. PAYER-only route authorization is unchanged by the new blueprint.
    # ------------------------------------------------------------------

    def test_payer_only_authorization_is_unchanged(self) -> None:
        p1, p2 = self._patches()
        with p1, p2:
            self._register_and_login_patient("profile-patient-payer-check@example.com")
            patients_response = self.client.get("/api/patients")
            self.assertEqual(patients_response.status_code, 403)

            logout = self.client.post("/api/auth/logout")
            self.assertEqual(logout.status_code, 200)

            self._login_as_payer("profile-payer-check@example.com")
            payer_patients_response = self.client.get("/api/patients")
            self.assertEqual(payer_patients_response.status_code, 200)

            # A PAYER can also have their own self-entered profile row,
            # scoped identically by user_id, with no effect on PAYER-only
            # CMS-Member routes.
            payer_profile_response = self.client.get("/api/profile")
            self.assertEqual(payer_profile_response.status_code, 200)
            self.assertFalse(payer_profile_response.get_json()["exists"])


if __name__ == "__main__":
    unittest.main()
