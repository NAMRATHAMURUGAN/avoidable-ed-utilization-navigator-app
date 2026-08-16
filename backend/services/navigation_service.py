"""Informational, non-clinical care-navigation recommendations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


class CareNavigationService:
    """Translate available member utilization data into coordination suggestions.

    The rules deliberately use only historical administrative and model data. They
    never determine medical necessity, emergency necessity, ED avoidability, or
    clinical deterioration.
    """

    def generate_recommendation(
        self,
        member_data: Mapping[str, Any],
        utilization_data: Mapping[str, Any],
        ml_data: Mapping[str, Any],
        anomaly_data: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return a JSON-ready, informational navigation recommendation."""
        member_id = self._first(member_data, "member_id", "id", "bene_id", "BENE_ID")
        age = self._number(member_data.get("age"))
        chronic_conditions = self._number(
            self._first(member_data, "chronic_condition_count", "chronicConditionCount")
        )
        ed_visits = self._number(self._first(utilization_data, "ed_visit_count", "edVisitCount12m"))
        inpatient_visits = self._number(
            self._first(utilization_data, "inpatient_visit_count", "inpatientVisitCount")
        )
        outpatient_visits = self._number(
            self._first(utilization_data, "outpatient_visit_count", "outpatientVisitCount")
        )
        provider_count = self._number(self._first(utilization_data, "provider_count", "providerCount"))
        probability = self._probability(
            self._first(ml_data, "high_utilization_probability", "predicted_probability")
        )
        anomaly_flag = self._flag(anomaly_data.get("anomaly_flag"))

        priority = self._priority(
            ed_visits=ed_visits,
            inpatient_visits=inpatient_visits,
            probability=probability,
            anomaly_flag=anomaly_flag,
        )
        recommendations = self._recommendations(
            age=age,
            chronic_conditions=chronic_conditions,
            ed_visits=ed_visits,
            inpatient_visits=inpatient_visits,
            outpatient_visits=outpatient_visits,
            provider_count=provider_count,
            probability=probability,
            anomaly_flag=anomaly_flag,
            priority=priority,
        )

        return {
            "member_id": str(member_id) if member_id is not None else None,
            "navigation_priority": priority,
            "recommendations": recommendations,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _priority(
        *,
        ed_visits: float | None,
        inpatient_visits: float | None,
        probability: float | None,
        anomaly_flag: bool | None,
    ) -> str:
        """Set priority from observed utilization characteristics only."""
        if anomaly_flag is True or (probability is not None and probability > 0.70):
            return "HIGH"
        if (
            (ed_visits is not None and ed_visits > 2)
            or (inpatient_visits is not None and inpatient_visits > 0)
            or (probability is not None and probability > 0.40)
        ):
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _recommendations(
        *,
        age: float | None,
        chronic_conditions: float | None,
        ed_visits: float | None,
        inpatient_visits: float | None,
        outpatient_visits: float | None,
        provider_count: float | None,
        probability: float | None,
        anomaly_flag: bool | None,
        priority: str,
    ) -> list[dict[str, str]]:
        recommendations: list[dict[str, str]] = []

        def add(category: str, recommendation: str, reason: str) -> None:
            recommendations.append(
                {"category": category, "recommendation": recommendation, "reason": reason}
            )

        if priority == "HIGH":
            high_reason = (
                "An unusual historical utilization pattern was recorded."
                if anomaly_flag is True
                else "The historical high-utilization-pattern score is above the coordination threshold."
            )
            add("Care Coordination", "Case management referral", high_reason)
            add("Care Coordination", "Care navigator outreach", high_reason)
        elif priority == "MEDIUM":
            if ed_visits is not None and ed_visits > 2:
                reason = f"{ed_visits:g} historical ED visits were recorded in the available snapshot."
            elif inpatient_visits is not None and inpatient_visits > 0:
                reason = f"{inpatient_visits:g} inpatient visit(s) were recorded in the available snapshot."
            else:
                reason = "The historical high-utilization-pattern score is above the monitoring threshold."
            add("Primary Care Follow-up", "Schedule PCP follow-up", reason)

        if chronic_conditions is not None and chronic_conditions >= 2:
            add(
                "Chronic Disease Management",
                "Chronic condition management review",
                f"The member record lists {chronic_conditions:g} chronic condition(s).",
            )
            add(
                "Chronic Disease Management",
                "Medication review",
                "A medication review can support coordination across documented chronic conditions.",
            )

        if ed_visits is not None and ed_visits > 0:
            add(
                "Utilization Monitoring",
                "Monitor utilization trends",
                f"{ed_visits:g} historical ED visit(s) are present in the available utilization snapshot.",
            )
        if provider_count is not None and provider_count >= 3:
            add(
                "Utilization Monitoring",
                "Review provider fragmentation",
                f"The utilization snapshot includes {provider_count:g} provider(s).",
            )
        if outpatient_visits is not None and outpatient_visits > 0:
            add(
                "Utilization Monitoring",
                "Review care continuity",
                f"{outpatient_visits:g} outpatient visit(s) are present in the available utilization snapshot.",
            )

        if age is not None and age >= 65:
            add(
                "Primary Care Follow-up",
                "Preventive wellness review",
                "Age is available in the member record; a preventive wellness review is a care-coordination option.",
            )

        if not recommendations:
            add(
                "Patient Education",
                "Care-access education",
                "Available utilization data does not meet a higher navigation-priority rule.",
            )
        else:
            add(
                "Patient Education",
                "Follow-up adherence reminders",
                "Reminder support can help with completion of selected care-navigation follow-up.",
            )
        return recommendations

    @staticmethod
    def _first(data: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            value = data.get(key)
            if value is not None:
                return value
        return None

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number >= 0 else None

    @classmethod
    def _probability(cls, value: Any) -> float | None:
        number = cls._number(value)
        return number if number is not None and number <= 1 else None

    @staticmethod
    def _flag(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes"}:
                return True
            if normalized in {"false", "0", "no"}:
                return False
        return None
