# ASA Invariant Metrics

- Join mode: `inner_join_question_id`
- Question IDs evaluated: 6603
- Dedupe policy: `last`
- Group D filtered: 0

| Set | Rows | EX Acc | ASA Strict Acc | ASA Lower Bound | FPER | FPER Lower Bound | Inv Evaluability | Inv Failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| before | 6603 | 0.7645 | 0.7350 | 0.6620 | 0.0048 | 0.1341 | 0.8700 | 21 |
| after | 6603 | 0.7710 | 0.7415 | 0.6674 | 0.0054 | 0.1344 | 0.8704 | 24 |

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
| `unsupported_finance_bearing_expression` | 39 |
| `unsupported_finance_bearing_lineage` | 621 |

## Deltas

- EX accuracy delta: 0.0065
- ASA strict accuracy delta: 0.0066
- ASA lower-bound accuracy delta: 0.0055
- FPER delta: 0.0006
- FPER lower-bound delta: 0.0002
