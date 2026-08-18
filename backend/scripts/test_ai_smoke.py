"""Manual, opt-in smoke test for the RightPath AI assistant layer.

This makes REAL calls to Gemini (and Pinecone, for the RAG-backed payer
question) using whatever credentials are configured in the environment. It
is intentionally NOT collected or run by pytest (it lives outside every
directory pytest.ini discovers, has no test_*.py-style test functions, and
is only ever invoked directly), so it never affects the automated suite or
runs in CI by accident.

Run it explicitly:

    .venv\\Scripts\\python backend\\scripts\\test_ai_smoke.py

Requires GEMINI_API_KEY (and, for the RAG-backed question, PINECONE_API_KEY
+ PINECONE_INDEX_NAME with the knowledge base already ingested via
backend/ingest_rag_documents.py) to be set in the environment/.env.

Prints each answer; does not assert anything or exit non-zero on a
disagreeable answer -- this is for human review, not CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.config import get_assistant_settings
from backend.rag.retrieval import KnowledgeRetriever, RetrievalError, RetrievalValidationError
from backend.routes.assistant import PATIENT_SYSTEM_INSTRUCTION, PAYER_SYSTEM_INSTRUCTION
from backend.services.assistant_service import AssistantGenerationError, GeminiAssistant


def _print_section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def smoke_test_payer_analytics_question() -> None:
    _print_section("1. Payer analytics question (no RAG)")
    prompt = (
        "Aggregate analytics context (JSON):\n"
        '{"totalMembers": 8671, "totalEdVisits": 43072, "totalEdSpend": 126946706.46, '
        '"confirmedNonEdNavigationActions": 3, "averageEdClaimCost": 2947.31, '
        '"potentialEdCostOpportunity": 8841.93}\n\n'
        "Payer question: Summarize current RightPath activity."
    )
    settings = get_assistant_settings()
    assistant = GeminiAssistant(api_key=settings.gemini_api_key, model=settings.generation_model)
    try:
        reply = assistant.generate(system_instruction=PAYER_SYSTEM_INSTRUCTION, user_content=prompt)
        print("REPLY:\n" + reply)
    except AssistantGenerationError as error:
        print(f"FAILED (controlled): {error}")


def smoke_test_patient_explanation_question() -> None:
    _print_section("2. Patient explanation question (non-emergency)")
    prompt = (
        "Triage context (already decided by the deterministic safety/triage engine -- "
        "do not re-evaluate or contradict it):\n"
        "Recommended acuity: TELEHEALTH\n"
        "Recommended care setting: 24/7 Telehealth Nurse Line\n"
        "Clinical rationale: Based on your reported complaint without high-risk red flags, "
        "care can be safely delivered via a 24/7 Virtual Telehealth session.\n\n"
        "Patient question: Why was I recommended telehealth instead of urgent care?"
    )
    settings = get_assistant_settings()
    assistant = GeminiAssistant(api_key=settings.gemini_api_key, model=settings.generation_model)
    try:
        reply = assistant.generate(system_instruction=PATIENT_SYSTEM_INSTRUCTION, user_content=prompt)
        print("REPLY:\n" + reply)
    except AssistantGenerationError as error:
        print(f"FAILED (controlled): {error}")


def smoke_test_rag_backed_payer_question() -> None:
    _print_section("3. RAG-backed payer question (Pinecone + Gemini)")
    question = "What does Medicare typically cover for urgent care visits?"
    sources = []
    knowledge_context = ""
    try:
        chunks = KnowledgeRetriever().retrieve(question, top_k=3)
        usable = [c for c in chunks if c.text]
        if usable:
            knowledge_context = "\n\n".join(f"[{c.title or c.source_file}] {c.text}" for c in usable)
            sources = [{"title": c.title, "category": c.category, "source": c.source_file} for c in usable]
            print(f"Retrieved {len(usable)} knowledge chunk(s): {sources}")
        else:
            print("Retrieval succeeded but returned no chunks.")
    except (RetrievalValidationError, RetrievalError, RuntimeError) as error:
        print(f"Retrieval failed (this is expected if Pinecone isn't ingested yet): {error}")

    prompt_parts = ['Aggregate analytics context (JSON):\n{"totalMembers": 8671}']
    if knowledge_context:
        prompt_parts.append(f"Approved knowledge-base context:\n{knowledge_context}")
    prompt_parts.append(f"Payer question: {question}")
    prompt = "\n\n".join(prompt_parts)

    settings = get_assistant_settings()
    assistant = GeminiAssistant(api_key=settings.gemini_api_key, model=settings.generation_model)
    try:
        reply = assistant.generate(system_instruction=PAYER_SYSTEM_INSTRUCTION, user_content=prompt)
        print("REPLY:\n" + reply)
    except AssistantGenerationError as error:
        print(f"FAILED (controlled): {error}")


if __name__ == "__main__":
    print("RightPath AI assistant manual smoke test -- makes REAL Gemini/Pinecone API calls.")
    smoke_test_payer_analytics_question()
    smoke_test_patient_explanation_question()
    smoke_test_rag_backed_payer_question()
    print("\nDone. Review the replies above for quality/tone -- this script does not assert anything.")
