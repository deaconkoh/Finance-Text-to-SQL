from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.asa_metrics.financial_contradiction import evaluate_financial_contradiction


HARD = "hard_financial_contradiction"
NONE = "no_financial_contradiction"
NOT_EVALUABLE = "not_evaluable"


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
            "Rate": {
                "semantic_role": "rate_measure",
                "measure_type": "rate",
                "unit": "monetary_per_unit",
            },
            "Open_balance": {
                "semantic_role": "financial_measure",
                "financial_role": "balance",
                "measure_type": "stock",
                "unit": "money",
            },
            "AR_paid": {
                "semantic_role": "settlement_status_flag",
                "status_dimension": "payment_status",
            },
            "Transaction_ID": {"semantic_role": "transaction_group_identifier"},
            "Transaction_TYPE": {
                "semantic_role": "transaction_type_classifier",
                "measure_type": "categorical",
                "value_concepts": {
                    "invoice": "invoice",
                    "bill": "bill",
                    "deposit": "deposit",
                },
            },
            "Account_Type": {
                "semantic_role": "account_type_classifier",
                "value_concepts": {
                    "AP": "accounts_payable",
                    "AR": "accounts_receivable",
                    "Income": "income",
                    "Expense": "expense",
                    "Asset": "asset",
                    "Liability": "liability",
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
                    "expense": {
                        "financial_element": "expense",
                        "domain_object": "expense",
                    },
                    "asset": {"financial_element": "asset"},
                    "liability": {"financial_element": "liability"},
                },
            },
            "Vendor_name": {"semantic_role": "entity_identifier", "entity_scope": "vendor"},
            "customer_name": {"semantic_role": "entity_identifier", "entity_scope": "customer"},
            "customers_id": {"semantic_role": "entity_identifier", "entity_scope": "customer"},
            "vendors_id": {"semantic_role": "entity_identifier", "entity_scope": "vendor"},
            "Billing_address": {"semantic_role": "entity_attribute"},
        },
        "customers": {
            "Balance": {
                "semantic_role": "financial_measure",
                "financial_role": "balance",
                "measure_type": "stock",
                "unit": "money",
                "financial_element": "asset",
                "domain_object": "accounts_receivable",
            },
        },
        "vendors": {
            "Balance": {
                "semantic_role": "financial_measure",
                "financial_role": "balance",
                "measure_type": "stock",
                "unit": "money",
                "financial_element": "liability",
                "domain_object": "accounts_payable",
            },
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


def result(gold: str, generated: str, ann: dict | None = None) -> dict:
    return evaluate_financial_contradiction(gold, generated, ann or annotations())


def status(gold: str, generated: str, ann: dict | None = None) -> str:
    return result(gold, generated, ann)["primary_status"]


def finding_codes(gold: str, generated: str, ann: dict | None = None) -> set[str]:
    return {finding["code"] for finding in result(gold, generated, ann)["findings"]}


def warning_codes(gold: str, generated: str, ann: dict | None = None) -> set[str]:
    return {warning["code"] for warning in result(gold, generated, ann)["warnings"]}


def assert_hard_code(gold: str, generated: str, code: str, ann: dict | None = None) -> None:
    evaluated = result(gold, generated, ann)
    assert evaluated["primary_status"] == HARD
    assert code in {finding["code"] for finding in evaluated["findings"]}


def assert_not_hard_code(gold: str, generated: str, code: str, ann: dict | None = None) -> None:
    assert code not in finding_codes(gold, generated, ann)


def test_rate_as_total_amount_substitution_hard() -> None:
    assert_hard_code("SELECT SUM(Credit) FROM ledger", "SELECT SUM(Rate) FROM ledger", "rate_as_total_amount_substitution")
    assert_hard_code("SELECT SUM(Amount) FROM ledger", "SELECT SUM(Rate) FROM ledger", "rate_as_total_amount_substitution")
    assert_hard_code("SELECT SUM(Credit) FROM ledger", "SELECT AVG(Rate) FROM ledger", "rate_as_total_amount_substitution")
    assert_hard_code("SELECT SUM(Amount) FROM ledger", "SELECT Rate FROM ledger", "rate_as_total_amount_substitution")


def test_rate_as_total_amount_substitution_non_hard() -> None:
    assert_not_hard_code(
        "SELECT SUM(Open_balance) FROM ledger",
        "SELECT SUM(Rate) FROM ledger",
        "rate_as_total_amount_substitution",
    )
    assert_not_hard_code(
        "SELECT SUM(Amount) FROM ledger",
        "SELECT SUM(Quantity * Rate) FROM ledger",
        "rate_as_total_amount_substitution",
    )
    assert status("SELECT AVG(Rate) FROM ledger", "SELECT AVG(Rate) FROM ledger") == NONE
    assert_not_hard_code(
        "SELECT SUM(Amount) FROM ledger",
        "SELECT SUM(Amount) FROM ledger WHERE Rate > 10",
        "rate_as_total_amount_substitution",
    )


def test_invoice_bill_transaction_type_substitution_hard() -> None:
    assert_hard_code(
        "SELECT SUM(Amount) FROM ledger WHERE Transaction_TYPE = 'invoice'",
        "SELECT SUM(Amount) FROM ledger WHERE Transaction_TYPE = 'bill'",
        "invoice_bill_transaction_type_substitution",
    )
    assert_hard_code(
        "SELECT SUM(Amount) FROM ledger WHERE Transaction_TYPE = 'bill'",
        "SELECT SUM(Amount) FROM ledger WHERE Transaction_TYPE = 'invoice'",
        "invoice_bill_transaction_type_substitution",
    )
    assert_hard_code(
        "SELECT SUM(Amount) FROM ledger WHERE Transaction_TYPE = 'Invoice'",
        "SELECT SUM(Amount) FROM ledger WHERE Transaction_TYPE = 'BILL'",
        "invoice_bill_transaction_type_substitution",
    )


def test_invoice_bill_transaction_type_substitution_non_hard() -> None:
    assert_not_hard_code(
        "SELECT SUM(Amount) FROM ledger WHERE Transaction_TYPE = 'invoice'",
        "SELECT SUM(Amount) FROM ledger",
        "invoice_bill_transaction_type_substitution",
    )
    assert_not_hard_code(
        "SELECT SUM(Amount) FROM ledger WHERE Transaction_TYPE = 'deposit'",
        "SELECT SUM(Amount) FROM ledger WHERE Transaction_TYPE = 'invoice'",
        "invoice_bill_transaction_type_substitution",
    )
    assert_not_hard_code(
        "SELECT SUM(Amount) FROM ledger WHERE Transaction_TYPE = 'memo'",
        "SELECT SUM(Amount) FROM ledger WHERE Transaction_TYPE = 'invoice'",
        "invoice_bill_transaction_type_substitution",
    )
    assert_not_hard_code(
        "SELECT Transaction_TYPE FROM ledger",
        "SELECT SUM(Amount) FROM ledger",
        "invoice_bill_transaction_type_substitution",
    )


def test_balance_stock_replaced_by_flow_amount_hard() -> None:
    assert_hard_code("SELECT SUM(Open_balance) FROM ledger", "SELECT SUM(Credit) FROM ledger", "balance_stock_replaced_by_flow_amount")
    assert_hard_code("SELECT SUM(Open_balance) FROM ledger", "SELECT SUM(Debit) FROM ledger", "balance_stock_replaced_by_flow_amount")
    assert_hard_code("SELECT SUM(Open_balance) FROM ledger", "SELECT SUM(Amount) FROM ledger", "balance_stock_replaced_by_flow_amount")
    assert_hard_code(
        "SELECT SUM(customers.Balance) FROM customers",
        "SELECT SUM(Amount) FROM ledger",
        "balance_stock_replaced_by_flow_amount",
    )
    assert_hard_code(
        "SELECT SUM(vendors.Balance) FROM vendors",
        "SELECT SUM(Amount) FROM ledger",
        "balance_stock_replaced_by_flow_amount",
    )


def test_balance_stock_replaced_by_flow_amount_non_hard() -> None:
    assert status("SELECT SUM(Open_balance) FROM ledger", "SELECT SUM(Open_balance) FROM ledger") == NONE
    assert_not_hard_code(
        "SELECT SUM(customers.Balance) FROM customers",
        "SELECT SUM(vendors.Balance) FROM vendors",
        "balance_stock_replaced_by_flow_amount",
    )
    assert_not_hard_code(
        "SELECT SUM(Credit) FROM ledger",
        "SELECT SUM(Amount) FROM ledger",
        "balance_stock_replaced_by_flow_amount",
    )
    assert_not_hard_code(
        "SELECT SUM(Open_balance) FROM ledger",
        "SELECT SUM(Open_balance) FROM ledger WHERE Amount > 0",
        "balance_stock_replaced_by_flow_amount",
    )


def test_existing_hard_regressions() -> None:
    assert_hard_code("SELECT SUM(Credit) FROM ledger", "SELECT SUM(Debit) FROM ledger", "posting_side_reversal")
    assert_hard_code(
        "SELECT SUM(Credit)-SUM(Debit) FROM ledger",
        "SELECT SUM(Debit)-SUM(Credit) FROM ledger",
        "posting_side_reversal",
    )
    assert_hard_code(
        "SELECT SUM(Amount) FROM ledger WHERE Account_Type = 'AR'",
        "SELECT SUM(Amount) FROM ledger WHERE Account_Type = 'AP'",
        "financial_filter_changed_incompatibly",
    )
    assert_hard_code(
        "SELECT SUM(Amount) FROM ledger WHERE Account_Type = 'Income'",
        "SELECT SUM(Amount) FROM ledger WHERE Account_Type = 'Expense'",
        "financial_filter_changed_incompatibly",
    )
    assert_hard_code(
        "SELECT SUM(Amount) FROM ledger WHERE Account_Type = 'Asset'",
        "SELECT SUM(Amount) FROM ledger WHERE Account_Type = 'Liability'",
        "financial_filter_changed_incompatibly",
    )
    assert_hard_code("SELECT SUM(Open_balance) FROM ledger", "SELECT COUNT(*) FROM ledger", "balance_replaced_by_count_or_status_proxy")
    assert_hard_code("SELECT SUM(Open_balance) FROM ledger", "SELECT AR_paid FROM ledger", "balance_replaced_by_count_or_status_proxy")
    assert_hard_code(
        "SELECT SUM(Open_balance) FROM ledger WHERE AR_paid = 'paid'",
        "SELECT COUNT(*) FROM ledger WHERE AR_paid = 'paid'",
        "balance_replaced_by_count_or_status_proxy",
    )


def test_warning_and_non_hard_regressions() -> None:
    gold = "SELECT SUM(Quantity) FROM ledger"
    generated = "SELECT COUNT(Transaction_ID) FROM ledger"
    assert status(gold, generated) == NONE
    assert "aggregation_or_grain_error" in warning_codes(gold, generated)

    gold = "SELECT SUM(Amount) FROM ledger"
    generated = "SELECT AVG(Amount) FROM ledger"
    assert status(gold, generated) == NONE
    assert "aggregation_or_grain_error" in warning_codes(gold, generated)

    gold = "SELECT SUM(Amount) FROM ledger WHERE Account_Type = 'AP'"
    generated = "SELECT SUM(Amount) FROM ledger"
    assert status(gold, generated) == NONE
    assert "financial_scope_warning" in warning_codes(gold, generated)

    gold = "SELECT SUM(Amount) FROM ledger"
    generated = "SELECT SUM(Amount) FROM ledger WHERE Account_Type = 'Income'"
    assert status(gold, generated) == NONE
    assert "financial_scope_warning" in warning_codes(gold, generated)

    gold = "SELECT SUM(Amount) FROM ledger"
    generated = "SELECT SUM(Amount), SUM(Credit) FROM ledger"
    assert status(gold, generated) == NONE
    assert "output_shape_warning" in warning_codes(gold, generated)


def test_amount_does_not_trigger_direction_or_class_hard_rules() -> None:
    codes = finding_codes("SELECT SUM(Amount) FROM ledger", "SELECT SUM(Credit) FROM ledger")
    assert "posting_side_reversal" not in codes
    assert "incompatible_financial_output" not in codes
    assert "financial_filter_changed_incompatibly" not in codes
    assert status("SELECT SUM(Amount) FROM ledger", "SELECT SUM(Credit) FROM ledger") == NONE


def test_existing_balance_reconstruction_behavior_is_preserved() -> None:
    assert status(
        "SELECT SUM(Open_balance) FROM ar",
        "SELECT SUM(Debit)-SUM(Credit) FROM ar",
        annotations(reconstruction=True),
    ) == NONE


def test_evaluability_regressions() -> None:
    assert status("SELECT SUM(Amount FROM ledger", "SELECT SUM(Amount) FROM ledger") == NOT_EVALUABLE
