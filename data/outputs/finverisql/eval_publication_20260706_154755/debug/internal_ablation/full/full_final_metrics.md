# Final Repair Evaluation

## Headline Results

| Metric | Value |
| --- | ---: |
| Total examples | 6603 |
| Original execution accuracy | 0.7645 |
| Final execution accuracy | 0.7710 |
| Execution accuracy delta | 0.0065 |
| Net correct gain | 43 |
| Corruption rate | 1/2 (0.5000) |
| Targeted repair success | 44/195 (0.2256) |
| End-to-end repair precision | 44/197 (0.2234) |

## Original vs Final Groups

| Group | Original | Final After Repair | Delta |
| --- | ---: | ---: | ---: |
| A: correct executable | 5048/6603 (0.7645) | 5091/6603 (0.7710) | +43 |
| B: wrong executable | 1348/6603 (0.2041) | 1379/6603 (0.2088) | +31 |
| C: non-executable | 207/6603 (0.0313) | 133/6603 (0.0201) | -74 |
| D: ambiguous/excluded | 0/6603 (0.0000) | 0/6603 (0.0000) | +0 |

## Repair Funnel

| Metric | Value |
| --- | ---: |
| Verifier rejected rows | 5008/6603 (0.7584) |
| Attempted repairs | 4497/6603 (0.6811) |
| Generated repairs | 197/4497 (0.0438) |
| Applied repairs | 197/6603 (0.0298) |
| Fallback to original SQL | 6380/6603 (0.9662) |

## Repair Effectiveness

| Metric | Value |
| --- | ---: |
| True repair targets touched | 195/197 (0.9898) |
| True targets fixed | 44/195 (0.2256) |
| All original wrong/non-exec fixed | 44/1555 (0.0283) |
| End-to-end precision across all applied repairs | 44/197 (0.2234) |
| Repaired SQL executable | 90/197 (0.4569) |

## Repair Safety

| Metric | Value |
| --- | ---: |
| Originally correct rows touched by repair | 2/5048 (0.0004) |
| Preserved originally correct rows | 5047/5048 (0.9998) |
| Corrupted originally correct touched rows | 1/2 (0.5000) |
| Overall corruption among originally correct | 1/5048 (0.0002) |
| Net gain after corruption | 0.0065 |
| Net gain after corruption count | 43/6603 |

## Readout

- Repairs changed correctness by 0.0065 execution-accuracy points.
- The repairer fixed 44 true wrong/non-executable targets.
- The pipeline corrupted 1 originally correct rows that were touched by repair.
- Low end-to-end repair precision can come from either weak repair quality or verifier over-routing correct SQL into repair.
