# RAG Knowledge Base

Ready-to-ingest source-backed Markdown documents for the Avoidable ED Utilization Navigator.

Folders:
- `care_navigation/` — care coordination and Medicare service navigation
- `emergency_guidelines/` — emergency-care safety boundaries
- `patient_education/` — general patient education and navigation prompts
- `rag_source_registry.md` — source registry and metadata recommendations

These documents intentionally avoid clinical diagnosis, emergency-necessity decisions, and instructions to avoid emergency care.

Primary sources: CMS, Medicare.gov, and AHRQ.

Additional folders:
- `emr/` — synthetic EMR structure and interoperability context; no real patient data
- `clinical_guidelines/` — clinical-guidance boundary and metadata requirements
- `rules_procedures/` — authoritative emergency-care rules/procedures such as EMTALA
- `us_insurance_policies/` — general Medicare/Medicare Advantage policy and navigation information

These additions are source-backed summaries or project-defined synthetic content.
They are not a substitute for patient-specific clinical, legal, or plan-specific advice.
