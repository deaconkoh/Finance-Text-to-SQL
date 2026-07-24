# ASA Invariant Metrics

- Join mode: `inner_join_question_id`
- Question IDs evaluated: 4874
- Dedupe policy: `last`
- Group D filtered: 731

| Set | Rows | EX Acc | ASA Strict Acc | ASA Lower Bound | FPER | FPER Lower Bound | Inv Evaluability | Inv Failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| before | 4874 | 0.7647 | 0.7348 | 0.6617 | 0.0052 | 0.1347 | 0.8699 | 17 |
| after | 4874 | 0.7325 | 0.7016 | 0.6377 | 0.0058 | 0.1294 | 0.8756 | 18 |

## FCR Hard Findings

### before

| Code | Count |
| --- | ---: |
| `posting_side_reversal` | 17 |

### after

| Code | Count |
| --- | ---: |
| `posting_side_reversal` | 18 |


## Inv Not Evaluable Reasons

### before

| Code | Count |
| --- | ---: |
| `missing_financial_annotation` | 445 |
| `unsupported_finance_bearing_expression` | 33 |
| `unsupported_finance_bearing_lineage` | 452 |

### after

| Code | Count |
| --- | ---: |
| `missing_financial_annotation` | 301 |
| `unsupported_finance_bearing_expression` | 33 |
| `unsupported_finance_bearing_lineage` | 608 |

## Deltas

- EX accuracy delta: -0.0322
- ASA strict accuracy delta: -0.0332
- ASA lower-bound accuracy delta: -0.0240
- FPER delta: 0.0005
- FPER lower-bound delta: -0.0053
