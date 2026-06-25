from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.asa_metrics.old.eq_acct_v1 import (
    AR_AP_TEMPLATE,
    ASSET_LIABILITY_TEMPLATE,
    BALANCE_COUNT_TEMPLATE,
    CUSTOMER_VENDOR_TEMPLATE,
    INCOME_EXPENSE_TEMPLATE,
    POSTING_TEMPLATE,
    QUANTITY_COUNT_TEMPLATE,
    TRANSACTION_TYPE_TEMPLATE,
    activate_templates,
    build_eq_acct_evidence,
    build_gold_support_requirements,
    evaluate_eq_acct_v1,
    generate_template_mutants,
)


def schema_annotations() -> dict[str, Any]:
    with Path("data/booksql/schema_annotations.json").open("r", encoding="utf-8") as f:
        return json.load(f)


def activated(sql: str) -> set[str]:
    evidence = build_eq_acct_evidence(sql, schema_annotations())
    return set(activate_templates(evidence))


def test_template_activation_positive_and_negative_cases() -> None:
    cases = [
        (POSTING_TEMPLATE, "SELECT SUM(Credit) FROM master_txn_table", "SELECT SUM(Amount) FROM master_txn_table"),
        (AR_AP_TEMPLATE, "SELECT COUNT(*) FROM master_txn_table WHERE AR_paid = 'paid'", "SELECT SUM(Amount) FROM master_txn_table WHERE Transaction_TYPE = 'invoice'"),
        (INCOME_EXPENSE_TEMPLATE, "SELECT SUM(Amount) FROM master_txn_table m JOIN chart_of_accounts c ON m.Account = c.Account_name WHERE Account_type = 'Income'", "SELECT SUM(Amount) FROM master_txn_table WHERE Product_Service = 'Widget'"),
        (ASSET_LIABILITY_TEMPLATE, "SELECT SUM(Open_balance) FROM master_txn_table m JOIN chart_of_accounts c ON m.Account = c.Account_name WHERE Account_type = 'Other Current Assets'", "SELECT SUM(Amount) FROM master_txn_table m JOIN chart_of_accounts c ON m.Account = c.Account_name WHERE Account_type = 'Income'"),
        (BALANCE_COUNT_TEMPLATE, "SELECT SUM(Open_balance) FROM master_txn_table", "SELECT SUM(Credit) FROM master_txn_table"),
        (QUANTITY_COUNT_TEMPLATE, "SELECT SUM(Quantity) FROM master_txn_table", "SELECT SUM(Amount) FROM master_txn_table"),
        (TRANSACTION_TYPE_TEMPLATE, "SELECT SUM(Amount) FROM master_txn_table WHERE Transaction_TYPE = 'invoice'", "SELECT SUM(Amount) FROM master_txn_table WHERE Misc = 'invoice'"),
        (CUSTOMER_VENDOR_TEMPLATE, "SELECT Customers, SUM(Amount) FROM master_txn_table GROUP BY Customers", "SELECT Product_Service, SUM(Amount) FROM master_txn_table GROUP BY Product_Service"),
    ]

    for template, positive_sql, negative_sql in cases:
        assert template in activated(positive_sql)
        assert template not in activated(negative_sql)


def test_eval_returns_canonical_pass_fail_and_none() -> None:
    annotations = schema_annotations()
    gold_sql = "SELECT SUM(Credit) FROM master_txn_table WHERE Account = 'Sales Income'"

    passed = evaluate_eq_acct_v1(gold_sql, gold_sql, annotations)
    assert passed["eq_acct_version"] == "v1"
    assert passed["eq_acct_result"] == 1
    assert passed["adversarial_pass"] is True
    assert passed["validated_templates"] == [POSTING_TEMPLATE]
    assert passed["tested_fixture_state_count"] > 0

    failed = evaluate_eq_acct_v1(
        gold_sql,
        "SELECT SUM(Debit) FROM master_txn_table WHERE Account = 'Sales Income'",
        annotations,
    )
    assert failed["eq_acct_result"] == 0
    assert failed["adversarial_pass"] is False
    assert failed["failed_templates"] == [POSTING_TEMPLATE]
    assert failed["failed_states"][0]["failed_reason"] == "generated_mismatch"

    no_template = evaluate_eq_acct_v1(
        "SELECT SUM(Amount) FROM master_txn_table",
        "SELECT SUM(Amount) FROM master_txn_table",
        annotations,
    )
    assert no_template["eq_acct_result"] is None
    assert no_template["adversarial_pass"] is None
    assert no_template["not_testable_reason_counts"] == {"no_applicable_template": 1}


def test_generated_execution_error_on_validated_state_is_failure() -> None:
    result = evaluate_eq_acct_v1(
        "SELECT SUM(Credit) FROM master_txn_table WHERE Account = 'Sales Income'",
        "SELECT SUM(Credit) FROM missing_table",
        schema_annotations(),
    )

    assert result["eq_acct_result"] == 0
    assert result["failed_states"]
    assert result["failed_states"][0]["failed_reason"] == "generated_execution_error"


def test_simple_date_literal_is_seeded_into_fixture_state() -> None:
    result = evaluate_eq_acct_v1(
        "SELECT SUM(Credit) FROM master_txn_table WHERE Transaction_DATE = '2024-01-31' AND Account = 'Sales Income'",
        "SELECT SUM(Credit) FROM master_txn_table WHERE Transaction_DATE = '2024-01-31' AND Account = 'Sales Income'",
        schema_annotations(),
    )

    assert result["eq_acct_result"] == 1
    assert result["invalid_fixture_state_count"] == 0


def test_support_requirements_extract_gold_only_constraints() -> None:
    annotations = schema_annotations()
    gold_sql = """
        SELECT COUNT(DISTINCT Transaction_ID)
        FROM master_txn_table
        WHERE Transaction_TYPE = 'invoice'
          AND INSTR(Account, "Appearances and speeches")
          AND Customers = "Amanda Fry"
          AND Product_Service = 'Eucalyptus oil'
          AND Transaction_DATE BETWEEN date(current_date, '-3 months') AND date(current_date)
    """
    generated_sql = "SELECT * FROM master_txn_table WHERE Customers = 'Generated Only'"
    evidence = build_eq_acct_evidence(gold_sql, annotations)
    support = build_gold_support_requirements(gold_sql, evidence, "2026-06-23")

    assert support.count_used is True
    assert support.transaction_types == {"invoice"}
    assert support.account_substrings == {"Appearances and speeches"}
    assert support.customers == {"Amanda Fry"}
    assert support.products == {"Eucalyptus oil"}
    assert support.date_lower == "2026-03-23"
    assert support.date_upper == "2026-06-23"
    assert "Generated Only" not in support.customers


def test_instr_account_support_does_not_invent_account_type() -> None:
    gold_sql = 'SELECT COUNT(*) FROM master_txn_table WHERE INSTR(Account, "Arizona Dept. of Revenue Payable")'
    evidence = build_eq_acct_evidence(
        gold_sql,
        schema_annotations(),
    )
    support = build_gold_support_requirements(gold_sql, evidence, "2026-06-23")

    assert support.account_substrings == {"Arizona Dept. of Revenue Payable"}
    assert support.account_type_concepts == set()
    assert support.account_type_literals == set()


def test_combined_predicates_seed_single_supported_transaction_scope() -> None:
    annotations = schema_annotations()
    gold_sql = """
        SELECT SUM(Quantity)
        FROM master_txn_table
        WHERE Customers = "Sylvia Bennett"
          AND Product_Service = 'Citronella oil'
          AND Transaction_TYPE IN ('invoice', 'sales receipt')
          AND Transaction_DATE BETWEEN date(current_date, 'start of year') AND date(current_date)
    """

    result = evaluate_eq_acct_v1(gold_sql, gold_sql, annotations, fixture_date="2026-06-23", include_debug=True)

    assert result["eq_acct_result"] == 1
    assert result["invalid_fixture_state_count"] == 0
    support_debug = result["debug"]["fixture_states"][0]["support_requirements"]
    assert support_debug["customers"] == ["Sylvia Bennett"]
    assert support_debug["products"] == ["Citronella oil"]
    assert support_debug["transaction_types"] == ["invoice"]
    assert support_debug["unsupported_transaction_types"] == ["sales receipt"]


def test_unsupported_date_predicate_reports_support_failure() -> None:
    result = evaluate_eq_acct_v1(
        """
        SELECT SUM(Debit)
        FROM master_txn_table
        WHERE strftime('%m', Transaction_DATE) = '07'
        """,
        """
        SELECT SUM(Debit)
        FROM master_txn_table
        WHERE strftime('%m', Transaction_DATE) = '07'
        """,
        schema_annotations(),
    )

    assert result["eq_acct_result"] is None
    assert result["not_testable_reason_counts"]["unsupported_support_requirement"] > 0


def test_count_zero_is_support_probe_failure_not_empty_result() -> None:
    result = evaluate_eq_acct_v1(
        "SELECT COUNT(*) FROM master_txn_table WHERE Customers = 'Probe Customer' AND Open_balance > 999999",
        "SELECT COUNT(*) FROM master_txn_table WHERE Customers = 'Probe Customer' AND Open_balance > 999999",
        schema_annotations(),
    )

    assert result["eq_acct_result"] is None
    assert "gold_result_empty" not in result["not_testable_reason_counts"]
    assert result["not_testable_reason_counts"]["gold_count_zero_despite_supported_rows"] > 0


def test_count_and_balance_mutants_are_generated_for_count_gold() -> None:
    mutants = generate_template_mutants(
        "SELECT COUNT(DISTINCT Transaction_ID) FROM master_txn_table WHERE Transaction_TYPE = 'invoice'",
        BALANCE_COUNT_TEMPLATE,
    )

    families = {mutant["family"] for mutant in mutants}
    assert "replace_count_with_balance" in families
    assert "replace_count_with_status" in families


def test_quantity_count_mutants_cover_distinct_and_row_grain() -> None:
    distinct_mutants = generate_template_mutants(
        "SELECT COUNT(DISTINCT Transaction_ID) FROM master_txn_table WHERE Transaction_TYPE = 'invoice'",
        QUANTITY_COUNT_TEMPLATE,
    )
    quantity_mutants = generate_template_mutants(
        "SELECT SUM(Quantity) FROM master_txn_table WHERE Transaction_TYPE = 'invoice'",
        QUANTITY_COUNT_TEMPLATE,
    )

    assert "count_distinct_to_rows" in {mutant["family"] for mutant in distinct_mutants}
    assert "replace_quantity_with_distinct_transactions" in {mutant["family"] for mutant in quantity_mutants}
