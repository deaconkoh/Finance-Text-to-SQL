from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.asa_metrics.old.accounting_adversarial import (
    ALLOWED_TRANSACTION_TYPES,
    evaluate_rows,
    evaluate_sql_pair_on_fixture,
    build_fixture,
    assert_fixture_integrity,
    preflight_template,
    TEMPLATE_NAMES,
)


def fixture_conn(sql: str = "SELECT SUM(Credit) FROM master_txn_table") -> tuple[sqlite3.Connection, object]:
    fixture = build_fixture(sql)
    return sqlite3.connect(fixture.db_path), fixture


def close_fixture(conn: sqlite3.Connection, fixture: object) -> None:
    conn.close()
    fixture.db_path.unlink(missing_ok=True)


def test_fixture_fk_validity_and_balanced_groups() -> None:
    conn, fixture = fixture_conn()
    try:
        assert_fixture_integrity(conn)
    finally:
        close_fixture(conn, fixture)


def test_no_unobserved_transaction_types_are_inserted() -> None:
    conn, fixture = fixture_conn(
        "SELECT COUNT(*) FROM master_txn_table WHERE Transaction_TYPE IN ('invoice', 'bill')"
    )
    try:
        observed = {row[0] for row in conn.execute("SELECT DISTINCT Transaction_TYPE FROM master_txn_table")}
        assert observed <= ALLOWED_TRANSACTION_TYPES
        assert "payment" not in observed
        assert {"invoice", "bill"} <= fixture.seed_values
    finally:
        close_fixture(conn, fixture)


def test_deposit_does_not_imply_income_or_asset_by_label_alone() -> None:
    conn, fixture = fixture_conn("SELECT SUM(Amount) FROM master_txn_table WHERE Transaction_TYPE = 'deposit'")
    try:
        deposit_types = {
            row[0]
            for row in conn.execute(
                """
                SELECT c.Account_type
                FROM master_txn_table m
                JOIN chart_of_accounts c
                  ON m.businessID = c.businessID AND m.Account = c.Account_name
                WHERE m.Transaction_TYPE = 'deposit'
                """
            )
        }
        assert "income" not in deposit_types
        assert "bank" in deposit_types
        assert "other current liabilities" in deposit_types
    finally:
        close_fixture(conn, fixture)


def test_missing_status_placeholder_is_not_unpaid() -> None:
    conn, fixture = fixture_conn()
    try:
        unpaid = conn.execute(
            "SELECT COUNT(*) FROM master_txn_table WHERE AR_paid = 'unpaid' OR AP_paid = 'unpaid'"
        ).fetchone()[0]
        placeholders = conn.execute(
            "SELECT COUNT(*) FROM master_txn_table WHERE AR_paid = '--' OR AP_paid = '--'"
        ).fetchone()[0]
        assert unpaid == 0
        assert placeholders > 0
    finally:
        close_fixture(conn, fixture)


def test_each_template_preflight_rule_is_discriminative() -> None:
    conn, fixture = fixture_conn(
        "SELECT SUM(Quantity) FROM master_txn_table WHERE Transaction_TYPE = 'invoice'"
    )
    try:
        for template in TEMPLATE_NAMES:
            passed, probes = preflight_template(conn, template)
            assert passed, (template, probes)
    finally:
        close_fixture(conn, fixture)


def test_gold_literal_seeding_does_not_invent_accounting_meaning_for_account_names() -> None:
    conn, fixture = fixture_conn(
        "SELECT SUM(Amount) FROM master_txn_table WHERE Account = 'Deposit Income'"
    )
    try:
        account_type = conn.execute(
            "SELECT Account_type FROM chart_of_accounts WHERE Account_name = 'Deposit Income'"
        ).fetchone()[0]
        assert account_type is None
    finally:
        close_fixture(conn, fixture)


def test_generated_only_literal_failure_and_debug() -> None:
    result = evaluate_sql_pair_on_fixture(
        gold_sql="SELECT SUM(Credit) FROM master_txn_table WHERE Account = 'Sales Income'",
        generated_sql="SELECT SUM(Credit) FROM master_txn_table WHERE Account = 'Unseeded Income'",
    )
    assert "posting_side_debit_credit" in result["failed_templates"]
    assert result["generated_only_literal_debug"]
    debug = result["generated_only_literal_debug"][0]
    assert debug["literal_value"] == "Unseeded Income"
    assert debug["debug_reason"] == "generated_literal_not_in_fixture"
    assert debug["appears_in_fixture_seed_values"] is False


def test_empty_generated_output_is_failure_when_gold_and_preflight_pass() -> None:
    result = evaluate_sql_pair_on_fixture(
        gold_sql="SELECT SUM(Credit) FROM master_txn_table WHERE Account = 'Sales Income'",
        generated_sql="SELECT Account FROM master_txn_table WHERE Account = 'Missing Account'",
    )
    assert result["tested_templates"]
    assert result["adversarial_pass"] is False
    assert result["failed_templates"]


def test_gold_error_empty_gold_and_preflight_are_exclusions() -> None:
    gold_error = evaluate_sql_pair_on_fixture(
        gold_sql="SELECT SUM(Credit) FROM missing_table",
        generated_sql="SELECT 1",
    )
    assert gold_error["gold_error_excluded_templates"]
    assert gold_error["adversarial_pass"] is None

    gold_empty = evaluate_sql_pair_on_fixture(
        gold_sql="SELECT Credit FROM master_txn_table WHERE Credit < 0",
        generated_sql="SELECT Account FROM master_txn_table",
    )
    assert gold_empty["gold_empty_excluded_templates"]
    assert gold_empty["adversarial_pass"] is None

    conn, fixture = fixture_conn()
    try:
        conn.execute("UPDATE master_txn_table SET Quantity = 1")
        passed, _ = preflight_template(conn, "quantity_transaction_count")
        assert passed is False
    finally:
        close_fixture(conn, fixture)


def test_metrics_denominators_and_rates() -> None:
    rows = [
        {
            "question_id": "pass",
            "gold_sql": "SELECT SUM(Credit) FROM master_txn_table WHERE Account = 'Sales Income'",
            "generated_sql": "SELECT SUM(Credit) FROM master_txn_table WHERE Account = 'Sales Income'",
            "execution_match": True,
            "excluded_from_primary_metrics": False,
        },
        {
            "question_id": "fail",
            "gold_sql": "SELECT SUM(Credit) FROM master_txn_table WHERE Account = 'Sales Income'",
            "generated_sql": "SELECT SUM(Debit) FROM master_txn_table",
            "execution_match": True,
            "excluded_from_primary_metrics": False,
        },
        {
            "question_id": "not_testable",
            "gold_sql": "SELECT Employee_name FROM employees",
            "generated_sql": "SELECT Employee_name FROM employees",
            "execution_match": True,
            "excluded_from_primary_metrics": False,
        },
    ]
    metrics, outputs = evaluate_rows(rows, set_label="before")
    assert len(outputs) == 3
    assert metrics["original_execution_accuracy"] == 1.0
    assert metrics["adversarial_tested_rows"] == 2
    assert metrics["adversarial_pass_rows"] == 1
    assert metrics["accounting_adversarial_test_suite_accuracy"] == 0.5
    assert metrics["adversarial_testability_rate"] == 2 / 3
    assert metrics["original_ex_pass_adversarial_fail_rate"] == 1 / 3
