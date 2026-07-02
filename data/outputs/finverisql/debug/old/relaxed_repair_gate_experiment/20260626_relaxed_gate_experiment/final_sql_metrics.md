# Final Repair Evaluation

## Headline Results

| Metric | Value |
| --- | ---: |
| Total examples | 1713 |
| Original execution accuracy | 0.6906 |
| Final execution accuracy | 0.6877 |
| Execution accuracy delta | -0.0029 |
| Net correct gain | -5 |
| Corruption rate | 186/430 (0.4326) |
| Targeted repair success | 181/389 (0.4653) |
| End-to-end repair precision | 181/819 (0.2210) |

## Original vs Final Groups

| Group | Original | Final After Repair | Delta |
| --- | ---: | ---: | ---: |
| A: correct executable | 1183/1713 (0.6906) | 1178/1713 (0.6877) | -5 |
| B: wrong executable | 530/1713 (0.3094) | 483/1713 (0.2820) | -47 |
| C: non-executable | 0/1713 (0.0000) | 52/1713 (0.0304) | +52 |
| D: ambiguous/excluded | 0/1713 (0.0000) | 0/1713 (0.0000) | +0 |

## Repair Funnel

| Metric | Value |
| --- | ---: |
| Verifier rejected rows | 820/1713 (0.4787) |
| Attempted repairs | 820/1713 (0.4787) |
| Generated repairs | 819/820 (0.9988) |
| Applied repairs | 819/1713 (0.4781) |
| Fallback to original SQL | 894/1713 (0.5219) |

## Repair Effectiveness

| Metric | Value |
| --- | ---: |
| True repair targets touched | 389/819 (0.4750) |
| True targets fixed | 181/389 (0.4653) |
| All original wrong/non-exec fixed | 181/530 (0.3415) |
| End-to-end precision across all applied repairs | 181/819 (0.2210) |
| Repaired SQL executable | 767/819 (0.9365) |

## Repair Safety

| Metric | Value |
| --- | ---: |
| Originally correct rows touched by repair | 430/1183 (0.3635) |
| Preserved originally correct rows | 997/1183 (0.8428) |
| Corrupted originally correct touched rows | 186/430 (0.4326) |
| Overall corruption among originally correct | 186/1183 (0.1572) |
| Net gain after corruption | -5 |

## Readout

- Repairs changed correctness by -0.0029 execution-accuracy points.
- The repairer fixed 181 true wrong/non-executable targets.
- The pipeline corrupted 186 originally correct rows that were touched by repair.
- Low end-to-end repair precision can come from either weak repair quality or verifier over-routing correct SQL into repair.
