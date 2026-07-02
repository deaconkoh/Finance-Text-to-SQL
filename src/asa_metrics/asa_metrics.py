"""Invariant-only ASA metric orchestration for finance SQL outputs.

ASA is a deterministic composite over:

- EX: original execution match, required as a gate.
- Inv: financial contradiction rejection of hard semantic mismatches.

ASA(x) = 1[EX(x) = 1 and Inv(x) = 1].

This module intentionally keeps orchestration thin. The invariant check is
delegated to the existing FCR implementation and can be injected in tests.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from src.asa_metrics.financial_contradiction import (
    HARD,
    NONE,
    NOT_EVALUABLE,
    evaluate_financial_contradiction,
)


Checker = Callable[..., dict[str, Any]]


def safe_rate(numerator: int | float, denominator: int | float) -> float:
    return numerator / denominator if denominator else 0.0


def default_inv_checker(
    *,
    gold_sql: str,
    generated_sql: str,
    schema_annotations: dict[str, Any],
) -> dict[str, Any]:
    return evaluate_financial_contradiction(
        gold_sql=gold_sql,
        generated_sql=generated_sql,
        schema_annotations=schema_annotations,
    )


def _base_row_output(row: dict[str, Any], label: str | None) -> dict[str, Any]:
    generated_sql = row.get("generated_sql") or row.get("pred_sql") or ""
    output = {
        "question_id": row.get("question_id"),
        "gold_sql": row.get("gold_sql") or "",
        "generated_sql": generated_sql,
        "execution_match": row.get("execution_match"),
        "EX": None,
        "asa_strict": None,
        "asa_lower_bound": None,
        "asa_decision_available": False,
        "asa_not_testable_reasons": [],
        "Inv": None,
        "fcr_primary_status": None,
        "fcr_hard_finding_codes": [],
        "fcr_not_evaluable_codes": [],
        "fcr_warning_codes": [],
    }
    if label is not None:
        output["set"] = label
    return output


def _map_inv_result(result: dict[str, Any]) -> int | None:
    status = result.get("primary_status")
    if status == HARD:
        return 0
    if status == NONE:
        return 1
    if status == NOT_EVALUABLE:
        return None
    return None


def _codes(items: list[dict[str, Any]], *, status: str | None = None) -> list[str]:
    codes: list[str] = []
    for item in items:
        if status is not None and item.get("status") != status:
            continue
        code = item.get("code")
        if code:
            codes.append(str(code))
    return codes


def evaluate_asa_row(
    row: dict[str, Any],
    schema_annotations: dict[str, Any],
    *,
    label: str | None = None,
    inv_checker: Checker | None = None,
    include_fcr_details: bool = False,
) -> dict[str, Any]:
    """Evaluate one row under invariant-only ASA semantics."""

    output = _base_row_output(row, label)
    execution_match = row.get("execution_match")

    if execution_match is not True and execution_match is not False:
        output["asa_not_testable_reasons"].append("missing_execution_match")
        return output

    if execution_match is False:
        output.update(
            {
                "EX": 0,
                "asa_strict": 0,
                "asa_lower_bound": 0,
                "asa_decision_available": True,
            }
        )
        return output

    output["EX"] = 1
    gold_sql = output["gold_sql"]
    generated_sql = output["generated_sql"]
    inv_checker = inv_checker or default_inv_checker

    inv_result = inv_checker(
        gold_sql=gold_sql,
        generated_sql=generated_sql,
        schema_annotations=schema_annotations,
    )
    inv_value = _map_inv_result(inv_result)
    findings = list(inv_result.get("findings", []))
    warnings = list(inv_result.get("warnings", []))
    output.update(
        {
            "Inv": inv_value,
            "fcr_primary_status": inv_result.get("primary_status"),
            "fcr_hard_finding_codes": _codes(findings, status=HARD),
            "fcr_not_evaluable_codes": _codes(findings, status=NOT_EVALUABLE),
            "fcr_warning_codes": _codes(warnings),
        }
    )
    if include_fcr_details:
        output["fcr_findings"] = findings
        output["fcr_warnings"] = warnings

    if inv_value == 1:
        output["asa_strict"] = 1
        output["asa_lower_bound"] = 1
        output["asa_decision_available"] = True
    elif inv_value == 0:
        output["asa_strict"] = 0
        output["asa_lower_bound"] = 0
        output["asa_decision_available"] = True
    else:
        output["asa_not_testable_reasons"] = ["inv_not_evaluable"]
        output["asa_strict"] = None
        output["asa_lower_bound"] = 0
        output["asa_decision_available"] = False

    return output


def evaluate_asa_rows(
    rows: list[dict[str, Any]],
    schema_annotations: dict[str, Any],
    *,
    label: str | None = None,
    inv_checker: Checker | None = None,
    include_fcr_details: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Evaluate rows and return aggregate ASA metrics plus row diagnostics."""

    diagnostics = [
        evaluate_asa_row(
            row,
            schema_annotations,
            label=label,
            inv_checker=inv_checker,
            include_fcr_details=include_fcr_details,
        )
        for row in rows
    ]

    execution_available = [row for row in diagnostics if row["EX"] in {0, 1}]
    ex_pass = [row for row in diagnostics if row["EX"] == 1]
    decision_available = [row for row in diagnostics if row["asa_decision_available"] is True]
    ex_pass_decision_available = [
        row for row in diagnostics if row["EX"] == 1 and row["asa_decision_available"] is True
    ]

    fcr_hard_finding_counts: Counter[str] = Counter()
    inv_not_evaluable_reason_counts: Counter[str] = Counter()
    for row in diagnostics:
        fcr_hard_finding_counts.update(row.get("fcr_hard_finding_codes", []))
        inv_not_evaluable_reason_counts.update(row.get("fcr_not_evaluable_codes", []))

    metrics = {
        "label": label,
        "total_rows": len(diagnostics),
        "execution_match_available_rows": len(execution_available),
        "ex_pass_rows": len(ex_pass),
        "ex_fail_rows": sum(1 for row in diagnostics if row["EX"] == 0),
        "asa_decision_available_rows": len(decision_available),
        "asa_strict_pass_rows": sum(1 for row in diagnostics if row["asa_strict"] == 1),
        "asa_strict_fail_rows": sum(1 for row in diagnostics if row["asa_strict"] == 0),
        "asa_lower_bound_pass_rows": sum(
            1 for row in diagnostics if row["asa_lower_bound"] == 1
        ),
        "inv_evaluable_rows_among_ex_pass": sum(1 for row in ex_pass if row["Inv"] in {0, 1}),
        "inv_failure_count": sum(1 for row in ex_pass if row["Inv"] == 0),
        "ex_accuracy": safe_rate(
            sum(1 for row in diagnostics if row["EX"] == 1),
            len(execution_available),
        ),
        "asa_strict_accuracy": safe_rate(
            sum(1 for row in diagnostics if row["asa_strict"] == 1),
            len(decision_available),
        ),
        "asa_lower_bound_accuracy": safe_rate(
            sum(1 for row in diagnostics if row["asa_lower_bound"] == 1),
            len(execution_available),
        ),
        "fper": safe_rate(
            sum(1 for row in ex_pass_decision_available if row["asa_strict"] == 0),
            len(ex_pass_decision_available),
        ),
        "fper_lower_bound": safe_rate(
            sum(1 for row in ex_pass if row["asa_lower_bound"] == 0),
            len(ex_pass),
        ),
        "inv_evaluability_rate_among_ex_pass": safe_rate(
            sum(1 for row in ex_pass if row["Inv"] in {0, 1}),
            len(ex_pass),
        ),
        "inv_failure_rate_among_ex_pass_decision_available": safe_rate(
            sum(1 for row in ex_pass_decision_available if row["Inv"] == 0),
            len(ex_pass_decision_available),
        ),
        "fcr_hard_finding_counts": dict(sorted(fcr_hard_finding_counts.items())),
        "inv_not_evaluable_reason_counts": dict(
            sorted(inv_not_evaluable_reason_counts.items())
        ),
    }
    return metrics, diagnostics
