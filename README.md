# Care Navigation Navigator

A safety-first healthcare utilization analytics prototype. It helps care-management teams identify members with historical high emergency-department utilization patterns so that they can prioritize proactive outreach and care navigation.

> The model does **not** determine whether emergency care is medically necessary. It does **not** recommend that a member avoid emergency care. For signs of an emergency, members should call 911 or seek emergency care immediately.

## Current status

This repository is ready to share as a frontend and offline-ML milestone.

- The user interface is a responsive, plain HTML/CSS/JavaScript application.
- Express provides the API and serves the static site.
- API responses currently use synthetic member and provider data from `src/data/mockCmsData.ts`.
- The XGBoost model is trained offline against the processed feature dataset; it is not yet connected to the live API or UI.
- No production database, authentication, or persistence logic has been implemented yet.

## Architecture

```text
public/ (HTML, CSS, JavaScript)
        |
        v
Express API (server.ts) ----> synthetic API data (current milestone)

Python ML pipeline (offline)
processed_data/utilization_features.csv --> XGBoost model artifacts
```

## Tech stack

- Frontend: plain HTML5, CSS3, and modular vanilla JavaScript
- Application server: Express with TypeScript
- ML/data pipeline: Python, pandas, NumPy, scikit-learn, XGBoost
- Optional AI integration: Google GenAI, controlled by `GEMINI_API_KEY`
- Dataset format: CSV member, inpatient, outpatient, and engineered feature files

React, Vite, Tailwind, Motion, Recharts, and their UI component sources have been removed. The project now has no frontend build framework.

## Run locally

Prerequisites:

- Node.js 22 or later
- Python 3.12 or later for the data and ML workflow

Install JavaScript dependencies and start the application:

```bash
npm install
npm run dev
```

Then open `http://localhost:3000`.

For a production build:

```bash
npm run build
npm start
```

## UI features

- Population overview and historical-utilization dashboard
- Searchable member cohort and member detail panel
- Care-navigation provider directory
- Safety-first symptom triage interface
- Care-plan generation workflow
- Care-manager copilot chat panel

The UI calls the existing Express endpoints under `/api/*`.

## ML workflow

Create a Python environment and install dependencies:

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

The baseline model identifies members in the top decile of historical ED visit counts:

```bash
.venv\Scripts\python src\train_risk_model.py
```

This produces the baseline model and related outputs locally:

- `ml_models/xgboost_risk_model.pkl`
- `ml_models/xgboost_feature_importance.csv`
- `processed_data/risk_predictions.csv`

The tuned experiment remains isolated in `src/train_risk_model_tuned.py`. The baseline pipeline and baseline artifacts are the current reference implementation.

### Leakage controls

The baseline model excludes these from training features:

- `BENE_ID`
- `ed_visit_count` (used to create the proxy label)
- `total_ed_related_cost` (a direct aggregate of ED encounters)

The tuned experiment additionally excludes ED-count-derived engineered features. This keeps the model focused on other historical utilization patterns rather than target leakage.

## Data workflow

Raw and processed CSV data are intentionally ignored by Git because the claims-derived files are large. Recreate local feature data with:

```bash
.venv\Scripts\python scratch\run_data_cleaning_safe.py
.venv\Scripts\python scratch\build_full_utilization_features.py
```

Both scripts use project-relative paths.

## Project layout

```text
public/                  Static plain HTML/CSS/JavaScript application
server.ts                Express API and static-site server
src/data/                Synthetic API data used by the current UI milestone
src/types.ts             Express API TypeScript types
src/train_risk_model.py  Baseline offline XGBoost training pipeline
scratch/                 Reproducible data cleaning and feature-building scripts
processed_data/          Local generated cleaned data, features, and predictions
ml_models/               Local generated model artifacts and reports
datasets/                Local raw source CSV datasets
notebooks/               Exploration and validation notebooks
```

## GitHub guidance

It is appropriate to push this milestone before backend database work. It establishes a clean frontend, API contract, and reproducible model-training foundation. The `.gitignore` excludes large local datasets, generated model outputs, environment files, dependency folders, caches, and build outputs.

Do not commit patient-identifiable data, API keys, `.env` files, or production credentials. Before a production deployment, add authenticated API access, a database-backed member service, audit logging, model serving/versioning, and a clinical/safety review.
