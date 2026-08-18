"""Regression tests for account-level (user_id) persistence of triage
encounters and navigation actions, and the GET /api/navigation/my-history
endpoint.

This is deliberately independent of, and does not exercise or alter, the
CMS-Member-based PAYER-only linkage tested in test_authorization.py and
test_navigation_history.py: user_id is identity/persistence linkage only
(which login identity created a record), never clinical/ML enrichment.
"""

from __future__ import annotations

from contextlib import contextmanager
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app import create_app
from backend.database import Base
from backend.models import Member, TriageEncounter
from backend.services.auth_service import register_user


class AccountHistoryTestCase(unittest.TestCase):
    """Test suite for user_id-linked persistence and /api/navigation/my-history."""

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

    def _register_and_login_patient(self, email: str) -> None:
        """Register+login a PATIENT via the real public registration flow.
        Caller must be inside a patch of auth_service.session_scope."""
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
        """Provision+login a PAYER user directly via the service layer, since
        POST /api/auth/register only ever creates a PATIENT account. Caller
        must be inside a patch of auth_service.session_scope."""
        register_user(email=email, password="password123", role="PAYER", session=self.db_session)
        response = self.client.post(
            "/api/auth/login", json={"email": email, "password": "password123"}
        )
        self.assertEqual(response.status_code, 200, response.get_json())

    def _seed_member(self) -> None:
        self.db_session.add(Member(
            id=1001,
            bene_id="CMS-ACCT-1001",
            age=68,
            gender="Male",
            dual_eligibility_months=0,
            chronic_condition_count=2,
        ))
        self.db_session.commit()

    # ------------------------------------------------------------------
    # 1. Authenticated PATIENT triage creates a persistent encounter
    #    associated with that user's user_id.
    # ------------------------------------------------------------------

    def test_authenticated_patient_triage_persists_with_user_id(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope), \
             patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._register_and_login_patient("patient-persist@example.com")

            response = self.client.post(
                "/api/triage", json={"chiefComplaint": "Mild persistent cough"}
            )
            self.assertEqual(response.status_code, 200)
            data = response.get_json()

            enc = self.db_session.query(TriageEncounter).filter_by(id=data["encounterId"]).first()
            self.assertIsNotNone(enc)
            self.assertIsNotNone(enc.user_id)
            # No CMS Member linkage for a PATIENT caller -- unaffected by user_id.
            self.assertIsNone(enc.member_id)

    # ------------------------------------------------------------------
    # 2. Anonymous triage still works (user_id stays None; nothing required).
    # ------------------------------------------------------------------

    def test_anonymous_triage_still_works_with_no_user_id(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope):
            response = self.client.post(
                "/api/triage", json={"chiefComplaint": "Mild seasonal allergies"}
            )
            self.assertEqual(response.status_code, 200)
            data = response.get_json()

            enc = self.db_session.query(TriageEncounter).filter_by(id=data["encounterId"]).first()
            self.assertIsNotNone(enc)
            self.assertIsNone(enc.user_id)
            self.assertIsNone(enc.member_id)

    # ------------------------------------------------------------------
    # 3. Authenticated PAYER triage still gets its existing PAYER-only
    #    member/ML enrichment behavior, and also gets user_id stamped.
    # ------------------------------------------------------------------

    def test_payer_triage_keeps_member_linkage_and_also_gets_user_id(self) -> None:
        self._seed_member()
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope), \
             patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._login_as_payer("payer-persist@example.com")

            response = self.client.post("/api/triage", json={
                "patientId": "1001",
                "chiefComplaint": "Medication review follow-up",
            })
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            # Existing PAYER-only member/ML enrichment is unchanged.
            self.assertEqual(data["patientContext"]["beneficiaryId"], "CMS-ACCT-1001")

            enc = self.db_session.query(TriageEncounter).filter_by(id=data["encounterId"]).first()
            self.assertIsNotNone(enc)
            self.assertEqual(enc.member_id, 1001)
            # user_id linkage is orthogonal/additive, not a replacement.
            self.assertIsNotNone(enc.user_id)

    # ------------------------------------------------------------------
    # 4. GET /api/navigation/my-history returns the authenticated user's
    #    own history, most-recent-first, actions grouped by encounter.
    # ------------------------------------------------------------------

    def test_my_history_returns_own_encounters_and_actions(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope), \
             patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.navigation.session_scope", self._mock_session_scope):
            self._register_and_login_patient("patient-myhistory@example.com")

            first = self.client.post("/api/triage", json={"chiefComplaint": "Mild headache"}).get_json()
            second = self.client.post("/api/triage", json={"chiefComplaint": "Sore throat"}).get_json()

            self.client.post("/api/navigation/action", json={
                "encounterId": second["encounterId"],
                "actionType": "APPOINTMENT_BOOKED",
                "selectedAcuity": "TELEHEALTH",
            })

            response = self.client.get("/api/navigation/my-history")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()

            self.assertEqual(data["totalEncounters"], 2)
            self.assertEqual(data["totalActions"], 1)
            # Most recent (second) encounter first.
            self.assertEqual(data["encounters"][0]["encounterId"], second["encounterId"])
            self.assertEqual(data["encounters"][1]["encounterId"], first["encounterId"])
            # The action stays associated with its own encounter.
            self.assertEqual(data["actions"][0]["encounterId"], second["encounterId"])

    def test_my_history_rejects_unauthenticated_caller(self) -> None:
        response = self.client.get("/api/navigation/my-history")
        self.assertEqual(response.status_code, 401)

    # ------------------------------------------------------------------
    # 5. One authenticated user cannot retrieve another user's history.
    #    There is no user_id request parameter at all -- identity comes
    #    solely from the session.
    # ------------------------------------------------------------------

    def test_my_history_never_returns_another_users_data(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope), \
             patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.navigation.session_scope", self._mock_session_scope):
            self._register_and_login_patient("patient-a@example.com")
            enc_a = self.client.post(
                "/api/triage", json={"chiefComplaint": "Patient A complaint"}
            ).get_json()

            logout = self.client.post("/api/auth/logout")
            self.assertEqual(logout.status_code, 200)

            self._register_and_login_patient("patient-b@example.com")
            enc_b = self.client.post(
                "/api/triage", json={"chiefComplaint": "Patient B complaint"}
            ).get_json()

            # A client-supplied user_id/patientId is not a recognized
            # parameter for this endpoint; identity comes only from the
            # session cookie, so this cannot be used to read A's data.
            response = self.client.get(
                "/api/navigation/my-history?userId=1&patientId=1"
            )
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["totalEncounters"], 1)
            self.assertEqual(data["encounters"][0]["encounterId"], enc_b["encounterId"])
            returned_ids = {enc["encounterId"] for enc in data["encounters"]}
            self.assertNotIn(enc_a["encounterId"], returned_ids)

    # ------------------------------------------------------------------
    # 6. History survives a fresh request/browser-session lifecycle
    #    because it is retrieved from PostgreSQL, not frontend memory.
    #    Modeled here as two independent HTTP calls through the same
    #    authenticated (cookie-persisted) client, with nothing carried
    #    over except the session cookie itself -- the equivalent of a
    #    browser refresh, which also only keeps the session cookie.
    # ------------------------------------------------------------------

    def test_history_is_retrievable_across_independent_requests(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope), \
             patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._register_and_login_patient("patient-refresh@example.com")
            created = self.client.post(
                "/api/triage", json={"chiefComplaint": "Twisted ankle"}
            ).get_json()

        # A completely separate call, as if the page had been refreshed:
        # only the session cookie persists (handled by the test client,
        # mirroring a browser), no Python variable references the
        # encounter created above beyond its id for the assertion.
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.navigation.session_scope", self._mock_session_scope):
            response = self.client.get("/api/navigation/my-history")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["totalEncounters"], 1)
            self.assertEqual(data["encounters"][0]["encounterId"], created["encounterId"])

    # ------------------------------------------------------------------
    # 7. Navigation actions can be associated with the authenticated user.
    # ------------------------------------------------------------------

    def test_navigation_action_is_associated_with_authenticated_user(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope), \
             patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.navigation.session_scope", self._mock_session_scope):
            self._register_and_login_patient("patient-action@example.com")
            enc = self.client.post(
                "/api/triage", json={"chiefComplaint": "Minor burn"}
            ).get_json()

            response = self.client.post("/api/navigation/action", json={
                "encounterId": enc["encounterId"],
                "actionType": "PROVIDER_SELECTED",
                "selectedAcuity": "URGENT_CARE",
            })
            self.assertEqual(response.status_code, 200)
            action_id = response.get_json()["actionId"]

        from backend.models import NavigationAction
        action = self.db_session.query(NavigationAction).filter_by(id=action_id).first()
        self.assertIsNotNone(action)
        self.assertIsNotNone(action.user_id)

    def test_anonymous_navigation_action_still_works_with_no_user_id(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.navigation.session_scope", self._mock_session_scope):
            triage = self.client.post(
                "/api/triage", json={"chiefComplaint": "Mild rash"}
            ).get_json()

            response = self.client.post("/api/navigation/action", json={
                "sessionId": triage["sessionId"],
                "actionType": "PROVIDER_SELECTED",
                "selectedAcuity": "TELEHEALTH",
            })
            self.assertEqual(response.status_code, 200)
            action_id = response.get_json()["actionId"]

        from backend.models import NavigationAction
        action = self.db_session.query(NavigationAction).filter_by(id=action_id).first()
        self.assertIsNotNone(action)
        self.assertIsNone(action.user_id)

    # ------------------------------------------------------------------
    # 8. Existing session-history endpoint still works, unaffected by
    #    the new user_id column/endpoint.
    # ------------------------------------------------------------------

    def test_existing_session_history_endpoint_still_works(self) -> None:
        with patch("backend.services.triage_service.session_scope", self._mock_session_scope), \
             patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.navigation.session_scope", self._mock_session_scope):
            self._register_and_login_patient("patient-session@example.com")
            triage = self.client.post(
                "/api/triage", json={"chiefComplaint": "Mild dizziness"}
            ).get_json()

            response = self.client.get(f"/api/navigation/session/{triage['sessionId']}")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data["sessionId"], triage["sessionId"])
            self.assertEqual(data["encounter"]["encounterId"], triage["encounterId"])


if __name__ == "__main__":
    unittest.main()
