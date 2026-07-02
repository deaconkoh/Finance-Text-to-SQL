# Final Repair Evaluation

## Headline Results

| Metric | Value |
| --- | ---: |
| Total examples | 1713 |
| Original execution accuracy | 0.6906 |
| Final execution accuracy | 0.6953 |
| Execution accuracy delta | 0.0047 |
| Net correct gain | 8 |
| Corruption rate | 190/383 (0.4961) |
| Targeted repair success | 198/357 (0.5546) |
| End-to-end repair precision | 198/740 (0.2676) |

## Original vs Final Groups

| Group | Original | Final After Repair | Delta |
| --- | ---: | ---: | ---: |
| A: correct executable | 1183/1713 (0.6906) | 1191/1713 (0.6953) | +8 |
| B: wrong executable | 530/1713 (0.3094) | 468/1713 (0.2732) | -62 |
| C: non-executable | 0/1713 (0.0000) | 54/1713 (0.0315) | +54 |
| D: ambiguous/excluded | 0/1713 (0.0000) | 0/1713 (0.0000) | +0 |

## Repair Funnel

| Metric | Value |
| --- | ---: |
| Verifier rejected rows | 740/1713 (0.4320) |
| Attempted repairs | 740/1713 (0.4320) |
| Generated repairs | 740/740 (1.0000) |
| Applied repairs | 740/1713 (0.4320) |
| Fallback to original SQL | 973/1713 (0.5680) |

## Repair Effectiveness

| Metric | Value |
| --- | ---: |
| True repair targets touched | 357/740 (0.4824) |
| True targets fixed | 198/357 (0.5546) |
| All original wrong/non-exec fixed | 198/530 (0.3736) |
| End-to-end precision across all applied repairs | 198/740 (0.2676) |
| Repaired SQL executable | 686/740 (0.9270) |

## Repair Safety

| Metric | Value |
| --- | ---: |
| Originally correct rows touched by repair | 383/1183 (0.3238) |
| Preserved originally correct rows | 993/1183 (0.8394) |
| Corrupted originally correct touched rows | 190/383 (0.4961) |
| Overall corruption among originally correct | 190/1183 (0.1606) |
| Net gain after corruption | 8 |

## Readout

- Repairs changed correctness by 0.0047 execution-accuracy points.
- The repairer fixed 198 true wrong/non-executable targets.
- The pipeline corrupted 190 originally correct rows that were touched by repair.
- Low end-to-end repair precision can come from either weak repair quality or verifier over-routing correct SQL into repair.
