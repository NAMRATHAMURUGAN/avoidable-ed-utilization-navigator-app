"""Validation-focused tests for the ML-output ingestion boundary."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from backend import ingest_ml_data as ingestion


def _utilization_row(bene_id: str) -> dict[str, str]:
    return {
        "BENE_ID": bene_id, "age": "65", "gender": "Female",
        "dual_eligibility_months": "0", "chronic_condition_count": "2",
        "inpatient_visit_count": "1", "inpatient_total_cost": "125.50",
        "outpatient_visit_count": "3", "outpatient_total_cost": "75.25",
        "ed_visit_count": "1", "total_claim_payment_amount": "200.75",
        "total_ed_related_cost": "50.00", "average_claim_cost": "50.19",
        "provider_count": "2",
    }


def _risk_row(bene_id: str) -> dict[str, str]:
    return {
        "BENE_ID": bene_id, "high_utilization_pattern": "0",
        "predicted_high_utilization_pattern": "0",
        "high_utilization_probability": "0.10", "dataset_split": "test",
    }


def _anomaly_row(bene_id: str, rank: int) -> dict[str, str]:
    return {
        "BENE_ID": bene_id, "anomaly_score": "0.35", "anomaly_flag": "0",
        "anomaly_rank": str(rank), "model_version": "isolation-forest-utilization-v1",
        "generated_at": "2026-08-13T08:43:15Z",
    }


def _write_csv(path: Path, columns: set[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        fieldnames = sorted(columns)
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows([{field: row.get(field, "") for field in fieldnames} for row in rows])


def _write_sources(tmp_path: Path, identifiers: list[str]) -> tuple[Path, Path, Path]:
    utilization_path = tmp_path / "utilization_features.csv"
    risk_path = tmp_path / "risk_predictions.csv"
    anomaly_path = tmp_path / "anomaly_results.csv"
    _write_csv(utilization_path, ingestion.UTILIZATION_COLUMNS, [_utilization_row(value) for value in identifiers])
    _write_csv(risk_path, ingestion.RISK_COLUMNS, [_risk_row(value) for value in identifiers])
    _write_csv(anomaly_path, ingestion.ANOMALY_COLUMNS, [_anomaly_row(value, index + 1) for index, value in enumerate(identifiers)])
    return utilization_path, risk_path, anomaly_path


def test_current_8671_member_dataset_is_valid() -> None:
    sources = ingestion.load_and_validate_sources()
    assert sources.member_count == 8_671


def test_valid_dataset_with_different_member_count_is_valid(tmp_path: Path) -> None:
    paths = _write_sources(tmp_path, ["member-1", "member-2"])
    sources = ingestion.load_and_validate_sources(*paths)
    assert sources.member_count == 2
    assert sources.dataset_identifier.startswith("sha256:")
    assert sources.source_filenames["utilization_features"] == "utilization_features.csv"


def test_duplicate_bene_id_fails_before_database_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _write_sources(tmp_path, ["member-1", "member-1"])
    database_calls: list[str] = []
    monkeypatch.setattr(ingestion, "create_database_schema", lambda: database_calls.append("schema"))
    with pytest.raises(ingestion.SourceValidationError, match="duplicate BENE_ID"):
        ingestion.ingest_ml_data(*paths)
    assert database_calls == []


def test_missing_required_column_fails(tmp_path: Path) -> None:
    paths = _write_sources(tmp_path, ["member-1"])
    _write_csv(paths[0], ingestion.UTILIZATION_COLUMNS - {"provider_count"}, [_utilization_row("member-1")])
    with pytest.raises(ingestion.SourceValidationError, match="schema mismatch"):
        ingestion.load_and_validate_sources(*paths)


def test_mismatched_bene_id_sets_fail(tmp_path: Path) -> None:
    paths = _write_sources(tmp_path, ["member-1"])
    _write_csv(paths[1], ingestion.RISK_COLUMNS, [_risk_row("other-member")])
    with pytest.raises(ingestion.SourceValidationError, match="sets must be identical"):
        ingestion.load_and_validate_sources(*paths)


def test_invalid_numeric_value_fails(tmp_path: Path) -> None:
    paths = _write_sources(tmp_path, ["member-1"])
    invalid_row = _utilization_row("member-1")
    invalid_row["age"] = "not-a-number"
    _write_csv(paths[0], ingestion.UTILIZATION_COLUMNS, [invalid_row])
    with pytest.raises(ingestion.SourceValidationError, match="age must be an integer"):
        ingestion.load_and_validate_sources(*paths)


def test_empty_dataset_fails(tmp_path: Path) -> None:
    paths = _write_sources(tmp_path, [])
    with pytest.raises(ingestion.SourceValidationError, match="at least one member"):
        ingestion.load_and_validate_sources(*paths)
