"""API routes for navigation actions and persistent interaction history.

GET /patients/<id>/history exposes a CMS member's full triage/navigation
history and is PAYER-only. POST /navigation/action and the session-history
lookup remain reachable anonymously to preserve the existing anonymous
patient navigation flow; only the member-linking branch of the action
endpoint requires PAYER. Provider existence is validated against
ProviderRepository; no mock provider data is seeded on this request path.
This module also exposes live urgent-care facility discovery, ORS-backed
driving directions, and database-backed navigation recommendations.
GET /navigation/members/<id>/recommendations exposes per-member CMS
utilization/ML-derived detail and is PAYER-only, matching every other
CMS-Member-keyed endpoint in this application.

GET /navigation/my-history is login-required (any role) and returns only
the authenticated caller's own encounters/actions, resolved from their
session identity -- never from a caller-supplied identifier. This is a
separate, identity-scoped persistence mechanism from the CMS-Member-based
/patients/<id>/history above; it does not use or alter member_id linkage.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from flask import Blueprint, g, jsonify, request

from backend.auth.decorators import login_required, role_required
from backend.database import session_scope
from backend.models.encounter import NavigationAction
from backend.repositories.encounter_repository import EncounterRepository
from backend.repositories.member_repository import MemberRepository
from backend.repositories.model_run_repository import ModelRunRepository
from backend.repositories.provider_repository import ProviderRepository
from backend.routes.patients import validate_patient_identifier
from backend.services.auth_service import get_current_user
from backend.services.navigation_service import CareNavigationService
from backend.services.urgent_care_map_service import (
    UrgentCareMapError,
    calculate_urgent_care_route,
    discover_urgent_care_facilities,
)

navigation_blueprint = Blueprint("navigation", __name__, url_prefix="/api")
VALID_ACTION_TYPES = {"PROVIDER_SELECTED", "APPOINTMENT_BOOKED"}
VALID_ACUITIES = {"EMERGENCY", "URGENT_CARE", "PRIMARY_CARE", "TELEHEALTH", "RETAIL_CLINIC"}
MAX_IDENTIFIER_LENGTH = 64
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")


def _validate_identifier(value: Any, field: str) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > MAX_IDENTIFIER_LENGTH:
        return f"{field} must be a nonblank string up to {MAX_IDENTIFIER_LENGTH} characters"
    if not _IDENTIFIER_PATTERN.fullmatch(value.strip()):
        return f"{field} contains invalid characters"
    return None


def _positive_integer(value: Any, field: str) -> tuple[int | None, str | None]:
    if isinstance(value, bool):
        return None, f"{field} must be a positive integer"
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None, f"{field} must be a positive integer"
    if parsed <= 0 or str(value).strip() not in {str(parsed), f"+{parsed}"}:
        return None, f"{field} must be a positive integer"
    return parsed, None


@navigation_blueprint.get("/navigation/urgent-care/facilities", strict_slashes=False)
def get_urgent_care_facilities():
    """Return live OSM facilities explicitly identified as urgent care."""
    try:
        response = discover_urgent_care_facilities(
            request.args.get("latitude"),
            request.args.get("longitude"),
            request.args.get("radiusMeters"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except UrgentCareMapError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    return jsonify(response)


@navigation_blueprint.post("/navigation/urgent-care/route", strict_slashes=False)
def get_urgent_care_route():
    """Return a live ORS driving route for a selected urgent-care facility."""
    if request.content_length and not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Missing JSON request payload"}), 400
    origin = data.get("origin")
    destination = data.get("destination")
    if not isinstance(origin, dict) or not isinstance(destination, dict):
        return jsonify({"error": "origin and destination are required"}), 400
    try:
        response = calculate_urgent_care_route(origin, destination)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except UrgentCareMapError as exc:
        return jsonify({"error": str(exc)}), exc.status_code
    return jsonify(response)


@navigation_blueprint.get("/navigation/members/<member_id>/recommendations", strict_slashes=False)
@role_required("PAYER")
def get_member_navigation_recommendations(member_id: str):
    """GET care-navigation suggestions based on available historical member data."""
    with session_scope() as session:
        member_repo = MemberRepository(session)
        run_repo = ModelRunRepository(session)
        xgb_run = run_repo.get_latest("xgboost")
        anomaly_run = run_repo.get_latest("isolation_forest")
        result = member_repo.get_combined_result_by_id_or_bene(
            member_id,
            xgb_model_run_id=xgb_run.model_run_id if xgb_run else None,
            anomaly_model_run_id=anomaly_run.model_run_id if anomaly_run else None,
            # A missing run must mean an unavailable signal.  It must not fall
            # back to an unscoped historical output from another/older run.
            include_xgboost=xgb_run is not None,
            include_anomaly=anomaly_run is not None,
        )
        if result is None:
            return jsonify({"error": "Member not found"}), 404

        member = result.member
        snapshot = result.utilization_snapshot
        prediction = result.xgboost_prediction
        anomaly = result.anomaly_result
        response = CareNavigationService().generate_recommendation(
            member_data={
                "member_id": member.id,
                "age": member.age,
                "chronic_condition_count": member.chronic_condition_count,
            },
            utilization_data={
                "ed_visit_count": snapshot.ed_visit_count if snapshot else None,
                "inpatient_visit_count": snapshot.inpatient_visit_count if snapshot else None,
                "outpatient_visit_count": snapshot.outpatient_visit_count if snapshot else None,
                "provider_count": snapshot.provider_count if snapshot else None,
            },
            ml_data={
                "high_utilization_probability": (
                    prediction.high_utilization_probability if prediction else None
                )
            },
            anomaly_data={"anomaly_flag": anomaly.anomaly_flag if anomaly else None},
        )
    return jsonify(response)


@navigation_blueprint.post("/navigation/action", strict_slashes=False)
def record_navigation_action():
    """POST /api/navigation/action - Record patient navigation action / selection."""
    if request.content_length and not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400

    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Missing JSON request payload"}), 400
    if not isinstance(data, dict):
        return jsonify({"error": "JSON request payload must be an object"}), 400

    action_type_raw = data.get("actionType")
    if not isinstance(action_type_raw, str) or not action_type_raw.strip():
        return jsonify({"error": "actionType is required"}), 400
    action_type = action_type_raw.strip()
    if action_type not in VALID_ACTION_TYPES:
        return jsonify({"error": "actionType is not supported"}), 400

    encounter_id_raw = data.get("encounterId")
    encounter_id: int | None = None
    if encounter_id_raw is not None:
        encounter_id, error = _positive_integer(encounter_id_raw, "encounterId")
        if error:
            return jsonify({"error": error}), 400

    supplied_patient_ids = [
        data[field] for field in ("patientId", "member_id") if field in data and data[field] is not None
    ]
    if len(supplied_patient_ids) > 1 and str(supplied_patient_ids[0]) != str(supplied_patient_ids[1]):
        return jsonify({"error": "patientId and member_id must match when both are supplied"}), 400
    patient_id_raw = supplied_patient_ids[0] if supplied_patient_ids else None
    member_id: int | None = None
    if patient_id_raw is not None:
        if isinstance(patient_id_raw, bool) or not isinstance(patient_id_raw, (str, int)):
            return jsonify({"error": "patientId must be a string or integer identifier"}), 400
        error = validate_patient_identifier(str(patient_id_raw).strip())
        if error:
            return jsonify({"error": error}), 400

    session_id = data.get("sessionId")
    if session_id is not None:
        error = _validate_identifier(session_id, "sessionId")
        if error:
            return jsonify({"error": error}), 400

    selected_provider_id = data.get("selectedProviderId")
    if selected_provider_id is not None:
        error = _validate_identifier(selected_provider_id, "selectedProviderId")
        if error:
            return jsonify({"error": error}), 400

    selected_acuity = data.get("selectedAcuity")
    if selected_acuity is not None and selected_acuity not in VALID_ACUITIES:
        return jsonify({"error": "selectedAcuity is not supported"}), 400

    action_details = data.get("actionDetails")
    if action_details is not None and not isinstance(action_details, dict):
        return jsonify({"error": "actionDetails must be an object"}), 400

    # Attaching an action directly to an arbitrary CMS member requires an
    # authenticated PAYER caller; there is no User<->Member mapping to
    # authorize this for an unauthenticated or PATIENT caller. This does not
    # affect inheriting member_id from an already-linked encounter below,
    # since that link can only exist if a PAYER established it during triage.
    current_user = get_current_user()
    is_payer = current_user is not None and current_user.role == "PAYER"

    now = datetime.now(timezone.utc)
    action_id: int | None = None

    with session_scope() as session:
        if patient_id_raw is not None and is_payer:
            m_repo = MemberRepository(session)
            member_res = m_repo.get_combined_result_by_id_or_bene(str(patient_id_raw))
            if member_res is None:
                return jsonify({"error": "Patient not found"}), 404
            member_id = member_res.member.id

        e_repo = EncounterRepository(session)
        encounter = None
        if encounter_id is not None:
            encounter = e_repo.get_encounter(encounter_id)
            if encounter is None:
                return jsonify({"error": "Encounter not found"}), 404

        # If encounter_id was not directly supplied, resolve and validate session_id.
        if encounter_id is None and session_id:
            enc = e_repo.get_encounter_by_session_id(str(session_id))
            if enc is None:
                return jsonify({"error": "Session not found"}), 404
            encounter = enc
            encounter_id = enc.id
        elif encounter is not None and session_id and encounter.session_id != session_id:
            return jsonify({"error": "sessionId does not match encounterId"}), 400

        if encounter is not None:
            if member_id is not None and encounter.member_id != member_id:
                return jsonify({"error": "encounterId does not belong to patientId"}), 400
            if member_id is None and encounter.member_id is not None:
                member_id = encounter.member_id

        if encounter_id is None and member_id is None:
            return jsonify({"error": "A valid encounterId, sessionId, or patientId is required"}), 400

        if selected_provider_id is not None:
            provider_repo = ProviderRepository(session)
            if provider_repo.get_by_id(selected_provider_id) is None:
                return jsonify({"error": "Provider not found"}), 404

        action = NavigationAction(
            encounter_id=encounter_id,
            member_id=member_id,
            # Identity/persistence linkage only, independent of the
            # PAYER-only member_id linkage above: any authenticated caller's
            # own action is tagged with their user id. Anonymous callers
            # continue to record actions exactly as before (user_id stays
            # None), which is unaffected and unauthorized-by-default.
            user_id=current_user.id if current_user is not None else None,
            action_type=action_type,
            selected_provider_id=selected_provider_id,
            selected_acuity=selected_acuity,
            action_details=action_details,
            recorded_at=now,
        )
        saved_action = e_repo.create_action(action)
        if saved_action.id is None:
            return jsonify({"error": "Unable to record navigation action"}), 503
        action_id = saved_action.id

    return jsonify({
        "status": "recorded",
        "actionId": action_id,
        "recordedAt": now.isoformat(),
    })


@navigation_blueprint.get("/patients/<patient_id>/history", strict_slashes=False)
@role_required("PAYER")
def get_patient_history(patient_id: str):
    """GET /api/patients/<id>/history - Return persistent interaction and triage history."""
    error = validate_patient_identifier(patient_id)
    if error:
        return jsonify({"error": error}), 400
    with session_scope() as session:
        m_repo = MemberRepository(session)

        member_res = m_repo.get_combined_result_by_id_or_bene(patient_id)
        if member_res is None:
            return jsonify({"error": "Patient not found"}), 404

        member_id = member_res.member.id
        e_repo = EncounterRepository(session)

        encounters = e_repo.get_member_encounters(member_id)
        actions = e_repo.get_member_actions(member_id)

        encounters_json = [
            {
                "encounterId": enc.id,
                "sessionId": enc.session_id,
                "chiefComplaint": enc.chief_complaint,
                "symptomsDuration": enc.symptoms_duration,
                "associatedSymptoms": enc.associated_symptoms or [],
                "isEmergency": enc.is_emergency,
                "recommendedAcuity": enc.recommended_acuity,
                "urgencyLevel": enc.urgency_level,
                "recommendedSettingName": enc.recommended_setting_name,
                "createdAt": enc.created_at.isoformat() if enc.created_at else None,
            }
            for enc in encounters
        ]

        actions_json = [
            {
                "actionId": act.id,
                "encounterId": act.encounter_id,
                "actionType": act.action_type,
                "selectedProviderId": act.selected_provider_id,
                "selectedAcuity": act.selected_acuity,
                "actionDetails": act.action_details or {},
                "recordedAt": act.recorded_at.isoformat() if act.recorded_at else None,
            }
            for act in actions
        ]

        return jsonify({
            "patientId": str(member_res.member.id),
            "beneficiaryId": member_res.member.bene_id,
            "totalEncounters": len(encounters_json),
            "totalActions": len(actions_json),
            "encounters": encounters_json,
            "actions": actions_json,
        })


@navigation_blueprint.get("/navigation/my-history", strict_slashes=False)
@login_required
def get_my_history():
    """GET /api/navigation/my-history - Return the authenticated caller's own
    persistent triage/navigation history.

    Scoped entirely to the session's own identity (``g.current_user``, set by
    @login_required) -- there is no request parameter for a user id, so a
    caller can never supply one to retrieve another user's history. This is
    independent of the CMS-Member-based /patients/<id>/history above.
    """
    with session_scope() as session:
        e_repo = EncounterRepository(session)

        encounters = e_repo.get_user_encounters(g.current_user.id)
        actions = e_repo.get_user_actions(g.current_user.id)

        encounters_json = [
            {
                "encounterId": enc.id,
                "sessionId": enc.session_id,
                "chiefComplaint": enc.chief_complaint,
                "symptomsDuration": enc.symptoms_duration,
                "associatedSymptoms": enc.associated_symptoms or [],
                "isEmergency": enc.is_emergency,
                "recommendedAcuity": enc.recommended_acuity,
                "urgencyLevel": enc.urgency_level,
                "recommendedSettingName": enc.recommended_setting_name,
                "createdAt": enc.created_at.isoformat() if enc.created_at else None,
            }
            for enc in encounters
        ]

        actions_json = [
            {
                "actionId": act.id,
                "encounterId": act.encounter_id,
                "actionType": act.action_type,
                "selectedProviderId": act.selected_provider_id,
                "selectedAcuity": act.selected_acuity,
                "actionDetails": act.action_details or {},
                "recordedAt": act.recorded_at.isoformat() if act.recorded_at else None,
            }
            for act in actions
        ]

        return jsonify({
            "totalEncounters": len(encounters_json),
            "totalActions": len(actions_json),
            "encounters": encounters_json,
            "actions": actions_json,
        })


@navigation_blueprint.get("/navigation/session/<session_id>", strict_slashes=False)
def get_session_history(session_id: str):
    """GET /api/navigation/session/<session_id> - Return triage and action history for a session."""
    error = _validate_identifier(session_id, "sessionId")
    if error:
        return jsonify({"error": error}), 400
    with session_scope() as session:
        e_repo = EncounterRepository(session)
        encounter = e_repo.get_encounter_by_session_id(session_id)
        if encounter is None:
            return jsonify({"error": "Session not found"}), 404

        actions = e_repo.get_encounter_actions(encounter.id)

        return jsonify({
            "sessionId": session_id,
            "encounter": {
                "encounterId": encounter.id,
                "memberId": encounter.member_id,
                "chiefComplaint": encounter.chief_complaint,
                "symptomsDuration": encounter.symptoms_duration,
                "associatedSymptoms": encounter.associated_symptoms or [],
                "isEmergency": encounter.is_emergency,
                "recommendedAcuity": encounter.recommended_acuity,
                "urgencyLevel": encounter.urgency_level,
                "recommendedSettingName": encounter.recommended_setting_name,
                "createdAt": encounter.created_at.isoformat() if encounter.created_at else None,
            },
            "totalActions": len(actions),
            "actions": [
                {
                    "actionId": act.id,
                    "encounterId": act.encounter_id,
                    "actionType": act.action_type,
                    "selectedProviderId": act.selected_provider_id,
                    "selectedAcuity": act.selected_acuity,
                    "actionDetails": act.action_details or {},
                    "recordedAt": act.recorded_at.isoformat() if act.recorded_at else None,
                }
                for act in actions
            ],
        })
