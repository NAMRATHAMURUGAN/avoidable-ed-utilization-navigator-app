"""Validate and transactionally ingest the repository's ML-ready CSV outputs.

This module deliberately does not run inference or alter model artifacts.  It
loads the existing member-level aggregate data and associated model outputs.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
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
    dataset_identifier: str
    ingested_at: datetime
    source_filenames: dict[str, str]

    @property
    def member_count(self) -> int:
        return len(self.utilization)


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
    if not indexed:
        raise SourceValidationError(f"{source_name} must contain at least one member.")
    return indexed


def _dataset_identifier(paths: tuple[Path, Path, Path]) -> str:
    """Create a stable content identity without treating BENE_ID as a number."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1_048_576), b""):
                digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def load_and_validate_sources(
    utilization_path: Path | None = None,
    risk_path: Path | None = None,
    anomaly_path: Path | None = None,
) -> SourceData:
    """Load every source and validate all data before any database transaction."""
    utilization_path = utilization_path or PROCESSED_DATA_DIR / "utilization_features.csv"
    risk_path = risk_path or PROCESSED_DATA_DIR / "risk_predictions.csv"
    anomaly_path = anomaly_path or PROCESSED_DATA_DIR / "anomaly_results.csv"
    utilization_rows = _index_rows(
        _read_csv(utilization_path, UTILIZATION_COLUMNS),
        "utilization_features.csv",
    )
    risk_rows = _index_rows(
        _read_csv(risk_path, RISK_COLUMNS),
        "risk_predictions.csv",
    )
    anomaly_rows = _index_rows(
        _read_csv(anomaly_path, ANOMALY_COLUMNS),
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
    source_paths = (utilization_path, risk_path, anomaly_path)
    return SourceData(
        utilization=utilization,
        risk=risk,
        anomaly=anomaly,
        dataset_identifier=_dataset_identifier(source_paths),
        ingested_at=datetime.now(timezone.utc),
        source_filenames={
            "utilization_features": utilization_path.name,
            "risk_predictions": risk_path.name,
            "anomaly_results": anomaly_path.name,
        },
    )


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
    ingestion_context = {
        "dataset_identifier": sources.dataset_identifier,
        "ingested_at": sources.ingested_at.isoformat(),
        "source_filenames": sources.source_filenames,
        "member_count": sources.member_count,
    }
    xgb_run = _get_or_create_model_run(
        session,
        model_type="xgboost",
        model_version=None,
        generated_at=None,
        purpose=xgb_metrics["model_purpose"],
        target_definition=xgb_metrics["target_definition"],
        leakage_exclusions=xgb_metrics["excluded_from_training_for_leakage"],
        configuration={
            "ed_visit_count_cutoff": xgb_metrics["ed_visit_count_cutoff"],
            "ingestion_context": ingestion_context,
        },
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
        configuration={
            **anomaly_metadata["selected_configuration"],
            "ingestion_context": ingestion_context,
        },
        metrics=anomaly_metrics,
        artifact_reference="ml_models/isolation_forest.pkl",
    )
    return xgb_run, anomaly_run


def ingest_ml_data(
    utilization_path: Path | None = None,
    risk_path: Path | None = None,
    anomaly_path: Path | None = None,
) -> dict[str, int]:
    """Validate sources, then create or update this exact dataset atomically."""
    sources = load_and_validate_sources(utilization_path, risk_path, anomaly_path)
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
            "members": sources.member_count,
            "member_utilization_snapshots": sources.member_count,
            "xgboost_utilization_predictions": sources.member_count,
            "utilization_anomaly_results": sources.member_count,
            "model_runs": 2,
        }


if __name__ == "__main__":
    counts = ingest_ml_data()
    print("Ingestion complete: " + ", ".join(f"{name}={count}" for name, count in counts.items()))
