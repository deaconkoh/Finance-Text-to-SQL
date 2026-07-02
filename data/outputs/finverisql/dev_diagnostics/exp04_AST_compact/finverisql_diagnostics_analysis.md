# FinVeriSQL Diagnostics Analysis

## Input Summary

- Files read: 4
- Rows read: 80
- `verified_groupA_nl_intend_compact_semantic_sample20.jsonl`: 20 rows
- `verified_groupA_nl_intend_probe_ast_sample20.jsonl`: 20 rows
- `verified_groupB_nl_intend_compact_semantic_sample20.jsonl`: 20 rows
- `verified_groupB_nl_intend_probe_ast_sample20.jsonl`: 20 rows

## Mode Performance Comparison

Probe Gain / Cost is the accuracy gain over the `direct` baseline, measured in percentage points, divided by the additional average probes used per query. Higher is better, but values can be large when probe usage is very low.

| Mode    |   N |  Label Mix |      Accuracy | Macro F1 | Accept Precision | Accept Recall (Group A) | Accept F1 | Reject Precision | Reject Recall (Group B) | Reject F1 | Avg Probes / Query | Probe Gain / Cost |
| ------- | --: | ---------: | ------------: | -------: | ---------------: | ----------------------: | --------: | ---------------: | ----------------------: | --------: | -----------------: | ----------------: |
| AST     |  20 | A=20, B=20 | 25/40 (62.5%) |   56.94% |    18/31 (58.3%) |           18/20 (75.0%) |   (65.6%) |      7/9 (77.8%) |            7/20 (35.0%) |  (48.28%) |               0.07 |          baseline |
| Compact |  20 | A=20, B=20 | 30/40 (75.0%) |    74.4% |    12/14 (85.7%) |           12/20 (60.0%) |     70.6% |    18/26 (69.2%) |           18/20 (90.0%) |     78.3% |               0.12 |    160.0 pp/probe |

## Operational Signals

| Mode  | Ambiguous A | Ambiguous B |   Abstain A |   Abstain B |
| ----- | ----------: | ----------: | ----------: | ----------: |
| probe | 0/40 (0.0%) | 0/40 (0.0%) | 0/40 (0.0%) | 0/40 (0.0%) |

## High-Confidence Accuracy

| Mode  |   N | High Confidence | High Confidence Precision | High Confidence Right | High Confidence Wrong | Non-High Confidence |
| ----- | --: | --------------: | ------------------------: | --------------------: | --------------------: | ------------------: |
| probe |  80 |   72/80 (90.0%) |                     69.4% |         50/72 (69.4%) |         22/72 (30.6%) |        8/80 (10.0%) |
