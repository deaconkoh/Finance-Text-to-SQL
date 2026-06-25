# Accounting-Semantic Accuracy (ASA)

## Current Definition

The active ASA metric is invariant-only:

```text
ASA(x) = 1[EX(x) = 1 and Inv(x) = 1]
```

`EX` is the original execution-match result. `Inv` is the deterministic financial-contradiction/invariant result from the FCR checker.

Eq_acct is no longer part of the active ASA pipeline. It is retained only as deprecated research code.

## Row Semantics

Each row is evaluated as follows:

| Condition | EX | Inv | asa_strict | asa_lower_bound | asa_decision_available |
| --- | ---: | ---: | ---: | ---: | --- |
| missing `execution_match` | `None` | `None` | `None` | `None` | `False` |
| `execution_match = False` | `0` | `None` | `0` | `0` | `True` |
| `execution_match = True`, `Inv = 1` | `1` | `1` | `1` | `1` | `True` |
| `execution_match = True`, `Inv = 0` | `1` | `0` | `0` | `0` | `True` |
| `execution_match = True`, `Inv = None` | `1` | `None` | `None` | `0` | `False` |

For EX-failing rows, ASA is decisively zero and the invariant checker is not run.

For EX-passing rows where the invariant checker is not evaluable, strict ASA is unknown and the lower bound is zero.

## Active Diagnostics

Active row diagnostics are limited to EX/Inv/FCR fields:

- `EX`
- `Inv`
- `asa_strict`
- `asa_lower_bound`
- `asa_decision_available`
- `asa_not_testable_reasons`
- `fcr_primary_status`
- `fcr_hard_finding_codes`
- `fcr_not_evaluable_codes`
- `fcr_warning_codes`
- optional `fcr_findings` and `fcr_warnings` when `--include-fcr-details` is used

Eq_acct fields are not emitted by active ASA row diagnostics.

## Active Aggregate Metrics

The reporting script emits invariant-based aggregate metrics:

- `ex_accuracy`
- `asa_strict_accuracy`
- `asa_lower_bound_accuracy`
- `inv_evaluability_rate_among_ex_pass`
- `fper`
- `fper_lower_bound`
- `inv_failure_count`
- `inv_failure_rate_among_ex_pass_decision_available`
- `fcr_hard_finding_counts`
- `inv_not_evaluable_reason_counts`

`fper` measures the share of EX-passing, decision-available rows that ASA rejects:

```text
fper = #(EX = 1 and asa_decision_available and asa_strict = 0)
       / #(EX = 1 and asa_decision_available)
```

`fper_lower_bound` treats invariant-not-evaluable EX-passing rows as lower-bound ASA failures:

```text
fper_lower_bound = #(EX = 1 and asa_lower_bound = 0) / #(EX = 1)
```

## Active Files

The active ASA implementation lives in:

- `src/asa_metrics/asa_metrics.py`
- `src/asa_metrics/financial_contradiction.py`
- `scripts/evaluate_asa_metrics.py`

The active CLI no longer accepts Eq_acct fixture options such as `--fixture-date`, `--max-progress-steps`, or `--progress-check-interval`.

## Legacy Research Code

Deprecated Eq_acct and accounting-adversarial code has been moved under:

- `src/asa_metrics/old/accounting_adversarial.py`
- `src/asa_metrics/old/eq_acct_v1.py`

Those modules are kept for historical experiments, debug scripts, and direct research tests. They are not imported, invoked, counted, or emitted by the active ASA evaluator.

Legacy tooling that still uses these modules should import from `src.asa_metrics.old`.
