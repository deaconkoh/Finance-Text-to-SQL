# ASA Invariant Metrics

- Join mode: `inner_join_question_id`
- Question IDs evaluated: 4874
- Dedupe policy: `last`
- Group D filtered: 0

| Set | Rows | EX Acc | ASA Strict Acc | ASA Lower Bound | FPER | FPER Lower Bound | Inv Evaluability | Inv Failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| before | 4874 | 0.7647 | 0.7348 | 0.6617 | 0.0052 | 0.1347 | 0.8699 | 17 |
| after | 4874 | 0.7708 | 0.7408 | 0.6666 | 0.0061 | 0.1352 | 0.8701 | 20 |

## FCR Hard Findings

### before

| Code | Count |
| --- | ---: |
| `posting_side_reversal` | 17 |

### after

| Code | Count |
| --- | ---: |
| `posting_side_reversal` | 20 |


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
| `missing_financial_annotation` | 445 |
| `unsupported_finance_bearing_expression` | 33 |
| `unsupported_finance_bearing_lineage` | 455 |

## Deltas

- EX accuracy delta: 0.0062
- ASA strict accuracy delta: 0.0060
- ASA lower-bound accuracy delta: 0.0049
- FPER delta: 0.0009
- FPER lower-bound delta: 0.0005
