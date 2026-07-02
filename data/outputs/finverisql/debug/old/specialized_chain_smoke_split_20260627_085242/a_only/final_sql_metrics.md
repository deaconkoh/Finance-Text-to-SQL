# Final Repair Evaluation

## Headline Results

| Metric | Value |
| --- | ---: |
| Total examples | 100 |
| Original execution accuracy | 1.0000 |
| Final execution accuracy | 0.6900 |
| Execution accuracy delta | -0.3100 |
| Net correct gain | -31 |
| Corruption rate | 31/44 (0.7045) |
| Targeted repair success | 0/0 (0.0000) |
| End-to-end repair precision | 0/44 (0.0000) |

## Original vs Final Groups

| Group | Original | Final After Repair | Delta |
| --- | ---: | ---: | ---: |
| A: correct executable | 100/100 (1.0000) | 69/100 (0.6900) | -31 |
| B: wrong executable | 0/100 (0.0000) | 31/100 (0.3100) | +31 |
| C: non-executable | 0/100 (0.0000) | 0/100 (0.0000) | +0 |
| D: ambiguous/excluded | 0/100 (0.0000) | 0/100 (0.0000) | +0 |

## Repair Funnel

| Metric | Value |
| --- | ---: |
| Verifier rejected rows | 100/100 (1.0000) |
| Attempted repairs | 100/100 (1.0000) |
| Generated repairs | 44/100 (0.4400) |
| Applied repairs | 44/100 (0.4400) |
| Fallback to original SQL | 56/100 (0.5600) |

## Repair Effectiveness

| Metric | Value |
| --- | ---: |
| True repair targets touched | 0/44 (0.0000) |
| True targets fixed | 0/0 (0.0000) |
| All original wrong/non-exec fixed | 0/0 (0.0000) |
| End-to-end precision across all applied repairs | 0/44 (0.0000) |
| Repaired SQL executable | 44/44 (1.0000) |

## Repair Safety

| Metric | Value |
| --- | ---: |
| Originally correct rows touched by repair | 44/100 (0.4400) |
| Preserved originally correct rows | 69/100 (0.6900) |
| Corrupted originally correct touched rows | 31/44 (0.7045) |
| Overall corruption among originally correct | 31/100 (0.3100) |
| Net gain after corruption | -31 |

## Readout

- Repairs changed correctness by -0.3100 execution-accuracy points.
- The repairer fixed 0 true wrong/non-executable targets.
- The pipeline corrupted 31 originally correct rows that were touched by repair.
- Low end-to-end repair precision can come from either weak repair quality or verifier over-routing correct SQL into repair.
