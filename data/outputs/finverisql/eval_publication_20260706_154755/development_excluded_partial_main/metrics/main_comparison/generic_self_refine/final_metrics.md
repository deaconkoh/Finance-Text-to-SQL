# Final Repair Evaluation

## Headline Results

| Metric | Value |
| --- | ---: |
| Total examples | 5605 |
| Original execution accuracy | 0.7647 |
| Final execution accuracy | 0.7325 |
| Execution accuracy delta | -0.0322 |
| Net correct gain | -157 |
| Corruption rate | 189/1312 (0.1441) |
| Targeted repair success | 32/363 (0.0882) |
| End-to-end repair precision | 32/1675 (0.0191) |

## Original vs Final Groups

| Group | Original | Final After Repair | Delta |
| --- | ---: | ---: | ---: |
| A: correct executable | 3727/4874 (0.7647) | 3570/4874 (0.7325) | -157 |
| B: wrong executable | 987/4874 (0.2025) | 997/4874 (0.2046) | +10 |
| C: non-executable | 160/4874 (0.0328) | 307/4874 (0.0630) | +147 |
| D: ambiguous/excluded | 731/5605 (0.1304) | 731/5605 (0.1304) | +0 |

## Repair Funnel

| Metric | Value |
| --- | ---: |
| Verifier rejected rows | 0/5605 (0.0000) |
| Attempted repairs | 4874/5605 (0.8696) |
| Generated repairs | 1675/4874 (0.3437) |
| Applied repairs | 1675/5605 (0.2988) |
| Fallback to original SQL | 3912/5605 (0.6979) |

## Repair Effectiveness

| Metric | Value |
| --- | ---: |
| True repair targets touched | 363/1675 (0.2167) |
| True targets fixed | 32/363 (0.0882) |
| All original wrong/non-exec fixed | 32/1147 (0.0279) |
| End-to-end precision across all applied repairs | 32/1675 (0.0191) |
| Repaired SQL executable | 1446/1675 (0.8633) |

## Repair Safety

| Metric | Value |
| --- | ---: |
| Originally correct rows touched by repair | 1312/3727 (0.3520) |
| Preserved originally correct rows | 3538/3727 (0.9493) |
| Corrupted originally correct touched rows | 189/1312 (0.1441) |
| Overall corruption among originally correct | 189/3727 (0.0507) |
| Net gain after corruption | -0.0322 |
| Net gain after corruption count | -157/4874 |

## Readout

- Repairs changed correctness by -0.0322 execution-accuracy points.
- The repairer fixed 32 true wrong/non-executable targets.
- The pipeline corrupted 189 originally correct rows that were touched by repair.
- Low end-to-end repair precision can come from either weak repair quality or verifier over-routing correct SQL into repair.
