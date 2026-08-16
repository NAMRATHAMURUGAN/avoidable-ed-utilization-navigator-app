"""Unit tests for authentication: registration, login, logout, session identity,
and the login_required/role_required decorators.

Uses an isolated in-memory SQLite database and Flask test client, matching the
conventions already used by backend/tests/test_api_endpoints.py and
backend/tests/test_navigation_history.py. Never uses production PostgreSQL
credentials, and never prints/logs passwords or session contents.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from flask import jsonify
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from werkzeug.security import generate_password_hash

from backend.app import create_app
from backend.auth.decorators import login_required, role_required
from backend.database import Base
from backend.models import User
from backend.repositories.user_repository import UserRepository


class AuthTestCase(unittest.TestCase):
    """Test suite for /api/auth/* endpoints and the auth decorators."""

    def setUp(self) -> None:
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

        # In-memory SQLite database engine for isolated unit testing
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self.db_session: Session = self.SessionLocal()

        self._register_decorator_probe_routes()

    def tearDown(self) -> None:
        self.db_session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @contextmanager
    def _mock_session_scope(self):
        yield self.db_session

    def _register_decorator_probe_routes(self) -> None:
        """Minimal test-only routes to exercise the decorators in isolation,
        without touching any real business route file (route protection is a
        separate, later review step)."""

        @login_required
        def _login_required_probe():
            return jsonify({"ok": True}), 200

        @role_required("PAYER")
        def _payer_required_probe():
            return jsonify({"ok": True}), 200

        @role_required("PATIENT")
        def _patient_required_probe():
            return jsonify({"ok": True}), 200

        self.app.add_url_rule("/__test/login-required", view_func=_login_required_probe)
        self.app.add_url_rule("/__test/payer-required", view_func=_payer_required_probe)
        self.app.add_url_rule("/__test/patient-required", view_func=_patient_required_probe)

    def _register(self, email: str, password: str, role: str):
        return self.client.post(
            "/api/auth/register", json={"email": email, "password": password, "role": role}
        )

    def _login(self, email: str, password: str):
        return self.client.post("/api/auth/login", json={"email": email, "password": password})

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def test_successful_registration_returns_safe_user_info(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            response = self._register("newuser@example.com", "password123", "PATIENT")
            self.assertEqual(response.status_code, 201)
            data = response.get_json()
            self.assertEqual(data["email"], "newuser@example.com")
            self.assertEqual(data["role"], "PATIENT")
            self.assertIn("id", data)
            self.assertNotIn("password", data)
            self.assertNotIn("password_hash", data)

    def test_duplicate_registration_rejected(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            first = self._register("dupe@example.com", "password123", "PATIENT")
            self.assertEqual(first.status_code, 201)
            second = self._register("dupe@example.com", "password123", "PAYER")
            self.assertEqual(second.status_code, 400)
            self.assertIn("error", second.get_json())

    def test_concurrent_duplicate_registration_handled_cleanly(self) -> None:
        """Regression test for the check-then-insert race between the
        pre-check and the insert in _execute_register_user.

        A true concurrent-database test isn't practical in this harness:
        every test uses a single shared in-memory SQLite session (patched in
        via _mock_session_scope), so there is no second, independent
        transaction that could genuinely race against this one. Instead this
        test deterministically reproduces the exact failure mode the race
        produces: a row with the target email already committed in the
        database, combined with UserRepository.get_by_email missing it on
        its first call (simulating the pre-check running in the instant
        before a concurrent request's insert would have committed). This
        forces a real IntegrityError out of the database on the subsequent
        insert, exactly as a genuine race would, and verifies it is turned
        into the same clean 400 duplicate-account response rather than an
        unhandled 500 -- without weakening or bypassing the unique
        constraint itself.
        """
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            # Simulate the "winning" concurrent request: a row already
            # exists and is committed before our request's insert runs.
            existing = User(
                email="racecondition@example.com",
                password_hash=generate_password_hash("password123"),
                role="PATIENT",
                created_at=datetime.now(timezone.utc),
            )
            self.db_session.add(existing)
            self.db_session.commit()

            original_get_by_email = UserRepository.get_by_email
            call_count = {"n": 0}

            def flaky_get_by_email(self, email):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    # The pre-check misses the row that "just" committed.
                    return None
                return original_get_by_email(self, email)

            with patch.object(UserRepository, "get_by_email", flaky_get_by_email):
                response = self._register("racecondition@example.com", "password123", "PAYER")

            self.assertEqual(response.status_code, 400)
            self.assertEqual(
                response.get_json(), {"error": "An account with this email already exists."}
            )

            # Exactly one row for this email must exist: no duplicate was
            # created, and the failed insert did not corrupt the original.
            count = (
                self.db_session.query(User)
                .filter_by(email="racecondition@example.com")
                .count()
            )
            self.assertEqual(count, 1)

    def test_invalid_role_rejected(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            response = self._register("baduser@example.com", "password123", "ADMIN")
            self.assertEqual(response.status_code, 400)
            self.assertIn("error", response.get_json())

    def test_email_normalization(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            response = self._register("  User@Example.COM  ", "password123", "PATIENT")
            self.assertEqual(response.status_code, 201)
            data = response.get_json()
            self.assertEqual(data["email"], "user@example.com")

    def test_password_is_hashed_and_never_stored_as_plaintext(self) -> None:
        plaintext = "correct-horse-battery-staple"
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            response = self._register("hashcheck@example.com", plaintext, "PATIENT")
            self.assertEqual(response.status_code, 201)

        stored = self.db_session.query(User).filter_by(email="hashcheck@example.com").first()
        self.assertIsNotNone(stored)
        self.assertNotEqual(stored.password_hash, plaintext)
        self.assertNotIn(plaintext, stored.password_hash)
        self.assertGreater(len(stored.password_hash), 20)

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def test_successful_login(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._register("loginok@example.com", "password123", "PAYER")
            response = self._login("loginok@example.com", "password123")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["email"], "loginok@example.com")
            self.assertEqual(data["role"], "PAYER")
            self.assertNotIn("password_hash", data)

    def test_wrong_password_returns_generic_401(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._register("wrongpass@example.com", "password123", "PATIENT")
            response = self._login("wrongpass@example.com", "not-the-password")
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.get_json(), {"error": "Invalid email or password."})

    def test_nonexistent_email_returns_same_generic_401(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._register("existing@example.com", "password123", "PATIENT")
            wrong_password_response = self._login("existing@example.com", "wrong-password")
            nonexistent_response = self._login("nobody@example.com", "whatever-password")

            self.assertEqual(nonexistent_response.status_code, 401)
            # Identical status and body prove the endpoint does not leak
            # whether an email address is registered.
            self.assertEqual(nonexistent_response.status_code, wrong_password_response.status_code)
            self.assertEqual(nonexistent_response.get_json(), wrong_password_response.get_json())

    def test_login_with_empty_body_returns_generic_401(self) -> None:
        """A malformed/empty request must never surface as an unhandled 500."""
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            response = self.client.post("/api/auth/login", json={})
            self.assertEqual(response.status_code, 401)
            data = response.get_json()
            self.assertEqual(data, {"error": "Invalid email or password."})
            self.assertNotIn("password_hash", data)

    def test_login_with_missing_email_returns_generic_401(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            response = self.client.post("/api/auth/login", json={"password": "password123"})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.get_json(), {"error": "Invalid email or password."})

    def test_login_with_malformed_email_returns_generic_401(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            response = self.client.post(
                "/api/auth/login", json={"email": "not-an-email", "password": "password123"}
            )
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.get_json(), {"error": "Invalid email or password."})

    def test_login_with_missing_password_returns_generic_401(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            response = self.client.post(
                "/api/auth/login", json={"email": "someone@example.com"}
            )
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.get_json(), {"error": "Invalid email or password."})

    # ------------------------------------------------------------------
    # Session identity (/me) and logout
    # ------------------------------------------------------------------

    def test_me_without_session_returns_401(self) -> None:
        response = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 401)

    def test_me_with_valid_session_returns_safe_user_info(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._register("meuser@example.com", "password123", "PAYER")
            login_response = self._login("meuser@example.com", "password123")
            self.assertEqual(login_response.status_code, 200)

            me_response = self.client.get("/api/auth/me")
            self.assertEqual(me_response.status_code, 200)
            data = me_response.get_json()
            self.assertEqual(data["email"], "meuser@example.com")
            self.assertEqual(data["role"], "PAYER")
            self.assertNotIn("password_hash", data)

    def test_logout_clears_session(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._register("logoutuser@example.com", "password123", "PATIENT")
            self._login("logoutuser@example.com", "password123")

            pre_logout = self.client.get("/api/auth/me")
            self.assertEqual(pre_logout.status_code, 200)

            logout_response = self.client.post("/api/auth/logout")
            self.assertEqual(logout_response.status_code, 200)

            post_logout = self.client.get("/api/auth/me")
            self.assertEqual(post_logout.status_code, 401)

    # ------------------------------------------------------------------
    # Decorators, tested in isolation via dedicated probe routes
    # ------------------------------------------------------------------

    def test_login_required_rejects_unauthenticated(self) -> None:
        response = self.client.get("/__test/login-required")
        self.assertEqual(response.status_code, 401)

    def test_login_required_permits_authenticated(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._register("decoratoruser@example.com", "password123", "PATIENT")
            self._login("decoratoruser@example.com", "password123")
            response = self.client.get("/__test/login-required")
            self.assertEqual(response.status_code, 200)

    def test_role_required_payer_permits_payer(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._register("payeruser@example.com", "password123", "PAYER")
            self._login("payeruser@example.com", "password123")
            response = self.client.get("/__test/payer-required")
            self.assertEqual(response.status_code, 200)

    def test_role_required_payer_rejects_patient_with_403(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._register("patientuser@example.com", "password123", "PATIENT")
            self._login("patientuser@example.com", "password123")
            response = self.client.get("/__test/payer-required")
            self.assertEqual(response.status_code, 403)

    def test_role_required_patient_permits_patient(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._register("patientuser2@example.com", "password123", "PATIENT")
            self._login("patientuser2@example.com", "password123")
            response = self.client.get("/__test/patient-required")
            self.assertEqual(response.status_code, 200)

    # ------------------------------------------------------------------
    # Cross-cutting security requirements
    # ------------------------------------------------------------------

    def test_sensitive_fields_never_returned(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            register_response = self._register("sensitive@example.com", "password123", "PATIENT")
            login_response = self._login("sensitive@example.com", "password123")
            me_response = self.client.get("/api/auth/me")

            for response in (register_response, login_response, me_response):
                data = response.get_json()
                self.assertNotIn("password", data)
                self.assertNotIn("password_hash", data)
                self.assertNotIn("session", data)

    def test_session_cookie_is_httponly(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._register("cookie@example.com", "password123", "PATIENT")
            response = self._login("cookie@example.com", "password123")
            self.assertEqual(response.status_code, 200)
            set_cookie_headers = response.headers.get_all("Set-Cookie")
            self.assertTrue(
                any("HttpOnly" in header for header in set_cookie_headers),
                "Session cookie must be marked HttpOnly.",
            )

    def test_authentication_does_not_alter_safety_engine_behavior(self) -> None:
        """Regression guard: the same emergency triage input must produce the
        identical safety-engine decision whether the caller is unauthenticated
        or holds a valid authenticated session."""
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            emergency_payload = {
                "chiefComplaint": "Crushing chest pain and severe shortness of breath",
                "symptomsDuration": "10 minutes",
                "hasRedFlags": True,
            }

            unauth_response = self.client.post("/api/triage", json=emergency_payload)
            self.assertEqual(unauth_response.status_code, 200)
            unauth_data = unauth_response.get_json()

            self._register("safetycheck@example.com", "password123", "PATIENT")
            login_response = self._login("safetycheck@example.com", "password123")
            self.assertEqual(login_response.status_code, 200)

            auth_response = self.client.post("/api/triage", json=emergency_payload)
            self.assertEqual(auth_response.status_code, 200)
            auth_data = auth_response.get_json()

            self.assertTrue(auth_data["isEmergencyRedFlag"])
            self.assertEqual(auth_data["recommendedAcuity"], "EMERGENCY")
            self.assertEqual(unauth_data["isEmergencyRedFlag"], auth_data["isEmergencyRedFlag"])
            self.assertEqual(unauth_data["recommendedAcuity"], auth_data["recommendedAcuity"])
            self.assertEqual(unauth_data["urgencyLevel"], auth_data["urgencyLevel"])
            self.assertEqual(unauth_data["triggeredRules"], auth_data["triggeredRules"])


if __name__ == "__main__":
    unittest.main()
