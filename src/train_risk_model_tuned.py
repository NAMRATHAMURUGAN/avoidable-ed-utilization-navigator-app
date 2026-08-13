"""Optimize leakage-safe historical high-ED-utilization pattern modeling.

The model prioritizes members for proactive care navigation from historical
utilization patterns. It does not determine emergency necessity or recommend
that any person avoid emergency care.
"""

from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import RandomizedSearchCV, StratifiedKFold, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from xgboost import XGBClassifier


RANDOM_STATE = 42
TARGET = "high_utilization_pattern"
ID = "BENE_ID"
OBSERVATION_MONTHS = 12  # The source feature file is aggregated for the 2022 annual observation period.
THRESHOLDS = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
BASELINE_PARAMS = {
    "max_depth": 4,
    "learning_rate": 0.05,
    "n_estimators": 300,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
}

# These variables either define the target or are direct transformations of it.
# They are retained only as audit columns and are never passed to a model.
LEAKAGE_COLUMNS = {
    "ed_visit_count",
    "total_ed_related_cost",
    "ed_visit_intensity",
    "ed_to_outpatient_dependency_ratio",
}


def proxy_target(data: pd.DataFrame) -> tuple[pd.Series, float]:
    cutoff = float(data["ed_visit_count"].quantile(0.90))
    return (data["ed_visit_count"] >= cutoff).astype(int), cutoff


def add_utilization_features(data: pd.DataFrame) -> pd.DataFrame:
    """Create requested utilization behavior features without altering source data."""
    output = data.copy()
    output["active_utilization_months"] = OBSERVATION_MONTHS
    denominator = output["active_utilization_months"] + 1
    # Requested audit features: excluded as leakage because each contains ED count.
    output["ed_visit_intensity"] = output["ed_visit_count"] / denominator
    output["ed_to_outpatient_dependency_ratio"] = output["ed_visit_count"] / (output["outpatient_visit_count"] + 1)
    # Leakage-safe behavior features used in training.
    output["inpatient_utilization_intensity"] = output["inpatient_visit_count"] / denominator
    output["provider_fragmentation_score"] = output["provider_count"] / (
        output["inpatient_visit_count"] + output["outpatient_visit_count"] + 1
    )
    output["cost_intensity"] = output["total_claim_payment_amount"] / denominator
    return output


def make_preprocessor(X: pd.DataFrame) -> ColumnTransformer:
    numeric = X.select_dtypes(include=["number", "bool"]).columns.tolist()
    categorical = [column for column in X.columns if column not in numeric]
    return ColumnTransformer(
        [
            ("numeric", Pipeline([("imputer", SimpleImputer(strategy="median"))]), numeric),
            (
                "categorical",
                Pipeline([
                    ("imputer", SimpleImputer(strategy="most_frequent")),
                    ("one_hot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
                ]),
                categorical,
            ),
        ],
        verbose_feature_names_out=False,
    )


def metrics(y_true: pd.Series, probabilities: np.ndarray, threshold: float) -> dict[str, object]:
    predicted = (probabilities >= threshold).astype(int)
    return {
        "accuracy": float(accuracy_score(y_true, predicted)),
        "precision": float(precision_score(y_true, predicted, zero_division=0)),
        "recall": float(recall_score(y_true, predicted, zero_division=0)),
        "f1_score": float(f1_score(y_true, predicted, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true, probabilities)),
        "confusion_matrix": confusion_matrix(y_true, predicted).tolist(),
    }


def choose_threshold(y_valid: pd.Series, probabilities: np.ndarray) -> tuple[float, list[dict[str, float]]]:
    table = []
    for threshold in THRESHOLDS:
        row = metrics(y_valid, probabilities, threshold)
        table.append({"threshold": threshold, **{key: row[key] for key in ("precision", "recall", "f1_score")}})
    # Favor F1, but require recall near the requested 0.80 target when feasible.
    eligible = [row for row in table if row["recall"] >= 0.80]
    selected = max(eligible or table, key=lambda row: (row["f1_score"], row["precision"]))
    return float(selected["threshold"]), table


def run_hyperparameter_search(X_train: pd.DataFrame, y_train: pd.Series, weight: float) -> RandomizedSearchCV:
    pipeline = Pipeline([
        ("preprocessor", make_preprocessor(X_train)),
        ("classifier", XGBClassifier(
            objective="binary:logistic", eval_metric="logloss", random_state=RANDOM_STATE, n_jobs=1,
            scale_pos_weight=weight,
        )),
    ])
    search = RandomizedSearchCV(
        pipeline,
        param_distributions={
            "classifier__max_depth": [2, 3, 4, 5, 6],
            "classifier__learning_rate": [0.02, 0.03, 0.05, 0.08, 0.1],
            "classifier__n_estimators": [150, 250, 350, 500],
            "classifier__min_child_weight": [1, 2, 4, 6],
            "classifier__subsample": [0.65, 0.8, 0.95, 1.0],
            "classifier__colsample_bytree": [0.65, 0.8, 0.95, 1.0],
        },
        # Eight representative random configurations per class-weight strategy
        # keep the reproducible search within the project's execution budget.
        n_iter=8,
        scoring="f1",
        cv=StratifiedKFold(n_splits=3, shuffle=True, random_state=RANDOM_STATE),
        random_state=RANDOM_STATE,
        # Keep CV single-process: parallel worker processes can exceed the
        # constrained deployment environment's memory budget.
        n_jobs=1,
        refit=True,
    )
    search.fit(X_train, y_train)
    return search


def main(input_path: Path, model_path: Path, importance_path: Path, metrics_path: Path, report_path: Path) -> None:
    data = pd.read_csv(input_path)
    if {ID, "ed_visit_count"}.difference(data.columns):
        raise ValueError("Input must contain BENE_ID and ed_visit_count.")
    data = add_utilization_features(data)
    data[TARGET], cutoff = proxy_target(data)
    feature_columns = [column for column in data.columns if column not in {ID, TARGET, *LEAKAGE_COLUMNS}]
    X, y = data[feature_columns], data[TARGET]
    X_train_full, X_test, y_train_full, y_test = train_test_split(
        X, y, test_size=0.20, stratify=y, random_state=RANDOM_STATE
    )

    # Reproduces the original pipeline exactly, on the same held-out split.
    class_ratio = (len(y_train_full) - y_train_full.sum()) / y_train_full.sum()
    baseline_exclusions = {
        "active_utilization_months",
        "inpatient_utilization_intensity",
        "provider_fragmentation_score",
        "cost_intensity",
    }
    baseline = Pipeline([
        ("preprocessor", make_preprocessor(X_train_full.drop(columns=list(baseline_exclusions)))),
        ("classifier", XGBClassifier(
            objective="binary:logistic", eval_metric="logloss", n_estimators=300, max_depth=4,
            learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
            scale_pos_weight=float(class_ratio), random_state=RANDOM_STATE, n_jobs=1,
        )),
    ])
    baseline_columns = [column for column in feature_columns if column not in baseline_exclusions]
    baseline.fit(X_train_full[baseline_columns], y_train_full)
    baseline_probability = baseline.predict_proba(X_test[baseline_columns])[:, 1]
    baseline_metrics = metrics(y_test, baseline_probability, 0.5)

    # Tune class-weight variants on a training-only development split. The held-out
    # test set is never used for feature, parameter, weight, or threshold choice.
    X_train, X_valid, y_train, y_valid = train_test_split(
        X_train_full, y_train_full, test_size=0.25, stratify=y_train_full, random_state=RANDOM_STATE
    )
    base_weight = (len(y_train) - y_train.sum()) / y_train.sum()
    weight_options = {"no_class_weight": 1.0, "current_scale_pos_weight": float(base_weight), "adjusted_scale_pos_weight": float(base_weight * 0.75)}
    # Cross-validate model complexity using the current weighting approach, then
    # isolate the class-weight comparison on the same training-only validation set.
    search = run_hyperparameter_search(X_train, y_train, weight_options["current_scale_pos_weight"])
    selected_search_params = search.best_params_
    experiments: dict[str, dict[str, object]] = {}
    for name, weight in weight_options.items():
        candidate = Pipeline([
            ("preprocessor", make_preprocessor(X_train)),
            ("classifier", XGBClassifier(
                objective="binary:logistic", eval_metric="logloss", random_state=RANDOM_STATE, n_jobs=1,
                scale_pos_weight=weight,
                **{key.removeprefix("classifier__"): value for key, value in selected_search_params.items()},
            )),
        ])
        candidate.fit(X_train, y_train)
        valid_probability = candidate.predict_proba(X_valid)[:, 1]
        threshold, threshold_table = choose_threshold(y_valid, valid_probability)
        experiments[name] = {
            "weight": weight,
            "best_cv_f1": float(search.best_score_),
            "best_params": selected_search_params,
            "validation_metrics": metrics(y_valid, valid_probability, threshold),
            "threshold": threshold,
            "threshold_table": threshold_table,
        }

    # Include the existing configuration with training-only threshold selection.
    # This tests whether probability calibration alone improves the current model.
    baseline_candidate = Pipeline([
        ("preprocessor", make_preprocessor(X_train)),
        ("classifier", XGBClassifier(
            objective="binary:logistic", eval_metric="logloss", random_state=RANDOM_STATE, n_jobs=1,
            scale_pos_weight=weight_options["current_scale_pos_weight"], **BASELINE_PARAMS,
        )),
    ])
    baseline_candidate.fit(X_train, y_train)
    baseline_candidate_probability = baseline_candidate.predict_proba(X_valid)[:, 1]
    baseline_threshold, baseline_threshold_table = choose_threshold(y_valid, baseline_candidate_probability)
    experiments["baseline_parameters_threshold_tuned"] = {
        "weight": weight_options["current_scale_pos_weight"],
        "best_cv_f1": None,
        "best_params": {f"classifier__{key}": value for key, value in BASELINE_PARAMS.items()},
        "validation_metrics": metrics(y_valid, baseline_candidate_probability, baseline_threshold),
        "threshold": baseline_threshold,
        "threshold_table": baseline_threshold_table,
    }

    # Select by validation F1 under an approximately 0.80-recall constraint.
    eligible = [item for item in experiments.items() if item[1]["validation_metrics"]["recall"] >= 0.80]
    selected_name, selected = max(eligible or experiments.items(), key=lambda item: (
        item[1]["validation_metrics"]["f1_score"], item[1]["validation_metrics"]["precision"]
    ))
    final_classifier_params = {
        key.removeprefix("classifier__"): value for key, value in selected["best_params"].items()
    }
    final_pipeline = Pipeline([
        ("preprocessor", make_preprocessor(X_train_full)),
        ("classifier", XGBClassifier(
            objective="binary:logistic", eval_metric="logloss", random_state=RANDOM_STATE, n_jobs=1,
            scale_pos_weight=selected["weight"], **final_classifier_params,
        )),
    ])
    final_pipeline.fit(X_train_full, y_train_full)
    test_probability = final_pipeline.predict_proba(X_test)[:, 1]
    tuned_metrics = metrics(y_test, test_probability, selected["threshold"])

    names = final_pipeline.named_steps["preprocessor"].get_feature_names_out()
    importances = final_pipeline.named_steps["classifier"].feature_importances_
    importance = pd.DataFrame({"feature": names, "importance": importances}).sort_values("importance", ascending=False)
    importance_path.parent.mkdir(parents=True, exist_ok=True)
    importance.to_csv(importance_path, index=False)

    report = {
        "model_purpose": "Historical utilization-pattern prioritization for proactive care navigation; not an emergency-necessity determination.",
        "target_definition": "high_utilization_pattern = ed_visit_count at or above annual dataset 90th percentile",
        "ed_visit_count_cutoff": cutoff,
        "observation_months_assumption": OBSERVATION_MONTHS,
        "excluded_from_training_for_leakage": sorted(LEAKAGE_COLUMNS | {ID}),
        "baseline_xgboost": baseline_metrics,
        "class_weight_experiments": experiments,
        "selected_experiment": selected_name,
        "selected_threshold": selected["threshold"],
        "tuned_xgboost": tuned_metrics,
        "model_comparison": [
            {"model": "Baseline XGBoost", **{k: baseline_metrics[k] for k in ("precision", "recall", "f1_score", "roc_auc")}},
            {"model": "Tuned XGBoost", **{k: tuned_metrics[k] for k in ("precision", "recall", "f1_score", "roc_auc")}},
        ],
        "top_feature_importance": importance.head(15).to_dict(orient="records"),
        "shap_analysis": "Not generated: the optional shap package is not installed in this environment. Tree feature importance is saved instead.",
        "interpretation": "Higher importance indicates historical member-level utilization patterns that most influenced model scores; it is not a causal or clinical explanation.",
    }
    with model_path.open("wb") as file:
        pickle.dump({"pipeline": final_pipeline, "feature_columns": feature_columns, "threshold": selected["threshold"], "report": report}, file)
    with metrics_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=2)
    report_path.write_text(
        "# Tuned XGBoost model comparison\n\n"
        "The optimized model identifies historical utilization patterns and prioritizes members for care navigation. It does not determine emergency necessity.\n\n"
        "| Model | Precision | Recall | F1 | ROC-AUC |\n|---|---:|---:|---:|---:|\n"
        f"| Baseline XGBoost | {baseline_metrics['precision']:.3f} | {baseline_metrics['recall']:.3f} | {baseline_metrics['f1_score']:.3f} | {baseline_metrics['roc_auc']:.3f} |\n"
        f"| Tuned XGBoost | {tuned_metrics['precision']:.3f} | {tuned_metrics['recall']:.3f} | {tuned_metrics['f1_score']:.3f} | {tuned_metrics['roc_auc']:.3f} |\n\n"
        f"Selected threshold: {selected['threshold']:.1f}. Threshold selection and model tuning used training-only development data; these metrics are from the held-out test set.\n",
        encoding="utf-8",
    )
    print(json.dumps(report["model_comparison"], indent=2))
    print(f"Selected class-weight experiment: {selected_name}; threshold: {selected['threshold']:.1f}")
    print(f"Saved {model_path}, {importance_path}, {metrics_path}, and {report_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("processed_data/utilization_features.csv"))
    parser.add_argument("--model-output", type=Path, default=Path("ml_models/xgboost_risk_model_tuned.pkl"))
    parser.add_argument("--importance-output", type=Path, default=Path("ml_models/xgboost_feature_importance_tuned.csv"))
    parser.add_argument("--metrics-output", type=Path, default=Path("ml_models/xgboost_metrics_tuned.json"))
    parser.add_argument("--report-output", type=Path, default=Path("ml_models/xgboost_model_comparison.md"))
    args = parser.parse_args()
    main(args.input, args.model_output, args.importance_output, args.metrics_output, args.report_output)
