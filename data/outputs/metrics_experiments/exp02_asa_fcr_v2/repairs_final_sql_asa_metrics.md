# ASA Invariant Metrics

- Join mode: `single_file`
- Question IDs evaluated: 1701
- Dedupe policy: `last`

| Set | Rows | EX Acc | ASA Strict Acc | ASA Lower Bound | FPER | FPER Lower Bound | Inv Evaluability | Inv Failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| before | 1701 | 0.7155 | 0.6193 | 0.5509 | 0.0894 | 0.2301 | 0.8455 | 92 |

## FCR Hard Findings

### before

| Code | Count |
| --- | ---: |
| `posting_side_reversal` | 92 |


## Inv Not Evaluable Reasons

### before

| Code | Count |
| --- | ---: |
| `missing_financial_annotation` | 161 |
| `unsupported_finance_bearing_expression` | 14 |
| `unsupported_finance_bearing_lineage` | 174 |
