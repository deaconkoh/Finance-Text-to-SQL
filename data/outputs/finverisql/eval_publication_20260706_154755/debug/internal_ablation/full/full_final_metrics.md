# Final Repair Evaluation

## Headline Results

| Metric | Value |
| --- | ---: |
| Total examples | 8735 |
| Original execution accuracy | 0.7942 |
| Final execution accuracy | 0.7991 |
| Execution accuracy delta | 0.0049 |
| Net correct gain | 43 |
| Corruption rate | 1/2 (0.5000) |
| Targeted repair success | 44/195 (0.2256) |
| End-to-end repair precision | 44/197 (0.2234) |

## Original vs Final Groups

| Group | Original | Final After Repair | Delta |
| --- | ---: | ---: | ---: |
| A: correct executable | 6937/8735 (0.7942) | 6980/8735 (0.7991) | +43 |
| B: wrong executable | 1565/8735 (0.1792) | 1596/8735 (0.1827) | +31 |
| C: non-executable | 233/8735 (0.0267) | 159/8735 (0.0182) | -74 |
| D: ambiguous/excluded | 0/8735 (0.0000) | 0/8735 (0.0000) | +0 |

## Repair Funnel

| Metric | Value |
| --- | ---: |
| Verifier rejected rows | 5545/8735 (0.6348) |
| Attempted repairs | 4523/8735 (0.5178) |
| Generated repairs | 197/4523 (0.0436) |
| Applied repairs | 197/8735 (0.0226) |
| Fallback to original SQL | 8486/8735 (0.9715) |

## Repair Effectiveness

| Metric | Value |
| --- | ---: |
| True repair targets touched | 195/197 (0.9898) |
| True targets fixed | 44/195 (0.2256) |
| All original wrong/non-exec fixed | 44/1798 (0.0245) |
| End-to-end precision across all applied repairs | 44/197 (0.2234) |
| Repaired SQL executable | 90/197 (0.4569) |

## Repair Safety

| Metric | Value |
| --- | ---: |
| Originally correct rows touched by repair | 2/6937 (0.0003) |
| Preserved originally correct rows | 6936/6937 (0.9999) |
| Corrupted originally correct touched rows | 1/2 (0.5000) |
| Overall corruption among originally correct | 1/6937 (0.0001) |
| Net gain after corruption | 0.0049 |
| Net gain after corruption count | 43/8735 |

## Readout

- Repairs changed correctness by 0.0049 execution-accuracy points.
- The repairer fixed 44 true wrong/non-executable targets.
- The pipeline corrupted 1 originally correct rows that were touched by repair.
- Low end-to-end repair precision can come from either weak repair quality or verifier over-routing correct SQL into repair.
