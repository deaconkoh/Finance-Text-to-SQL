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

| Mode   |   N |  Label Mix |      Accuracy | Macro F1 | Accept Precision | Accept Recall (Group A) | Accept F1 | Reject Precision | Reject Recall (Group B) | Reject F1 | Avg Probes / Query | Probe Gain / Cost |
| ------ | --: | ---------: | ------------: | -------: | ---------------: | ----------------------: | --------: | ---------------: | ----------------------: | --------: | -----------------: | ----------------: |
| direct |  40 | A=20, B=20 | 24/40 (60.0%) |    59.6% |    14/24 (58.3%) |           14/20 (70.0%) |     63.6% |    10/16 (62.5%) |           10/20 (50.0%) |     55.6% |               0.00 |          baseline |
| hybrid |  40 | A=20, B=20 | 25/40 (62.5%) |    61.3% |     9/13 (69.2%) |            9/20 (45.0%) |     54.5% |    16/27 (59.3%) |           16/20 (80.0%) |     68.1% |               0.10 |     25.0 pp/probe |
| probe  |  40 | A=20, B=20 | 25/40 (62.5%) |    61.9% |    10/15 (66.7%) |           10/20 (50.0%) |     57.1% |    15/25 (60.0%) |           15/20 (75.0%) |     66.7% |               0.20 |     12.5 pp/probe |

## Operational Signals

| Mode   | Ambiguous A | Ambiguous B |   Abstain A |   Abstain B |
| ------ | ----------: | ----------: | ----------: | ----------: |
| direct | 0/20 (0.0%) | 0/20 (0.0%) | 0/20 (0.0%) | 0/20 (0.0%) |
| hybrid | 0/20 (0.0%) | 0/20 (0.0%) | 0/20 (0.0%) | 0/20 (0.0%) |
| probe  | 0/20 (0.0%) | 0/20 (0.0%) | 0/20 (0.0%) | 0/20 (0.0%) |

## High-Confidence Accuracy

| Mode   |   N | High Confidence | High Confidence Precision | High Confidence Right | High Confidence Wrong | Non-High Confidence |
| ------ | --: | --------------: | ------------------------: | --------------------: | --------------------: | ------------------: |
| direct |  40 |   33/40 (82.5%) |                     57.6% |         19/33 (57.6%) |         14/33 (42.4%) |        7/40 (17.5%) |
| hybrid |  40 |   35/40 (87.5%) |                     65.7% |         23/35 (65.7%) |         12/35 (34.3%) |        5/40 (12.5%) |
| probe  |  40 |   36/40 (90.0%) |                     63.9% |         23/36 (63.9%) |         13/36 (36.1%) |        4/40 (10.0%) |
