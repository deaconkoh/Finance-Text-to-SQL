# ASA Invariant Metrics

- Join mode: `inner_join_question_id`
- Question IDs evaluated: 1701
- Dedupe policy: `last`

| Set | Rows | EX Acc | ASA Strict Acc | ASA Lower Bound | FPER | FPER Lower Bound | Inv Evaluability | Inv Failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| before | 1701 | 0.6890 | 0.5906 | 0.5291 | 0.0955 | 0.2321 | 0.8490 | 95 |
| after | 1701 | 0.7155 | 0.6193 | 0.5509 | 0.0894 | 0.2301 | 0.8455 | 92 |

## FCR Hard Findings

### before

| Code | Count |
| --- | ---: |
| `posting_side_reversal` | 95 |

### after

| Code | Count |
| --- | ---: |
| `posting_side_reversal` | 92 |


## Inv Not Evaluable Reasons

### before

| Code | Count |
| --- | ---: |
| `missing_financial_annotation` | 161 |
| `unsupported_finance_bearing_expression` | 14 |
| `unsupported_finance_bearing_lineage` | 163 |

### after

| Code | Count |
| --- | ---: |
| `missing_financial_annotation` | 161 |
| `unsupported_finance_bearing_expression` | 14 |
| `unsupported_finance_bearing_lineage` | 174 |

## Deltas

- EX accuracy delta: 0.0265
- ASA strict accuracy delta: 0.0287
- ASA lower-bound accuracy delta: 0.0218
- FPER delta: -0.0061
- FPER lower-bound delta: -0.0020
