# Final Repair Evaluation

## Headline Results

| Metric | Value |
| --- | ---: |
| Total examples | 1726 |
| Original execution accuracy | 0.6790 |
| Final execution accuracy | 0.7074 |
| Execution accuracy delta | 0.0284 |
| Net correct gain | 49 |
| Corruption rate | 58/107 (0.5421) |
| Targeted repair success | 107/120 (0.8917) |
| End-to-end repair precision | 107/227 (0.4714) |

## Original vs Final Groups

| Group | Original | Final After Repair | Delta |
| --- | ---: | ---: | ---: |
| A: correct executable | 1172/1726 (0.6790) | 1221/1726 (0.7074) | +49 |
| B: wrong executable | 529/1726 (0.3065) | 486/1726 (0.2816) | -43 |
| C: non-executable | 25/1726 (0.0145) | 19/1726 (0.0110) | -6 |
| D: ambiguous/excluded | 0/1726 (0.0000) | 0/1726 (0.0000) | +0 |

## Repair Funnel

| Metric | Value |
| --- | ---: |
| Verifier rejected rows | 845/1726 (0.4896) |
| Attempted repairs | 687/1726 (0.3980) |
| Generated repairs | 227/687 (0.3304) |
| Applied repairs | 227/1726 (0.1315) |
| Fallback to original SQL | 1499/1726 (0.8685) |

## Repair Effectiveness

| Metric | Value |
| --- | ---: |
| True repair targets touched | 120/227 (0.5286) |
| True targets fixed | 107/120 (0.8917) |
| All original wrong/non-exec fixed | 107/554 (0.1931) |
| End-to-end precision across all applied repairs | 107/227 (0.4714) |
| Repaired SQL executable | 224/227 (0.9868) |

## Repair Safety

| Metric | Value |
| --- | ---: |
| Originally correct rows touched by repair | 107/1172 (0.0913) |
| Preserved originally correct rows | 1114/1172 (0.9505) |
| Corrupted originally correct touched rows | 58/107 (0.5421) |
| Overall corruption among originally correct | 58/1172 (0.0495) |
| Net gain after corruption | 49 |

## Readout

- Repairs changed correctness by 0.0284 execution-accuracy points.
- The repairer fixed 107 true wrong/non-executable targets.
- The pipeline corrupted 58 originally correct rows that were touched by repair.
- Low end-to-end repair precision can come from either weak repair quality or verifier over-routing correct SQL into repair.
