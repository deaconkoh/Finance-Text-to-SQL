# Final Repair Evaluation

## Headline Results

| Metric | Value |
| --- | ---: |
| Total examples | 5605 |
| Original execution accuracy | 0.7647 |
| Final execution accuracy | 0.7684 |
| Execution accuracy delta | 0.0037 |
| Net correct gain | 18 |
| Corruption rate | 20/1282 (0.0156) |
| Targeted repair success | 38/473 (0.0803) |
| End-to-end repair precision | 38/1755 (0.0217) |

## Original vs Final Groups

| Group | Original | Final After Repair | Delta |
| --- | ---: | ---: | ---: |
| A: correct executable | 3727/4874 (0.7647) | 3745/4874 (0.7684) | +18 |
| B: wrong executable | 987/4874 (0.2025) | 1014/4874 (0.2080) | +27 |
| C: non-executable | 160/4874 (0.0328) | 115/4874 (0.0236) | -45 |
| D: ambiguous/excluded | 731/5605 (0.1304) | 731/5605 (0.1304) | +0 |

## Repair Funnel

| Metric | Value |
| --- | ---: |
| Verifier rejected rows | 0/5605 (0.0000) |
| Attempted repairs | 4874/5605 (0.8696) |
| Generated repairs | 1755/4874 (0.3601) |
| Applied repairs | 1755/5605 (0.3131) |
| Fallback to original SQL | 3832/5605 (0.6837) |

## Repair Effectiveness

| Metric | Value |
| --- | ---: |
| True repair targets touched | 473/1755 (0.2695) |
| True targets fixed | 38/473 (0.0803) |
| All original wrong/non-exec fixed | 38/1147 (0.0331) |
| End-to-end precision across all applied repairs | 38/1755 (0.0217) |
| Repaired SQL executable | 1660/1755 (0.9459) |

## Repair Safety

| Metric | Value |
| --- | ---: |
| Originally correct rows touched by repair | 1282/3727 (0.3440) |
| Preserved originally correct rows | 3707/3727 (0.9946) |
| Corrupted originally correct touched rows | 20/1282 (0.0156) |
| Overall corruption among originally correct | 20/3727 (0.0054) |
| Net gain after corruption | 0.0037 |
| Net gain after corruption count | 18/4874 |

## Readout

- Repairs changed correctness by 0.0037 execution-accuracy points.
- The repairer fixed 38 true wrong/non-executable targets.
- The pipeline corrupted 20 originally correct rows that were touched by repair.
- Low end-to-end repair precision can come from either weak repair quality or verifier over-routing correct SQL into repair.
