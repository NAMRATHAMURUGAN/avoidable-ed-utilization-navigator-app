"""Train an unsupervised utilization-anomaly model.

This workflow identifies unusual patterns within the observed member-level
utilization data.  It is not a clinical risk model and does not determine
whether an emergency department visit was necessary or avoidable.
"""

from __future__ import annotations

import argparse
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


RANDOM_STATE = 42
IDENTIFIER_COLUMN = "BENE_ID"
CONTAMINATION_VALUES = (0.01, 0.02, 0.05, 0.10)
SELECTED_CONTAMINATION = 0.02
MODEL_VERSION = "isolation-forest-utilization-v1"

# These fields identify the XGBoost proxy target or are direct aggregates of
# its ED encounters.  They must never be passed to the unsupervised model.
EXCLUDED_COLUMNS = {
    IDENTIFIER_COLUMN,
    "high_utilization_pattern",
    "ed_visit_count",
    "total_ed_related_cost",
}


def validate_input(data: pd.DataFrame) -> None:
    required = {
        IDENTIFIER_COLUMN,
        "age",
        "gender",
        "dual_eligibility_months",
        "chronic_condition_count",
        "inpatient_visit_count",
        "inpatient_total_cost",
        "outpatient_visit_count",
        "outpatient_total_cost",
        "total_claim_payment_amount",
        "average_claim_cost",
        "provider_count",
    }
    missing = sorted(required.difference(data.columns))
    if missing:
        raise ValueError(f"Input is missing required utilization columns: {missing}")
    if data[IDENTIFIER_COLUMN].isna().any() or data[IDENTIFIER_COLUMN].duplicated().any():
        raise ValueError("BENE_ID must be present and unique in member-level input.")


def add_leakage_safe_features(data: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    """Add transparent ratios using only non-ED utilization fields."""
    output = data.copy()
    visits = (
        pd.to_numeric(output["inpatient_visit_count"], errors="coerce").fillna(0)
        + pd.to_numeric(output["outpatient_visit_count"], errors="coerce").fillna(0)
    )
    denominator = visits.clip(lower=1)
    output["recorded_visit_count"] = visits
    output["cost_per_recorded_visit"] = (
        pd.to_numeric(output["total_claim_payment_amount"], errors="coerce") / denominator
    )
    output["provider_fragmentation_ratio"] = (
        pd.to_numeric(output["provider_count"], errors="coerce") / denominator
    )
    output["inpatient_visit_share"] = (
        pd.to_numeric(output["inpatient_visit_count"], errors="coerce") / denominator
    )
    formulas = {
        "recorded_visit_count": "inpatient_visit_count + outpatient_visit_count",
        "cost_per_recorded_visit": "total_claim_payment_amount / max(inpatient_visit_count + outpatient_visit_count, 1)",
        "provider_fragmentation_ratio": "provider_count / max(inpatient_visit_count + outpatient_visit_count, 1)",
        "inpatient_visit_share": "inpatient_visit_count / max(inpatient_visit_count + outpatient_visit_count, 1)",
    }
    return output, formulas


def build_preprocessor(features: pd.DataFrame) -> ColumnTransformer:
    numeric = features.select_dtypes(include=["number", "bool"]).columns.tolist()
    categorical = [column for column in features.columns if column not in numeric]
    transformers = [
        (
            "numeric",
            Pipeline([
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
            ]),
            numeric,
        )
    ]
    if categorical:
        transformers.append(
            (
                "categorical",
                Pipeline([
                    ("imputer", SimpleImputer(strategy="most_frequent")),
                    ("one_hot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
                ]),
                categorical,
            )
        )
    return ColumnTransformer(transformers, remainder="drop", verbose_feature_names_out=False)


def score_quantiles(scores: np.ndarray) -> dict[str, float]:
    return {
        f"p{int(quantile * 100):02d}": float(np.quantile(scores, quantile))
        for quantile in (0, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 1)
    }


def profile_groups(data: pd.DataFrame, anomaly_flags: np.ndarray, columns: list[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for column in columns:
        series = pd.to_numeric(data[column], errors="coerce")
        for group, mask in (("normal", ~anomaly_flags), ("anomalous", anomaly_flags)):
            subset = series[mask]
            rows.append({
                "feature": column,
                "group": group,
                "member_count": int(mask.sum()),
                "non_missing_count": int(subset.notna().sum()),
                "mean": float(subset.mean()) if subset.notna().any() else None,
                "median": float(subset.median()) if subset.notna().any() else None,
                "p25": float(subset.quantile(0.25)) if subset.notna().any() else None,
                "p75": float(subset.quantile(0.75)) if subset.notna().any() else None,
            })
    return pd.DataFrame(rows)


def temporal_data_status(processed_dir: Path) -> dict[str, object]:
    claim_files = [processed_dir / "inpatient_clean.csv", processed_dir / "outpatient_clean.csv"]
    available = [str(path) for path in claim_files if path.exists()]
    return {
        "temporal_results_created": False,
        "available_claim_files": available,
        "reason": (
            "No cleaned inpatient/outpatient claim files are available for date validation. "
            "The member-level feature file is aggregated and cannot support actual anomaly start/end dates."
            if not available
            else "Claim files exist but this run did not validate a shared service-date field; no dates were inferred."
        ),
    }


def main(
    input_path: Path,
    model_path: Path,
    metrics_path: Path,
    metadata_path: Path,
    results_path: Path,
    summary_path: Path,
    overlap_path: Path,
    profile_path: Path,
) -> None:
    data = pd.read_csv(input_path)
    validate_input(data)
    enriched, formulas = add_leakage_safe_features(data)
    feature_columns = [column for column in enriched.columns if column not in EXCLUDED_COLUMNS]
    features = enriched[feature_columns]

    preprocessor = build_preprocessor(features)
    transformed = preprocessor.fit_transform(features)
    experiments: list[dict[str, object]] = []
    for contamination in CONTAMINATION_VALUES:
        candidate = IsolationForest(
            n_estimators=300,
            contamination=contamination,
            random_state=RANDOM_STATE,
            n_jobs=1,
        ).fit(transformed)
        raw_scores = candidate.score_samples(transformed)
        flags = candidate.predict(transformed) == -1
        experiments.append({
            "contamination": contamination,
            "anomaly_count": int(flags.sum()),
            "anomaly_percentage": float(flags.mean() * 100),
            "raw_score_distribution": score_quantiles(raw_scores),
        })

    model = IsolationForest(
        n_estimators=300,
        contamination=SELECTED_CONTAMINATION,
        random_state=RANDOM_STATE,
        n_jobs=1,
    ).fit(transformed)
    # Negating score_samples makes higher values more unusual.  This is a
    # utilization anomaly score, not a probability or a clinical risk score.
    anomaly_scores = -model.score_samples(transformed)
    anomaly_flags = model.predict(transformed) == -1
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    results = pd.DataFrame(
        {
            IDENTIFIER_COLUMN: data[IDENTIFIER_COLUMN],
            "anomaly_score": anomaly_scores,
            "anomaly_flag": anomaly_flags.astype(int),
            "model_version": MODEL_VERSION,
            "generated_at": generated_at,
        }
    ).sort_values(["anomaly_score", IDENTIFIER_COLUMN], ascending=[False, True], ignore_index=True)
    # A stable member identifier tie-breaker gives every record a unique,
    # reproducible rank even when two model scores are identical.
    results.insert(3, "anomaly_rank", np.arange(1, len(results) + 1))

    profile_columns = [
        "age", "dual_eligibility_months", "chronic_condition_count",
        "inpatient_visit_count", "inpatient_total_cost", "outpatient_visit_count",
        "outpatient_total_cost", "total_claim_payment_amount", "average_claim_cost",
        "provider_count", *formulas.keys(),
    ]
    profile = profile_groups(enriched, anomaly_flags, profile_columns)
    profile_summary = profile.pivot(index="feature", columns="group", values=["mean", "median"]).round(4)
    profile_summary.columns = [f"{stat}_{group}" for stat, group in profile_summary.columns]

    risk_path = input_path.parent / "risk_predictions.csv"
    if not risk_path.exists():
        raise FileNotFoundError(f"Required post-hoc comparison file not found: {risk_path}")
    risk_predictions = pd.read_csv(risk_path)
    if {IDENTIFIER_COLUMN, "high_utilization_pattern"}.difference(risk_predictions.columns):
        raise ValueError("risk_predictions.csv must contain BENE_ID and high_utilization_pattern for post-hoc comparison.")
    overlap = results[[IDENTIFIER_COLUMN, "anomaly_score", "anomaly_flag"]].merge(
        risk_predictions[[IDENTIFIER_COLUMN, "high_utilization_pattern"]], on=IDENTIFIER_COLUMN, how="inner", validate="one_to_one"
    )
    if len(overlap) != len(data):
        raise ValueError("Post-hoc XGBoost comparison did not retain every member.")
    overlap["comparison_group"] = np.select(
        [
            (overlap["high_utilization_pattern"] == 1) & (overlap["anomaly_flag"] == 1),
            (overlap["high_utilization_pattern"] == 1) & (overlap["anomaly_flag"] == 0),
            (overlap["high_utilization_pattern"] == 0) & (overlap["anomaly_flag"] == 1),
        ],
        ["high_utilization_and_anomaly", "high_utilization_no_anomaly", "low_utilization_and_anomaly"],
        default="low_utilization_no_anomaly",
    )
    overlap_counts = overlap["comparison_group"].value_counts().reindex(
        ["high_utilization_and_anomaly", "high_utilization_no_anomaly", "low_utilization_and_anomaly", "low_utilization_no_anomaly"], fill_value=0
    ).to_dict()

    metadata = {
        "model_version": MODEL_VERSION,
        "model_type": "sklearn.ensemble.IsolationForest",
        "random_state": RANDOM_STATE,
        "selected_configuration": {"n_estimators": 300, "contamination": SELECTED_CONTAMINATION, "n_jobs": 1},
        "selection_rationale": "A conservative 2% review cohort was selected without optimizing against any label. The 1% option was reserved for only the most extreme observations, while 5% and 10% would broaden the review cohort substantially. This is an operational, non-clinical threshold choice rather than evidence of anomaly prevalence.",
        "training_feature_columns": feature_columns,
        "categorical_features_one_hot_encoded": [column for column in feature_columns if column not in features.select_dtypes(include=["number", "bool"]).columns],
        "excluded_columns_for_leakage_prevention": sorted(EXCLUDED_COLUMNS),
        "engineered_feature_formulas": formulas,
        "score_interpretation": "Higher utilization anomaly score means the observed member-level pattern was more unusual to this fitted Isolation Forest. It is not a probability, clinical risk score, or determination of ED avoidability.",
        "feature_importance": "Isolation Forest does not provide native feature_importances_. Use isolation_forest_anomaly_profile.csv for descriptive normal-versus-anomalous feature profiles.",
    }
    summary = {
        "model_purpose": "Unsupervised detection of unusual healthcare utilization patterns within the observed data.",
        "generated_at": generated_at,
        "total_members": int(len(data)),
        "number_of_anomalies": int(anomaly_flags.sum()),
        "anomaly_percentage": float(anomaly_flags.mean() * 100),
        "utilization_anomaly_score_distribution": score_quantiles(anomaly_scores),
        "contamination_experiments": experiments,
        "selected_configuration": metadata["selected_configuration"],
        "xgboost_post_hoc_overlap_counts": {key: int(value) for key, value in overlap_counts.items()},
        "temporal_analysis": temporal_data_status(input_path.parent),
        "limitation": "An anomaly is not evidence of medical necessity, clinical deterioration, inappropriate ED use, or ED avoidability.",
    }

    for path in (model_path, metrics_path, metadata_path, results_path, summary_path, overlap_path, profile_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    with model_path.open("wb") as file:
        pickle.dump({"preprocessor": preprocessor, "model": model, "feature_metadata": metadata}, file)
    metrics_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    results.to_csv(results_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    overlap.to_csv(overlap_path, index=False)
    profile_summary.reset_index().to_csv(profile_path, index=False)

    print(f"Members: {len(data)}")
    print(f"Anomalies: {int(anomaly_flags.sum())} ({anomaly_flags.mean() * 100:.2f}%)")
    print("Contamination experiments:")
    for experiment in experiments:
        print(f"  {experiment['contamination']:.2f}: {experiment['anomaly_count']} ({experiment['anomaly_percentage']:.2f}%)")
    print(f"Saved model: {model_path}")
    print(f"Saved anomaly results: {results_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("processed_data/utilization_features.csv"))
    parser.add_argument("--model-output", type=Path, default=Path("ml_models/isolation_forest.pkl"))
    parser.add_argument("--metrics-output", type=Path, default=Path("ml_models/isolation_forest_metrics.json"))
    parser.add_argument("--metadata-output", type=Path, default=Path("ml_models/isolation_forest_feature_metadata.json"))
    parser.add_argument("--results-output", type=Path, default=Path("processed_data/anomaly_results.csv"))
    parser.add_argument("--summary-output", type=Path, default=Path("processed_data/anomaly_summary.json"))
    parser.add_argument("--overlap-output", type=Path, default=Path("processed_data/xgboost_isolation_forest_overlap.csv"))
    parser.add_argument("--profile-output", type=Path, default=Path("ml_models/isolation_forest_anomaly_profile.csv"))
    args = parser.parse_args()
    main(
        input_path=args.input,
        model_path=args.model_output,
        metrics_path=args.metrics_output,
        metadata_path=args.metadata_output,
        results_path=args.results_output,
        summary_path=args.summary_output,
        overlap_path=args.overlap_output,
        profile_path=args.profile_output,
    )
