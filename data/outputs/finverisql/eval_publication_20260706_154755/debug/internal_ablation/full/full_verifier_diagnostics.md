# FinVeriSQL Diagnostics Analysis

## Input Summary

- Files read: 1
- Rows read: 6603
- `full_verify.jsonl`: 6603 rows

## Mode Performance Comparison

Probe Gain / Cost is the accuracy gain over the `direct` baseline, measured in percentage points, divided by the additional average probes used per query. Higher is better, but values can be large when probe usage is very low.

| Mode | N | Label Mix | Accuracy | Detection Precision | Detection Recall | Detection F1 | Accept Precision | Accept Recall (Group A) | Accept F1 | Reject Precision | Reject Recall (Group B) | Reject F1 | Avg Probes / Query | Probe Gain / Cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| probe | 6603 | A=5048, B=1348 | 1183/6603 (17.9%) | 1175/4827 (24.3%) | 1175/1348 (87.2%) | 38.1% | 8/8 (100.0%) | 8/5048 (0.2%) | 0.3% | 1175/5008 (23.5%) | 1175/1348 (87.2%) | 37.0% | 1.40 | baseline |

## Operational Signals

| Mode | Ambiguous A | Ambiguous B | Abstain A | Abstain B |
|---|---:|---:|---:|---:|
| probe | 1388/5048 (27.5%) | 173/1348 (12.8%) | 1388/5048 (27.5%) | 173/1348 (12.8%) |

## High-Confidence Accuracy

| Mode | N | High Confidence | High Confidence Precision | High Confidence Right | High Confidence Wrong | Non-High Confidence |
|---|---:|---:|---:|---:|---:|---:|
| probe | 6603 | 5554/6603 (84.1%) | 17.7% | 984/5554 (17.7%) | 4570/5554 (82.3%) | 1049/6603 (15.9%) |
