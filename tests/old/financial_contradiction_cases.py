from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.asa_metrics.financial_contradiction import evaluate_financial_contradiction


def annotations(*, reconstruction: bool = False) -> dict:
    return {
        "__schema_metadata__": {
            "full_history_balance_reconstruction_supported": reconstruction,
            "as_of_balance_reconstruction_supported": reconstruction,
        },
        "ledger": {
            "Credit": {
                "semantic_role": "financial_measure",
                "measure_type": "flow",
                "unit": "money",
                "posting_side": "credit",
            },
            "Debit": {
                "semantic_role": "financial_measure",
                "measure_type": "flow",
                "unit": "money",
                "posting_side": "debit",
            },
            "Amount": {
                "semantic_role": "financial_measure",
                "measure_type": "flow",
                "unit": "money",
            },
            "Quantity": {
                "semantic_role": "quantity_measure",
                "measure_type": "quantity",
                "unit": "quantity",
            },
            "AR_paid": {
                "semantic_role": "settlement_status_flag",
                "status_dimension": "payment_status",
            },
            "Transaction_ID": {"semantic_role": "transaction_group_identifier"},
            "Account_Type": {
                "semantic_role": "account_type_classifier",
                "value_concepts": {
                    "AP": "accounts_payable",
                    "AR": "accounts_receivable",
                    "Income": "income",
                },
                "concept_metadata": {
                    "accounts_payable": {
                        "financial_element": "liability",
                        "domain_object": "accounts_payable",
                    },
                    "accounts_receivable": {
                        "financial_element": "asset",
                        "domain_object": "accounts_receivable",
                    },
                    "income": {
                        "financial_element": "income",
                        "domain_object": "income",
                    },
                },
            },
            "Vendor_name": {"semantic_role": "entity_identifier", "entity_scope": "vendor"},
            "customer_name": {"semantic_role": "entity_identifier", "entity_scope": "customer"},
            "customers_id": {"semantic_role": "entity_identifier", "entity_scope": "customer"},
            "vendors_id": {"semantic_role": "entity_identifier", "entity_scope": "vendor"},
            "Billing_address": {"semantic_role": "entity_attribute"},
        },
        "ar": {
            "Open_balance": {
                "semantic_role": "financial_measure",
                "financial_role": "balance",
                "measure_type": "stock",
                "unit": "money",
                "financial_element": "asset",
                "domain_object": "accounts_receivable",
            },
            "Debit": {
                "semantic_role": "financial_measure",
                "measure_type": "flow",
                "unit": "money",
                "posting_side": "debit",
                "financial_element": "asset",
                "domain_object": "accounts_receivable",
            },
            "Credit": {
                "semantic_role": "financial_measure",
                "measure_type": "flow",
                "unit": "money",
                "posting_side": "credit",
                "financial_element": "asset",
                "domain_object": "accounts_receivable",
            },
            "Amount": {
                "semantic_role": "financial_measure",
                "measure_type": "flow",
                "unit": "money",
                "financial_element": "asset",
                "domain_object": "accounts_receivable",
            },
            "Quantity": {
                "semantic_role": "quantity_measure",
                "measure_type": "quantity",
                "unit": "quantity",
                "domain_object": "accounts_receivable",
            },
        },
        "ap": {
            "Open_balance": {
                "semantic_role": "financial_measure",
                "financial_role": "balance",
                "measure_type": "stock",
                "unit": "money",
                "financial_element": "liability",
                "domain_object": "accounts_payable",
            },
            "Debit": {
                "semantic_role": "financial_measure",
                "measure_type": "flow",
                "unit": "money",
                "posting_side": "debit",
                "financial_element": "liability",
                "domain_object": "accounts_payable",
            },
            "Credit": {
                "semantic_role": "financial_measure",
                "measure_type": "flow",
                "unit": "money",
                "posting_side": "credit",
                "financial_element": "liability",
                "domain_object": "accounts_payable",
            },
        },
        "income": {
            "Credit": {
                "semantic_role": "financial_measure",
                "measure_type": "flow",
                "unit": "money",
                "posting_side": "credit",
                "financial_element": "income",
                "domain_object": "income",
            },
        },
    }


def status(gold: str, generated: str, ann: dict | None = None) -> str:
    result = evaluate_financial_contradiction(gold, generated, ann or annotations())
    return result["primary_status"]


def warning_codes(gold: str, generated: str, ann: dict | None = None) -> set[str]:
    result = evaluate_financial_contradiction(gold, generated, ann or annotations())
    return {warning["code"] for warning in result["warnings"]}


def test_posting_side_credit_vs_debit_is_hard() -> None:
    assert (
        status("SELECT SUM(Credit) FROM ledger", "SELECT SUM(Debit) FROM ledger")
        == "hard_financial_contradiction"
    )
    assert (
        status("SELECT SUM(Debit) FROM ledger", "SELECT SUM(Credit) FROM ledger")
        == "hard_financial_contradiction"
    )


def test_posting_side_formula_reversal_is_hard() -> None:
    assert (
        status("SELECT SUM(Credit)-SUM(Debit) FROM ledger", "SELECT SUM(Debit)-SUM(Credit) FROM ledger")
        == "hard_financial_contradiction"
    )


def test_resolved_posting_side_vs_unresolved_amount_is_warning() -> None:
    gold = "SELECT SUM(Credit) FROM ledger"
    generated = "SELECT SUM(Amount) FROM ledger"
    assert status(gold, generated) == "no_financial_contradiction"
    assert "unresolved_measure_warning" in warning_codes(gold, generated)


def test_ar_balance_vs_net_formula_without_history_support_is_warning() -> None:
    gold = "SELECT SUM(Open_balance) FROM ar"
    generated = "SELECT SUM(Debit)-SUM(Credit) FROM ar"
    assert status(gold, generated) == "no_financial_contradiction"
    assert "financial_measure_mismatch" in warning_codes(gold, generated)


def test_ar_balance_vs_supported_net_formula_is_no_contradiction() -> None:
    assert status(
        "SELECT SUM(Open_balance) FROM ar",
        "SELECT SUM(Debit)-SUM(Credit) FROM ar",
        annotations(reconstruction=True),
    ) == "no_financial_contradiction"


def test_ap_balance_vs_supported_net_formula_is_no_contradiction() -> None:
    assert status(
        "SELECT SUM(Open_balance) FROM ap",
        "SELECT SUM(Credit)-SUM(Debit) FROM ap",
        annotations(reconstruction=True),
    ) == "no_financial_contradiction"


def test_stock_balance_vs_generic_amount_is_warning() -> None:
    gold = "SELECT SUM(Open_balance) FROM ar"
    generated = "SELECT SUM(Amount) FROM ar"
    assert status(gold, generated) == "no_financial_contradiction"
    assert "financial_measure_mismatch" in warning_codes(gold, generated)


def test_stock_balance_object_and_proxy_contradictions_are_hard() -> None:
    assert (
        status("SELECT SUM(Open_balance) FROM ar", "SELECT SUM(Credit) FROM income")
        == "hard_financial_contradiction"
    )
    assert (
        status("SELECT SUM(Open_balance) FROM ar", "SELECT SUM(Open_balance) FROM ap")
        == "hard_financial_contradiction"
    )
    assert (
        status("SELECT SUM(Open_balance) FROM ar", "SELECT COUNT(*) FROM ar")
        == "hard_financial_contradiction"
    )
    assert (
        status("SELECT SUM(Open_balance) FROM ar", "SELECT AR_paid FROM ledger")
        == "hard_financial_contradiction"
    )


def test_finance_bearing_filter_changes() -> None:
    assert status(
        "SELECT SUM(Amount) FROM ledger WHERE Account_Type = 'AP'",
        "SELECT SUM(Amount) FROM ledger WHERE Account_Type = 'Income'",
    ) == "hard_financial_contradiction"
    gold = "SELECT SUM(Amount) FROM ledger WHERE Account_Type = 'AP'"
    generated = "SELECT SUM(Amount) FROM ledger"
    assert status(gold, generated) == "no_financial_contradiction"
    assert "financial_scope_warning" in warning_codes(gold, generated)
    gold = "SELECT SUM(Amount) FROM ledger"
    generated = "SELECT SUM(Amount) FROM ledger WHERE Account_Type = 'Income'"
    assert status(gold, generated) == "no_financial_contradiction"
    assert "financial_scope_warning" in warning_codes(gold, generated)
    assert status(
        "SELECT SUM(Amount) FROM ledger WHERE Vendor_name = 'A'",
        "SELECT SUM(Amount) FROM ledger WHERE Vendor_name = 'B'",
    ) == "no_financial_contradiction"


def test_multiple_select_output_coverage() -> None:
    assert status(
        "SELECT SUM(Credit), SUM(Debit) FROM ledger",
        "SELECT SUM(Debit), SUM(Credit) FROM ledger",
    ) == "no_financial_contradiction"
    assert (
        status("SELECT customer_name FROM ledger", "SELECT customer_name, Vendor_name FROM ledger")
        == "no_financial_contradiction"
    )
    gold = "SELECT customer_name FROM ledger"
    generated = "SELECT customer_name, SUM(Amount) FROM ledger"
    assert status(gold, generated) == "no_financial_contradiction"
    assert "output_shape_warning" in warning_codes(gold, generated)
    assert (
        status("SELECT SUM(Credit) FROM ledger", "SELECT SUM(Credit), SUM(Debit) FROM ledger")
        == "hard_financial_contradiction"
    )
    gold = "SELECT SUM(Open_balance) FROM ar"
    generated = "SELECT SUM(Open_balance), SUM(Amount) FROM ar"
    assert status(gold, generated) == "no_financial_contradiction"
    assert "output_shape_warning" in warning_codes(gold, generated)
    gold = "SELECT SUM(Amount) FROM ledger"
    generated = "SELECT customer_name FROM ledger"
    assert status(gold, generated) == "no_financial_contradiction"
    assert "output_shape_warning" in warning_codes(gold, generated)


def test_aggregation_changes() -> None:
    gold = "SELECT SUM(Quantity) FROM ledger"
    generated = "SELECT COUNT(DISTINCT Transaction_ID) FROM ledger"
    assert status(gold, generated) == "no_financial_contradiction"
    assert "aggregation_or_grain_error" in warning_codes(gold, generated)
    gold = "SELECT SUM(Quantity) FROM ledger"
    generated = "SELECT COUNT(*) FROM ledger"
    assert status(gold, generated) == "no_financial_contradiction"
    assert "aggregation_or_grain_error" in warning_codes(gold, generated)
    gold = "SELECT SUM(Amount) FROM ledger"
    generated = "SELECT COUNT(*) FROM ledger"
    assert status(gold, generated) == "no_financial_contradiction"
    assert "aggregation_or_grain_error" in warning_codes(gold, generated)
    assert (
        status("SELECT COUNT(*) FROM ledger", "SELECT COUNT(DISTINCT Transaction_ID) FROM ledger")
        == "no_financial_contradiction"
    )
    gold = "SELECT SUM(Amount) FROM ledger"
    generated = "SELECT AVG(Amount) FROM ledger"
    assert status(gold, generated) == "no_financial_contradiction"
    assert "aggregation_or_grain_error" in warning_codes(gold, generated)


def test_non_financial_cases() -> None:
    assert (
        status("SELECT Billing_address FROM ledger", "SELECT SUM(Open_balance) FROM ar")
        == "no_financial_contradiction"
    )
    assert status("SELECT customer_name FROM ledger", "SELECT Vendor_name FROM ledger") == "no_financial_contradiction"
    assert status("SELECT customers_id FROM ledger", "SELECT vendors_id FROM ledger") == "no_financial_contradiction"
    assert (
        status("SELECT customer_name FROM ledger", "SELECT Vendor_name FROM ledger WHERE Vendor_name = 'A'")
        == "no_financial_contradiction"
    )


def test_evaluability_cases() -> None:
    assert status("SELECT SUM(Amount FROM ledger", "SELECT SUM(Amount) FROM ledger") == "not_evaluable"
    assert status(
        "SELECT Amount FROM (SELECT Amount FROM ledger)",
        "SELECT Amount FROM ledger",
    ) == "not_evaluable"
    assert status(
        "SELECT CASE WHEN Vendor_name = 'A' THEN customer_name END FROM ledger",
        "SELECT customer_name FROM ledger",
    ) == "no_financial_contradiction"
    assert status(
        "SELECT CASE WHEN Vendor_name = 'A' THEN SUM(Amount) END FROM ledger",
        "SELECT SUM(Amount) FROM ledger",
    ) == "not_evaluable"


def test_missing_annotations_only_block_financial_looking_fields() -> None:
    ann = annotations()
    del ann["ledger"]["Amount"]
    assert status("SELECT SUM(Amount) FROM ledger", "SELECT SUM(Credit) FROM ledger", ann) == "not_evaluable"

    ann = annotations()
    del ann["ledger"]["customer_name"]
    assert status("SELECT customer_name FROM ledger", "SELECT Vendor_name FROM ledger", ann) == "no_financial_contradiction"
