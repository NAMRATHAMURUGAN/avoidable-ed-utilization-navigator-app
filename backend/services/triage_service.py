"""Triage service wrapping the deterministic Python Safety Engine.

Enforces deterministic emergency warning-sign screening without altering
clinical rules or adding external API/LLM dependencies. Persists triage encounters
to PostgreSQL database tables.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
import re
from typing import Any

from sqlalchemy.orm import Session

from backend.database import session_scope
from backend.models.encounter import TriageEncounter
from backend.repositories.encounter_repository import EncounterRepository
from backend.repositories.member_repository import MemberRepository
from backend.repositories.model_run_repository import ModelRunRepository
from backend.safety.engine import evaluate_safety
from backend.safety.nlp_normalization import extract_safety_concept_phrases
from backend.services import provider_service
from backend.services.navigation_service import CareNavigationService
from backend.services.patient_service import member_analytical_result_to_dict


MAX_CHIEF_COMPLAINT_LENGTH = 1_000
MAX_SYMPTOM_DURATION_LENGTH = 128
MAX_LIST_ITEM_LENGTH = 256
MAX_LIST_ITEMS = 50
MAX_IDENTIFIER_LENGTH = 64
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")


class TriageValidationError(ValueError):
    """Raised when a triage API payload is structurally invalid."""


class TriageMemberNotFoundError(LookupError):
    """Raised when a supplied member identifier cannot be resolved."""


class TriagePersistenceError(RuntimeError):
    """Raised when a triage encounter cannot be durably persisted."""


def _validate_identifier(value: Any, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TriageValidationError(f"{field} must be a string or integer identifier")
    normalized = str(value).strip()
    if not normalized or len(normalized) > MAX_IDENTIFIER_LENGTH:
        raise TriageValidationError(f"{field} must be between 1 and {MAX_IDENTIFIER_LENGTH} characters")
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise TriageValidationError(f"{field} contains invalid characters")


def validate_triage_request(data: Any) -> dict[str, Any]:
    """Validate the public triage request before safety evaluation or persistence."""
    if not isinstance(data, dict):
        raise TriageValidationError("JSON request payload must be an object")

    chief_complaint = data.get("chiefComplaint")
    if not isinstance(chief_complaint, str) or not chief_complaint.strip():
        raise TriageValidationError("chiefComplaint is required and must be a nonblank string")
    if len(chief_complaint.strip()) > MAX_CHIEF_COMPLAINT_LENGTH:
        raise TriageValidationError(
            f"chiefComplaint must not exceed {MAX_CHIEF_COMPLAINT_LENGTH} characters"
        )

    symptoms_duration = data.get("symptomsDuration")
    if symptoms_duration is not None:
        if not isinstance(symptoms_duration, str):
            raise TriageValidationError("symptomsDuration must be a string")
        if len(symptoms_duration.strip()) > MAX_SYMPTOM_DURATION_LENGTH:
            raise TriageValidationError(
                f"symptomsDuration must not exceed {MAX_SYMPTOM_DURATION_LENGTH} characters"
            )

    for field in ("associatedSymptoms", "selectedRedFlags"):
        value = data.get(field, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise TriageValidationError(f"{field} must be a list of strings")
        if len(value) > MAX_LIST_ITEMS or any(len(item) > MAX_LIST_ITEM_LENGTH for item in value):
            raise TriageValidationError(f"{field} contains too many or overly long values")

    if "hasRedFlags" in data and not isinstance(data["hasRedFlags"], bool):
        raise TriageValidationError("hasRedFlags must be a boolean")

    for field in ("patientId", "member_id", "patient_id", "sessionId", "session_id"):
        if field in data and data[field] is not None:
            _validate_identifier(data[field], field)
    return data


def _execute_triage_request(
    session: Session, data: dict[str, Any], *, allow_member_linkage: bool, user_id: int | None = None
) -> dict[str, Any]:
    chief_complaint = str(data.get("chiefComplaint", "") or "").strip()
    symptoms_duration = str(data.get("symptomsDuration", "") or "").strip()

    associated_symptoms_raw = data.get("associatedSymptoms")
    associated_symptoms = (
        [str(item) for item in associated_symptoms_raw if isinstance(item, str)]
        if isinstance(associated_symptoms_raw, list)
        else []
    )

    selected_red_flags_raw = data.get("selectedRedFlags")
    selected_red_flags = (
        [str(item) for item in selected_red_flags_raw if isinstance(item, str)]
        if isinstance(selected_red_flags_raw, list)
        else []
    )

    has_red_flags = data.get("hasRedFlags") is True
    session_id = str(data.get("sessionId") or data.get("session_id") or uuid.uuid4())

    patient_id_raw = data.get("patientId") or data.get("member_id") or data.get("patient_id")
    member_id: int | None = None
    patient_context: dict[str, Any] | None = None
    proactive_rec: dict[str, Any] | None = None

    # Member-linked ML enrichment (patientContext / proactiveRecommendation)
    # is only ever resolved for an authenticated PAYER caller. There is no
    # User<->Member mapping, so an unauthenticated or PATIENT caller
    # supplying a patientId is treated identically to anonymous triage: the
    # safety-engine screening below is entirely unaffected either way.
    if patient_id_raw is not None and allow_member_linkage:
        m_repo = MemberRepository(session)
        run_repo = ModelRunRepository(session)
        # Explicitly resolve the latest model run for each model type, mirroring
        # patient_service.py, so the linked prediction/anomaly always reflect the
        # current model run rather than falling back to row-insertion order.
        xgb_run = run_repo.get_latest("xgboost")
        anomaly_run = run_repo.get_latest("isolation_forest")
        member_res = m_repo.get_combined_result_by_id_or_bene(
            str(patient_id_raw),
            xgb_model_run_id=xgb_run.model_run_id if xgb_run else None,
            anomaly_model_run_id=anomaly_run.model_run_id if anomaly_run else None,
        )
        if member_res is None:
            raise TriageMemberNotFoundError("Patient not found")
        member_id = member_res.member.id
        anal_dict = member_analytical_result_to_dict(member_res)
        xgb_prob = (
            member_res.xgboost_prediction.high_utilization_probability
            if member_res.xgboost_prediction
            else None
        )
        anomaly_flag = (
            bool(member_res.anomaly_result.anomaly_flag)
            if member_res.anomaly_result
            else False
        )
        patient_context = {
            "patientId": str(member_res.member.id),
            "beneficiaryId": member_res.member.bene_id,
            "riskLevel": anal_dict["riskLevel"],
            "riskLevelInterpretation": anal_dict["riskLevelInterpretation"],
            "highUtilizationProbability": xgb_prob,
            "anomalyFlag": anomaly_flag,
            "edVisitCount12m": anal_dict["edVisitCount12m"],
        }
        nav_service = CareNavigationService()
        proactive_rec = nav_service.generate_recommendation(
            member_data={"bene_id": member_res.member.bene_id},
            utilization_data={"ed_visit_count": anal_dict["edVisitCount12m"]},
            ml_data={"predicted_probability": xgb_prob},
            anomaly_data={"anomaly_flag": anomaly_flag},
        )

    # Natural-language safety-concept extraction: a small, deterministic,
    # non-ML normalization layer that detects controlled high-confidence
    # emergency phrases (including typo/synonym variants) in the patient's
    # own free text and free-text-style associated symptoms, and maps each
    # one to an EXISTING backend/safety/rules.py alias phrase. It does not
    # decide is_emergency itself -- the phrases it surfaces are merged into
    # the symptom text handed to the unmodified evaluate_safety() below,
    # exactly as if the patient had typed that phrase directly. The engine's
    # own decision logic and rule configuration are untouched.
    safety_screening_symptoms = list(associated_symptoms)
    for phrase in extract_safety_concept_phrases(chief_complaint, associated_symptoms):
        if phrase not in safety_screening_symptoms:
            safety_screening_symptoms.append(phrase)

    triage_input = {
        "chiefComplaint": chief_complaint,
        "symptomsDuration": symptoms_duration,
        "associatedSymptoms": safety_screening_symptoms,
        "selectedRedFlags": selected_red_flags,
        "hasRedFlags": has_red_flags,
    }

    safety_decision = evaluate_safety(triage_input)
    is_emergency = safety_decision["isEmergency"]

    if is_emergency:
        recommended_acuity = "EMERGENCY"
        urgency_level = "IMMEDIATE_911_ER"
        recommended_setting_name = "Nearest Hospital Emergency Department or Call 911"
        clinical_rationale = (
            "Emergency Red Flags Detected: Symptoms such as chest pressure, acute severe "
            "shortness of breath, stroke signs, or loss of consciousness require immediate "
            "emergency medical evaluation. Do not delay seeking emergency services."
        )
        safety_disclaimer = (
            "EMERGENCY WARNING: Your reported symptoms indicate a potential life-threatening "
            "emergency. Call 911 or proceed immediately to the nearest Emergency Department. "
            "This tool is for care navigation only and NEVER replaces emergency medical care."
        )
        er_cost = 1800
        rec_cost = 1800
        potential_savings = 0
        er_wait = 0
        rec_wait = 0
        suitable_providers = []
    else:
        safety_disclaimer = (
            "SAFETY NOTICE: If symptoms worsen, or if you develop chest pain, shortness of breath, "
            "or severe pain, proceed to the nearest Emergency Room or call 911 immediately."
        )
        cc_lower = chief_complaint.lower()
        is_urgent = "cut" in cc_lower or "sprain" in cc_lower or "burn" in cc_lower
        is_pcp = (
            "checkup" in cc_lower
            or "medication review" in cc_lower
            or "follow-up" in cc_lower
            or "followup" in cc_lower
        )
        is_high_risk_patient = (
            patient_context is not None and patient_context.get("riskLevel") == "HIGH"
        )

        if is_urgent:
            recommended_acuity = "URGENT_CARE"
            urgency_level = "SAME_DAY_URGENT"
            recommended_setting_name = "Local Urgent Care Center"
            clinical_rationale = (
                f'Based on your reported complaint of "{chief_complaint}" without high-risk red flags, '
                "care can be safely delivered at a Same-day Urgent Care Center."
            )
            rec_cost = 180
            potential_savings = 1620
            rec_wait = 20
        elif is_pcp or is_high_risk_patient:
            recommended_acuity = "PRIMARY_CARE"
            urgency_level = "SCHEDULED_PCP"
            recommended_setting_name = "Primary Care Provider Clinic"
            clinical_rationale = (
                f'Based on your reported complaint of "{chief_complaint}", primary care follow-up is '
                "the recommended setting for ongoing management."
            )
            if is_high_risk_patient and not is_pcp:
                clinical_rationale += " (High historical risk profile indicates primary care coordination)."
            rec_cost = 120
            potential_savings = 1680
            rec_wait = 20
        else:
            recommended_acuity = "TELEHEALTH"
            urgency_level = "SAME_DAY_TELEHEALTH"
            recommended_setting_name = "24/7 Telehealth Nurse Line"
            clinical_rationale = (
                f'Based on your reported complaint of "{chief_complaint}" without high-risk red flags, '
                "care can be safely delivered via a 24/7 Virtual Telehealth session."
            )
            rec_cost = 45
            potential_savings = 1755
            rec_wait = 8

        er_cost = 1800
        er_wait = 180
        suitable_providers = provider_service.get_providers(
            provider_type=recommended_acuity, session=session
        )
        if not suitable_providers:
            suitable_providers = provider_service.get_providers(session=session)

    now = datetime.now(timezone.utc)

    repo = EncounterRepository(session)
    enc = TriageEncounter(
        member_id=member_id,
        user_id=user_id,
        session_id=session_id,
        chief_complaint=chief_complaint,
        symptoms_duration=symptoms_duration,
        associated_symptoms=associated_symptoms,
        selected_red_flags=selected_red_flags,
        has_red_flags=has_red_flags,
        is_emergency=is_emergency,
        recommended_acuity=recommended_acuity,
        urgency_level=urgency_level,
        recommended_setting_name=recommended_setting_name,
        clinical_rationale=clinical_rationale,
        safety_disclaimer=safety_disclaimer,
        triggered_rules=safety_decision["triggeredRules"],
        rule_set_version=safety_decision["ruleSetVersion"],
        created_at=now,
    )
    saved = repo.create_encounter(enc)
    if saved.id is None:
        raise TriagePersistenceError("Unable to record triage encounter")

    response_payload: dict[str, Any] = {
        "encounterId": saved.id,
        "sessionId": session_id,
        "isEmergencyRedFlag": is_emergency,
        "recommendedAcuity": recommended_acuity,
        "recommendedSettingName": recommended_setting_name,
        "urgencyLevel": urgency_level,
        "clinicalRationale": clinical_rationale,
        "safetyDisclaimer": safety_disclaimer,
        "estimatedCostComparison": {
            "erEstimatedCost": er_cost,
            "recommendedCost": rec_cost,
            "potentialSavings": potential_savings,
        },
        "estimatedWaitTimeComparison": {
            "erWaitMinutes": er_wait,
            "recommendedWaitMinutes": rec_wait,
        },
        "suitableProviders": suitable_providers,
        "triggeredRules": safety_decision["triggeredRules"],
        "ruleSetVersion": safety_decision["ruleSetVersion"],
    }
    if patient_context is not None:
        response_payload["patientContext"] = patient_context
    if proactive_rec is not None:
        response_payload["proactiveRecommendation"] = proactive_rec

    return response_payload


def process_triage_request(
    data: dict[str, Any],
    session: Session | None = None,
    *,
    allow_member_linkage: bool = False,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Process a symptom triage request via the deterministic safety engine and persist encounter.

    ``allow_member_linkage`` gates whether a supplied patientId is resolved
    against CMS Member/ML data; it must only be True for an authenticated
    PAYER caller. Defaults to False (no member linkage) so any caller that
    doesn't explicitly authorize it gets the safe, anonymous-equivalent
    behavior. The deterministic safety-engine screening is identical either
    way and is never influenced by this flag.

    ``user_id`` is identity/persistence linkage only -- which login identity
    (if any) created this encounter, so it can later be retrieved via
    GET /api/navigation/my-history. It is independent of, and has no effect
    on, ``allow_member_linkage`` or the CMS Member/ML enrichment behavior:
    it should be set for any authenticated caller (PATIENT or PAYER), not
    only when member linkage is also allowed.
    """
    validate_triage_request(data)
    if session is not None:
        return _execute_triage_request(
            session, data, allow_member_linkage=allow_member_linkage, user_id=user_id
        )

    with session_scope() as sess:
        return _execute_triage_request(
            sess, data, allow_member_linkage=allow_member_linkage, user_id=user_id
        )
