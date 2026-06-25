# ASA Strict Metrics

- Join mode: `single_file`
- Question IDs evaluated: 1701
- Dedupe policy: `last`
- Fixture date: `2026-06-23`

| Set | Rows | EX Acc | ASA Strict Acc | ASA Lower Bound | FPER | FPER Lower Bound | Semantic Testability |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| before | 1701 | 0.7155 | 0.1830 | 0.0758 | 0.4163 | 0.8940 | 0.1060 |

## Testability Reasons

### before

| Reason | Count |
| --- | ---: |
| `eq_acct_not_tested` | 1081 |
| `ex_failed` | 484 |
| `inv_not_evaluable` | 188 |


## FCR Hard Findings

### before

| Code | Count |
| --- | ---: |
| `posting_side_reversal` | 92 |


## Eq_acct Template Failures

### before

| Template | Count |
| --- | ---: |
