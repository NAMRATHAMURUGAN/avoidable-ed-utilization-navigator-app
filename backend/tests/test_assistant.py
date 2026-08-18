"""Tests for the RightPath AI assistant layer:

- backend/services/assistant_service.py (GeminiAssistant, generation only)
- backend/routes/assistant.py (POST /api/patient/assistant, POST /api/payer/assistant)

Every test mocks Gemini/Pinecone -- nothing here makes a real network call.
See backend/scripts/test_ai_smoke.py for the separate, opt-in, real-API
smoke test (never run automatically by pytest).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.app import create_app
from backend.database import Base
from backend.models import NavigationAction, TriageEncounter
from backend.services.assistant_service import AssistantGenerationError
from backend.services.auth_service import register_user


def _encounter(
    *,
    user_id: int | None,
    is_emergency: bool,
    recommended_acuity: str,
    session_id: str = "s",
) -> TriageEncounter:
    return TriageEncounter(
        user_id=user_id,
        session_id=session_id,
        chief_complaint="test complaint text -- never sent to Gemini",
        symptoms_duration="",
        has_red_flags=is_emergency,
        is_emergency=is_emergency,
        recommended_acuity=recommended_acuity,
        urgency_level="EMERGENCY" if is_emergency else "ROUTINE",
        recommended_setting_name=recommended_acuity.replace("_", " ").title(),
        clinical_rationale="Test clinical rationale text.",
        safety_disclaimer="Test safety disclaimer.",
        rule_set_version="1.0.0",
        created_at=datetime.now(timezone.utc),
    )


class _FakeGeminiAssistant:
    """Drop-in stand-in for GeminiAssistant -- never touches the network."""

    def __init__(self, *, api_key: str, model: str) -> None:  # noqa: D401 - mirror real signature
        self.api_key = api_key
        self.model = model

    def generate(self, *, system_instruction: str, user_content: str) -> str:
        _FakeGeminiAssistant.last_system_instruction = system_instruction
        _FakeGeminiAssistant.last_user_content = user_content
        return "This is a mocked RightPath AI reply."


class _FailingGeminiAssistant:
    def __init__(self, *, api_key: str, model: str) -> None:
        pass

    def generate(self, *, system_instruction: str, user_content: str) -> str:
        raise AssistantGenerationError("Gemini text generation failed.")


class _FakeRetrievedChunk:
    def __init__(self, *, text: str, title: str, category: str, source_file: str) -> None:
        self.text = text
        self.title = title
        self.category = category
        self.source_file = source_file


class _FakeKnowledgeRetriever:
    """Returns two canned chunks -- never calls Gemini embeddings or Pinecone."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def retrieve(self, query: str, *, top_k: int = 5):
        return [
            _FakeRetrievedChunk(
                text="Medicare covers urgent care visits under Part B.",
                title="Medicare Emergency & Urgent Care",
                category="patient_education",
                source_file="patient_education/medicare_emergency_urgent_care.md",
            ),
        ][:top_k]


class _FailingKnowledgeRetriever:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def retrieve(self, query: str, *, top_k: int = 5):
        raise RuntimeError("Pinecone query failed.")


class AssistantRouteTestCase(unittest.TestCase):
    """HTTP-level tests via the Flask test client against an isolated
    in-memory SQLite database -- never the live PostgreSQL database, and
    never a real Gemini/Pinecone call."""

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

    def _login(self, email: str, role: str) -> None:
        register_user(email=email, password="password123", role=role, session=self.session)
        response = self.client.post("/api/auth/login", json={"email": email, "password": "password123"})
        self.assertEqual(response.status_code, 200, response.get_json())

    # ------------------------------------------------------------------
    # PATIENT assistant
    # ------------------------------------------------------------------

    def test_patient_non_emergency_gets_assistant_response(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.get_assistant_settings", return_value=type(
                 "S", (), {"gemini_api_key": "fake", "generation_model": "fake-model"}
             )()), \
             patch("backend.routes.assistant.GeminiAssistant", _FakeGeminiAssistant):
            self._login("assistant-patient-a@example.com", "PATIENT")
            self.session.add(_encounter(user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH", session_id="a"))
            self.session.commit()

            response = self.client.post("/api/patient/assistant", json={
                "question": "What should I do next?",
                "encounterId": 1,
                "triageContext": {
                    "recommendedAcuity": "TELEHEALTH",
                    "recommendedSettingName": "Telehealth",
                    "clinicalRationale": "Test clinical rationale text.",
                    "isEmergencyRedFlag": False,
                },
            })
            self.assertEqual(response.status_code, 200, response.get_json())
            body = response.get_json()
            self.assertEqual(body["reply"], "This is a mocked RightPath AI reply.")
            # The raw chief_complaint must never reach the Gemini prompt.
            self.assertNotIn("test complaint text", _FakeGeminiAssistant.last_user_content)
            self.assertIn("TELEHEALTH", _FakeGeminiAssistant.last_user_content)

    def test_emergency_encounter_never_calls_gemini(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant") as mock_assistant_cls:
            self._login("assistant-patient-b@example.com", "PATIENT")
            self.session.add(_encounter(user_id=1, is_emergency=True, recommended_acuity="EMERGENCY", session_id="b"))
            self.session.commit()

            response = self.client.post("/api/patient/assistant", json={
                "question": "Am I really having an emergency?",
                "encounterId": 1,
                "triageContext": {"isEmergencyRedFlag": False},  # even if the client lies, the DB wins
            })
            self.assertEqual(response.status_code, 200)
            body = response.get_json()
            self.assertEqual(body["emergency"], True)
            self.assertIn("error", body)
            mock_assistant_cls.assert_not_called()

    def test_client_supplied_emergency_flag_also_blocks_even_if_db_says_non_emergency(self) -> None:
        """Fail-safe OR: either signal indicating emergency blocks the assistant."""
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant") as mock_assistant_cls:
            self._login("assistant-patient-c@example.com", "PATIENT")
            self.session.add(_encounter(user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH", session_id="c"))
            self.session.commit()

            response = self.client.post("/api/patient/assistant", json={
                "question": "Help",
                "encounterId": 1,
                "triageContext": {"isEmergencyRedFlag": True},
            })
            self.assertEqual(response.get_json()["emergency"], True)
            mock_assistant_cls.assert_not_called()

    def test_anonymous_cannot_access_patient_assistant(self) -> None:
        response = self.client.post("/api/patient/assistant", json={"question": "hi", "encounterId": 1})
        self.assertEqual(response.status_code, 401)

    def test_payer_role_cannot_access_patient_assistant(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._login("assistant-payer-for-patient-check@example.com", "PAYER")
            response = self.client.post("/api/patient/assistant", json={"question": "hi", "encounterId": 1})
            self.assertEqual(response.status_code, 403)

    def test_patient_cannot_access_another_patients_encounter(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant") as mock_assistant_cls:
            register_user(email="owner@example.com", password="password123", role="PATIENT", session=self.session)
            self.session.add(_encounter(user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH", session_id="owner-enc"))
            self.session.commit()

            self._login("assistant-intruder@example.com", "PATIENT")
            response = self.client.post("/api/patient/assistant", json={
                "question": "hi", "encounterId": 1, "triageContext": {"isEmergencyRedFlag": False},
            })
            self.assertEqual(response.status_code, 404)
            mock_assistant_cls.assert_not_called()

    def test_missing_question_is_rejected(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._login("assistant-patient-d@example.com", "PATIENT")
            response = self.client.post("/api/patient/assistant", json={"encounterId": 1})
            self.assertEqual(response.status_code, 400)

    def test_gemini_failure_returns_controlled_error_not_fabricated_reply(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant", _FailingGeminiAssistant):
            self._login("assistant-patient-e@example.com", "PATIENT")
            self.session.add(_encounter(user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH", session_id="e"))
            self.session.commit()

            response = self.client.post("/api/patient/assistant", json={
                "question": "hi", "encounterId": 1, "triageContext": {"isEmergencyRedFlag": False},
            })
            self.assertEqual(response.status_code, 503)
            body = response.get_json()
            self.assertIn("error", body)
            self.assertNotIn("reply", body)

    # ------------------------------------------------------------------
    # PAYER assistant
    # ------------------------------------------------------------------

    def test_knowledge_question_still_uses_rag_and_returns_sources(self) -> None:
        """A genuine knowledge/guidance question (classified 'knowledge') keeps
        the existing RAG flow: KnowledgeRetriever -> Gemini, with sources."""
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.analytics_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant", _FakeGeminiAssistant), \
             patch("backend.routes.assistant.KnowledgeRetriever", _FakeKnowledgeRetriever):
            self._login("assistant-payer-a@example.com", "PAYER")
            response = self.client.post("/api/payer/assistant", json={"question": "What does EMTALA require?"})
            self.assertEqual(response.status_code, 200, response.get_json())
            body = response.get_json()
            self.assertEqual(body["reply"], "This is a mocked RightPath AI reply.")
            # Sources can only be present if KnowledgeRetriever was actually used.
            self.assertEqual(len(body["sources"]), 1)
            self.assertEqual(body["sources"][0]["title"], "Medicare Emergency & Urgent Care")
            self.assertEqual(body["sources"][0]["category"], "patient_education")
            # No embeddings/internal ids/secrets in the source payload.
            self.assertNotIn("vector_id", body["sources"][0])
            self.assertNotIn("score", body["sources"][0])

    def test_payer_context_is_aggregate_only_and_never_raw_patient_data(self) -> None:
        """A population-analytics question this router doesn't recognize as a
        single deterministic field ('risk distribution' is a dict, not a
        scalar) still skips RAG but falls through to a narrow Gemini call --
        exercising the same privacy-relevant prompt-construction path."""
        captured_prompt = {}

        class _CapturingAssistant(_FakeGeminiAssistant):
            def generate(self, *, system_instruction: str, user_content: str) -> str:
                captured_prompt["prompt"] = user_content
                captured_prompt["system"] = system_instruction
                return "ok"

        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.analytics_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant", _CapturingAssistant), \
             patch("backend.routes.assistant.KnowledgeRetriever") as mock_retriever_cls:
            self._login("assistant-payer-b@example.com", "PAYER")
            response = self.client.post("/api/payer/assistant", json={"question": "What is the risk distribution?"})
            self.assertEqual(response.status_code, 200, response.get_json())

        # This is an analytics question -- RAG must never be invoked at all.
        mock_retriever_cls.assert_not_called()

        prompt = captured_prompt["prompt"]
        for forbidden in ("chief_complaint", "action_details", "assistant-payer-b@example.com", "BENE_ID", "bene_id"):
            self.assertNotIn(forbidden, prompt)
        # Aggregate fields ARE present.
        self.assertIn("totalMembers", prompt)
        self.assertIn("potentialEdCostOpportunity", prompt)
        self.assertIn("Do not claim that a navigation action proves", captured_prompt["system"])

    # ------------------------------------------------------------------
    # PAYER assistant -- deterministic analytics routing (this phase)
    # ------------------------------------------------------------------

    def test_pathway_question_is_answered_deterministically_without_rag_or_gemini(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.analytics_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant") as mock_assistant_cls, \
             patch("backend.routes.assistant.KnowledgeRetriever") as mock_retriever_cls:
            self._login("assistant-payer-f@example.com", "PAYER")
            self.session.add(_encounter(user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH", session_id="pf1"))
            self.session.commit()
            self.session.add(NavigationAction(
                user_id=1, encounter_id=1, action_type="APPOINTMENT_BOOKED", selected_acuity="TELEHEALTH",
                recorded_at=datetime.now(timezone.utc),
            ))
            self.session.commit()

            response = self.client.post("/api/payer/assistant", json={"question": "Which care pathway is most used?"})
            self.assertEqual(response.status_code, 200, response.get_json())
            body = response.get_json()
            self.assertIn("Most Used Pathway", body["reply"])
            self.assertIn("Telehealth", body["reply"])
            self.assertNotIn("sources", body)
            mock_retriever_cls.assert_not_called()
            mock_assistant_cls.assert_not_called()

    def test_summarize_question_is_answered_deterministically_without_rag_or_gemini(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.analytics_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant") as mock_assistant_cls, \
             patch("backend.routes.assistant.KnowledgeRetriever") as mock_retriever_cls:
            self._login("assistant-payer-g@example.com", "PAYER")
            response = self.client.post("/api/payer/assistant", json={"question": "Summarize current RightPath activity"})
            self.assertEqual(response.status_code, 200, response.get_json())
            body = response.get_json()
            self.assertIn("RightPath Activity", body["reply"])
            self.assertIn("Note: This is a modeled opportunity", body["reply"])
            mock_retriever_cls.assert_not_called()
            mock_assistant_cls.assert_not_called()

    def test_field_lookup_question_reflects_real_seeded_values_no_fabrication(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.analytics_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant") as mock_assistant_cls, \
             patch("backend.routes.assistant.KnowledgeRetriever") as mock_retriever_cls:
            self._login("assistant-payer-h@example.com", "PAYER")
            self.session.add_all([
                _encounter(user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH", session_id="h1"),
                _encounter(user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH", session_id="h2"),
                _encounter(user_id=1, is_emergency=True, recommended_acuity="EMERGENCY", session_id="h3"),
            ])
            self.session.commit()

            response = self.client.post("/api/payer/assistant", json={"question": "How many RightPath assessments are there?"})
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(response.get_json()["reply"], "Total RightPath Assessments: 3")
            mock_retriever_cls.assert_not_called()
            mock_assistant_cls.assert_not_called()

    def test_missing_rag_configuration_does_not_break_analytics_question(self) -> None:
        """Simulates a broken/missing Pinecone config (KnowledgeRetriever()
        raising on construction) -- an analytics question must still succeed
        because it never constructs KnowledgeRetriever in the first place."""
        class _BrokenKnowledgeRetriever:
            def __init__(self, *args, **kwargs) -> None:
                raise RuntimeError("RAG ingestion requires these environment variables: PINECONE_API_KEY.")

        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.analytics_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.KnowledgeRetriever", _BrokenKnowledgeRetriever):
            self._login("assistant-payer-i@example.com", "PAYER")
            response = self.client.post("/api/payer/assistant", json={"question": "Which care pathway is most used?"})
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertIn("Most Used Pathway", response.get_json()["reply"])

    def test_narrow_analytics_gemini_failure_returns_controlled_error(self) -> None:
        """An analytics question this router can't format deterministically
        still uses the existing controlled-error behavior if Gemini fails."""
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.analytics_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant", _FailingGeminiAssistant), \
             patch("backend.routes.assistant.KnowledgeRetriever") as mock_retriever_cls:
            self._login("assistant-payer-j@example.com", "PAYER")
            response = self.client.post("/api/payer/assistant", json={"question": "What is the utilization distribution?"})
            self.assertEqual(response.status_code, 503)
            body = response.get_json()
            self.assertIn("error", body)
            self.assertNotIn("reply", body)
            mock_retriever_cls.assert_not_called()

    def test_classify_question_matches_every_documented_example(self) -> None:
        from backend.routes.assistant import _classify_question

        rightpath_examples = (
            "Summarize current RightPath activity",
            "How many RightPath assessments are there?",
            "Which care pathway is most used?",
            "How many telehealth actions?",
            "How many primary care navigations?",
            "How many urgent care actions?",
            "How many non-ED navigation actions?",
            "What is the current utilization opportunity?",
            "What is the potential ED cost opportunity?",
        )
        population_examples = (
            "How many members are in the population?",
            "How many ED visits are recorded?",
            "What is the total ED spend?",
            "How many high-utilization members are there?",
            "How many anomalous members are there?",
            "What is the utilization distribution?",
            "What is the risk distribution?",
        )
        knowledge_examples = (
            "What does EMTALA require?",
            "What are the emergency-care rights?",
            "What does care coordination mean?",
            "What guidance applies to Medicare service navigation?",
        )
        for question in rightpath_examples:
            with self.subTest(question=question):
                self.assertEqual(_classify_question(question), "rightpath")
        for question in population_examples:
            with self.subTest(question=question):
                self.assertEqual(_classify_question(question), "population")
        for question in knowledge_examples:
            with self.subTest(question=question):
                self.assertEqual(_classify_question(question), "knowledge")

    def test_patient_cannot_access_payer_assistant(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._login("assistant-patient-for-payer-check@example.com", "PATIENT")
            response = self.client.post("/api/payer/assistant", json={"question": "hi"})
            self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_access_payer_assistant(self) -> None:
        response = self.client.post("/api/payer/assistant", json={"question": "hi"})
        self.assertEqual(response.status_code, 401)

    def test_rag_retrieval_failure_still_answers_using_analytics_only(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.analytics_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant", _FakeGeminiAssistant), \
             patch("backend.routes.assistant.KnowledgeRetriever", _FailingKnowledgeRetriever):
            self._login("assistant-payer-c@example.com", "PAYER")
            response = self.client.post("/api/payer/assistant", json={"question": "Summarize activity"})
            self.assertEqual(response.status_code, 200, response.get_json())
            body = response.get_json()
            self.assertEqual(body["reply"], "This is a mocked RightPath AI reply.")
            self.assertNotIn("sources", body)  # no fabricated retrieved knowledge

    def test_payer_gemini_failure_returns_controlled_error(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.analytics_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant", _FailingGeminiAssistant), \
             patch("backend.routes.assistant.KnowledgeRetriever", _FakeKnowledgeRetriever):
            self._login("assistant-payer-d@example.com", "PAYER")
            response = self.client.post("/api/payer/assistant", json={"question": "hi"})
            self.assertEqual(response.status_code, 503)
            body = response.get_json()
            self.assertIn("error", body)
            self.assertNotIn("reply", body)

    def test_payer_missing_question_is_rejected(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._login("assistant-payer-e@example.com", "PAYER")
            response = self.client.post("/api/payer/assistant", json={})
            self.assertEqual(response.status_code, 400)


class AssistantServiceUnitTests(unittest.TestCase):
    """Direct unit tests for backend/services/assistant_service.py, mocking
    google.genai so no network call is ever made."""

    def test_generate_rejects_blank_user_content(self) -> None:
        from backend.services.assistant_service import AssistantGenerationError, GeminiAssistant

        assistant = GeminiAssistant(api_key="fake", model="fake-model")
        with self.assertRaises(AssistantGenerationError):
            assistant.generate(system_instruction="You are helpful.", user_content="   ")

    def test_generate_returns_text_on_success(self) -> None:
        from backend.services.assistant_service import GeminiAssistant

        fake_response = type("Resp", (), {"text": "  A generated reply.  "})()
        fake_client = type("Client", (), {
            "models": type("Models", (), {
                "generate_content": lambda self, **kwargs: fake_response,
            })(),
        })()

        with patch("google.genai.Client", return_value=fake_client):
            assistant = GeminiAssistant(api_key="fake", model="fake-model")
            result = assistant.generate(system_instruction="You are helpful.", user_content="Hello")
        self.assertEqual(result, "A generated reply.")

    def test_generate_raises_on_empty_response_never_fabricates(self) -> None:
        from backend.services.assistant_service import AssistantGenerationError, GeminiAssistant

        fake_response = type("Resp", (), {"text": ""})()
        fake_client = type("Client", (), {
            "models": type("Models", (), {
                "generate_content": lambda self, **kwargs: fake_response,
            })(),
        })()

        with patch("google.genai.Client", return_value=fake_client):
            assistant = GeminiAssistant(api_key="fake", model="fake-model")
            with self.assertRaises(AssistantGenerationError):
                assistant.generate(system_instruction="You are helpful.", user_content="Hello")

    def test_generate_wraps_client_exception(self) -> None:
        from backend.services.assistant_service import AssistantGenerationError, GeminiAssistant

        def _raise(**kwargs):
            raise ConnectionError("network down")

        fake_client = type("Client", (), {
            "models": type("Models", (), {"generate_content": lambda self, **kwargs: _raise(**kwargs)})(),
        })()

        with patch("google.genai.Client", return_value=fake_client):
            assistant = GeminiAssistant(api_key="fake", model="fake-model")
            with self.assertRaises(AssistantGenerationError):
                assistant.generate(system_instruction="You are helpful.", user_content="Hello")


if __name__ == "__main__":
    unittest.main()
