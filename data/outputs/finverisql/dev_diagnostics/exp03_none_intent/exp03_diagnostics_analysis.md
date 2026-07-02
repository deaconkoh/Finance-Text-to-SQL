# FinVeriSQL Diagnostics Analysis

## Input Summary

- Files read: 6
- Rows read: 120
- `verified_groupA_direct_sample20.jsonl`: 20 rows
- `verified_groupA_hybrid_sample20.jsonl`: 20 rows
- `verified_groupA_probe_sample20.jsonl`: 20 rows
- `verified_groupB_direct_sample20.jsonl`: 20 rows
- `verified_groupB_hybrid_sample20.jsonl`: 20 rows
- `verified_groupB_probe_sample20.jsonl`: 20 rows

## Mode Performance Comparison

Probe Gain / Cost is the accuracy gain over the `direct` baseline, measured in percentage points, divided by the additional average probes used per query. Higher is better, but values can be large when probe usage is very low.

| Mode | N | Label Mix | Accuracy | Macro F1 | Accept Precision | Accept Recall (Group A) | Accept F1 | Reject Precision | Reject Recall (Group B) | Reject F1 | Avg Probes / Query | Probe Gain / Cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct | 40 | A=20, B=20 | 19/40 (47.5%) | 44.7% | 14/29 (48.3%) | 14/20 (70.0%) | 57.1% | 5/11 (45.5%) | 5/20 (25.0%) | 32.3% | 0.00 | baseline |
| hybrid | 40 | A=20, B=20 | 19/40 (47.5%) | 46.7% | 12/25 (48.0%) | 12/20 (60.0%) | 53.3% | 7/15 (46.7%) | 7/20 (35.0%) | 40.0% | 0.00 | n/a |
| probe | 40 | A=20, B=20 | 19/40 (47.5%) | 46.7% | 12/25 (48.0%) | 12/20 (60.0%) | 53.3% | 7/15 (46.7%) | 7/20 (35.0%) | 40.0% | 0.00 | n/a |

## Operational Signals

| Mode | Ambiguous A | Ambiguous B | Abstain A | Abstain B |
|---|---:|---:|---:|---:|
| direct | 0/20 (0.0%) | 0/20 (0.0%) | 0/20 (0.0%) | 0/20 (0.0%) |
| hybrid | 0/20 (0.0%) | 0/20 (0.0%) | 0/20 (0.0%) | 0/20 (0.0%) |
| probe | 0/20 (0.0%) | 0/20 (0.0%) | 0/20 (0.0%) | 0/20 (0.0%) |

## High-Confidence Accuracy

| Mode | N | High Confidence | High Confidence Precision | High Confidence Right | High Confidence Wrong | Non-High Confidence |
|---|---:|---:|---:|---:|---:|---:|
| direct | 40 | 14/40 (35.0%) | 57.1% | 8/14 (57.1%) | 6/14 (42.9%) | 26/40 (65.0%) |
| hybrid | 40 | 15/40 (37.5%) | 46.7% | 7/15 (46.7%) | 8/15 (53.3%) | 25/40 (62.5%) |
| probe | 40 | 15/40 (37.5%) | 46.7% | 7/15 (46.7%) | 8/15 (53.3%) | 25/40 (62.5%) |
