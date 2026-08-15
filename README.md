# Avoidable ED Utilization Navigator

A safety-first healthcare navigation product with two experiences: a consumer-facing **Patient / Member** app for symptom triage and care navigation, and a **Payer** analytics workspace for population-level ED utilization insight. A deterministic Python Safety Engine is the authoritative emergency-detection boundary in both experiences — it is never influenced by ML risk scores, cost optimization, or provider ranking.

> The system does **not** determine whether emergency care is medically necessary and does **not** recommend that anyone avoid emergency care. For signs of an emergency, always call 911 or go to the nearest emergency room.

## Current status

- **Backend**: Flask (`backend/app.py`) serves both the JSON API and the static frontend. This has fully replaced the earlier Express/TypeScript prototype (`server.ts`, `src/`), which remains in the repository as superseded reference code but is not run by the current application.
- **Database**: PostgreSQL, via SQLAlchemy models in `backend/models/`. The database is populated from offline-trained ML artifacts by `backend/ingest_ml_data.py` and currently holds real member, utilization, XGBoost prediction, and anomaly data (~8,671 members and related records).
- **ML**: XGBoost (utilization-risk classification) and Isolation Forest (anomaly detection) are trained offline (`src/train_risk_model.py`, `src/train_anomaly_model.py`) and their outputs are ingested into Postgres. The frontend never shows raw probabilities or anomaly scores — only categorical risk badges (High / Moderate / Low), and only in the Payer experience.
- **Safety Engine**: `backend/safety/engine.py` and `backend/safety/rules.py` are a deterministic, dependency-free rule engine (no ML, no database, no network) that is the hard safety boundary for the `/api/triage` endpoint. It is a verified behavioral parity port of the original TypeScript engine in `src/services/safetyEngine.ts`.
- **Frontend**: Plain HTML5, CSS3, and vanilla ES module JavaScript (`public/`), served directly by Flask. No build step, framework, or component library.
- **Provider / location discovery**: Not yet implemented against a real data source. The "Find care near you" screen shows an honest "integration pending configuration" state rather than fabricated hospitals, clinics, or appointment slots. It is architected to be backed by Google Maps / Places once credentials are configured — no such integration exists yet.
- **RAG / knowledge base**: The approved Markdown knowledge base can be explicitly embedded and indexed offline; it is not part of Flask startup and no retrieval or Gemini response generation is implemented. See [`docs/rag_ingestion.md`](docs/rag_ingestion.md).

## Product experiences

**Patient / Member** — a consumer navigation flow: enter your own profile (no CMS beneficiary lookup required), run a safety-first symptom assessment, see clear emergency guidance when warranted, and review your own care history. Never shown: the member population list, ML probabilities/anomaly scores, or payer analytics.

**Payer** — a population analytics workspace: aggregate ED utilization and spend (real, database-backed), risk-stratified member population, and audit trail of care navigation decisions. Backed by the same Postgres data as the Patient experience, but scoped to population-level and operational views.

## Architecture

```text
public/ (HTML, CSS, vanilla JavaScript ES modules)
        |
        v
Flask API + static server (backend/app.py)
        |
        +--> backend/routes/*  -> backend/services/* -> backend/repositories/* -> PostgreSQL
        |
        +--> backend/safety/engine.py  (deterministic emergency-detection boundary)

Python ML pipeline (offline, not part of the live request path)
processed_data/utilization_features.csv --> XGBoost + Isolation Forest artifacts (ml_models/)
        --> backend/ingest_ml_data.py --> PostgreSQL
```

## Tech stack

- Frontend: plain HTML5, CSS3, vanilla ES module JavaScript — no framework or build step
- Backend: Flask (Python)
- Database: PostgreSQL via SQLAlchemy
- ML/data pipeline: Python, pandas, NumPy, scikit-learn, XGBoost
- Planned (not yet implemented): Google Maps / Places provider discovery; Gemini-grounded RAG responses and Flask RAG integration

## Run locally

Prerequisites: Python 3.12+, a PostgreSQL database.

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Set `DATABASE_URL` in a `.env` file (see `.env.example`), then initialize the schema and ingest ML results:

```bash
.venv\Scripts\python backend\initialize_database.py
.venv\Scripts\python backend\ingest_ml_data.py
```

To index only the approved shared knowledge base after configuring the RAG
variables in `.env.example`, run:

```bash
.venv\Scripts\python backend\ingest_rag_documents.py
```

Start the app:

```bash
.venv\Scripts\python backend\app.py
```

Then open `http://localhost:5000`.

## Run tests

The Postgres-backed and in-memory-SQLite test suites live under `backend/tests/`; ingestion-boundary tests live under `tests/`. A plain `pytest` run only discovers `tests/` by default (see `pytest.ini`), so run the backend suite explicitly:

```bash
.venv\Scripts\python -m pytest backend/tests
.venv\Scripts\python -m pytest tests
```

`backend/tests/test_postgres_verification.py` and `tests/test_ingest_postgres_integration.py` are opt-in and require a configured `DATABASE_URL` (the latter also requires `RUN_POSTGRES_INGESTION_TESTS=1`); both skip cleanly otherwise.

## ML workflow

```bash
.venv\Scripts\python src\train_risk_model.py
.venv\Scripts\python src\train_anomaly_model.py
```

These produce the model artifacts in `ml_models/` and prediction/anomaly CSVs in `processed_data/`, which `backend/ingest_ml_data.py` loads into PostgreSQL.

### Leakage controls

The baseline risk model excludes these from training features: `BENE_ID`, `ed_visit_count` (used to build the proxy label), and `total_ed_related_cost` (a direct aggregate of ED encounters). The tuned experiment (`src/train_risk_model_tuned.py`) additionally excludes ED-count-derived engineered features.

## Data workflow

Raw and processed CSV data are intentionally ignored by Git (claims-derived files are large). Recreate local feature data with:

```bash
.venv\Scripts\python scratch\run_data_cleaning_safe.py
.venv\Scripts\python scratch\build_full_utilization_features.py
```

## Project layout

```text
public/                  Live frontend: plain HTML/CSS/vanilla JavaScript
backend/                 Flask API, services, repositories, SQLAlchemy models, Safety Engine
backend/safety/          Deterministic emergency-detection boundary (do not weaken)
backend/tests/           API, safety-engine, and navigation-history test suites
tests/                   ML ingestion boundary and Postgres integration tests
src/train_*.py           Offline ML training pipelines (XGBoost, Isolation Forest)
src/services/            Superseded TypeScript reference implementation (not run)
server.ts                Superseded Express prototype (not run; kept for reference)
scratch/                 Reproducible data cleaning and feature-building scripts
processed_data/          Local generated cleaned data, features, and predictions
ml_models/                Local generated model artifacts and reports
datasets/                Local raw source CSV datasets
notebooks/                Exploration, validation, and RAG-preparation notebooks (08 is a placeholder)
rag_documents/            Approved Markdown sources for offline RAG knowledge-base ingestion
```

## What's implemented vs. planned

| Area | Status |
|---|---|
| Flask API + PostgreSQL persistence | Implemented |
| XGBoost risk model + Isolation Forest anomaly detection, ingested into Postgres | Implemented |
| Deterministic Safety Engine + emergency triage flow | Implemented |
| Patient self-entry profile, symptom triage, care history | Implemented |
| Payer population analytics (members, ED visits, ED spend), risk stratification | Implemented |
| Google Maps / Places provider discovery | Planned — integration boundary only, no credentials configured |
| RAG knowledge-base ingestion | Implemented offline — Markdown parsing, embeddings, and Pinecone upsert; no live ingestion without configured credentials/index |
| Gemini-grounded RAG responses / Flask integration | Planned — no generation, retrieval, or RAG endpoint yet |

## GitHub guidance

Do not commit patient-identifiable data, API keys, `.env` files, or production credentials. Before a production deployment, add authenticated API access, audit logging, model serving/versioning, and a clinical/safety review.
