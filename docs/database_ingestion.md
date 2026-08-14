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

Before it opens a write transaction, the script requires the exact source schemas, a non-empty set of non-null unique `BENE_ID` values in each source, and identical member sets. `BENE_ID` is handled only as a string business identifier. It validates numeric fields, non-negative counts and costs, binary labels/flags, probabilities in `[0, 1]`, positive anomaly ranks, and timezone-aware `generated_at` values.

There is no hard-coded member-count requirement. The current 8,671-member source dataset is valid, and a smaller or larger valid dataset is also valid. Empty files are rejected.

## Dataset identity and model-run provenance

For each ingestion, the importer calculates a SHA-256 dataset identifier from the three source files and records an ingestion context in the existing `model_runs.configuration` JSON. The context contains the dataset identifier, ingestion timestamp, source filenames, and member count. Existing `model_runs` fields continue to hold the model type, model version where available, and artifact reference. This is a lightweight provenance record and does not require a new table or schema change.

If validation fails, nothing is inserted. Once validation succeeds, SQLAlchemy's transaction scope rolls back all persistence changes if an exception occurs. The initializer uses `create_all`, which only creates missing project tables and never drops tables, databases, or records.

## Idempotency

The ingestion finds existing members by `bene_id`, uses the existing aggregate snapshot for that member when present, identifies model runs by model type and artifact reference, and finds prediction/anomaly outputs by model run plus snapshot. Rerunning the same source data therefore updates the matching source-derived records rather than creating duplicates.

There is no observation date or claim period in the source CSV. Consequently the current ingest treats each member's utilization features as the single current member-level aggregate snapshot. A future historical-snapshot design needs a supplied source period before more than one independently identifiable snapshot per member can be ingested.

## Repository access

`MemberRepository` provides member lookup by `BENE_ID`, aggregate utilization lookup, XGBoost result lookup, anomaly result lookup, and `get_combined_result`, which returns the member, utilization snapshot, XGBoost historical-utilization output, and Isolation Forest anomaly output. `ModelRunRepository` supports lookup by ID and retrieval of the latest run for a model type. These repositories query PostgreSQL only and are independent of Flask.

Future Flask routes can use these repository methods for member, demographic/context, utilization, latest XGBoost result, latest Isolation Forest result, and model-metadata retrieval. Runtime user symptoms or queries belong to a future application/service layer; they are not features of the existing XGBoost or Isolation Forest models.

## Interpretation boundaries

XGBoost identifies historical high-utilization patterns. Isolation Forest identifies unusual utilization patterns. Neither determines medical necessity, inappropriate ED use, or ED avoidability.

## Manual verification after ingestion

Run `python -m backend.ingest_ml_data` twice. For the current source dataset, confirm all four member-derived tables contain 8,671 rows, `model_runs` contains two records, XGBoost high-utilization count is 882, anomaly count is 174, and the analytical overlap is 55 / 827 / 119 / 7,670 for high+anomaly / high-only / anomaly-only / neither.

## Tests

Run `python -m pytest tests -q` for temporary-fixture validation tests. They cover a valid small dataset, the current 8,671-member dataset, duplicate IDs, missing columns, mismatched member sets, invalid numeric values, and empty files. Invalid-source tests verify that validation fails before the importer calls schema initialization or opens a persistence transaction.

The opt-in PostgreSQL idempotency check never drops or clears data. Run it only against the approved configured database with `RUN_POSTGRES_INGESTION_TESTS=1 python -m pytest tests/test_ingest_postgres_integration.py -q`. It runs the approved importer twice, then verifies table counts and the known current-source XGBoost/anomaly totals.
