# RightPath — Avoidable Emergency Department Utilization Navigator

RightPath is a **safety-first healthcare navigation and payer intelligence platform** designed to address two connected problems:

1. Helping members understand the appropriate next step for their current symptoms and navigate to suitable care.
2. Helping healthcare payers understand population-level Emergency Department (ED) utilization, identify high-utilization patterns, and surface unusual utilization patterns for review.

The platform combines **healthcare data engineering, machine learning, anomaly detection, deterministic safety rules, AI-assisted guidance, provider discovery, and payer analytics** in a single application.

> **Safety principle:** RightPath does not determine whether emergency care is medically necessary and does not tell users to avoid emergency care. Emergency warning signs are handled by a deterministic Safety Engine that is independent of ML predictions, cost optimization, provider ranking, and AI-generated responses.

---

## 🎯 Problem

Emergency Departments are sometimes used for situations that may be better handled through other care pathways such as:

* Telehealth
* Primary care
* Retail clinics
* Urgent care

At the same time, healthcare payers need better visibility into utilization patterns across their member population.

A useful solution therefore needs to address **both sides**:

### Member side

A member needs:

* A safe symptom assessment
* Clear emergency guidance when warning signs are present
* A recommended care pathway
* Nearby care options
* Navigation support
* A personal history of previous assessments and navigation actions

### Payer side

A payer needs:

* Population-level ED utilization analytics
* ED spend visibility
* Member utilization segmentation
* High-utilization pattern identification
* Anomaly detection
* RightPath navigation activity and impact analytics
* An AI-assisted payer intelligence interface

---

# 🏗️ Solution Overview

RightPath provides two connected experiences:

### 👤 Patient / Member Experience

The member can:

1. Create or enter their profile
2. Describe their symptoms
3. Go through a safety-first triage flow
4. Receive emergency guidance when red flags are detected
5. Receive an appropriate care pathway when no emergency warning signs are identified
6. Find nearby care options
7. View their previous care-navigation history
8. Ask the RightPath Care Assistant questions about their recommendation

### 📊 Payer Intelligence Experience

The payer can:

1. View population-level utilization metrics
2. Analyze ED visits and ED-related spending
3. View members by utilization-risk category
4. Review unusual utilization patterns detected by Isolation Forest
5. Track RightPath navigation activity
6. View pathway and acuity distributions
7. Monitor daily navigation trends
8. View RightPath impact metrics
9. Ask the Payer Intelligence Assistant questions using aggregate analytics and approved knowledge sources

---

# 🧠 Machine Learning Pipeline

The ML pipeline works offline and produces member-level analytical results that are subsequently loaded into PostgreSQL.

```text
CMS Synthetic Medicare Data
            |
            v
Data Cleaning & Feature Engineering
            |
            v
Member-Level Utilization Features
            |
      +-----+------+
      |            |
      v            v
   XGBoost    Isolation Forest
      |            |
      v            v
Utilization     Anomaly
Prediction      Detection
      |            |
      +-----+------+
            |
            v
      PostgreSQL
            |
            v
      Flask APIs
            |
            v
     Payer Portal
```

---

# 📚 Data & Feature Engineering

The project uses CMS Synthetic Medicare beneficiary and claims data.

The preprocessing pipeline combines information from:

* Beneficiary data
* Inpatient claims
* Outpatient claims

The raw claims are transformed into a **member-level utilization feature dataset**.

### Member profile features

Examples include:

* Age
* Gender
* Dual-eligibility months
* Chronic-condition count

### Inpatient utilization features

Examples include:

* Inpatient visit count
* Inpatient total cost
* Inpatient provider count
* ED-related inpatient utilization

### Outpatient utilization features

Examples include:

* Outpatient visit count
* Outpatient total cost
* Outpatient provider count
* ED-related outpatient utilization

### Derived utilization features

Examples include:

* Total ED visit count
* Total claim payment amount
* Total ED-related cost
* Average claim cost
* Provider count

The resulting dataset is stored as:

`processed_data/utilization_features.csv`

---

# 🤖 XGBoost — High-Utilization Pattern Detection

XGBoost is used as a **supervised classification model** to identify members exhibiting a historically high ED-utilization pattern.

Because the source data does not provide a direct clinical label for “high utilization,” the project creates a transparent analytical proxy.

### Target definition

The model uses the **90th percentile of historical ED visit counts**.

Members whose historical ED visit count is at or above that threshold are labelled:

```text
high_utilization_pattern = 1
```

Others are labelled:

```text
high_utilization_pattern = 0
```

This is a **historical utilization proxy**, not a clinical risk or medical-necessity label.

### Leakage prevention

The following information is excluded from the training features:

* `BENE_ID`
* `ed_visit_count`
* `total_ed_related_cost`

This prevents the model from directly receiving information used to construct the target.

### Preprocessing

Numerical features are handled using median imputation.

Categorical features are handled using most-frequent imputation followed by one-hot encoding.

### Model

The XGBoost classifier uses:

* Binary logistic objective
* 300 estimators
* Maximum depth of 4
* Learning rate of 0.05
* Subsampling
* Column subsampling
* Class-imbalance weighting

### Evaluation

The model is evaluated using:

* Accuracy
* Precision
* Recall
* F1-score
* ROC-AUC
* Confusion matrix

The resulting member-level predictions include a high-utilization probability and predicted class.

---

# 🔎 Isolation Forest — Utilization Anomaly Detection

Isolation Forest is used for a different purpose.

Instead of asking:

> “Does this member belong to the historically high-utilization group?”

it asks:

> **“Does this member's overall utilization pattern look unusual compared with the population?”**

This is an **unsupervised anomaly-detection problem**.

### Leakage-safe features

The anomaly model excludes:

* `BENE_ID`
* `high_utilization_pattern`
* `ed_visit_count`
* `total_ed_related_cost`

Additional utilization features are created, including:

* Recorded visit count
* Cost per recorded visit
* Provider fragmentation ratio
* Inpatient visit share

### Contamination selection

Several contamination levels were evaluated:

* 1%
* 2%
* 5%
* 10%

A conservative **2% review cohort** was selected for the final model.

This threshold is an operational prioritization choice; it is **not** a claim that exactly 2% of members are clinically anomalous.

### Important interpretation

An anomaly score means that the utilization pattern is unusual relative to the fitted population.

It does **not** mean:

* The member is clinically high-risk
* The member's ED visit was unnecessary
* The member's ED visit was avoidable
* The member has a particular medical condition

---

# 🔄 Why Two ML Models?

The two models answer different questions.

| Model            | Purpose                                         | Learning Type |
| ---------------- | ----------------------------------------------- | ------------- |
| XGBoost          | Identify historically high-utilization patterns | Supervised    |
| Isolation Forest | Identify unusual utilization patterns           | Unsupervised  |

This allows the payer to distinguish between:

* High utilization but otherwise typical behavior
* High utilization combined with unusual behavior
* Lower utilization but unusual behavior
* Normal utilization patterns

The project also performs post-hoc overlap analysis between the two model outputs.

---

# 🛡️ Safety Engine

Safety is intentionally separated from the ML layer.

RightPath uses a **deterministic Safety Engine** for emergency warning-sign detection.

The engine:

* Does not use ML
* Does not use the database
* Does not depend on network services
* Uses explicit safety rules
* Checks symptom text and selected red flags
* Produces a deterministic emergency decision

The Safety Engine is the authoritative safety boundary for the triage flow.

If an emergency warning sign is detected, the application provides emergency guidance rather than attempting to route the user toward a lower-acuity option.

The system also includes improved natural-language safety screening and handles negated symptom expressions to reduce incorrect emergency matches.

---

# 🤖 RightPath AI Assistants

The application includes two structurally separated assistant experiences.

## Patient Care Assistant

The Patient Assistant helps explain an already-established care recommendation.

It:

* Receives only the current patient's relevant triage context
* Does not diagnose
* Does not override the safety decision
* Does not downgrade an emergency recommendation
* Re-checks newly entered messages for emergency warning signs
* Can provide deterministic explanations for common pathway questions
* Uses Gemini for appropriate open-ended guidance
* Provides a safe deterministic fallback if AI generation is temporarily unavailable

The assistant does **not** run a second clinical decision-making process.

---

## Payer Intelligence Assistant

The Payer Assistant works only with:

* Aggregate CMS analytics
* Aggregate RightPath program analytics
* Approved knowledge-base content

It does not receive patient-level information.

A lightweight routing layer first determines whether a question is:

### Analytics-oriented

These questions can often be answered directly from existing aggregate analytics without invoking the RAG/Gemini path.

### Knowledge-oriented

These questions can use the approved knowledge base and RAG pipeline.

This routing reduces unnecessary AI calls and improves response efficiency.

The assistant is explicitly instructed not to:

* Reveal patient-level information
* Claim that navigation automatically prevented an ED visit
* Present potential cost opportunity as realized savings
* Treat ML utilization signals as clinical diagnoses
* Fabricate missing information

---

# 📍 Care & Provider Navigation

The member-facing application provides a **Find Care Near You** experience.

### Urgent-care / hospital discovery

Uses:

**OpenStreetMap Overpass API**

### Route distance and driving duration

Uses:

**OpenRouteService**

If the routing API is unavailable or not configured, the application reports that honestly rather than generating a fabricated route.

### Other care settings

The application supports provider information for:

* Telehealth
* Primary care
* Retail clinics

These are backed by the application's database-backed Provider model.

---

# 📊 Payer Intelligence & Impact Analytics

The payer portal provides population-level analytics including:

* Member population
* ED utilization
* ED visits
* ED spending
* Risk-stratified population
* Utilization anomalies
* Navigation activity
* Acuity distribution
* Care-pathway distribution
* Daily navigation trends

RightPath also tracks navigation actions and provides aggregate program-level impact metrics.

Potential cost opportunity is clearly distinguished from actual realized savings.

---

# 🗄️ Backend Architecture

The current backend is built with:

**Flask + Python**

The backend exposes separate route groups for:

* Authentication
* Patient/member operations
* Profiles
* Triage
* Navigation
* Providers
* ML results
* Analytics
* Payer analytics
* Patient assistant
* Payer assistant

The application uses a layered structure involving:

```text
Routes
  ↓
Services
  ↓
Repositories
  ↓
PostgreSQL
```

---

# 🗃️ Database

The application uses:

**PostgreSQL**

with:

**SQLAlchemy**

The database stores application and analytical information including:

* Members
* Patient profiles
* Utilization snapshots
* XGBoost predictions
* Isolation Forest anomaly results
* Model runs
* Providers
* Triage encounters
* Navigation actions
* Assistant-related application context

The ML results are generated offline and then ingested into PostgreSQL through a validation layer.

---

# 🔐 Authentication & Privacy

The application maintains separate patient and payer experiences.

Role-based authorization is used to prevent payer-only analytics from being exposed to patient users.

The patient and payer assistant endpoints are also structurally separated rather than relying on a single unrestricted chat endpoint.

The payer assistant receives aggregate information only and is not given patient-level records.

---

# 💻 Frontend

The current frontend uses:

* HTML5
* CSS3
* Vanilla JavaScript
* ES modules

No frontend framework or build step is required.

Flask serves the frontend directly.

---

# 🧪 Testing & Validation

The repository contains automated tests covering areas including:

* API behavior
* Safety Engine behavior
* Navigation history
* ML data-ingestion boundaries
* PostgreSQL integration
* Provider navigation

The ML ingestion layer validates source schemas and member-ID consistency before database transactions are performed.

---

# 🛠️ Technology Stack

### Data & Machine Learning

* Python
* Pandas
* NumPy
* Scikit-learn
* XGBoost
* Isolation Forest

### Backend

* Flask
* SQLAlchemy
* PostgreSQL

### Frontend

* HTML5
* CSS3
* Vanilla JavaScript
* ES Modules

### AI / Knowledge

* Gemini
* RAG
* Pinecone
* Approved Markdown knowledge base

### External Services

* OpenStreetMap Overpass API
* OpenRouteService

### Development

* Git
* GitHub
* Pytest

---

# 📁 Project Structure

```text
frontend/
    Live patient and payer frontend

backend/
    Flask application
    Authentication
    Routes
    Services
    Repositories
    Database models

backend/safety/
    Deterministic emergency safety engine
    Safety rules
    Safety NLP normalization

backend/rag/
    Knowledge-base ingestion
    Embeddings
    Pinecone integration
    Retrieval

src/
    Offline ML training pipelines

scratch/
    Data cleaning
    Feature engineering

processed_data/
    Generated utilization features
    ML predictions
    Anomaly results

ml_models/
    Trained model artifacts
    Model metrics
    Feature metadata

datasets/
    Source datasets

notebooks/
    Data exploration and analysis

docs/
    Project documentation
```

---

# 🚀 Running the Project

### 1. Create a virtual environment

```bash
python -m venv .venv
```

### 2. Install dependencies

```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

### 3. Configure environment variables

Create a `.env` file based on:

```text
.env.example
```

Configure the required PostgreSQL and external-service credentials.

### 4. Initialize the database

```bash
.venv\Scripts\python backend\initialize_database.py
```

### 5. Train / generate ML artifacts

```bash
.venv\Scripts\python src\train_risk_model.py
.venv\Scripts\python src\train_anomaly_model.py
```

### 6. Ingest ML results

```bash
.venv\Scripts\python backend\ingest_ml_data.py
```

### 7. Start the application

```bash
.venv\Scripts\python backend\app.py
```

Then open:

```text
http://localhost:5000
```

---

# 🔬 Key Design Principles

### Safety First

Emergency detection is deterministic and independent of ML.

### Explainable ML Purpose

ML identifies utilization patterns and anomalies; it does not make clinical diagnoses.

### Leakage Prevention

Features directly derived from the proxy target are excluded from model training.

### Privacy by Design

Patient and payer contexts are kept separate.

### Honest System Behavior

Unavailable external services and AI failures produce explicit fallback states rather than fabricated results.

### Offline ML + Online Application

Models are trained offline, while the application consumes their stored outputs through PostgreSQL.

---

# ⚠️ Scope & Limitations

RightPath is a **decision-support and navigation prototype**, not a clinical diagnostic system.

The ML models identify:

* Historical high-utilization patterns
* Unusual utilization patterns

They do not determine:

* Medical necessity
* Clinical diagnosis
* Whether an ED visit was definitively avoidable
* Whether a member should ignore emergency symptoms

Similarly, navigation activity or estimated cost opportunity should not be interpreted as proof that an ED visit was prevented or that savings were actually realized.

Before production deployment, the system would require additional work around:

* Production authentication and identity management
* Comprehensive audit logging
* Model versioning and monitoring
* Clinical/safety review
* Production infrastructure
* Security hardening
* Validation with appropriate real-world clinical and operational data

---

# 🎯 Project Outcome

RightPath brings together **healthcare data engineering, supervised learning, unsupervised anomaly detection, deterministic safety logic, AI-assisted guidance, provider navigation, and payer analytics** into one end-to-end application.

The key architectural idea is simple:

> **Use ML to understand utilization patterns, use deterministic rules to protect the safety boundary, and use the application layer to turn those signals into actionable navigation and payer intelligence.**
