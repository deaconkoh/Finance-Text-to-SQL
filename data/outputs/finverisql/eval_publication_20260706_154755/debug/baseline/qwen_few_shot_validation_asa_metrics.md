# ASA Invariant Metrics

- Join mode: `single_file`
- Question IDs evaluated: 6603
- Dedupe policy: `last`
- Group D filtered: 1002

| Set | Rows | EX Acc | ASA Strict Acc | ASA Lower Bound | FPER | FPER Lower Bound | Inv Evaluability | Inv Failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| before | 6603 | 0.7645 | 0.7350 | 0.6620 | 0.0048 | 0.1341 | 0.8700 | 21 |

## FCR Hard Findings

### before

| Code | Count |
| --- | ---: |
| `posting_side_reversal` | 21 |


## Inv Not Evaluable Reasons

### before

| Code | Count |
| --- | ---: |
| `missing_financial_annotation` | 606 |
| `unsupported_finance_bearing_expression` | 39 |
| `unsupported_finance_bearing_lineage` | 617 |
