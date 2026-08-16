# Synthetic EMR Structure for Demonstration

## Purpose

This document defines a synthetic, non-patient-specific EMR structure that may be used for RAG demonstrations, interface testing, and explanation examples.

It is not a real medical record and must not be treated as one.

## Suggested synthetic sections

A synthetic record may contain:

- encounter date/time
- encounter type
- presenting concern
- clinician-documented observations
- documented medications
- documented allergies
- documented diagnoses
- discharge/follow-up instructions
- care coordination notes
- referral information
- insurance/coverage context when relevant

## RAG boundary

RAG may use synthetic EMR examples to demonstrate how information can be organized or summarized.

RAG must not infer a diagnosis, emergency status, treatment plan, or medical necessity from a synthetic example.

## Privacy boundary

Never place real beneficiary identifiers, names, dates of birth, addresses, claims identifiers, or other patient-specific protected information into the vector knowledge base.

Patient/member-specific information should remain in the application data layer and should not be embedded into the shared knowledge index.

## Recommended metadata

For synthetic examples:

- document_id
- document_type = synthetic_emr
- audience
- created_for_demo = true
- source_type = synthetic
- version

Do not use real patient identifiers as metadata.
