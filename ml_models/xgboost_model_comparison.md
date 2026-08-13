# Tuned XGBoost model comparison

The optimized model identifies historical utilization patterns and prioritizes members for care navigation. It does not determine emergency necessity.

| Model | Precision | Recall | F1 | ROC-AUC |
|---|---:|---:|---:|---:|
| Baseline XGBoost | 0.608 | 0.898 | 0.725 | 0.976 |
| Tuned XGBoost | 0.620 | 0.864 | 0.722 | 0.975 |

Selected threshold: 0.3. Threshold selection and model tuning used training-only development data; these metrics are from the held-out test set.
