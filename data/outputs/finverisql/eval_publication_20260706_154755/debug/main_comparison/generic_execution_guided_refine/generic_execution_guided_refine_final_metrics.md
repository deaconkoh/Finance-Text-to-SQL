# Final Repair Evaluation

## Headline Results

| Metric | Value |
| --- | ---: |
| Total examples | 7605 |
| Original execution accuracy | 0.7645 |
| Final execution accuracy | 0.7683 |
| Execution accuracy delta | 0.0038 |
| Net correct gain | 25 |
| Corruption rate | 24/1729 (0.0139) |
| Targeted repair success | 49/615 (0.0797) |
| End-to-end repair precision | 49/2344 (0.0209) |

## Original vs Final Groups

| Group | Original | Final After Repair | Delta |
| --- | ---: | ---: | ---: |
| A: correct executable | 5048/6603 (0.7645) | 5073/6603 (0.7683) | +25 |
| B: wrong executable | 1348/6603 (0.2041) | 1388/6603 (0.2102) | +40 |
| C: non-executable | 207/6603 (0.0313) | 142/6603 (0.0215) | -65 |
| D: ambiguous/excluded | 1002/7605 (0.1318) | 1002/7605 (0.1318) | +0 |

## Repair Funnel

| Metric | Value |
| --- | ---: |
| Verifier rejected rows | 0/7605 (0.0000) |
| Attempted repairs | 6603/7605 (0.8682) |
| Generated repairs | 2344/6603 (0.3550) |
| Applied repairs | 2344/7605 (0.3082) |
| Fallback to original SQL | 5235/7605 (0.6884) |

## Repair Effectiveness

| Metric | Value |
| --- | ---: |
| True repair targets touched | 615/2344 (0.2624) |
| True targets fixed | 49/615 (0.0797) |
| All original wrong/non-exec fixed | 49/1555 (0.0315) |
| End-to-end precision across all applied repairs | 49/2344 (0.0209) |
| Repaired SQL executable | 2231/2344 (0.9518) |

## Repair Safety

| Metric | Value |
| --- | ---: |
| Originally correct rows touched by repair | 1729/5048 (0.3425) |
| Preserved originally correct rows | 5024/5048 (0.9952) |
| Corrupted originally correct touched rows | 24/1729 (0.0139) |
| Overall corruption among originally correct | 24/5048 (0.0048) |
| Net gain after corruption | 0.0038 |
| Net gain after corruption count | 25/6603 |

## Readout

- Repairs changed correctness by 0.0038 execution-accuracy points.
- The repairer fixed 49 true wrong/non-executable targets.
- The pipeline corrupted 24 originally correct rows that were touched by repair.
- Low end-to-end repair precision can come from either weak repair quality or verifier over-routing correct SQL into repair.
