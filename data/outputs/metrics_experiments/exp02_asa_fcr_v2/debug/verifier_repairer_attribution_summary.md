# Verifier/Repairer Attribution Audit

- ASA universe: 1701 unique question IDs
- Primary cohort: `before.Inv == 0` (95 rows)
- Dedupe policy for repair/eval artifacts: `last`

## Funnel

| Question | Count |
| --- | ---: |
| Baseline FCR hard failures | 95 |
| Verifier caught | 15 |
| Verifier-caught and actionable | 15 |
| Actionable with repair attempts | 5 |
| Repair attempts that produced SQL | 5 |
| Repaired SQL outputs that fixed Inv | 0 |
| Fixed Inv cases preserving EX | 0 |
| Baseline hard failures still hard after repair | 92 |
| Baseline hard failures became not evaluable after repair | 3 |
| Gate accepted repairs with worse EX, Inv, ASA strict, or ASA lower bound | 122 |
| Outside-primary over-repair candidates | 119 |

## Primary Bottlenecks

| Bottleneck | Count |
| --- | ---: |
| `gate_accepted_harmful_repair` | 3 |
| `repair_did_not_fix_inv` | 2 |
| `repair_not_attempted` | 10 |
| `verifier_miss` | 80 |

## Primary Breakdowns

### fcr_hard_finding_codes

| Value | Count |
| --- | ---: |
| `["posting_side_reversal"]` | 95 |

### verifier_mismatch_type

| Value | Count |
| --- | ---: |
| `None` | 80 |
| `computation_logic_error` | 15 |

### verifier_confidence

| Value | Count |
| --- | ---: |
| `high` | 93 |
| `low` | 2 |

### repair_status

| Value | Count |
| --- | ---: |
| `skipped` | 90 |
| `success` | 5 |

### final_sql_repaired

| Value | Count |
| --- | ---: |
| `False` | 90 |
| `True` | 5 |

### before_after_metrics

| Value | Count |
| --- | ---: |
| `{"after": {"EX": 0, "Inv": null, "asa_lower_bound": 0, "asa_strict": 0}, "before": {"EX": 1, "Inv": 0, "asa_lower_bound": 0, "asa_strict": 0}}` | 3 |
| `{"after": {"EX": 1, "Inv": 0, "asa_lower_bound": 0, "asa_strict": 0}, "before": {"EX": 1, "Inv": 0, "asa_lower_bound": 0, "asa_strict": 0}}` | 92 |
