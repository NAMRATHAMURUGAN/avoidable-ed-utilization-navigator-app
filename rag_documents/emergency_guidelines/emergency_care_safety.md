# Emergency-Care Safety Boundary

## Purpose

This document establishes the safety boundary for the RAG component.

## Core rule

The project does NOT determine whether an ED visit is medically
necessary, avoidable, inappropriate, or appropriate.

The machine-learning components identify historical utilization patterns
and unusual utilization patterns. These signals must never be presented
as clinical triage or as a recommendation to avoid emergency care.

## Response behavior

If a user describes a potentially serious or rapidly worsening
situation: - do not reassure the user that the situation is safe; - do
not tell the user to wait for a routine appointment; - do not tell the
user to avoid the emergency department; - encourage seeking appropriate
professional medical evaluation; - if the situation appears to require
immediate emergency attention, advise contacting local emergency
services or going to an emergency department.

The RAG system should use authoritative emergency-care guidance when
available and should avoid generating symptom-specific diagnostic
conclusions.

## Emergency versus urgent-care terminology

Medicare.gov describes emergency department services as services for an
injury, sudden illness, or illness that quickly gets much worse. It
describes urgently needed care as care for a sudden illness or injury
that is not a medical emergency and/or life-threatening.

These descriptions are general coverage/service descriptions. They are
not a substitute for clinical triage.

## Required project wording

Use wording such as: "Utilization patterns can help prioritize
care-navigation outreach, but they do not determine medical necessity or
whether an ED visit should be avoided."

Do not use wording such as: - "The model says you do not need the ER." -
"Your ED visit was unnecessary." - "The model has determined your
condition is non-emergency." - "You should avoid emergency care."

## Source

-   Medicare.gov, Emergency Department Services:
    https://www.medicare.gov/coverage/emergency-department-services
-   Medicare.gov, Urgently Needed Care:
    https://www.medicare.gov/coverage/urgently-needed-care
