# Utilization Analytics Patterns

## Purpose

This document describes project-level, non-clinical utilization patterns identified during model development. It is intended to support care-navigation explanations and analytics. It must not be used as a clinical triage rule.

## XGBoost historical high-utilization pattern

The project defines the XGBoost proxy target as:

`high_utilization_pattern = ed_visit_count >= 90th percentile`

For the current training dataset, the observed cutoff was 9 ED visits.

The XGBoost model identifies historical high-ED-utilization patterns. It does not determine whether an ED encounter was medically necessary, avoidable, inappropriate, or appropriate.

## Isolation Forest utilization anomalies

Isolation Forest was trained as an unsupervised model to identify unusual utilization patterns independently of the XGBoost target.

Current training population: 8,671 members.

Selected configuration:

`IsolationForest(n_estimators=300, contamination=0.02, random_state=42)`

Current anomaly result:

- 174 members flagged as anomalies
- 2.01% of the population

Contamination sensitivity:

| Contamination | Anomalies | Approx. percentage |
|---|---:|---:|
| 0.01 | 87 | 1.00% |
| 0.02 | 174 | 2.01% |
| 0.05 | 434 | 5.01% |
| 0.10 | 867 | 10.00% |

A higher utilization anomaly score means the observed utilization pattern is more unusual within the modeled dataset. It is not a clinical risk probability.

## XGBoost / Isolation Forest complementarity

Post-hoc overlap:

| Group | Members |
|---|---:|
| High utilization + anomaly | 55 |
| High utilization + no anomaly | 827 |
| Low utilization + anomaly | 119 |
| Low utilization + no anomaly | 7,670 |

Derived measures:

- XGBoost high-utilization members: 882 (10.17%)
- Isolation Forest anomalies: 174 (2.01%)
- Anomalies also high-utilization: 55/174 = 31.61%
- High-utilization members also anomalies: 55/882 = 6.24%
- Anomaly-only members: 119/174 = 68.39%

These results demonstrate that the two models provide complementary analytical signals.

## Allowed interpretation

Utilization patterns can help prioritize care-navigation outreach and help describe historical utilization behavior.

They do not determine:

- medical necessity
- emergency necessity
- clinical deterioration
- inappropriate ED use
- ED avoidability

## Source

Project-generated analytical results from the Avoidable ED Utilization Navigator model-training pipeline.

This document is not a clinical guideline and should not be presented as one.
