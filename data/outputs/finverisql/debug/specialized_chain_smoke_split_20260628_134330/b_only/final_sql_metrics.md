# Final Repair Evaluation

## Headline Results

| Metric | Value |
| --- | ---: |
| Total examples | 100 |
| Original execution accuracy | 0.0000 |
| Final execution accuracy | 0.3400 |
| Execution accuracy delta | 0.3400 |
| Net correct gain | 34 |
| Corruption rate | 0/0 (0.0000) |
| Targeted repair success | 34/36 (0.9444) |
| End-to-end repair precision | 34/36 (0.9444) |

## Original vs Final Groups

| Group | Original | Final After Repair | Delta |
| --- | ---: | ---: | ---: |
| A: correct executable | 0/100 (0.0000) | 34/100 (0.3400) | +34 |
| B: wrong executable | 100/100 (1.0000) | 66/100 (0.6600) | -34 |
| C: non-executable | 0/100 (0.0000) | 0/100 (0.0000) | +0 |
| D: ambiguous/excluded | 0/100 (0.0000) | 0/100 (0.0000) | +0 |

## Repair Funnel

| Metric | Value |
| --- | ---: |
| Verifier rejected rows | 100/100 (1.0000) |
| Attempted repairs | 100/100 (1.0000) |
| Generated repairs | 36/100 (0.3600) |
| Applied repairs | 36/100 (0.3600) |
| Fallback to original SQL | 64/100 (0.6400) |

## Repair Effectiveness

| Metric | Value |
| --- | ---: |
| True repair targets touched | 36/36 (1.0000) |
| True targets fixed | 34/36 (0.9444) |
| All original wrong/non-exec fixed | 34/100 (0.3400) |
| End-to-end precision across all applied repairs | 34/36 (0.9444) |
| Repaired SQL executable | 36/36 (1.0000) |

## Repair Safety

| Metric | Value |
| --- | ---: |
| Originally correct rows touched by repair | 0/0 (0.0000) |
| Preserved originally correct rows | 0/0 (0.0000) |
| Corrupted originally correct touched rows | 0/0 (0.0000) |
| Overall corruption among originally correct | 0/0 (0.0000) |
| Net gain after corruption | 34 |

## Readout

- Repairs changed correctness by 0.3400 execution-accuracy points.
- The repairer fixed 34 true wrong/non-executable targets.
- The pipeline corrupted 0 originally correct rows that were touched by repair.
- Low end-to-end repair precision can come from either weak repair quality or verifier over-routing correct SQL into repair.
