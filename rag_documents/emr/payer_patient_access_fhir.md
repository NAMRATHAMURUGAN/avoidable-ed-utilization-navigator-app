# Payer Patient Access and Clinical Data Exchange

## Purpose

This document provides general information about how CMS-regulated payers make certain claims, encounter, and clinical information available through standards-based patient access APIs.

It is intended for healthcare interoperability and navigation context, not for clinical decision-making.

## Patient Access API

CMS states that impacted payers are required to make claims, encounter, and certain clinical data available through a standards-based Patient Access API.

CMS describes use of HL7 FHIR standards and related interoperability requirements.

## RAG boundary

This document does not authorize the application to retrieve external patient records.

The Avoidable ED Utilization Navigator should not send member-specific data to the RAG knowledge index.

If future integrations retrieve clinical data from an external EHR or payer API, that data should remain in the appropriate protected application/data layer and should not automatically become shared knowledge-base content.

## Source

CMS, Patient Access API:
https://www.cms.gov/priorities/burden-reduction/overview/interoperability/frequently-asked-questions/patient-access-api

CMS, Interoperability and Patient Access Fact Sheet:
https://www.cms.gov/newsroom/fact-sheets/interoperability-and-patient-access-fact-sheet
