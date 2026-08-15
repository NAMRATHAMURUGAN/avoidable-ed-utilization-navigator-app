"""Public API routes for navigation actions and persistent interaction history."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, jsonify, request

from backend.database import session_scope
from backend.models.encounter import NavigationAction
from backend.repositories.encounter_repository import EncounterRepository
from backend.repositories.member_repository import MemberRepository

navigation_blueprint = Blueprint("navigation", __name__, url_prefix="/api")


@navigation_blueprint.post("/navigation/action", strict_slashes=False)
def record_navigation_action():
    """POST /api/navigation/action - Record patient navigation action / selection."""
    if request.content_length and not request.is_json:
        return jsonify({"error": "Content-Type must be application/json"}), 400

    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Missing JSON request payload"}), 400

    action_type = str(data.get("actionType", "") or "").strip()
    if not action_type:
        return jsonify({"error": "actionType is required"}), 400

    encounter_id_raw = data.get("encounterId")
    encounter_id: int | None = None
    if encounter_id_raw is not None:
        try:
            encounter_id = int(encounter_id_raw)
        except (TypeError, ValueError):
            encounter_id = None

    patient_id_raw = data.get("patientId") or data.get("member_id")
    member_id: int | None = None

    now = datetime.now(timezone.utc)
    action_id: int | None = None

    with session_scope() as session:
        # Resolve member_id if patientId string/bene_id is passed
        if patient_id_raw is not None:
            m_repo = MemberRepository(session)
            member_res = m_repo.get_combined_result_by_id_or_bene(str(patient_id_raw))
            if member_res is not None:
                member_id = member_res.member.id

        e_repo = EncounterRepository(session)

        # If encounter_id was not directly supplied, try resolving via session_id
        session_id = data.get("sessionId")
        if encounter_id is None and session_id:
            enc = e_repo.get_encounter_by_session_id(str(session_id))
            if enc is not None:
                encounter_id = enc.id
                if member_id is None and enc.member_id is not None:
                    member_id = enc.member_id

        action = NavigationAction(
            encounter_id=encounter_id,
            member_id=member_id,
            action_type=action_type,
            selected_provider_id=data.get("selectedProviderId"),
            selected_acuity=data.get("selectedAcuity"),
            action_details=data.get("actionDetails") if isinstance(data.get("actionDetails"), dict) else None,
            recorded_at=now,
        )
        saved_action = e_repo.create_action(action)
        action_id = saved_action.id

    return jsonify({
        "status": "recorded",
        "actionId": action_id,
        "recordedAt": now.isoformat(),
    })


@navigation_blueprint.get("/patients/<patient_id>/history", strict_slashes=False)
def get_patient_history(patient_id: str):
    """GET /api/patients/<id>/history - Return persistent interaction and triage history."""
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


@navigation_blueprint.get("/navigation/session/<session_id>", strict_slashes=False)
def get_session_history(session_id: str):
    """GET /api/navigation/session/<session_id> - Return triage and action history for a session."""
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

