# Final Repair Evaluation

## Headline Results

| Metric | Value |
| --- | ---: |
| Total examples | 40 |
| Original execution accuracy | 0.5000 |
| Final execution accuracy | 0.5250 |
| Execution accuracy delta | 0.0250 |
| Net correct gain | 1 |
| Corruption rate | 10/19 (0.5263) |
| Targeted repair success | 11/18 (0.6111) |
| End-to-end repair precision | 11/37 (0.2973) |

## Original vs Final Groups

| Group | Original | Final After Repair | Delta |
| --- | ---: | ---: | ---: |
| A: correct executable | 20/40 (0.5000) | 21/40 (0.5250) | +1 |
| B: wrong executable | 20/40 (0.5000) | 18/40 (0.4500) | -2 |
| C: non-executable | 0/40 (0.0000) | 1/40 (0.0250) | +1 |
| D: ambiguous/excluded | 0/40 (0.0000) | 0/40 (0.0000) | +0 |

## Repair Funnel

| Metric | Value |
| --- | ---: |
| Verifier rejected rows | 40/40 (1.0000) |
| Attempted repairs | 40/40 (1.0000) |
| Generated repairs | 37/40 (0.9250) |
| Applied repairs | 37/40 (0.9250) |
| Fallback to original SQL | 3/40 (0.0750) |

## Repair Effectiveness

| Metric | Value |
| --- | ---: |
| True repair targets touched | 18/37 (0.4865) |
| True targets fixed | 11/18 (0.6111) |
| All original wrong/non-exec fixed | 11/20 (0.5500) |
| End-to-end precision across all applied repairs | 11/37 (0.2973) |
| Repaired SQL executable | 36/37 (0.9730) |

## Repair Safety

| Metric | Value |
| --- | ---: |
| Originally correct rows touched by repair | 19/20 (0.9500) |
| Preserved originally correct rows | 10/20 (0.5000) |
| Corrupted originally correct touched rows | 10/19 (0.5263) |
| Overall corruption among originally correct | 10/20 (0.5000) |
| Net gain after corruption | 1 |

## Readout

- Repairs changed correctness by 0.0250 execution-accuracy points.
- The repairer fixed 11 true wrong/non-executable targets.
- The pipeline corrupted 10 originally correct rows that were touched by repair.
- Low end-to-end repair precision can come from either weak repair quality or verifier over-routing correct SQL into repair.
