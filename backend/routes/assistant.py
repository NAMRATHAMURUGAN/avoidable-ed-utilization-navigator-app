"""Patient and Payer AI-assistant routes.

Two structurally separate endpoints -- POST /api/patient/assistant and
POST /api/payer/assistant -- rather than one shared unrestricted chat
endpoint, so the patient/payer privacy and safety boundaries are enforced by
route-level authorization (@role_required) and by what context each handler
is even capable of building, not by a runtime branch inside a single
handler.

Both routes are stateless: no conversation is persisted. There is no
chat-history database table (see backend/models/) -- this is intentional for
this phase.

Neither route ever calls the deterministic safety engine
(backend/safety/engine.py) or re-decides is_emergency/recommended_acuity --
those are read-only, already-persisted facts here. The patient route
actively refuses to call Gemini at all for an emergency encounter; the payer
route never receives patient-level data in the first place, by construction
(see _payer_context).

PAYER PERFORMANCE/ROUTING (this phase's addition): a small, deterministic
keyword router (_classify_question) decides whether a payer question is an
analytics question (answerable directly from the existing aggregate
analytics_service functions) or a knowledge/guidance question (needs RAG).
Analytics questions never call KnowledgeRetriever/Gemini embeddings/Pinecone
at all -- that is the entire latency optimization. Many analytics questions
are answered with a fully deterministic, formatted string (no Gemini call
either); only genuinely open-ended analytics questions and knowledge
questions ever call Gemini generation. This is plain keyword matching, not
an NLP model -- see backend/safety/nlp_normalization.py for the (separate,
untouched) real safety NLP layer.
"""

from __future__ import annotations

import json

from flask import Blueprint, g, jsonify, request

from backend.auth.decorators import role_required
from backend.config import get_assistant_settings
from backend.database import session_scope
from backend.rag.retrieval import KnowledgeRetriever
from backend.repositories.encounter_repository import EncounterRepository
from backend.services import analytics_service
from backend.services.assistant_service import AssistantGenerationError, GeminiAssistant

assistant_blueprint = Blueprint("assistant", __name__, url_prefix="/api")

MAX_QUESTION_LENGTH = 1_000
RAG_TOP_K = 3

PATIENT_SYSTEM_INSTRUCTION = (
    "You are the RightPath care guidance assistant. You support patients after "
    "the deterministic safety and triage engine has already completed its "
    "assessment. You do not diagnose. You do not override the triage result. "
    "You do not downgrade an emergency recommendation. Explain the existing "
    "recommendation in clear, simple, reassuring language, and encourage the "
    "patient to follow the recommended care pathway. If the user describes "
    "new severe or emergency symptoms, tell them to seek emergency care "
    "immediately -- call 911 or go to the nearest Emergency Department -- "
    "rather than attempting to diagnose or reassure them about those symptoms."
)

PAYER_SYSTEM_INSTRUCTION = (
    "You are the RightPath payer intelligence assistant. You answer questions "
    "using only the supplied aggregate CMS analytics, aggregate RightPath "
    "program analytics, and approved knowledge-base context below. No "
    "patient-level information, raw symptoms, complaints, emails, or "
    "conversation data is ever provided to you, and you must never imply "
    "otherwise. Do not claim that a navigation action proves an ED visit was "
    "prevented. Do not describe a potential opportunity as realized or actual "
    "savings. Distinguish model-based utilization signals from clinical "
    "diagnoses. If the supplied context does not contain enough information "
    "to answer the question, say so plainly rather than inventing an answer.\n\n"
    "Response style: this is a dashboard, not a chat transcript. Be concise -- "
    "usually under about 120 words. Use a short heading when useful, and "
    "prefer bullets or compact sections over long paragraphs; use a table "
    "only if it genuinely improves readability. Put important numbers "
    "directly next to their labels. Do not repeat the user's question, "
    "restate the entire supplied context, or open with phrases like \"Based "
    "on the information provided\". Do not give a lengthy methodology "
    "explanation unless the user asks for methodology -- a short "
    "safety/methodology note is enough when relevant, and avoid repeating it "
    "more than once. Never fabricate missing data; if something is "
    "unavailable, state that clearly in one concise sentence."
)


def _error(message: str, status: int, **extra):
    return jsonify({"error": message, **extra}), status


def _validate_question(data: object) -> tuple[str | None, tuple | None]:
    if not isinstance(data, dict):
        return None, ("Missing JSON request payload", 400)
    question = data.get("question")
    if not isinstance(question, str) or not question.strip():
        return None, ("question is required", 400)
    question = question.strip()
    if len(question) > MAX_QUESTION_LENGTH:
        return None, (f"question must not exceed {MAX_QUESTION_LENGTH} characters", 400)
    return question, None


def _generate(system_instruction: str, prompt: str) -> str:
    """Resolve Gemini config and generate one reply, or raise AssistantGenerationError.

    Configuration errors (missing GEMINI_API_KEY) surface as RuntimeError from
    get_assistant_settings(); both that and AssistantGenerationError are
    treated identically by callers as "assistant unavailable right now" --
    never as a fabricated successful answer.
    """
    settings = get_assistant_settings()
    assistant = GeminiAssistant(api_key=settings.gemini_api_key, model=settings.generation_model)
    return assistant.generate(system_instruction=system_instruction, user_content=prompt)


@assistant_blueprint.post("/patient/assistant", strict_slashes=False)
@role_required("PATIENT")
def patient_assistant():
    """POST /api/patient/assistant - AI explanation for a NON-EMERGENCY result only.

    encounterId is required and re-fetched from PostgreSQL: the emergency gate
    is decided from the persisted TriageEncounter.is_emergency column, never
    trusted from the client-supplied triageContext alone (a client-supplied
    isEmergencyRedFlag=false could not force this route to answer for a real
    emergency encounter). The prompt context is built entirely from the
    persisted encounter row -- chief_complaint is never read or forwarded to
    Gemini; only the already-patient-facing recommendation fields are used.
    """
    data = request.get_json(silent=True)
    question, error = _validate_question(data)
    if error:
        return _error(*error)

    encounter_id_raw = data.get("encounterId")
    if isinstance(encounter_id_raw, bool) or not isinstance(encounter_id_raw, (int, str)):
        return _error("encounterId is required", 400)
    try:
        encounter_id = int(encounter_id_raw)
    except (TypeError, ValueError):
        return _error("encounterId must be a positive integer", 400)
    if encounter_id <= 0:
        return _error("encounterId must be a positive integer", 400)

    client_triage_context = data.get("triageContext")
    client_flagged_emergency = (
        isinstance(client_triage_context, dict) and client_triage_context.get("isEmergencyRedFlag") is True
    )

    with session_scope() as session:
        encounter = EncounterRepository(session).get_encounter(encounter_id)
        if encounter is None:
            return _error("Encounter not found", 404)
        if encounter.user_id != g.current_user.id:
            # Never let one patient probe another patient's encounter/recommendation.
            return _error("Encounter not found", 404)

        # Fail-safe OR: either signal indicating an emergency blocks the
        # assistant. The persisted, database-backed value is authoritative;
        # the client-supplied flag can only ever make this MORE restrictive,
        # never less.
        if encounter.is_emergency or client_flagged_emergency:
            return jsonify({
                "error": "AI assistance is not available for emergency triage.",
                "emergency": True,
            })

        recommended_acuity = encounter.recommended_acuity
        recommended_setting_name = encounter.recommended_setting_name
        clinical_rationale = encounter.clinical_rationale

    prompt = (
        "Triage context (already decided by the deterministic safety/triage engine -- "
        "do not re-evaluate or contradict it):\n"
        f"Recommended acuity: {recommended_acuity}\n"
        f"Recommended care setting: {recommended_setting_name}\n"
        f"Clinical rationale: {clinical_rationale}\n\n"
        f"Patient question: {question}"
    )

    try:
        reply = _generate(PATIENT_SYSTEM_INSTRUCTION, prompt)
    except (RuntimeError, AssistantGenerationError) as error:
        return _error(str(error), 503)

    return jsonify({"reply": reply})


def _payer_context(population: dict, rightpath: dict) -> dict:
    """Explicit whitelist of aggregate-only fields -- never the raw service
    response. Adding a field here is a deliberate choice, not an accident of
    serializing everything; anything not listed here can never reach Gemini
    (or the deterministic formatters below) through this function, regardless
    of what the underlying services ever return in the future."""
    return {
        "totalMembers": population.get("totalPatients"),
        "totalEdVisits": population.get("totalEdVisits"),
        "totalEdSpend": population.get("totalEdSpend"),
        "highUtilizationMemberCount": population.get("highUtilizationMemberCount"),
        "anomalousMemberCount": population.get("anomalousMemberCount"),
        "utilizationDistribution": population.get("utilizationDistribution"),
        "riskDistribution": population.get("riskDistribution"),
        "totalRightPathAssessments": rightpath.get("totalRightPathAssessments"),
        "emergencyAssessments": rightpath.get("emergencyAssessments"),
        "nonEmergencyRecommendations": rightpath.get("nonEmergencyRecommendations"),
        "confirmedNonEdNavigationActions": rightpath.get("confirmedNonEdNavigationActions"),
        "telehealthNavigations": rightpath.get("telehealthNavigations"),
        "primaryCareNavigations": rightpath.get("primaryCareNavigations"),
        "urgentCareNavigations": rightpath.get("urgentCareNavigations"),
        "emergencyNavigations": rightpath.get("emergencyNavigations"),
        "pathwayDistribution": rightpath.get("pathwayDistribution"),
        "activityTrend": rightpath.get("activityTrend"),
        "potentialEdUtilizationOpportunities": rightpath.get("potentialEdUtilizationOpportunities"),
        "averageEdClaimCost": rightpath.get("averageEdClaimCost"),
        "potentialEdCostOpportunity": rightpath.get("potentialEdCostOpportunity"),
        "costOpportunityMethodology": rightpath.get("costOpportunityMethodology"),
    }


# ---------------------------------------------------------------------------
# Lightweight, deterministic question routing (Task 1). Plain keyword
# containment on the normalized question text -- not an NLP model. Ambiguous
# questions (matching neither list) default to "knowledge" (RAG), the safest
# existing behavior, rather than risking an invented analytics answer.
# ---------------------------------------------------------------------------
_RIGHTPATH_KEYWORDS: tuple[str, ...] = (
    "rightpath", "assessment", "navigation action", "telehealth", "primary care",
    "urgent care", "care pathway", "pathway", "non-ed", "non ed",
    "utilization opportunity", "cost opportunity", "recommendation",
)

_POPULATION_KEYWORDS: tuple[str, ...] = (
    "population", "member", "ed visit", "ed spend", "high-utilization",
    "high utilization", "anomalous", "anomaly", "utilization distribution",
    "risk distribution", "cms",
)


def _classify_question(question: str) -> str:
    """Return 'rightpath', 'population', or 'knowledge' for a payer question."""
    normalized = question.lower()
    if any(keyword in normalized for keyword in _RIGHTPATH_KEYWORDS):
        return "rightpath"
    if any(keyword in normalized for keyword in _POPULATION_KEYWORDS):
        return "population"
    return "knowledge"


# ---------------------------------------------------------------------------
# Deterministic analytics answers (Tasks 3 & 5): for the question shapes this
# small router recognizes precisely, the reply is built directly from the
# existing aggregate analytics values -- no Gemini call, no reinterpretation
# of numbers the backend already knows exactly. Anything the lookup below
# doesn't recognize still skips RAG (Task 2) but falls through to a narrow,
# concise Gemini call over the same whitelisted context (see payer_assistant).
# ---------------------------------------------------------------------------
_NON_ED_PATHWAY_FIELDS: tuple[tuple[str, str], ...] = (
    ("Telehealth", "telehealthNavigations"),
    ("Primary Care", "primaryCareNavigations"),
    ("Urgent Care", "urgentCareNavigations"),
)


def _format_money(value: object) -> str:
    return f"${value:,.2f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "Not yet estimable"


def _format_count(value: object) -> str:
    return f"{value:,}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "Not yet available"


def _most_used_pathway_reply(context: dict) -> str:
    counts = [(label, context.get(field) or 0) for label, field in _NON_ED_PATHWAY_FIELDS]
    total = sum(count for _, count in counts)
    if total == 0:
        return "Most Used Pathway\n\nNo confirmed non-ED navigation actions have been recorded yet."
    lines = "\n".join(
        f"• {label} — {count} action{'' if count == 1 else 's'}" for label, count in counts
    )
    return (
        "Most Used Pathway\n\n"
        f"{lines}\n\n"
        f"Total non-ED navigation: {total}\n\n"
        "Note: Recorded navigation actions do not prove that an ED visit was prevented."
    )


def _summarize_rightpath_reply(context: dict) -> str:
    total_non_ed = sum(context.get(field) or 0 for _, field in _NON_ED_PATHWAY_FIELDS)
    return (
        "RightPath Activity\n\n"
        "Assessments\n"
        f"• Total — {_format_count(context.get('totalRightPathAssessments'))}\n"
        f"• Emergency — {_format_count(context.get('emergencyAssessments'))}\n"
        f"• Non-emergency recommendations — {_format_count(context.get('nonEmergencyRecommendations'))}\n\n"
        "Navigation\n"
        f"• Telehealth — {_format_count(context.get('telehealthNavigations'))}\n"
        f"• Primary Care — {_format_count(context.get('primaryCareNavigations'))}\n"
        f"• Urgent Care — {_format_count(context.get('urgentCareNavigations'))}\n\n"
        "Potential opportunity\n"
        f"• Non-ED navigation actions — {_format_count(total_non_ed)}\n"
        f"• Potential ED cost opportunity — {_format_money(context.get('potentialEdCostOpportunity'))}\n\n"
        "Note: This is a modeled opportunity, not realized savings or confirmed ED prevention."
    )


# (trigger substrings, context field key, display label, is_currency)
_FIELD_LOOKUPS: tuple[tuple[tuple[str, ...], str, str, bool], ...] = (
    (("utilization opportunity",), "potentialEdUtilizationOpportunities", "Potential ED Utilization Opportunities", False),
    (("cost opportunity",), "potentialEdCostOpportunity", "Potential ED Cost Opportunity", True),
    (("non-ed navigation", "non ed navigation", "confirmed non-ed"), "confirmedNonEdNavigationActions",
     "Confirmed Non-ED Navigation Actions", False),
    (("non-emergency recommendation", "non emergency recommendation"), "nonEmergencyRecommendations",
     "Non-Emergency Recommendations", False),
    (("emergency assessment",), "emergencyAssessments", "Emergency Assessments", False),
    (("rightpath assessment",), "totalRightPathAssessments", "Total RightPath Assessments", False),
    (("telehealth",), "telehealthNavigations", "Telehealth Navigations", False),
    (("primary care",), "primaryCareNavigations", "Primary Care Navigations", False),
    (("urgent care",), "urgentCareNavigations", "Urgent Care Navigations", False),
    (("high-utilization member", "high utilization member"), "highUtilizationMemberCount",
     "High-Utilization Members", False),
    (("anomalous",), "anomalousMemberCount", "Anomalous Members", False),
    (("total ed spend", "ed spend"), "totalEdSpend", "Total ED Spend", True),
    (("ed visit",), "totalEdVisits", "Total ED Visits", False),
    (("members are in the population", "how many member", "total member"), "totalMembers", "Total Members", False),
)


def _field_lookup_reply(question: str, context: dict) -> str | None:
    normalized = question.lower()
    for phrases, field, label, is_currency in _FIELD_LOOKUPS:
        if any(phrase in normalized for phrase in phrases):
            value = context.get(field)
            formatted = _format_money(value) if is_currency else _format_count(value)
            return f"{label}: {formatted}"
    return None


def _deterministic_analytics_reply(question: str, category: str, context: dict) -> str | None:
    """Return a fully deterministic reply for a recognized analytics question
    shape, or None if this small router doesn't recognize it precisely enough
    (caller falls back to a narrow, RAG-free Gemini call in that case)."""
    normalized = question.lower()
    if category == "rightpath":
        if "most used" in normalized and ("pathway" in normalized or "care" in normalized):
            return _most_used_pathway_reply(context)
        if "summarize" in normalized or "summary" in normalized:
            return _summarize_rightpath_reply(context)
    return _field_lookup_reply(question, context)


def _retrieve_knowledge_context(question: str) -> tuple[list[dict], str]:
    """RAG retrieval for genuine knowledge/guidance questions only (Task 2's
    optimization is that this function is simply never called for analytics
    questions). Any failure here (missing Pinecone config, network error,
    embedding failure) must never block an answer and must never be presented
    as fabricated retrieved knowledge -- both return values simply stay empty."""
    try:
        chunks = KnowledgeRetriever().retrieve(question, top_k=RAG_TOP_K)
        usable_chunks = [chunk for chunk in chunks if chunk.text]
        if not usable_chunks:
            return [], ""
        knowledge_context = "\n\n".join(
            f"[{chunk.title or chunk.source_file}] {chunk.text}" for chunk in usable_chunks
        )
        sources = [
            {"title": chunk.title, "category": chunk.category, "source": chunk.source_file}
            for chunk in usable_chunks
        ]
        return sources, knowledge_context
    except Exception:
        return [], ""


@assistant_blueprint.post("/payer/assistant", strict_slashes=False)
@role_required("PAYER")
def payer_assistant():
    """POST /api/payer/assistant - AI answer grounded in aggregate analytics
    and, optionally, approved RAG knowledge. Never receives or forwards raw
    patient complaint/conversation text -- context is built exclusively from
    _payer_context()'s explicit whitelist of the existing, already
    aggregate-only analytics_service functions.

    Routing: analytics questions (category 'rightpath'/'population') never
    call KnowledgeRetriever/Gemini embeddings/Pinecone at all, and are
    answered deterministically wherever this small router recognizes the
    question precisely; only knowledge/guidance questions (and analytics
    questions this router doesn't recognize) ever call Gemini generation.
    """
    data = request.get_json(silent=True)
    question, error = _validate_question(data)
    if error:
        return _error(*error)

    population = analytics_service.get_population_analytics()
    rightpath = analytics_service.get_rightpath_analytics()
    context = _payer_context(population, rightpath)

    category = _classify_question(question)

    if category in ("rightpath", "population"):
        deterministic_reply = _deterministic_analytics_reply(question, category, context)
        if deterministic_reply is not None:
            return jsonify({"reply": deterministic_reply})

        # Recognized as an analytics question but not a shape this router can
        # format deterministically (e.g. "what is the risk distribution?").
        # Still skip RAG entirely; use a small, analytics-only prompt.
        prompt = (
            f"Aggregate analytics context (JSON):\n{json.dumps(context, default=str)}\n\n"
            f"Payer question: {question}"
        )
        try:
            reply = _generate(PAYER_SYSTEM_INSTRUCTION, prompt)
        except (RuntimeError, AssistantGenerationError) as error:
            return _error(str(error), 503)
        return jsonify({"reply": reply})

    # category == "knowledge": keep the existing RAG flow unchanged.
    sources, knowledge_context = _retrieve_knowledge_context(question)
    prompt_parts = [f"Aggregate analytics context (JSON):\n{json.dumps(context, default=str)}"]
    if knowledge_context:
        prompt_parts.append(f"Approved knowledge-base context:\n{knowledge_context}")
    prompt_parts.append(f"Payer question: {question}")
    prompt = "\n\n".join(prompt_parts)

    try:
        reply = _generate(PAYER_SYSTEM_INSTRUCTION, prompt)
    except (RuntimeError, AssistantGenerationError) as error:
        return _error(str(error), 503)

    response_body = {"reply": reply}
    if sources:
        response_body["sources"] = sources
    return jsonify(response_body)
