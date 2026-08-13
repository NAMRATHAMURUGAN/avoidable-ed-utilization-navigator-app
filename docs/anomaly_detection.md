# Isolation Forest anomaly detection

## Objective

This component detects unusual healthcare utilization patterns in the observed member-level data. It is an unsupervised analytical signal for care-navigation review.

Isolation Forest detects unusual utilization patterns within the observed data. An anomaly is not evidence of medical necessity, clinical deterioration, inappropriate ED use, or ED avoidability.

## Relationship to XGBoost

The existing XGBoost model uses the historical proxy target `high_utilization_pattern = ed_visit_count >= 90th percentile`. Isolation Forest has no target and never trains on XGBoost scores or predictions. The post-hoc overlap file categorizes the two independent signals; it is not a clinical classification.

## Features and leakage prevention

The model uses demographic and utilization features from `utilization_features.csv`: age, gender (one-hot encoded), dual-eligibility months, chronic-condition count, inpatient/outpatient visit and cost measures, total claim payment, average claim cost, and provider count. It also derives only these transparent non-ED ratios:

- `recorded_visit_count = inpatient_visit_count + outpatient_visit_count`
- `cost_per_recorded_visit = total_claim_payment_amount / max(inpatient_visit_count + outpatient_visit_count, 1)`
- `provider_fragmentation_ratio = provider_count / max(inpatient_visit_count + outpatient_visit_count, 1)`
- `inpatient_visit_share = inpatient_visit_count / max(inpatient_visit_count + outpatient_visit_count, 1)`

It excludes `BENE_ID`, `high_utilization_pattern`, `ed_visit_count`, and `total_ed_related_cost`. Categorical variables are one-hot encoded; numeric values are median-imputed and standardized.

## Configuration and validation

The script compares contamination values 0.01, 0.02, 0.05, and 0.10, reporting counts and score distributions. It selects 0.02 as a conservative, non-clinical review cohort—not by optimizing against labels or an accuracy metric. The final configuration is `IsolationForest(n_estimators=300, contamination=0.02, random_state=42, n_jobs=1)`.

Higher `anomaly_score` means a member's observed utilization pattern is more unusual to the fitted model. It is a utilization anomaly score, not a probability or clinical risk score. Because there is no ground-truth anomaly label, accuracy, precision, recall, and F1 are not reported. Instead, the outputs report anomaly count, percentage, score distribution, contamination comparison, and descriptive normal-versus-anomalous profiles.

Isolation Forest does not have native feature importances comparable to XGBoost. `isolation_forest_anomaly_profile.csv` provides descriptive feature profiles only; it must not be read as causal importance.

## Temporal methodology and limitation

Temporal anomaly periods require real claim/service dates. At this run, the cleaned inpatient and outpatient claim files are unavailable, while the feature file is member-level aggregated. Therefore no anomaly start/end dates or temporal results are generated; no dates are inferred.

## Outputs

The training script writes the model, metadata, metrics/summary, member-level anomaly results, a normal-versus-anomalous feature profile, and the post-hoc XGBoost overlap analysis. Existing XGBoost artifacts are read only for the post-hoc overlap and are never modified.
