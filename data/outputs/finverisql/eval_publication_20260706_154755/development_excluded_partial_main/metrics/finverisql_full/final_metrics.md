# Final Repair Evaluation

## Headline Results

| Metric | Value |
| --- | ---: |
| Total examples | 4874 |
| Original execution accuracy | 0.7647 |
| Final execution accuracy | 0.7708 |
| Execution accuracy delta | 0.0062 |
| Net correct gain | 30 |
| Corruption rate | 0/0 (0.0000) |
| Targeted repair success | 30/151 (0.1987) |
| End-to-end repair precision | 30/151 (0.1987) |

## Original vs Final Groups

| Group | Original | Final After Repair | Delta |
| --- | ---: | ---: | ---: |
| A: correct executable | 3727/4874 (0.7647) | 3757/4874 (0.7708) | +30 |
| B: wrong executable | 987/4874 (0.2025) | 1011/4874 (0.2074) | +24 |
| C: non-executable | 160/4874 (0.0328) | 106/4874 (0.0217) | -54 |
| D: ambiguous/excluded | 0/4874 (0.0000) | 0/4874 (0.0000) | +0 |

## Repair Funnel

| Metric | Value |
| --- | ---: |
| Verifier rejected rows | 3689/4874 (0.7569) |
| Attempted repairs | 3319/4874 (0.6810) |
| Generated repairs | 151/3319 (0.0455) |
| Applied repairs | 151/4874 (0.0310) |
| Fallback to original SQL | 4705/4874 (0.9653) |

## Repair Effectiveness

| Metric | Value |
| --- | ---: |
| True repair targets touched | 151/151 (1.0000) |
| True targets fixed | 30/151 (0.1987) |
| All original wrong/non-exec fixed | 30/1147 (0.0262) |
| End-to-end precision across all applied repairs | 30/151 (0.1987) |
| Repaired SQL executable | 63/151 (0.4172) |

## Repair Safety

| Metric | Value |
| --- | ---: |
| Originally correct rows touched by repair | 0/3727 (0.0000) |
| Preserved originally correct rows | 3727/3727 (1.0000) |
| Corrupted originally correct touched rows | 0/0 (0.0000) |
| Overall corruption among originally correct | 0/3727 (0.0000) |
| Net gain after corruption | 0.0062 |
| Net gain after corruption count | 30/4874 |

## Readout

- Repairs changed correctness by 0.0062 execution-accuracy points.
- The repairer fixed 30 true wrong/non-executable targets.
- The pipeline corrupted 0 originally correct rows that were touched by repair.
- Low end-to-end repair precision can come from either weak repair quality or verifier over-routing correct SQL into repair.
