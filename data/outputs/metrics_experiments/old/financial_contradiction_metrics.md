# Financial Contradiction Metrics

- Join mode: `inner_join_question_id`
- Question IDs evaluated: 1701
- Dedupe policy: `last`

| Set | Rows | Evaluable | Hard FCR | No Contradiction | Not Evaluable | Execution Accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| before | 1701 | 1523 | 0.0624 | 0.9376 | 0.1046 | 0.6890 |
| after | 1701 | 1505 | 0.0631 | 0.9369 | 0.1152 | 0.7155 |

## Warning Subtypes

### before

| Warning | Count | Rate |
| --- | ---: | ---: |
| `aggregation_or_grain_error` | 96 | 0.0630 |
| `financial_measure_mismatch` | 379 | 0.2489 |
| `output_shape_warning` | 45 | 0.0295 |
| `financial_scope_warning` | 3540 | 2.3244 |
| `unresolved_measure_warning` | 3 | 0.0020 |

### after

| Warning | Count | Rate |
| --- | ---: | ---: |
| `aggregation_or_grain_error` | 99 | 0.0658 |
| `financial_measure_mismatch` | 350 | 0.2326 |
| `output_shape_warning` | 54 | 0.0359 |
| `financial_scope_warning` | 3492 | 2.3203 |
| `unresolved_measure_warning` | 3 | 0.0020 |


## Hard Finding Subtypes

### before

| Finding | Count |
| --- | ---: |
| `posting_side_reversal` | 95 |

### after

| Finding | Count |
| --- | ---: |
| `posting_side_reversal` | 95 |


## Financial Contradiction x Exact Set Match

### before

| Financial status | ESM pass | ESM fail |
| --- | ---: | ---: |
| Hard financial contradiction | 95 | 0 |
| No financial contradiction | 900 | 528 |

- Not evaluable excluded: 178
- Missing execution_match excluded: 0

### after

| Financial status | ESM pass | ESM fail |
| --- | ---: | ---: |
| Hard financial contradiction | 92 | 3 |
| No financial contradiction | 937 | 473 |

- Not evaluable excluded: 196
- Missing execution_match excluded: 0


## Deltas

- Execution accuracy delta: 0.0265
- Hard FCR delta: 0.0007
- Not evaluable rate delta: 0.0106
