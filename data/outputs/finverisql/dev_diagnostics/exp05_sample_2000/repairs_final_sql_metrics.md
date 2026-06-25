# Final Repair Evaluation

## Final Performance

| Metric | Value |
| --- | ---: |
| Total examples | 1713 |
| Metric denominator | 1713 |
| Execution accuracy | 0.7169 |
| Original execution accuracy | 0.6906 |
| Delta execution accuracy | 0.0263 |
| Net correct gain | 45 |
| Valid SQL rate | 0.9854 |
| Executable-wrong rate | 0.2685 |
| Non-executable rate | 0.0146 |

## Final Group Counts

| Group | Count |
| --- | ---: |
| Group A correct executable | 1228 |
| Group B wrong executable | 460 |
| Group C non-executable | 25 |
| Group D ambiguous/excluded | 0 |

## Repair Coverage

| Metric | Value |
| --- | ---: |
| Verifier rejected rows | 820/1713 (0.4787) |
| Attempted repairs | 662/1713 (0.3865) |
| Generated repairs | 661/662 (0.9985) |
| Applied repairs | 661/1713 (0.3859) |
| Skipped repairs | 1051/1713 (0.6135) |
| Repair failures | 1/662 (0.0015) |
| Fallback original SQL rows | 1052 |
| Missing final SQL rows | 0 |

## Repair Effectiveness

| Metric | Value |
| --- | ---: |
| Wrong/non-executable to correct | 156/530 (0.2943) |
| Applied wrong/non-executable to correct | 156/334 (0.4671) |
| Repair success rate | 156/662 (0.2356) |
| Repair precision | 156/661 (0.2360) |
| Executable repair rate | 636/661 (0.9622) |
| Repaired rows final correct | 372/661 (0.5628) |
| Wrong/non-executable still wrong/non-executable | 374/530 (0.7057) |
| Non-executable to correct | 0/0 (0.0000) |

## Repair Safety

| Metric | Value |
| --- | ---: |
| Originally correct rows | 1183 |
| Originally correct rows repaired | 327 |
| Preserved originally correct rows | 1072/1183 (0.9062) |
| Corrupted originally correct rows | 111/327 (0.3394) |
| Overall corruption rate | 111/1183 (0.0938) |
| Net gain after corruption | 45 |

## Probe Summary

| Metric | Value |
| --- | ---: |
| Probed rows | 283/1713 (0.1652) |
| Total probes | 455 |
| Avg probes/query | 0.2656 |
| Avg probes/rejected query | 0.0634 |
| Probe rejection rate | 48/283 (0.1696) |
| Non-probe rejection rate | 772/1430 (0.5399) |
| Ambiguous rows | 3/1713 (0.0018) |
| Abstention rows | 3/1713 (0.0018) |
| High-confidence rejected rows | 689/1577 (0.4369) |
