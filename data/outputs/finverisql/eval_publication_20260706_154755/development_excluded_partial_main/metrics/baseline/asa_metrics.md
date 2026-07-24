# ASA Invariant Metrics

- Join mode: `single_file`
- Question IDs evaluated: 4874
- Dedupe policy: `last`
- Group D filtered: 731

| Set | Rows | EX Acc | ASA Strict Acc | ASA Lower Bound | FPER | FPER Lower Bound | Inv Evaluability | Inv Failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| before | 4874 | 0.7647 | 0.7348 | 0.6617 | 0.0052 | 0.1347 | 0.8699 | 17 |

## FCR Hard Findings

### before

| Code | Count |
| --- | ---: |
| `posting_side_reversal` | 17 |


## Inv Not Evaluable Reasons

### before

| Code | Count |
| --- | ---: |
| `missing_financial_annotation` | 445 |
| `unsupported_finance_bearing_expression` | 33 |
| `unsupported_finance_bearing_lineage` | 452 |
