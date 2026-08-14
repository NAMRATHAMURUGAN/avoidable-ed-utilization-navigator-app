"""Rule-based care navigation recommendations.

This module is deliberately independent of Flask, SQLAlchemy, and model-loading
code.  Callers provide already retrieved member, utilization, prediction, and
anomaly dictionaries and receive a JSON-ready recommendation dictionary.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


class CareNavigationService:
    """Generate proactive, historical-utilization navigation recommendations.

    This service is not a clinical triage or emergency-decision component.  Its
    output can help prioritize care-navigation follow-up only after an
    authoritative safety screen has completed elsewhere.
    """

    def generate_recommendation(
        self,
        member_data: Mapping[str, Any],
        utilization_data: Mapping[str, Any],
        ml_data: Mapping[str, Any],
        anomaly_data: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return a historical-utilization navigation recommendation.

        Parameters are intentionally plain mappings so routes or orchestrators
        can supply database records and ML responses without this service being
        coupled to either implementation.
        """
        member_id = member_data.get("BENE_ID", member_data.get("bene_id"))
        predicted_probability = self._optional_probability(
            ml_data.get("predicted_probability", ml_data.get("high_utilization_probability"))
        )
        ed_visit_count = self._optional_nonnegative_number(utilization_data.get("ed_visit_count"))
        anomaly_flag = self._optional_flag(anomaly_data.get("anomaly_flag"))

        missing_fields = self._missing_history_fields(
            member_id=member_id,
            ed_visit_count=ed_visit_count,
            predicted_probability=predicted_probability,
            anomaly_flag=anomaly_flag,
        )
        if missing_fields:
            return self._insufficient_history_result(missing_fields)

        # Values are known after the explicit availability check above.
        assert ed_visit_count is not None
        assert predicted_probability is not None
        assert anomaly_flag is not None

        if anomaly_flag:
            recommended_action = "care_management_followup"
            priority_level = "high"
        elif predicted_probability > 0.7:
            recommended_action = "care_management_followup"
            priority_level = "medium"
        elif ed_visit_count > 2:
            recommended_action = "primary_care"
            priority_level = "medium"
        else:
            recommended_action = "telehealth"
            priority_level = "low"

        return {
            "recommended_action": recommended_action,
            "priority_level": priority_level,
            "confidence": self._determine_confidence(
                anomaly_flag=anomaly_flag,
                predicted_probability=predicted_probability,
                ed_visit_count=ed_visit_count,
            ),
            "confidence_interpretation": (
                "Rule-engine confidence for historical-utilization navigation "
                "prioritization; not a clinical probability or emergency-risk score."
            ),
            "proactive_interpretation": (
                "This is a proactive, historical-utilization-based care-navigation "
                "recommendation. It is not a clinical triage, medical-necessity, "
                "ED-avoidability, or emergency-status decision."
            ),
            "data_availability": {
                "status": "available",
                "insufficient_history": False,
                "missing_fields": [],
            },
            "reasoning": self._build_reasoning(
                member_data=member_data,
                anomaly_flag=anomaly_flag,
                predicted_probability=predicted_probability,
                ed_visit_count=ed_visit_count,
                recommended_action=recommended_action,
            ),
        }

    def _determine_confidence(
        self,
        *,
        anomaly_flag: bool,
        predicted_probability: float,
        ed_visit_count: float,
    ) -> float:
        """Return a bounded rule-engine confidence, never a clinical probability."""
        if anomaly_flag:
            return 0.90
        if predicted_probability > 0.7:
            return round(min(0.90, max(0.70, predicted_probability)), 2)
        if ed_visit_count > 2:
            return 0.70
        return 0.60

    def _build_reasoning(
        self,
        *,
        member_data: Mapping[str, Any],
        anomaly_flag: bool,
        predicted_probability: float,
        ed_visit_count: float,
        recommended_action: str,
    ) -> list[str]:
        """Build human-readable, non-clinical reasons for the selected action."""
        reasoning: list[str] = []
        member_id = member_data.get("BENE_ID", member_data.get("bene_id"))
        if member_id is not None:
            reasoning.append(f"Recommendation generated for member {member_id}.")

        if anomaly_flag:
            reasoning.append(
                "Isolation Forest flagged an unusual observed utilization pattern; "
                "this is not a clinical-risk, medical-necessity, or ED-avoidability finding."
            )
        elif predicted_probability > 0.7:
            reasoning.append(
                "The historical high-utilization-pattern probability "
                f"is {predicted_probability:.2f}, above the 0.70 navigation rule; "
                "it is not a current clinical-risk or emergency-status score."
            )
        elif ed_visit_count > 2:
            reasoning.append(
                f"The member has {ed_visit_count:g} ED visits, above the threshold of 2."
            )
        else:
            reasoning.append("No high-priority utilization signal met the current rules.")

        reasoning.append(
            f"Proactive care-navigation action: {recommended_action}; this is not a "
            "medically required care-setting decision."
        )
        return reasoning

    @staticmethod
    def _optional_nonnegative_number(value: Any) -> float | None:
        """Return a valid historical numeric value without inventing a default."""
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed) or parsed < 0:
            return None
        return parsed

    @classmethod
    def _optional_probability(cls, value: Any) -> float | None:
        """Return a valid historical pattern probability in the model's range."""
        parsed = cls._optional_nonnegative_number(value)
        if parsed is None or parsed > 1:
            return None
        return parsed

    @staticmethod
    def _optional_flag(value: Any) -> bool | None:
        """Interpret known flag representations without treating missing data as false."""
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes"}:
                return True
            if normalized in {"0", "false", "no"}:
                return False
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in {0, 1}:
            return bool(value)
        return None

    @staticmethod
    def _missing_history_fields(
        *,
        member_id: Any,
        ed_visit_count: float | None,
        predicted_probability: float | None,
        anomaly_flag: bool | None,
    ) -> list[str]:
        """Identify required historical signals that were unavailable or invalid."""
        missing_fields: list[str] = []
        if member_id is None or not str(member_id).strip():
            missing_fields.append("member_id")
        if ed_visit_count is None:
            missing_fields.append("ed_visit_count")
        if predicted_probability is None:
            missing_fields.append("high_utilization_probability")
        if anomaly_flag is None:
            missing_fields.append("anomaly_flag")
        return missing_fields

    @staticmethod
    def _insufficient_history_result(missing_fields: list[str]) -> dict[str, Any]:
        """Return a safe result when proactive-navigation history is incomplete."""
        return {
            "recommended_action": "care_management_followup",
            "priority_level": "unavailable",
            "confidence": None,
            "confidence_interpretation": (
                "No rule-engine confidence is available because required historical "
                "utilization data is incomplete."
            ),
            "proactive_interpretation": (
                "Historical data is insufficient for proactive care-navigation "
                "prioritization. This is not a clinical triage or emergency-status decision."
            ),
            "data_availability": {
                "status": "insufficient_history",
                "insufficient_history": True,
                "missing_fields": missing_fields,
            },
            "reasoning": [
                "Required historical utilization or model data is unavailable or invalid.",
                "No utilization values, clinical-risk conclusion, or lower-acuity care conclusion was inferred.",
                "A care-management follow-up is suggested only to resolve missing historical context.",
            ],
        }
