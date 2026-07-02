# Final Repair Evaluation

## Headline Results

| Metric | Value |
| --- | ---: |
| Total examples | 100 |
| Original execution accuracy | 1.0000 |
| Final execution accuracy | 0.8300 |
| Execution accuracy delta | -0.1700 |
| Net correct gain | -17 |
| Corruption rate | 17/35 (0.4857) |
| Targeted repair success | 0/0 (0.0000) |
| End-to-end repair precision | 0/35 (0.0000) |

## Original vs Final Groups

| Group | Original | Final After Repair | Delta |
| --- | ---: | ---: | ---: |
| A: correct executable | 100/100 (1.0000) | 83/100 (0.8300) | -17 |
| B: wrong executable | 0/100 (0.0000) | 17/100 (0.1700) | +17 |
| C: non-executable | 0/100 (0.0000) | 0/100 (0.0000) | +0 |
| D: ambiguous/excluded | 0/100 (0.0000) | 0/100 (0.0000) | +0 |

## Repair Funnel

| Metric | Value |
| --- | ---: |
| Verifier rejected rows | 100/100 (1.0000) |
| Attempted repairs | 100/100 (1.0000) |
| Generated repairs | 35/100 (0.3500) |
| Applied repairs | 35/100 (0.3500) |
| Fallback to original SQL | 65/100 (0.6500) |

## Repair Effectiveness

| Metric | Value |
| --- | ---: |
| True repair targets touched | 0/35 (0.0000) |
| True targets fixed | 0/0 (0.0000) |
| All original wrong/non-exec fixed | 0/0 (0.0000) |
| End-to-end precision across all applied repairs | 0/35 (0.0000) |
| Repaired SQL executable | 35/35 (1.0000) |

## Repair Safety

| Metric | Value |
| --- | ---: |
| Originally correct rows touched by repair | 35/100 (0.3500) |
| Preserved originally correct rows | 83/100 (0.8300) |
| Corrupted originally correct touched rows | 17/35 (0.4857) |
| Overall corruption among originally correct | 17/100 (0.1700) |
| Net gain after corruption | -17 |

## Readout

- Repairs changed correctness by -0.1700 execution-accuracy points.
- The repairer fixed 0 true wrong/non-executable targets.
- The pipeline corrupted 17 originally correct rows that were touched by repair.
- Low end-to-end repair precision can come from either weak repair quality or verifier over-routing correct SQL into repair.
