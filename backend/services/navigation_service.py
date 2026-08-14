"""Rule-based care navigation recommendations.

This module is deliberately independent of Flask, SQLAlchemy, and model-loading
code.  Callers provide already retrieved member, utilization, prediction, and
anomaly dictionaries and receive a JSON-ready recommendation dictionary.
"""

from __future__ import annotations

from typing import Any, Mapping


class CareNavigationService:
    """Generate an initial care-navigation recommendation from member signals."""

    def generate_recommendation(
        self,
        member_data: Mapping[str, Any],
        utilization_data: Mapping[str, Any],
        ml_data: Mapping[str, Any],
        anomaly_data: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return a recommendation based on anomaly, risk, and ED utilization.

        Parameters are intentionally plain mappings so routes or orchestrators
        can supply database records and ML responses without this service being
        coupled to either implementation.
        """
        anomaly_flag = self._as_flag(anomaly_data.get("anomaly_flag", 0))
        predicted_probability = self._as_float(
            ml_data.get(
                "predicted_probability",
                ml_data.get("high_utilization_probability", 0.0),
            )
        )
        ed_visit_count = self._as_float(utilization_data.get("ed_visit_count", 0))

        if anomaly_flag:
            recommended_action = "care_management_followup"
            urgency_level = "high"
        elif predicted_probability > 0.7:
            recommended_action = "urgent_care"
            urgency_level = "medium"
        elif ed_visit_count > 2:
            recommended_action = "primary_care"
            urgency_level = "medium"
        else:
            recommended_action = "telehealth"
            urgency_level = "low"

        return {
            "recommended_action": recommended_action,
            "urgency_level": urgency_level,
            "confidence": self._determine_confidence(
                anomaly_flag=anomaly_flag,
                predicted_probability=predicted_probability,
                ed_visit_count=ed_visit_count,
            ),
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
        """Return a bounded confidence score for the rule-based recommendation."""
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
            reasoning.append("Anomaly detection flagged an unusual utilization pattern.")
        elif predicted_probability > 0.7:
            reasoning.append(
                "The high-utilization prediction probability "
                f"is {predicted_probability:.2f}, above the 0.70 navigation rule."
            )
        elif ed_visit_count > 2:
            reasoning.append(
                f"The member has {ed_visit_count:g} ED visits, above the threshold of 2."
            )
        else:
            reasoning.append("No high-priority utilization signal met the current rules.")

        reasoning.append(f"Recommended action: {recommended_action}.")
        return reasoning

    @staticmethod
    def _as_float(value: Any) -> float:
        """Convert an input value to a non-negative float, defaulting safely to zero."""
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _as_flag(value: Any) -> bool:
        """Interpret common database/JSON flag representations as booleans."""
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes"}
        return bool(value)
