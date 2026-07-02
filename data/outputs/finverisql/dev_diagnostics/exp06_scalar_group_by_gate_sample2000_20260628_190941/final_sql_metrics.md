# Final Repair Evaluation

## Headline Results

| Metric | Value |
| --- | ---: |
| Total examples | 1701 |
| Original execution accuracy | 0.6890 |
| Final execution accuracy | 0.7143 |
| Execution accuracy delta | 0.0253 |
| Net correct gain | 43 |
| Corruption rate | 58/107 (0.5421) |
| Targeted repair success | 101/111 (0.9099) |
| End-to-end repair precision | 101/218 (0.4633) |

## Original vs Final Groups

| Group | Original | Final After Repair | Delta |
| --- | ---: | ---: | ---: |
| A: correct executable | 1172/1701 (0.6890) | 1215/1701 (0.7143) | +43 |
| B: wrong executable | 529/1701 (0.3110) | 483/1701 (0.2840) | -46 |
| C: non-executable | 0/1701 (0.0000) | 3/1701 (0.0018) | +3 |
| D: ambiguous/excluded | 0/1701 (0.0000) | 0/1701 (0.0000) | +0 |

## Repair Funnel

| Metric | Value |
| --- | ---: |
| Verifier rejected rows | 820/1701 (0.4821) |
| Attempted repairs | 662/1701 (0.3892) |
| Generated repairs | 218/662 (0.3293) |
| Applied repairs | 218/1701 (0.1282) |
| Fallback to original SQL | 1483/1701 (0.8718) |

## Repair Effectiveness

| Metric | Value |
| --- | ---: |
| True repair targets touched | 111/218 (0.5092) |
| True targets fixed | 101/111 (0.9099) |
| All original wrong/non-exec fixed | 101/529 (0.1909) |
| End-to-end precision across all applied repairs | 101/218 (0.4633) |
| Repaired SQL executable | 215/218 (0.9862) |

## Repair Safety

| Metric | Value |
| --- | ---: |
| Originally correct rows touched by repair | 107/1172 (0.0913) |
| Preserved originally correct rows | 1114/1172 (0.9505) |
| Corrupted originally correct touched rows | 58/107 (0.5421) |
| Overall corruption among originally correct | 58/1172 (0.0495) |
| Net gain after corruption | 43 |

## Readout

- Repairs changed correctness by 0.0253 execution-accuracy points.
- The repairer fixed 101 true wrong/non-executable targets.
- The pipeline corrupted 58 originally correct rows that were touched by repair.
- Low end-to-end repair precision can come from either weak repair quality or verifier over-routing correct SQL into repair.
