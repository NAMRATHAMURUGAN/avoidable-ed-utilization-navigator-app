# RAG Knowledge-Base Source Registry

## Scope

The RAG knowledge base supports general care navigation, Medicare
service navigation, patient education, and emergency-care safety
boundaries.

## Preferred source hierarchy

1.  CMS / Medicare.gov
2.  AHRQ
3.  Other U.S. government or authoritative clinical organizations added
    after review

## Current sources

  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
  Source                  Topic                   URL
  ----------------------- ----------------------- ---------------------------------------------------------------------------------------------------------------------
  Medicare.gov            Emergency department    https://www.medicare.gov/coverage/emergency-department-services
                          services                

  Medicare.gov            Urgently needed care    https://www.medicare.gov/coverage/urgently-needed-care

  Medicare.gov            Getting Medicare        https://www.medicare.gov/basics/get-started-with-medicare/using-medicare/how-to-get-medicare-services
                          services                

  Medicare.gov            How Medicare works      https://www.medicare.gov/basics/get-started-with-medicare/medicare-basics/how-does-medicare-work

  CMS                     Advanced Primary Care   https://www.cms.gov/medicare/payment/fee-schedules/physician-fee-schedule/advanced-primary-care-management-services
                          Management              

  AHRQ                    Care coordination       https://www.ahrq.gov/topics/care-coordination.html

  AHRQ                    Care Coordination       https://www.ahrq.gov/ncepcr/care/coordination/atlas/chapter2.html
                          Measures Atlas          
  ---------------------------------------------------------------------------------------------------------------------------------------------------------------------

## Retrieval metadata recommendation

Each chunk stored in the vector database should retain: - source_name -
source_url - topic - document_type - section - last_reviewed -
safety_level

## Safety-level suggestion

-   `general_information`: ordinary educational/navigation content
-   `coverage_information`: Medicare coverage and service information
-   `safety_boundary`: content controlling what the assistant must not
    claim
-   `care_coordination`: non-clinical navigation guidance

## RAG answer requirements

Retrieved answers should: - stay within the retrieved source content; -
cite or expose the source; - avoid patient-specific diagnosis; - avoid
claims that an ED visit is avoidable or unnecessary; - avoid treating ML
scores as clinical probabilities; - clearly distinguish utilization
analytics from medical decision-making.


## Added sources / documents

| Local document | Source | Topic |
|---|---|---|
| `rules_procedures/emtala_emergency_care_rights.md` | CMS | EMTALA emergency-care rights and procedures |
| `us_insurance_policies/medicare_advantage_networks_and_coverage.md` | Medicare.gov | Medicare Advantage coverage/network navigation |
| `us_insurance_policies/medicare_prior_authorization.md` | Medicare.gov / CMS | Prior authorization and plan rules |
| `emr/payer_patient_access_fhir.md` | CMS | Patient Access API and clinical data exchange |
| `clinical_guidelines/clinical_guidance_boundary.md` | Project policy | Scope and governance boundary for clinical guidance |
| `emr/synthetic_emr_structure.md` | Project-defined synthetic content | EMR demonstration structure |
| `utilization_patterns/utilization_analytics_patterns.md` | Project model-training results | Non-clinical utilization analytics |
