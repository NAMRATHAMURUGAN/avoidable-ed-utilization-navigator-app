"""Tests for informational care-navigation recommendations."""

from backend.services.navigation_service import CareNavigationService


def recommendation(**overrides):
    data = {
        "member_data": {"member_id": "member-1", "age": 68, "chronic_condition_count": 1},
        "utilization_data": {
            "ed_visit_count": 1,
            "inpatient_visit_count": 0,
            "outpatient_visit_count": 1,
            "provider_count": 1,
        },
        "ml_data": {"high_utilization_probability": 0.20},
        "anomaly_data": {"anomaly_flag": False},
    }
    data.update(overrides)
    return CareNavigationService().generate_recommendation(**data)


def test_low_priority_member_receives_informational_suggestions():
    result = recommendation()

    assert result["navigation_priority"] == "LOW"
    assert result["member_id"] == "member-1"
    assert any(item["category"] == "Patient Education" for item in result["recommendations"])


def test_medium_priority_member_with_observed_utilization_gets_pcp_follow_up():
    result = recommendation(
        utilization_data={
            "ed_visit_count": 3,
            "inpatient_visit_count": 0,
            "outpatient_visit_count": 1,
            "provider_count": 1,
        }
    )

    assert result["navigation_priority"] == "MEDIUM"
    assert any(
        item["recommendation"] == "Schedule PCP follow-up" for item in result["recommendations"]
    )


def test_high_priority_member_gets_case_management_referral():
    result = recommendation(ml_data={"high_utilization_probability": 0.85})

    assert result["navigation_priority"] == "HIGH"
    assert any(
        item["recommendation"] == "Case management referral" for item in result["recommendations"]
    )


def test_anomaly_member_is_high_priority_without_clinical_conclusion():
    result = recommendation(anomaly_data={"anomaly_flag": True})

    assert result["navigation_priority"] == "HIGH"
    assert any(
        item["recommendation"] == "Care navigator outreach" for item in result["recommendations"]
    )


def test_missing_data_is_handled_without_inventing_utilization():
    result = recommendation(
        member_data={"member_id": "member-missing"},
        utilization_data={},
        ml_data={},
        anomaly_data={},
    )

    assert result["navigation_priority"] == "LOW"
    assert result["recommendations"] == [
        {
            "category": "Patient Education",
            "recommendation": "Care-access education",
            "reason": "Available utilization data does not meet a higher navigation-priority rule.",
        }
    ]
