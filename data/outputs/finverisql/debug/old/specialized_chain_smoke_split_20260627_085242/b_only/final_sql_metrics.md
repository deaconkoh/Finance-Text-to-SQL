# Final Repair Evaluation

## Headline Results

| Metric | Value |
| --- | ---: |
| Total examples | 100 |
| Original execution accuracy | 0.0000 |
| Final execution accuracy | 0.2600 |
| Execution accuracy delta | 0.2600 |
| Net correct gain | 26 |
| Corruption rate | 0/0 (0.0000) |
| Targeted repair success | 26/28 (0.9286) |
| End-to-end repair precision | 26/28 (0.9286) |

## Original vs Final Groups

| Group | Original | Final After Repair | Delta |
| --- | ---: | ---: | ---: |
| A: correct executable | 0/100 (0.0000) | 26/100 (0.2600) | +26 |
| B: wrong executable | 100/100 (1.0000) | 74/100 (0.7400) | -26 |
| C: non-executable | 0/100 (0.0000) | 0/100 (0.0000) | +0 |
| D: ambiguous/excluded | 0/100 (0.0000) | 0/100 (0.0000) | +0 |

## Repair Funnel

| Metric | Value |
| --- | ---: |
| Verifier rejected rows | 100/100 (1.0000) |
| Attempted repairs | 100/100 (1.0000) |
| Generated repairs | 28/100 (0.2800) |
| Applied repairs | 28/100 (0.2800) |
| Fallback to original SQL | 72/100 (0.7200) |

## Repair Effectiveness

| Metric | Value |
| --- | ---: |
| True repair targets touched | 28/28 (1.0000) |
| True targets fixed | 26/28 (0.9286) |
| All original wrong/non-exec fixed | 26/100 (0.2600) |
| End-to-end precision across all applied repairs | 26/28 (0.9286) |
| Repaired SQL executable | 28/28 (1.0000) |

## Repair Safety

| Metric | Value |
| --- | ---: |
| Originally correct rows touched by repair | 0/0 (0.0000) |
| Preserved originally correct rows | 0/0 (0.0000) |
| Corrupted originally correct touched rows | 0/0 (0.0000) |
| Overall corruption among originally correct | 0/0 (0.0000) |
| Net gain after corruption | 26 |

## Readout

- Repairs changed correctness by 0.2600 execution-accuracy points.
- The repairer fixed 26 true wrong/non-executable targets.
- The pipeline corrupted 0 originally correct rows that were touched by repair.
- Low end-to-end repair precision can come from either weak repair quality or verifier over-routing correct SQL into repair.
