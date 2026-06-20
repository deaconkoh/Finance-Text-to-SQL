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
| direct | 40 | A=20, B=20 | 22/40 (55.0%) | 59.9% | 7/9 (77.8%) | 7/20 (35.0%) | 48.3% | 15/22 (68.2%) | 15/20 (75.0%) | 71.4% | 0.00 | baseline |
| hybrid | 40 | A=20, B=20 | 29/40 (72.5%) | 71.6% | 11/13 (84.6%) | 11/20 (55.0%) | 66.7% | 18/27 (66.7%) | 18/20 (90.0%) | 76.6% | 0.10 | 175.0 pp/probe |
| probe | 40 | A=20, B=20 | 30/40 (75.0%) | 74.4% | 12/14 (85.7%) | 12/20 (60.0%) | 70.6% | 18/26 (69.2%) | 18/20 (90.0%) | 78.3% | 0.12 | 160.0 pp/probe |

## Operational Signals

| Mode | Ambiguous A | Ambiguous B | Abstain A | Abstain B |
|---|---:|---:|---:|---:|
| direct | 6/20 (30.0%) | 3/20 (15.0%) | 6/20 (30.0%) | 3/20 (15.0%) |
| hybrid | 0/20 (0.0%) | 0/20 (0.0%) | 0/20 (0.0%) | 0/20 (0.0%) |
| probe | 0/20 (0.0%) | 0/20 (0.0%) | 0/20 (0.0%) | 0/20 (0.0%) |

## High-Confidence Accuracy

| Mode | N | High Confidence | High Confidence Precision | High Confidence Right | High Confidence Wrong | Non-High Confidence |
|---|---:|---:|---:|---:|---:|---:|
| direct | 40 | 29/40 (72.5%) | 69.0% | 20/29 (69.0%) | 9/29 (31.0%) | 11/40 (27.5%) |
| hybrid | 40 | 34/40 (85.0%) | 73.5% | 25/34 (73.5%) | 9/34 (26.5%) | 6/40 (15.0%) |
| probe | 40 | 34/40 (85.0%) | 76.5% | 26/34 (76.5%) | 8/34 (23.5%) | 6/40 (15.0%) |
