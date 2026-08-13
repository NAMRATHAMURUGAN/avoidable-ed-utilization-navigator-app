# Feature Dictionary

## Avoidable ED Utilization Navigator — Member-Level Feature Schema

This document defines all engineered features aggregated at the member level (`BENE_ID`) from CMS Medicare Enrollment and Claims Datasets (`beneficiary_clean.csv`, `inpatient_clean.csv`, `outpatient_clean.csv`). These features serve as the input vector for XGBoost High-ED Utilization Risk Stratification and Isolation Forest Anomaly Detection.

---

### 1. Member Profile Features

| Feature Name | Source Column(s) | Calculation Logic | Purpose in Model |
| :--- | :--- | :--- | :--- |
| `BENE_ID` | `beneficiary_clean.csv` (`BENE_ID`) | Unique string key per Medicare beneficiary. | Primary key / Member identifier. |
| `age` | `AGE_AT_END_REF_YR` or `BENE_BIRTH_DT` | `2022 - Year(BENE_BIRTH_DT)` or `AGE_AT_END_REF_YR` (default 65 if null). | Captures age-related health vulnerability and acuity patterns. |
| `gender` | `SEX_IDENT_CD` | Mapped `1` ➔ `'Male'`, `2` ➔ `'Female'` (default `'Unknown'`). | Demographic baseline control variable. |
| `dual_eligibility_months` | `DUAL_ELGBL_MONS` | Integer count of months eligible for Medicare & Medicaid dual coverage (0-12). | SDOH proxy for socio-economic complexity and care access barriers. |
| `chronic_condition_count` | `SP_CHF`, `SP_COPD`, `SP_DIABETES`, `SP_CHRNKIDN`, `SP_ISCHMCHT` | Sum of active chronic condition flags (`SP_* == 1`). | Measures multi-morbidity burden directly driving avoidable ED visits. |

---

### 2. Utilization Features

| Feature Name | Source Column(s) | Calculation Logic | Purpose in Model |
| :--- | :--- | :--- | :--- |
| `ed_visit_count` | `REV_CNTR` in `inpatient_clean.csv` & `outpatient_clean.csv` | Count of unique claims where Revenue Center `REV_CNTR == '0450'` (Emergency Room). | Primary signal for high-ED utilization patterns and target label baseline. |
| `inpatient_visit_count` | `inpatient_clean.csv` (`CLM_ID`) | Count of unique inpatient hospital admission claim IDs per member. | Measures high-acuity hospitalizations and severity. |
| `outpatient_visit_count` | `outpatient_clean.csv` (`CLM_ID`) | Count of unique outpatient service claim IDs per member (preserves raw CMS coding; does not force PCP label). | Measures routine clinic engagement vs. unmanaged care gaps. |

---

### 3. Financial & Cost Features

| Feature Name | Source Column(s) | Calculation Logic | Purpose in Model |
| :--- | :--- | :--- | :--- |
| `total_claim_payment_amount` | `CLM_PMT_AMT` across inpatient & outpatient | `Sum(inpatient_CLM_PMT_AMT) + Sum(outpatient_CLM_PMT_AMT)` | Total historic healthcare expenditure per member. |
| `total_ed_related_cost` | `CLM_PMT_AMT` for claims with `REV_CNTR == '0450'` | Sum of Medicare payments for claims with ED Revenue Center `0450`. | Identifies member-level ED expenditure for *Potential Utilization Opportunity* calculations. |
| `average_claim_cost` | `total_claim_payment_amount`, total visits | `total_claim_payment_amount / (inpatient_visit_count + outpatient_visit_count)` | Evaluates encounter intensity and detects high-cost anomaly outliers. |

---

### 4. Provider & Care Continuity Features

| Feature Name | Source Column(s) | Calculation Logic | Purpose in Model |
| :--- | :--- | :--- | :--- |
| `provider_count` | `PRVDR_NUM` across inpatient & outpatient | Count of distinct CMS facility/provider numbers (`PRVDR_NUM`) associated with member encounters. | Measures care fragmentation and doctor-shopping patterns vs. established PCP continuity. |

---

### 5. Temporal & Anomaly Features

| Feature Name | Source Column(s) | Calculation Logic | Purpose in Model |
| :--- | :--- | :--- | :--- |
| `utilization_span_days` | `CLM_FROM_DT` | `Max(CLM_FROM_DT) - Min(CLM_FROM_DT)` in days across all claims. | Captures duration of active health encounters over the observation period. |
| `active_utilization_months` | `CLM_FROM_DT` | Distinct count of `Year-Month` formatted encounter dates. | Distinguishes chronic year-round ED users from acute single-event clusters. |

---

> ⚠️ **Healthcare Data Normalization Note:**  
> Missing numeric values for financial fields were normalized to `0.0` strictly for numerical processing stability in XGBoost and Isolation Forest models. They should not be interpreted as confirmed zero patient financial responsibility.
