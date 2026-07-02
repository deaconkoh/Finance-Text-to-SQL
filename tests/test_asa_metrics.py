from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.asa_metrics.asa_metrics import evaluate_asa_row, evaluate_asa_rows


HARD = "hard_financial_contradiction"
NONE = "no_financial_contradiction"
NOT_EVALUABLE = "not_evaluable"


def row(question_id: str, execution_match: bool | None, generated_sql: str) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "gold_sql": "SELECT SUM(Credit) FROM ledger",
        "generated_sql": generated_sql,
        "execution_match": execution_match,
    }


def inv_result(
    value: int | None,
    *,
    hard_code: str = "hard_code",
    not_evaluable_code: str = "parse_error",
    warning_code: str | None = None,
) -> dict[str, Any]:
    if value == 1:
        status = NONE
        findings: list[dict[str, Any]] = []
    elif value == 0:
        status = HARD
        findings = [{"status": HARD, "code": hard_code}]
    else:
        status = NOT_EVALUABLE
        findings = [{"status": NOT_EVALUABLE, "code": not_evaluable_code}]
    warnings = [{"status": "warning", "code": warning_code}] if warning_code else []
    return {"primary_status": status, "findings": findings, "warnings": warnings}


def make_inv_checker(values: dict[str, dict[str, Any]]):
    def checker(*, generated_sql: str, **_: Any) -> dict[str, Any]:
        return values[generated_sql]

    return checker


def fail_if_called(**_: Any) -> dict[str, Any]:
    raise AssertionError("invariant checker should not be called")


def assert_no_eq_acct_keys(result: dict[str, Any]) -> None:
    assert "Eq_acct" not in result
    assert not any(key.startswith("eq_acct") for key in result)


def test_missing_execution_match_short_circuits_inv() -> None:
    result = evaluate_asa_row(
        row("missing", None, "unused"),
        {},
        inv_checker=fail_if_called,
    )

    assert result["EX"] is None
    assert result["asa_strict"] is None
    assert result["asa_lower_bound"] is None
    assert result["asa_decision_available"] is False
    assert result["asa_not_testable_reasons"] == ["missing_execution_match"]
    assert result["Inv"] is None
    assert_no_eq_acct_keys(result)
    assert "semantic_checks_testable" not in result


def test_execution_failure_short_circuits_inv_and_gets_zero_asa() -> None:
    result = evaluate_asa_row(
        row("ex_fail", False, "unused"),
        {},
        inv_checker=fail_if_called,
    )

    assert result["EX"] == 0
    assert result["asa_strict"] == 0
    assert result["asa_lower_bound"] == 0
    assert result["asa_decision_available"] is True
    assert result["asa_not_testable_reasons"] == []
    assert result["Inv"] is None
    assert_no_eq_acct_keys(result)


def test_ex_pass_decision_matrix_for_inv() -> None:
    cases = [
        ("inv_pass", 1, 1, 1, True, []),
        ("inv_fail", 0, 0, 0, True, []),
        ("inv_none", None, None, 0, False, ["inv_not_evaluable"]),
    ]
    inv_values = {name: inv_result(inv_value) for name, inv_value, *_ in cases}

    for (
        name,
        _inv_value,
        expected_strict,
        expected_lower_bound,
        expected_decision_available,
        expected_reasons,
    ) in cases:
        result = evaluate_asa_row(
            row(name, True, name),
            {},
            inv_checker=make_inv_checker(inv_values),
        )

        assert result["EX"] == 1
        assert result["asa_strict"] == expected_strict
        assert result["asa_lower_bound"] == expected_lower_bound
        assert result["asa_decision_available"] is expected_decision_available
        assert result["asa_not_testable_reasons"] == expected_reasons
        assert_no_eq_acct_keys(result)


def test_fcr_warnings_alone_do_not_fail_inv_or_asa() -> None:
    result = evaluate_asa_row(
        row("warning_only", True, "warning_only"),
        {},
        inv_checker=make_inv_checker(
            {"warning_only": inv_result(1, warning_code="financial_scope_warning")}
        ),
    )

    assert result["Inv"] == 1
    assert result["fcr_warning_codes"] == ["financial_scope_warning"]
    assert result["asa_strict"] == 1
    assert result["asa_lower_bound"] == 1
    assert result["asa_decision_available"] is True


def test_include_fcr_details_is_optional() -> None:
    base_result = evaluate_asa_row(
        row("base", True, "base"),
        {},
        inv_checker=make_inv_checker({"base": inv_result(0)}),
    )
    detailed_result = evaluate_asa_row(
        row("detailed", True, "detailed"),
        {},
        inv_checker=make_inv_checker({"detailed": inv_result(0)}),
        include_fcr_details=True,
    )

    assert "fcr_findings" not in base_result
    assert "fcr_warnings" not in base_result
    assert detailed_result["fcr_findings"] == [{"status": HARD, "code": "hard_code"}]
    assert detailed_result["fcr_warnings"] == []


def test_aggregate_denominators_and_diagnostic_breakdowns() -> None:
    rows = [
        row("missing", None, "missing"),
        row("ex_fail", False, "ex_fail"),
        row("inv_pass", True, "inv_pass"),
        row("inv_fail", True, "inv_fail"),
        row("inv_none", True, "inv_none"),
        row("warning_pass", True, "warning_pass"),
    ]
    inv_values = {
        "inv_pass": inv_result(1),
        "inv_fail": inv_result(0, hard_code="financial_scope_error"),
        "inv_none": inv_result(None, not_evaluable_code="parse_error"),
        "warning_pass": inv_result(1, warning_code="output_shape_warning"),
    }

    metrics, diagnostics = evaluate_asa_rows(
        rows,
        {},
        inv_checker=make_inv_checker(inv_values),
    )

    assert len(diagnostics) == 6
    assert metrics["execution_match_available_rows"] == 5
    assert metrics["ex_pass_rows"] == 4
    assert metrics["ex_fail_rows"] == 1
    assert metrics["ex_accuracy"] == 4 / 5
    assert metrics["asa_decision_available_rows"] == 4
    assert metrics["asa_strict_accuracy"] == 2 / 4
    assert metrics["asa_lower_bound_accuracy"] == 2 / 5
    assert metrics["inv_evaluable_rows_among_ex_pass"] == 3
    assert metrics["inv_evaluability_rate_among_ex_pass"] == 3 / 4
    assert metrics["fper"] == 1 / 3
    assert metrics["fper_lower_bound"] == 2 / 4
    assert metrics["inv_failure_count"] == 1
    assert metrics["inv_failure_rate_among_ex_pass_decision_available"] == 1 / 3
    assert metrics["fcr_hard_finding_counts"] == {"financial_scope_error": 1}
    assert metrics["inv_not_evaluable_reason_counts"] == {"parse_error": 1}
    assert "not_testable_reason_counts" not in metrics
    assert "fcr_warning_counts" not in metrics
    assert "semantic_check_testability_rate_among_ex_pass" not in metrics
    assert not any("Eq_acct" in diagnostic for diagnostic in diagnostics)
    assert not any(
        key.startswith("eq_acct")
        for diagnostic in diagnostics
        for key in diagnostic
    )
    assert not any(key.startswith("eq_acct") for key in metrics)
