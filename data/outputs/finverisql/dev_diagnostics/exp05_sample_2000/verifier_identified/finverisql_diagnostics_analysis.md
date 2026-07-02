# FinVeriSQL Diagnostics Analysis

## Input Summary

- Files read: 1
- Rows read: 1701
- `verified_sample_seed42_nl_only_compact_probe.jsonl`: 1701 rows

## Mode Performance Comparison

Probe Gain / Cost is the accuracy gain over the `direct` baseline, measured in percentage points, divided by the additional average probes used per query. Higher is better, but values can be large when probe usage is very low.

| Mode | N | Label Mix | Accuracy | Macro F1 | Accept Precision | Accept Recall (Group A) | Accept F1 | Reject Precision | Reject Recall (Group B) | Reject F1 | Avg Probes / Query | Probe Gain / Cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| probe | 1701 | A=1172, B=529 | 1130/1701 (66.4%) | 65.0% | 740/878 (84.3%) | 740/1172 (63.1%) | 72.2% | 390/820 (47.6%) | 390/529 (73.7%) | 57.8% | 0.26 | baseline |

## Operational Signals

| Mode | Ambiguous A | Ambiguous B | Abstain A | Abstain B |
|---|---:|---:|---:|---:|
| probe | 2/1172 (0.2%) | 1/529 (0.2%) | 2/1172 (0.2%) | 1/529 (0.2%) |

## High-Confidence Accuracy

| Mode | N | High Confidence | High Confidence Precision | High Confidence Right | High Confidence Wrong | Non-High Confidence |
|---|---:|---:|---:|---:|---:|---:|
| probe | 1701 | 1565/1701 (92.0%) | 68.1% | 1066/1565 (68.1%) | 499/1565 (31.9%) | 136/1701 (8.0%) |
