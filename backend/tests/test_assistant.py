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

    def generate(self, *, system_instruction: str, user_content: str, max_output_tokens: int | None = None) -> str:
        _FakeGeminiAssistant.last_system_instruction = system_instruction
        _FakeGeminiAssistant.last_user_content = user_content
        _FakeGeminiAssistant.last_max_output_tokens = max_output_tokens
        return "This is a mocked RightPath AI reply."


class _FailingGeminiAssistant:
    def __init__(self, *, api_key: str, model: str) -> None:
        pass

    def generate(self, *, system_instruction: str, user_content: str, max_output_tokens: int | None = None) -> str:
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

    # ------------------------------------------------------------------
    # PATIENT assistant -- hybrid intent routing (this phase)
    # ------------------------------------------------------------------

    def test_pathway_explanation_questions_are_deterministic_and_skip_gemini(self) -> None:
        variations = (
            "Why was I recommended this care pathway?",
            "Why was I recommended telehealth?",
            "Why did you recommend telehealth for me?",
            "Why did RightPath recommend this?",
        )
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant") as mock_assistant_cls:
            self._login("assistant-patient-pathway@example.com", "PATIENT")
            for index, question in enumerate(variations):
                session_id = f"pathway-{index}"
                self.session.add(_encounter(
                    user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH", session_id=session_id,
                ))
                self.session.commit()
                encounter_id = self.session.query(TriageEncounter).filter_by(session_id=session_id).one().id
                with self.subTest(question=question):
                    response = self.client.post("/api/patient/assistant", json={
                        "question": question,
                        "encounterId": encounter_id,
                        "triageContext": {"isEmergencyRedFlag": False},
                    })
                    self.assertEqual(response.status_code, 200, response.get_json())
                    body = response.get_json()
                    self.assertIn("Why this pathway?", body["reply"])
                    self.assertNotIn("emergency", body)
            mock_assistant_cls.assert_not_called()

    def test_ignore_recommendation_questions_are_deterministic_safety_response(self) -> None:
        variations = ("Can I ignore the recommendation?", "Is it safe to ignore the recommendation?")
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant") as mock_assistant_cls:
            self._login("assistant-patient-ignore@example.com", "PATIENT")
            for index, question in enumerate(variations):
                session_id = f"ignore-{index}"
                self.session.add(_encounter(
                    user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH", session_id=session_id,
                ))
                self.session.commit()
                encounter_id = self.session.query(TriageEncounter).filter_by(session_id=session_id).one().id
                with self.subTest(question=question):
                    response = self.client.post("/api/patient/assistant", json={
                        "question": question,
                        "encounterId": encounter_id,
                        "triageContext": {"isEmergencyRedFlag": False},
                    })
                    self.assertEqual(response.status_code, 200, response.get_json())
                    body = response.get_json()
                    self.assertIn("can't advise you to ignore", body["reply"])
                    self.assertNotIn("safe to ignore", body["reply"].lower())
            mock_assistant_cls.assert_not_called()

    def test_open_ended_workflow_questions_still_use_gemini(self) -> None:
        variations = (
            "What should I ask the telehealth nurse?",
            "How can I prepare for my appointment?",
            "What is the difference between urgent care and primary care?",
        )
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant", _FakeGeminiAssistant):
            self._login("assistant-patient-openended@example.com", "PATIENT")
            for index, question in enumerate(variations):
                session_id = f"open-{index}"
                self.session.add(_encounter(
                    user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH", session_id=session_id,
                ))
                self.session.commit()
                encounter_id = self.session.query(TriageEncounter).filter_by(session_id=session_id).one().id
                with self.subTest(question=question):
                    response = self.client.post("/api/patient/assistant", json={
                        "question": question,
                        "encounterId": encounter_id,
                        "triageContext": {"isEmergencyRedFlag": False},
                    })
                    self.assertEqual(response.status_code, 200, response.get_json())
                    self.assertEqual(response.get_json()["reply"], "This is a mocked RightPath AI reply.")

    def test_gemini_open_ended_question_calls_gemini_exactly_once_when_available(self) -> None:
        """B: a genuinely open-ended question calls Gemini exactly once when
        it succeeds on the first attempt -- no deterministic shortcut, and
        no redundant/duplicate generation call."""
        call_count = {"n": 0}

        class _CountingAssistant(_FakeGeminiAssistant):
            def generate(self, *, system_instruction: str, user_content: str, max_output_tokens=None) -> str:
                call_count["n"] += 1
                return super().generate(
                    system_instruction=system_instruction, user_content=user_content,
                    max_output_tokens=max_output_tokens,
                )

        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant", _CountingAssistant):
            self._login("assistant-patient-callcount@example.com", "PATIENT")
            self.session.add(_encounter(user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH", session_id="callcount"))
            self.session.commit()
            response = self.client.post("/api/patient/assistant", json={
                "question": "What should I ask the telehealth nurse?",
                "encounterId": 1,
                "triageContext": {"isEmergencyRedFlag": False},
            })
            self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(call_count["n"], 1)

    def test_patient_forwards_reduced_output_token_cap_and_reply_is_not_truncated(self) -> None:
        """The reduced PATIENT_MAX_OUTPUT_TOKENS (800, down from a prior
        1024 -- see backend/routes/assistant.py for the real usage data
        behind that number) actually reaches GeminiAssistant.generate(),
        and a normal-length structured reply still comes back intact."""
        from backend.routes.assistant import PATIENT_MAX_OUTPUT_TOKENS

        captured = {}
        normal_reply = (
            "### Preparing for Your Telehealth Call\n\n"
            "Here are a few helpful questions to ask:\n\n"
            "* What symptom relief options are safe for me?\n"
            "* How long should recovery typically take?\n"
            "* What would mean I need in-person care?"
        )

        class _CapturingAssistant(_FakeGeminiAssistant):
            def generate(self, *, system_instruction: str, user_content: str, max_output_tokens=None) -> str:
                captured["max_output_tokens"] = max_output_tokens
                return normal_reply

        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant", _CapturingAssistant):
            self._login("assistant-patient-tokencap@example.com", "PATIENT")
            self.session.add(_encounter(user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH", session_id="tokencap"))
            self.session.commit()
            response = self.client.post("/api/patient/assistant", json={
                "question": "What should I ask the telehealth nurse?",
                "encounterId": 1,
                "triageContext": {"isEmergencyRedFlag": False},
            })
            self.assertEqual(response.status_code, 200, response.get_json())
            body = response.get_json()

        self.assertEqual(captured["max_output_tokens"], PATIENT_MAX_OUTPUT_TOKENS)
        self.assertEqual(body["reply"], normal_reply)  # returned intact, not truncated

    def test_unexpected_but_appropriate_question_still_reaches_gemini(self) -> None:
        """A reviewer asking something not on any hardcoded list must still
        get a real AI response, not a scripted refusal."""
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant", _FakeGeminiAssistant):
            self._login("assistant-patient-unexpected@example.com", "PATIENT")
            self.session.add(_encounter(user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH", session_id="unexpected"))
            self.session.commit()
            response = self.client.post("/api/patient/assistant", json={
                "question": "Will my telehealth visit show up on my insurance statement?",
                "encounterId": 1,
                "triageContext": {"isEmergencyRedFlag": False},
            })
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(response.get_json()["reply"], "This is a mocked RightPath AI reply.")

    def test_new_emergency_symptom_in_chat_blocks_assistant_even_if_encounter_is_non_emergency(self) -> None:
        variations = (
            "I suddenly have severe chest pain.",
            "I am having severe difficulty breathing.",
            "I think I'm having a stroke.",
            "I suddenly passed out.",
        )
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant") as mock_assistant_cls:
            self._login("assistant-patient-newemergency@example.com", "PATIENT")
            for index, question in enumerate(variations):
                session_id = f"newemergency-{index}"
                self.session.add(_encounter(
                    user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH", session_id=session_id,
                ))
                self.session.commit()
                encounter_id = self.session.query(TriageEncounter).filter_by(session_id=session_id).one().id
                with self.subTest(question=question):
                    response = self.client.post("/api/patient/assistant", json={
                        "question": question,
                        "encounterId": encounter_id,
                        "triageContext": {"isEmergencyRedFlag": False},
                    })
                    self.assertEqual(response.status_code, 200, response.get_json())
                    body = response.get_json()
                    self.assertEqual(body["emergency"], True)
                    self.assertIn("error", body)
            mock_assistant_cls.assert_not_called()

    def test_patient_assistant_cannot_access_payer_analytics(self) -> None:
        """The prompt built for a genuinely open-ended question must never
        contain payer-only aggregate analytics fields."""
        captured_prompt = {}

        class _CapturingAssistant(_FakeGeminiAssistant):
            def generate(self, *, system_instruction: str, user_content: str, max_output_tokens: int | None = None) -> str:
                captured_prompt["prompt"] = user_content
                return "ok"

        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant", _CapturingAssistant):
            self._login("assistant-patient-privacy@example.com", "PATIENT")
            self.session.add(_encounter(user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH", session_id="privacy"))
            self.session.commit()
            response = self.client.post("/api/patient/assistant", json={
                "question": "Show me the population's total ED spend and utilization opportunity.",
                "encounterId": 1,
                "triageContext": {"isEmergencyRedFlag": False},
            })
            self.assertEqual(response.status_code, 200, response.get_json())

        for forbidden in ("totalEdSpend", "potentialEdCostOpportunity", "highUtilizationMemberCount"):
            self.assertNotIn(forbidden, captured_prompt["prompt"])

    def test_patient_assistant_cannot_access_another_patients_information(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant") as mock_assistant_cls:
            register_user(email="other-owner@example.com", password="password123", role="PATIENT", session=self.session)
            self.session.add(_encounter(user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH", session_id="other-owner-enc"))
            self.session.commit()

            self._login("assistant-patient-nosy@example.com", "PATIENT")
            response = self.client.post("/api/patient/assistant", json={
                "question": "Show me another patient's claims.",
                "encounterId": 1,
                "triageContext": {"isEmergencyRedFlag": False},
            })
            self.assertEqual(response.status_code, 404)
            mock_assistant_cls.assert_not_called()

    def test_classify_patient_workflow_intent_recognizes_documented_variations(self) -> None:
        from backend.routes.assistant import _classify_patient_workflow_intent

        pathway_examples = (
            "Why was I recommended this care pathway?",
            "Why was I recommended telehealth?",
            "Why was I recommended urgent care?",
            "Why was I recommended primary care?",
            "Why did you recommend telehealth for me?",
            "Why did RightPath recommend this?",
        )
        ignore_examples = (
            "Can I ignore the recommendation?",
            "Is it safe to ignore the recommendation?",
        )
        open_ended_examples = (
            "What should I ask the telehealth nurse?",
            "How can I prepare for my appointment?",
            "What should I expect from a telehealth visit?",
            "What is the difference between urgent care and primary care?",
            "What should I tell the doctor about my symptoms?",
            "What should I do if my symptoms change?",
        )
        for question in pathway_examples:
            with self.subTest(question=question):
                self.assertEqual(_classify_patient_workflow_intent(question), "pathway_explanation")
        for question in ignore_examples:
            with self.subTest(question=question):
                self.assertEqual(_classify_patient_workflow_intent(question), "ignore_recommendation")
        for question in open_ended_examples:
            with self.subTest(question=question):
                self.assertIsNone(_classify_patient_workflow_intent(question))

    def test_new_message_is_emergency_matches_reproduced_examples(self) -> None:
        from backend.routes.assistant import _new_message_is_emergency

        emergency_examples = (
            "I suddenly have severe chest pain.",
            "I am having severe difficulty breathing.",
            "I think I'm having a stroke.",
            "I suddenly passed out.",
        )
        non_emergency_examples = (
            "What should I ask the telehealth nurse?",
            "I have a mild cough and runny nose.",
            "I have no chest pain and no trouble breathing. I only have a mild cough.",
        )
        for question in emergency_examples:
            with self.subTest(question=question):
                self.assertTrue(_new_message_is_emergency(question))
        for question in non_emergency_examples:
            with self.subTest(question=question):
                self.assertFalse(_new_message_is_emergency(question))

    def test_patient_gemini_unavailable_returns_graceful_fallback_not_raw_error(self) -> None:
        """When Gemini is unavailable (after its own bounded retry), the
        patient must see a graceful, demo-safe reply grounded in the
        existing recommendation -- never a raw 503/error, and never a
        fabricated new medical recommendation."""
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant", _FailingGeminiAssistant):
            self._login("assistant-patient-e@example.com", "PATIENT")
            self.session.add(_encounter(user_id=1, is_emergency=False, recommended_acuity="TELEHEALTH", session_id="e"))
            self.session.commit()

            response = self.client.post("/api/patient/assistant", json={
                "question": "What should I ask the telehealth nurse?", "encounterId": 1,
                "triageContext": {"isEmergencyRedFlag": False},
            })
            self.assertEqual(response.status_code, 200, response.get_json())
            body = response.get_json()
            self.assertNotIn("error", body)
            self.assertNotIn("emergency", body)
            self.assertIn("reply", body)
            self.assertIn("Care guidance", body["reply"])
            # Grounded in the persisted recommended_setting_name, not invented.
            self.assertIn("Telehealth", body["reply"])
            self.assertIn("temporarily unavailable", body["reply"].lower())
            self.assertNotIn("gemini", body["reply"].lower())

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
            def generate(self, *, system_instruction: str, user_content: str, max_output_tokens: int | None = None) -> str:
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

    def test_payer_forwards_reduced_output_token_cap_and_reply_is_not_truncated(self) -> None:
        """The reduced PAYER_MAX_OUTPUT_TOKENS (800, down from a prior 1024)
        actually reaches GeminiAssistant.generate() for the payer's narrow
        analytics-Gemini path, and a normal-length reply comes back intact."""
        from backend.routes.assistant import PAYER_MAX_OUTPUT_TOKENS

        captured = {}
        normal_reply = "Risk Distribution\n\n* High risk: 12\n* Medium risk: 34\n* Low risk: 54"

        class _CapturingAssistant(_FakeGeminiAssistant):
            def generate(self, *, system_instruction: str, user_content: str, max_output_tokens=None) -> str:
                captured["max_output_tokens"] = max_output_tokens
                return normal_reply

        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.analytics_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant", _CapturingAssistant), \
             patch("backend.routes.assistant.KnowledgeRetriever") as mock_retriever_cls:
            self._login("assistant-payer-tokencap@example.com", "PAYER")
            response = self.client.post("/api/payer/assistant", json={"question": "What is the risk distribution?"})
            self.assertEqual(response.status_code, 200, response.get_json())
            body = response.get_json()

        mock_retriever_cls.assert_not_called()
        self.assertEqual(captured["max_output_tokens"], PAYER_MAX_OUTPUT_TOKENS)
        self.assertEqual(body["reply"], normal_reply)

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

    def test_narrow_analytics_gemini_failure_falls_back_to_deterministic_summary(self) -> None:
        """An analytics question this router can't format deterministically
        still falls back to a deterministic aggregate summary -- built from
        the same already-available analytics -- rather than a raw error if
        Gemini is unavailable."""
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.analytics_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant", _FailingGeminiAssistant), \
             patch("backend.routes.assistant.KnowledgeRetriever") as mock_retriever_cls:
            self._login("assistant-payer-j@example.com", "PAYER")
            response = self.client.post("/api/payer/assistant", json={"question": "What is the utilization distribution?"})
            self.assertEqual(response.status_code, 200, response.get_json())
            body = response.get_json()
            self.assertNotIn("error", body)
            self.assertIn("Population Analytics", body["reply"])
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

    def test_payer_knowledge_gemini_failure_shows_professional_unavailable_state(self) -> None:
        """A genuine knowledge/guidance question needs Gemini/RAG and cannot
        be answered safely from aggregate analytics alone -- if Gemini is
        unavailable, show a concise professional unavailable state rather
        than a broken-looking error."""
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope), \
             patch("backend.services.analytics_service.session_scope", self._mock_session_scope), \
             patch("backend.routes.assistant.GeminiAssistant", _FailingGeminiAssistant), \
             patch("backend.routes.assistant.KnowledgeRetriever", _FakeKnowledgeRetriever):
            self._login("assistant-payer-d@example.com", "PAYER")
            response = self.client.post("/api/payer/assistant", json={"question": "hi"})
            self.assertEqual(response.status_code, 200, response.get_json())
            body = response.get_json()
            self.assertNotIn("error", body)
            self.assertNotIn("sources", body)
            self.assertIn("Assistant Temporarily Unavailable", body["reply"])

    def test_payer_missing_question_is_rejected(self) -> None:
        with patch("backend.services.auth_service.session_scope", self._mock_session_scope):
            self._login("assistant-payer-e@example.com", "PAYER")
            response = self.client.post("/api/payer/assistant", json={})
            self.assertEqual(response.status_code, 400)


class AssistantServiceUnitTests(unittest.TestCase):
    """Direct unit tests for backend/services/assistant_service.py, mocking
    google.genai so no network call is ever made."""

    def setUp(self) -> None:
        # GeminiAssistant.generate() now reuses one cached genai.Client per
        # api_key (see _get_client) instead of constructing a fresh one per
        # call -- a directly-measured latency fix (~1.4-1.7s saved per
        # request). Each test here patches google.genai.Client to its own
        # fake, so the cache must start empty every test or a later test
        # would silently get an earlier test's stale mocked client instead
        # of its own.
        from backend.services.assistant_service import _get_client
        _get_client.cache_clear()

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

    def test_generate_reuses_cached_client_across_multiple_calls(self) -> None:
        """Latency fix: genai.Client() is directly measured to cost
        ~1.4-1.7s per construction. This locks in that it is constructed at
        most once per api_key and reused -- not reconstructed on every
        request (two separate GeminiAssistant instances, as the real route
        code creates per-request, sharing the same api_key)."""
        from backend.services.assistant_service import GeminiAssistant

        construction_count = {"n": 0}
        fake_response = type("Resp", (), {"text": "ok"})()
        fake_client = type("Client", (), {
            "models": type("Models", (), {"generate_content": lambda self, **kwargs: fake_response})(),
        })()

        def _construct_client(**kwargs):
            construction_count["n"] += 1
            return fake_client

        with patch("google.genai.Client", side_effect=_construct_client):
            GeminiAssistant(api_key="fake-shared-key", model="fake-model").generate(
                system_instruction="You are helpful.", user_content="First question"
            )
            GeminiAssistant(api_key="fake-shared-key", model="fake-model").generate(
                system_instruction="You are helpful.", user_content="Second question"
            )

        self.assertEqual(construction_count["n"], 1)

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
        """A network-level ConnectionError is transient (retried once
        internally -- see the retry tests below) but still ends up wrapped
        as a controlled AssistantGenerationError if it never recovers."""
        from backend.services.assistant_service import AssistantGenerationError, GeminiAssistant

        def _raise(**kwargs):
            raise ConnectionError("network down")

        fake_client = type("Client", (), {
            "models": type("Models", (), {"generate_content": lambda self, **kwargs: _raise(**kwargs)})(),
        })()

        with patch("google.genai.Client", return_value=fake_client), \
             patch("backend.services.assistant_service.time.sleep"):
            assistant = GeminiAssistant(api_key="fake", model="fake-model")
            with self.assertRaises(AssistantGenerationError):
                assistant.generate(system_instruction="You are helpful.", user_content="Hello")

    def test_generate_applies_low_thinking_and_forwards_max_output_tokens(self) -> None:
        """Locks in the demo-hardening config: a low thinking level (the
        lowest this SDK/model combination accepts -- thinking_budget=0 is
        rejected by gemini-3.6-flash with a live 400 INVALID_ARGUMENT,
        confirmed by direct testing) and a caller-supplied output-token cap
        are both actually threaded into the GenerateContentConfig sent to
        Gemini, not just accepted as unused parameters."""
        from google.genai import types

        from backend.services.assistant_service import GeminiAssistant

        captured = {}
        fake_response = type("Resp", (), {"text": "ok"})()

        def _generate_content(self, **kwargs):
            captured["config"] = kwargs["config"]
            return fake_response

        fake_client = type("Client", (), {
            "models": type("Models", (), {"generate_content": _generate_content})(),
        })()

        with patch("google.genai.Client", return_value=fake_client):
            assistant = GeminiAssistant(api_key="fake", model="fake-model")
            assistant.generate(
                system_instruction="You are helpful.", user_content="Hello", max_output_tokens=1024,
            )

        config = captured["config"]
        self.assertEqual(config.max_output_tokens, 1024)
        self.assertIsNotNone(config.thinking_config)
        self.assertEqual(config.thinking_config.thinking_level, types.ThinkingLevel.LOW)

    def test_generate_disables_sdk_internal_retry_and_caps_http_timeout(self) -> None:
        """Latency investigation finding: the SDK's OWN internal HTTP retry
        policy defaults to up to 5 attempts with backoff up to 60s BETWEEN
        attempts -- measured directly to make a single "attempt" from our
        code's perspective take 53.8s before it ever raised to us, with our
        own bounded retry then firing again on top of that. This locks in
        that the SDK's internal retry is disabled (attempts=1, i.e. no
        retries -- our own single bounded retry, tested elsewhere, is the
        only retry policy in effect) and a sane per-request timeout is set,
        so a transient failure fails fast into our own fast/predictable
        retry instead of silently blocking for up to a minute."""
        from backend.services.assistant_service import GeminiAssistant

        captured = {}
        fake_response = type("Resp", (), {"text": "ok"})()

        def _generate_content(self, **kwargs):
            captured["config"] = kwargs["config"]
            return fake_response

        fake_client = type("Client", (), {
            "models": type("Models", (), {"generate_content": _generate_content})(),
        })()

        with patch("google.genai.Client", return_value=fake_client):
            assistant = GeminiAssistant(api_key="fake", model="fake-model")
            assistant.generate(system_instruction="You are helpful.", user_content="Hello")

        http_options = captured["config"].http_options
        self.assertIsNotNone(http_options)
        self.assertEqual(http_options.retry_options.attempts, 1)
        self.assertIsInstance(http_options.timeout, int)
        self.assertGreater(http_options.timeout, 0)
        # Must stay well under the SDK default's ~60s per-attempt ceiling --
        # a "sane timeout" that still fails fast, not a copy of the problem.
        self.assertLessEqual(http_options.timeout, 30_000)

    def test_generate_retries_once_on_transient_error_then_succeeds(self) -> None:
        """C: first call fails with a transient (503) error, second
        succeeds -- exactly one retry occurs and the successful result is
        returned, never a fabricated reply."""
        from google.genai import errors as genai_errors

        from backend.services.assistant_service import GeminiAssistant

        transient_error = genai_errors.ServerError(
            503, {"error": {"status": "UNAVAILABLE", "message": "high demand, please retry"}}
        )
        fake_response = type("Resp", (), {"text": "Recovered reply."})()
        call_count = {"n": 0}

        def _generate_content(self, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise transient_error
            return fake_response

        fake_client = type("Client", (), {
            "models": type("Models", (), {"generate_content": _generate_content})(),
        })()

        with patch("google.genai.Client", return_value=fake_client), \
             patch("backend.services.assistant_service.time.sleep") as mock_sleep:
            assistant = GeminiAssistant(api_key="fake", model="fake-model")
            result = assistant.generate(system_instruction="You are helpful.", user_content="Hello")

        self.assertEqual(result, "Recovered reply.")
        self.assertEqual(call_count["n"], 2)
        mock_sleep.assert_called_once()

    def test_generate_does_not_retry_daily_quota_exhaustion(self) -> None:
        """D: a daily/project quota exhaustion (429 RESOURCE_EXHAUSTED with
        a "PerDay" quotaId, matching the real error observed from the live
        API) must fail fast -- no retry loop, since retrying cannot
        possibly succeed within a demo-appropriate window."""
        from google.genai import errors as genai_errors

        from backend.services.assistant_service import AssistantGenerationError, GeminiAssistant

        quota_error = genai_errors.ClientError(429, {
            "error": {
                "status": "RESOURCE_EXHAUSTED",
                "message": "You exceeded your current quota, please check your plan and billing details.",
                "details": [{
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}],
                }],
            }
        })
        call_count = {"n": 0}

        def _generate_content(self, **kwargs):
            call_count["n"] += 1
            raise quota_error

        fake_client = type("Client", (), {
            "models": type("Models", (), {"generate_content": _generate_content})(),
        })()

        with patch("google.genai.Client", return_value=fake_client), \
             patch("backend.services.assistant_service.time.sleep") as mock_sleep:
            assistant = GeminiAssistant(api_key="fake", model="fake-model")
            with self.assertRaises(AssistantGenerationError):
                assistant.generate(system_instruction="You are helpful.", user_content="Hello")

        self.assertEqual(call_count["n"], 1)  # no retry loop
        mock_sleep.assert_not_called()

    def test_generate_retries_at_most_once_even_if_the_retry_also_fails(self) -> None:
        """Bounds the retry to exactly one attempt -- a persistently
        transient failure must not turn into a retry loop."""
        from google.genai import errors as genai_errors

        from backend.services.assistant_service import AssistantGenerationError, GeminiAssistant

        transient_error = genai_errors.ServerError(500, {"error": {"status": "INTERNAL", "message": "internal error"}})
        call_count = {"n": 0}

        def _generate_content(self, **kwargs):
            call_count["n"] += 1
            raise transient_error

        fake_client = type("Client", (), {
            "models": type("Models", (), {"generate_content": _generate_content})(),
        })()

        with patch("google.genai.Client", return_value=fake_client), \
             patch("backend.services.assistant_service.time.sleep") as mock_sleep:
            assistant = GeminiAssistant(api_key="fake", model="fake-model")
            with self.assertRaises(AssistantGenerationError):
                assistant.generate(system_instruction="You are helpful.", user_content="Hello")

        self.assertEqual(call_count["n"], 2)  # original attempt + exactly one retry
        mock_sleep.assert_called_once()

    def test_generate_does_not_retry_non_transient_errors(self) -> None:
        """A non-transient error (e.g. a 400 invalid-argument config
        problem) is not worth retrying and must fail on the first attempt."""
        from google.genai import errors as genai_errors

        from backend.services.assistant_service import AssistantGenerationError, GeminiAssistant

        bad_request_error = genai_errors.ClientError(400, {"error": {"status": "INVALID_ARGUMENT", "message": "bad request"}})
        call_count = {"n": 0}

        def _generate_content(self, **kwargs):
            call_count["n"] += 1
            raise bad_request_error

        fake_client = type("Client", (), {
            "models": type("Models", (), {"generate_content": _generate_content})(),
        })()

        with patch("google.genai.Client", return_value=fake_client), \
             patch("backend.services.assistant_service.time.sleep") as mock_sleep:
            assistant = GeminiAssistant(api_key="fake", model="fake-model")
            with self.assertRaises(AssistantGenerationError):
                assistant.generate(system_instruction="You are helpful.", user_content="Hello")

        self.assertEqual(call_count["n"], 1)
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
