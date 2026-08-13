"""Train a historical ED-utilization-pattern classifier.

This is an analytic prioritization model only: its target is a transparent
historical utilization proxy, not a clinical determination of whether any ED
visit was necessary or avoidable.
"""

from __future__ import annotations

import argparse
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier


RANDOM_STATE = 42
TARGET_COLUMN = "high_utilization_pattern"
IDENTIFIER_COLUMN = "BENE_ID"

# ``ed_visit_count`` defines the target.  ``total_ed_related_cost`` is a direct
# aggregate of those same ED encounters, so both are excluded to prevent target
# leakage.  The model otherwise uses member-level utilization features only.
LEAKAGE_COLUMNS = {"ed_visit_count", "total_ed_related_cost"}


def build_proxy_target(ed_visit_count: pd.Series) -> tuple[pd.Series, float]:
    """Label members in the top decile of historical ED visit counts.

    Ties at the percentile cutoff are included deliberately; this is more
    transparent than arbitrarily splitting members with the same count.
    """
    cutoff = float(ed_visit_count.quantile(0.90))
    target = (ed_visit_count >= cutoff).astype(int)
    return target, cutoff


def main(
    input_path: Path,
    model_path: Path,
    predictions_path: Path,
    feature_importance_path: Path,
) -> None:
    data = pd.read_csv(input_path)
    required = {IDENTIFIER_COLUMN, "ed_visit_count"}
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"Input is missing required columns: {sorted(missing)}")
    if data[IDENTIFIER_COLUMN].isna().any() or data[IDENTIFIER_COLUMN].duplicated().any():
        raise ValueError("BENE_ID must be present and unique for member-level predictions.")

    data[TARGET_COLUMN], cutoff = build_proxy_target(
        pd.to_numeric(data["ed_visit_count"], errors="raise")
    )
    feature_columns = [
        column
        for column in data.columns
        if column not in {IDENTIFIER_COLUMN, TARGET_COLUMN, *LEAKAGE_COLUMNS}
    ]
    if not feature_columns:
        raise ValueError("No non-leaking training features are available.")

    X = data[feature_columns].copy()
    y = data[TARGET_COLUMN]
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=RANDOM_STATE, stratify=y
    )

    numeric_features = X.select_dtypes(include=["number", "bool"]).columns.tolist()
    categorical_features = [column for column in X.columns if column not in numeric_features]
    preprocessor = ColumnTransformer(
        transformers=[
            ("numeric", Pipeline([("imputer", SimpleImputer(strategy="median"))]), numeric_features),
            (
                "categorical",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("one_hot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
                    ]
                ),
                categorical_features,
            ),
        ],
        remainder="drop",
        verbose_feature_names_out=False,
    )

    # Weight positives according to their inverse prevalence in the training set.
    # This is neutral (1.0) when the classes are already balanced.
    positive_count = int(y_train.sum())
    negative_count = len(y_train) - positive_count
    scale_pos_weight = negative_count / positive_count if positive_count else 1.0
    classifier = XGBClassifier(
        objective="binary:logistic",
        eval_metric="logloss",
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos_weight,
        random_state=RANDOM_STATE,
        n_jobs=1,
    )
    pipeline = Pipeline([("preprocessor", preprocessor), ("classifier", classifier)])
    pipeline.fit(X_train, y_train)

    test_probability = pipeline.predict_proba(X_test)[:, 1]
    test_prediction = (test_probability >= 0.5).astype(int)
    metrics = {
        "accuracy": float(accuracy_score(y_test, test_prediction)),
        "precision": float(precision_score(y_test, test_prediction, zero_division=0)),
        "recall": float(recall_score(y_test, test_prediction, zero_division=0)),
        "f1_score": float(f1_score(y_test, test_prediction, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_test, test_probability)),
        "confusion_matrix": confusion_matrix(y_test, test_prediction).tolist(),
    }

    transformed_names = pipeline.named_steps["preprocessor"].get_feature_names_out()
    importances = pipeline.named_steps["classifier"].feature_importances_
    feature_importance = (
        pd.DataFrame({"feature": transformed_names, "importance": importances})
        .sort_values("importance", ascending=False, ignore_index=True)
    )

    # Score every member, while retaining split membership so held-out evaluation
    # results can be distinguished from in-sample scores.
    test_indices = set(X_test.index)
    probabilities = pipeline.predict_proba(X)[:, 1]
    predictions = pd.DataFrame(
        {
            IDENTIFIER_COLUMN: data[IDENTIFIER_COLUMN],
            TARGET_COLUMN: y,
            "predicted_high_utilization_pattern": (probabilities >= 0.5).astype(int),
            "high_utilization_probability": probabilities,
            "dataset_split": np.where(data.index.isin(test_indices), "test", "train"),
        }
    )

    model_path.parent.mkdir(parents=True, exist_ok=True)
    predictions_path.parent.mkdir(parents=True, exist_ok=True)
    feature_importance_path.parent.mkdir(parents=True, exist_ok=True)
    with model_path.open("wb") as model_file:
        pickle.dump(
            {
                "pipeline": pipeline,
                "feature_columns": feature_columns,
                "excluded_leakage_columns": sorted(LEAKAGE_COLUMNS),
                "target_definition": "ed_visit_count >= 90th percentile cutoff",
                "ed_visit_count_cutoff": cutoff,
                "class_balance": {"negative": negative_count, "positive": positive_count},
                "test_metrics": metrics,
                "feature_importance": feature_importance,
            },
            model_file,
        )
    predictions.to_csv(predictions_path, index=False)
    feature_importance.to_csv(feature_importance_path, index=False)

    print(f"Top-decile ED visit cutoff: {cutoff:g}")
    print(f"Class balance (all members): {y.value_counts().to_dict()}")
    print("Test metrics:")
    for name, value in metrics.items():
        print(f"  {name}: {value}")
    print("Feature importance (top 10):")
    print(feature_importance.head(10).to_string(index=False))
    print(f"Saved model: {model_path}")
    print(f"Saved predictions: {predictions_path}")
    print(f"Saved feature importance: {feature_importance_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("processed_data/utilization_features.csv"))
    parser.add_argument("--model-output", type=Path, default=Path("ml_models/xgboost_risk_model.pkl"))
    parser.add_argument("--predictions-output", type=Path, default=Path("processed_data/risk_predictions.csv"))
    parser.add_argument(
        "--feature-importance-output",
        type=Path,
        default=Path("ml_models/xgboost_feature_importance.csv"),
    )
    args = parser.parse_args()
    main(args.input, args.model_output, args.predictions_output, args.feature_importance_output)
