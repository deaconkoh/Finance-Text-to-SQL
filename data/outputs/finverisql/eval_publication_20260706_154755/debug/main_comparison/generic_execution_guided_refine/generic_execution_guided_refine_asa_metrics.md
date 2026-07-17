# ASA Invariant Metrics

- Join mode: `inner_join_question_id`
- Question IDs evaluated: 6603
- Dedupe policy: `last`
- Group D filtered: 1002

| Set | Rows | EX Acc | ASA Strict Acc | ASA Lower Bound | FPER | FPER Lower Bound | Inv Evaluability | Inv Failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| before | 6603 | 0.7645 | 0.7350 | 0.6620 | 0.0048 | 0.1341 | 0.8700 | 21 |
| after | 6603 | 0.7683 | 0.7356 | 0.6549 | 0.0055 | 0.1476 | 0.8571 | 24 |

## FCR Hard Findings

### before

| Code | Count |
| --- | ---: |
| `posting_side_reversal` | 21 |

### after

| Code | Count |
| --- | ---: |
| `posting_side_reversal` | 24 |


## Inv Not Evaluable Reasons

### before

| Code | Count |
| --- | ---: |
| `missing_financial_annotation` | 606 |
| `unsupported_finance_bearing_expression` | 39 |
| `unsupported_finance_bearing_lineage` | 617 |

### after

| Code | Count |
| --- | ---: |
| `missing_financial_annotation` | 606 |
| `unsupported_finance_bearing_expression` | 37 |
| `unsupported_finance_bearing_lineage` | 701 |

## Deltas

- EX accuracy delta: 0.0038
- ASA strict accuracy delta: 0.0006
- ASA lower-bound accuracy delta: -0.0071
- FPER delta: 0.0007
- FPER lower-bound delta: 0.0135
