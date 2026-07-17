# Final Repair Evaluation

## Headline Results

| Metric | Value |
| --- | ---: |
| Total examples | 7605 |
| Original execution accuracy | 0.7645 |
| Final execution accuracy | 0.7325 |
| Execution accuracy delta | -0.0320 |
| Net correct gain | -211 |
| Corruption rate | 250/1763 (0.1418) |
| Targeted repair success | 39/479 (0.0814) |
| End-to-end repair precision | 39/2242 (0.0174) |

## Original vs Final Groups

| Group | Original | Final After Repair | Delta |
| --- | ---: | ---: | ---: |
| A: correct executable | 5048/6603 (0.7645) | 4837/6603 (0.7325) | -211 |
| B: wrong executable | 1348/6603 (0.2041) | 1363/6603 (0.2064) | +15 |
| C: non-executable | 207/6603 (0.0313) | 403/6603 (0.0610) | +196 |
| D: ambiguous/excluded | 1002/7605 (0.1318) | 1002/7605 (0.1318) | +0 |

## Repair Funnel

| Metric | Value |
| --- | ---: |
| Verifier rejected rows | 0/7605 (0.0000) |
| Attempted repairs | 6603/7605 (0.8682) |
| Generated repairs | 2242/6603 (0.3395) |
| Applied repairs | 2242/7605 (0.2948) |
| Fallback to original SQL | 5337/7605 (0.7018) |

## Repair Effectiveness

| Metric | Value |
| --- | ---: |
| True repair targets touched | 479/2242 (0.2136) |
| True targets fixed | 39/479 (0.0814) |
| All original wrong/non-exec fixed | 39/1555 (0.0251) |
| End-to-end precision across all applied repairs | 39/2242 (0.0174) |
| Repaired SQL executable | 1940/2242 (0.8653) |

## Repair Safety

| Metric | Value |
| --- | ---: |
| Originally correct rows touched by repair | 1763/5048 (0.3492) |
| Preserved originally correct rows | 4798/5048 (0.9505) |
| Corrupted originally correct touched rows | 250/1763 (0.1418) |
| Overall corruption among originally correct | 250/5048 (0.0495) |
| Net gain after corruption | -0.0320 |
| Net gain after corruption count | -211/6603 |

## Readout

- Repairs changed correctness by -0.0320 execution-accuracy points.
- The repairer fixed 39 true wrong/non-executable targets.
- The pipeline corrupted 250 originally correct rows that were touched by repair.
- Low end-to-end repair precision can come from either weak repair quality or verifier over-routing correct SQL into repair.
