# Database ingestion

`backend/ingest_ml_data.py` loads the repository's existing member-level ML-ready outputs into the PostgreSQL schema. It does not run models, create inference output, or import raw CMS claims.

## Sources and mappings

| Source | Tables |
|---|---|
| `processed_data/utilization_features.csv` | `members`, `member_utilization_snapshots` |
| `processed_data/risk_predictions.csv` | `xgboost_utilization_predictions` |
| `processed_data/anomaly_results.csv` | `utilization_anomaly_results` |
| `ml_models/xgboost_metrics.json` | XGBoost `model_runs` metadata |
| `ml_models/isolation_forest_metrics.json` and `ml_models/isolation_forest_feature_metadata.json` | Isolation Forest `model_runs` metadata |

The ingestion creates two reusable model-run records: the baseline XGBoost artifact (`ml_models/xgboost_risk_model.pkl`) and the Isolation Forest artifact (`ml_models/isolation_forest.pkl`). The XGBoost metadata supplies the historical high-utilization target (ED visits at or above the cutoff of 9); the Isolation Forest metadata supplies its selected configuration of 300 estimators, contamination `0.02`, and random state `42`.

## Validation and transaction behavior

Before it opens a write transaction, the script requires the exact source schemas, 8,671 non-null unique `BENE_ID` values in each source, and identical member sets. `BENE_ID` is handled only as a string business identifier. It validates numeric fields, non-negative counts and costs, binary labels/flags, probabilities in `[0, 1]`, positive anomaly ranks, and timezone-aware `generated_at` values.

If validation fails, nothing is inserted. Once validation succeeds, SQLAlchemy's transaction scope rolls back all persistence changes if an exception occurs. The initializer uses `create_all`, which only creates missing project tables and never drops tables, databases, or records.

## Idempotency

The ingestion finds existing members by `bene_id`, uses the existing aggregate snapshot for that member when present, identifies model runs by model type and artifact reference, and finds prediction/anomaly outputs by model run plus snapshot. Rerunning the same source data therefore updates the matching source-derived records rather than creating duplicates.

There is no observation date or claim period in the source CSV. Consequently the current ingest treats each member's utilization features as the single current member-level aggregate snapshot. A future historical-snapshot design needs a supplied source period before more than one independently identifiable snapshot per member can be ingested.

## Repository access

`MemberRepository` provides member lookup by `BENE_ID`, aggregate utilization lookup, XGBoost result lookup, anomaly result lookup, and `get_combined_result`, which returns the member, utilization snapshot, XGBoost historical-utilization output, and Isolation Forest anomaly output. `ModelRunRepository` supports lookup by ID and retrieval of the latest run for a model type. These repositories query PostgreSQL only and are independent of Flask.

## Interpretation boundaries

XGBoost identifies historical high-utilization patterns. Isolation Forest identifies unusual utilization patterns. Neither determines medical necessity, inappropriate ED use, or ED avoidability.

## Manual verification after ingestion

Run `python -m backend.ingest_ml_data` twice, then confirm all four member-derived tables contain 8,671 rows, `model_runs` contains two records, XGBoost high-utilization count is 882, anomaly count is 174, and the analytical overlap is 55 / 827 / 119 / 7,670 for high+anomaly / high-only / anomaly-only / neither.
