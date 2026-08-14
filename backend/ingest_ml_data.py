"""Validate and transactionally ingest the repository's ML-ready CSV outputs.

This module deliberately does not run inference or alter model artifacts.  It
loads the existing member-level aggregate data and associated model outputs.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database import create_database_schema, session_scope
from backend.models import (
    Member,
    MemberUtilizationSnapshot,
    ModelRun,
    UtilizationAnomalyResult,
    XGBoostUtilizationPrediction,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DATA_DIR = PROJECT_ROOT / "processed_data"
MODEL_DIR = PROJECT_ROOT / "ml_models"
EXPECTED_MEMBER_COUNT = 8_671

UTILIZATION_COLUMNS = {
    "BENE_ID", "age", "gender", "dual_eligibility_months",
    "chronic_condition_count", "inpatient_visit_count", "inpatient_total_cost",
    "outpatient_visit_count", "outpatient_total_cost", "ed_visit_count",
    "total_claim_payment_amount", "total_ed_related_cost", "average_claim_cost",
    "provider_count",
}
RISK_COLUMNS = {
    "BENE_ID", "high_utilization_pattern", "predicted_high_utilization_pattern",
    "high_utilization_probability", "dataset_split",
}
ANOMALY_COLUMNS = {
    "BENE_ID", "anomaly_score", "anomaly_flag", "anomaly_rank", "model_version",
    "generated_at",
}
INTEGER_COLUMNS = {
    "age", "dual_eligibility_months", "chronic_condition_count",
    "inpatient_visit_count", "outpatient_visit_count", "ed_visit_count", "provider_count",
}
DECIMAL_COLUMNS = {
    "inpatient_total_cost", "outpatient_total_cost", "total_claim_payment_amount",
    "total_ed_related_cost", "average_claim_cost",
}


class SourceValidationError(ValueError):
    """Raised before database work when a source artifact is incompatible."""


@dataclass(frozen=True)
class SourceData:
    utilization: dict[str, dict[str, Any]]
    risk: dict[str, dict[str, Any]]
    anomaly: dict[str, dict[str, Any]]


def _read_csv(path: Path, expected_columns: set[str]) -> list[dict[str, str]]:
    if not path.exists():
        raise SourceValidationError(f"Required source file is missing: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        actual_columns = set(reader.fieldnames or [])
        if actual_columns != expected_columns:
            raise SourceValidationError(
                f"{path.name} schema mismatch; expected {sorted(expected_columns)}, "
                f"received {sorted(actual_columns)}."
            )
        rows = list(reader)
    return rows


def _required_bene_id(row: dict[str, str], source_name: str, row_number: int) -> str:
    bene_id = (row.get("BENE_ID") or "").strip()
    if not bene_id:
        raise SourceValidationError(f"{source_name} row {row_number} has a missing BENE_ID.")
    return bene_id


def _integer(value: str, field: str, source_name: str, row_number: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise SourceValidationError(f"{source_name} row {row_number}: {field} must be an integer.") from error
    return parsed


def _decimal(value: str, field: str, source_name: str, row_number: int) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise SourceValidationError(f"{source_name} row {row_number}: {field} must be numeric.") from error
    if not parsed.is_finite():
        raise SourceValidationError(f"{source_name} row {row_number}: {field} must be finite.")
    return parsed


def _binary(value: str, field: str, source_name: str, row_number: int) -> int:
    parsed = _integer(value, field, source_name, row_number)
    if parsed not in (0, 1):
        raise SourceValidationError(f"{source_name} row {row_number}: {field} must be 0 or 1.")
    return parsed


def _index_rows(rows: list[dict[str, str]], source_name: str) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    for row_number, row in enumerate(rows, start=2):
        bene_id = _required_bene_id(row, source_name, row_number)
        if bene_id in indexed:
            raise SourceValidationError(f"{source_name} contains duplicate BENE_ID: {bene_id!r}.")
        indexed[bene_id] = row
    if len(indexed) != EXPECTED_MEMBER_COUNT:
        raise SourceValidationError(
            f"{source_name} must contain {EXPECTED_MEMBER_COUNT} members; found {len(indexed)}."
        )
    return indexed


def load_and_validate_sources() -> SourceData:
    """Load every source and validate all data before any database transaction."""
    utilization_rows = _index_rows(
        _read_csv(PROCESSED_DATA_DIR / "utilization_features.csv", UTILIZATION_COLUMNS),
        "utilization_features.csv",
    )
    risk_rows = _index_rows(
        _read_csv(PROCESSED_DATA_DIR / "risk_predictions.csv", RISK_COLUMNS),
        "risk_predictions.csv",
    )
    anomaly_rows = _index_rows(
        _read_csv(PROCESSED_DATA_DIR / "anomaly_results.csv", ANOMALY_COLUMNS),
        "anomaly_results.csv",
    )
    if set(utilization_rows) != set(risk_rows) or set(utilization_rows) != set(anomaly_rows):
        raise SourceValidationError("BENE_ID sets must be identical across all three source files.")

    utilization: dict[str, dict[str, Any]] = {}
    risk: dict[str, dict[str, Any]] = {}
    anomaly: dict[str, dict[str, Any]] = {}
    for bene_id, row in utilization_rows.items():
        parsed: dict[str, Any] = {"BENE_ID": bene_id, "gender": row["gender"]}
        if not parsed["gender"].strip():
            raise SourceValidationError(f"utilization_features.csv member {bene_id!r} has blank gender.")
        for column in INTEGER_COLUMNS:
            value = _integer(row[column], column, "utilization_features.csv", 0)
            if value < 0:
                raise SourceValidationError(f"utilization_features.csv member {bene_id!r}: {column} is negative.")
            parsed[column] = value
        for column in DECIMAL_COLUMNS:
            value = _decimal(row[column], column, "utilization_features.csv", 0)
            if value < 0:
                raise SourceValidationError(f"utilization_features.csv member {bene_id!r}: {column} is negative.")
            parsed[column] = value
        utilization[bene_id] = parsed

    for bene_id, row in risk_rows.items():
        probability = _decimal(row["high_utilization_probability"], "high_utilization_probability", "risk_predictions.csv", 0)
        if not Decimal("0") <= probability <= Decimal("1"):
            raise SourceValidationError(f"risk_predictions.csv member {bene_id!r}: probability is outside [0, 1].")
        split = row["dataset_split"].strip()
        if not split:
            raise SourceValidationError(f"risk_predictions.csv member {bene_id!r}: dataset_split is blank.")
        risk[bene_id] = {
            "high_utilization_pattern": _binary(row["high_utilization_pattern"], "high_utilization_pattern", "risk_predictions.csv", 0),
            "predicted_high_utilization_pattern": _binary(row["predicted_high_utilization_pattern"], "predicted_high_utilization_pattern", "risk_predictions.csv", 0),
            "high_utilization_probability": float(probability),
            "dataset_split": split,
        }

    for bene_id, row in anomaly_rows.items():
        score = _decimal(row["anomaly_score"], "anomaly_score", "anomaly_results.csv", 0)
        rank = _integer(row["anomaly_rank"], "anomaly_rank", "anomaly_results.csv", 0)
        if rank <= 0:
            raise SourceValidationError(f"anomaly_results.csv member {bene_id!r}: anomaly_rank must be positive.")
        try:
            generated_at = datetime.fromisoformat(row["generated_at"].replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            raise SourceValidationError(f"anomaly_results.csv member {bene_id!r}: generated_at is invalid.") from error
        if generated_at.tzinfo is None:
            raise SourceValidationError(f"anomaly_results.csv member {bene_id!r}: generated_at must include a timezone.")
        model_version = row["model_version"].strip()
        if not model_version:
            raise SourceValidationError(f"anomaly_results.csv member {bene_id!r}: model_version is blank.")
        anomaly[bene_id] = {
            "anomaly_score": float(score),
            "anomaly_flag": _binary(row["anomaly_flag"], "anomaly_flag", "anomaly_results.csv", 0),
            "anomaly_rank": rank,
            "model_version": model_version,
            "generated_at": generated_at,
        }
    return SourceData(utilization=utilization, risk=risk, anomaly=anomaly)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def _get_or_create_model_run(session: Session, *, model_type: str, model_version: str | None,
                             generated_at: datetime | None, purpose: str, target_definition: str | None,
                             leakage_exclusions: list[str] | None, configuration: dict[str, Any] | None,
                             metrics: dict[str, Any] | None, artifact_reference: str) -> ModelRun:
    run = session.scalar(
        select(ModelRun).where(
            ModelRun.model_type == model_type,
            ModelRun.artifact_reference == artifact_reference,
        )
    )
    if run is None:
        run = ModelRun(model_type=model_type, artifact_reference=artifact_reference, purpose=purpose)
        session.add(run)
    run.model_version = model_version
    run.generated_at = generated_at
    run.purpose = purpose
    run.target_definition = target_definition
    run.leakage_exclusions = leakage_exclusions
    run.configuration = configuration
    run.metrics = metrics
    session.flush()
    return run


def _load_model_runs(session: Session, sources: SourceData) -> tuple[ModelRun, ModelRun]:
    xgb_metrics = _read_json(MODEL_DIR / "xgboost_metrics.json")
    anomaly_metrics = _read_json(MODEL_DIR / "isolation_forest_metrics.json")
    anomaly_metadata = _read_json(MODEL_DIR / "isolation_forest_feature_metadata.json")
    anomaly_generated_at = next(iter(sources.anomaly.values()))["generated_at"]
    xgb_run = _get_or_create_model_run(
        session,
        model_type="xgboost",
        model_version=None,
        generated_at=None,
        purpose=xgb_metrics["model_purpose"],
        target_definition=xgb_metrics["target_definition"],
        leakage_exclusions=xgb_metrics["excluded_from_training_for_leakage"],
        configuration={"ed_visit_count_cutoff": xgb_metrics["ed_visit_count_cutoff"]},
        metrics=xgb_metrics["test_metrics"],
        artifact_reference="ml_models/xgboost_risk_model.pkl",
    )
    anomaly_run = _get_or_create_model_run(
        session,
        model_type="isolation_forest",
        model_version=next(iter(sources.anomaly.values()))["model_version"],
        generated_at=anomaly_generated_at,
        purpose=anomaly_metrics["model_purpose"],
        target_definition=None,
        leakage_exclusions=anomaly_metadata["excluded_columns_for_leakage_prevention"],
        configuration=anomaly_metadata["selected_configuration"],
        metrics=anomaly_metrics,
        artifact_reference="ml_models/isolation_forest.pkl",
    )
    return xgb_run, anomaly_run


def ingest_ml_data() -> dict[str, int]:
    """Validate sources, then create or update this exact dataset atomically."""
    sources = load_and_validate_sources()
    create_database_schema()
    with session_scope() as session:
        xgb_run, anomaly_run = _load_model_runs(session, sources)
        for bene_id, utilization in sources.utilization.items():
            member = session.scalar(select(Member).where(Member.bene_id == bene_id))
            if member is None:
                member = Member(bene_id=bene_id)
                session.add(member)
            member.age = utilization["age"]
            member.gender = utilization["gender"]
            member.dual_eligibility_months = utilization["dual_eligibility_months"]
            member.chronic_condition_count = utilization["chronic_condition_count"]
            session.flush()

            snapshot = session.scalar(
                select(MemberUtilizationSnapshot).where(MemberUtilizationSnapshot.member_id == member.id)
            )
            if snapshot is None:
                snapshot = MemberUtilizationSnapshot(member_id=member.id)
                session.add(snapshot)
            for column in (
                "inpatient_visit_count", "inpatient_total_cost", "outpatient_visit_count",
                "outpatient_total_cost", "ed_visit_count", "total_claim_payment_amount",
                "total_ed_related_cost", "average_claim_cost", "provider_count",
            ):
                setattr(snapshot, column, utilization[column])
            session.flush()

            risk = sources.risk[bene_id]
            prediction = session.scalar(select(XGBoostUtilizationPrediction).where(
                XGBoostUtilizationPrediction.model_run_id == xgb_run.model_run_id,
                XGBoostUtilizationPrediction.utilization_snapshot_id == snapshot.id,
            ))
            if prediction is None:
                prediction = XGBoostUtilizationPrediction(
                    member_id=member.id, utilization_snapshot_id=snapshot.id, model_run_id=xgb_run.model_run_id
                )
                session.add(prediction)
            prediction.member_id = member.id
            prediction.high_utilization_pattern = risk["high_utilization_pattern"]
            prediction.predicted_high_utilization_pattern = risk["predicted_high_utilization_pattern"]
            prediction.high_utilization_probability = risk["high_utilization_probability"]
            prediction.dataset_split = risk["dataset_split"]

            anomaly = sources.anomaly[bene_id]
            anomaly_result = session.scalar(select(UtilizationAnomalyResult).where(
                UtilizationAnomalyResult.model_run_id == anomaly_run.model_run_id,
                UtilizationAnomalyResult.utilization_snapshot_id == snapshot.id,
            ))
            if anomaly_result is None:
                anomaly_result = UtilizationAnomalyResult(
                    member_id=member.id, utilization_snapshot_id=snapshot.id, model_run_id=anomaly_run.model_run_id
                )
                session.add(anomaly_result)
            anomaly_result.member_id = member.id
            anomaly_result.anomaly_score = anomaly["anomaly_score"]
            anomaly_result.anomaly_flag = anomaly["anomaly_flag"]
            anomaly_result.anomaly_rank = anomaly["anomaly_rank"]
            anomaly_result.generated_at = anomaly["generated_at"]

        return {
            "members": len(sources.utilization),
            "member_utilization_snapshots": len(sources.utilization),
            "xgboost_utilization_predictions": len(sources.risk),
            "utilization_anomaly_results": len(sources.anomaly),
            "model_runs": 2,
        }


if __name__ == "__main__":
    counts = ingest_ml_data()
    print("Ingestion complete: " + ", ".join(f"{name}={count}" for name, count in counts.items()))
