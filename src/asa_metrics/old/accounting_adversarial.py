"""Accounting-adversarial execution suite for BookSQL SQL outputs.

The suite builds a small BookSQL-faithful SQLite fixture per row, seeded only
from gold SQL literals plus deterministic accounting rows. It then executes
gold and generated SQL against template-specific stress data to catch
accounting semantic mismatches that can pass on the original database.
"""

from __future__ import annotations

import re
import sqlite3
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import sqlglot
from sqlglot import exp

from src.eval.evaluate_outputs import (
    compare_results,
    execute_sql,
    has_order_by,
    result_preview,
)


ALLOWED_TRANSACTION_TYPES = {"invoice", "bill", "deposit"}
PAID_STATUS = "paid"
MISSING_STATUS = "--"
BUSINESS_ID = 1
MAX_PREVIEW_ROWS = 25

TEMPLATE_NAMES = [
    "posting_side_debit_credit",
    "ar_ap_scope",
    "income_expense_scope",
    "asset_liability_scope",
    "balance_count_status_proxy",
    "quantity_transaction_count",
    "transaction_type_scope",
    "customer_vendor_scope",
]

ACCOUNT_TYPE_CONCEPTS = {
    "accounts payable (a/p)": "accounts_payable",
    "accounts receivable (a/r)": "accounts_receivable",
    "bank": "asset",
    "credit card": "liability",
    "equity": "equity",
    "expenses": "expense",
    "fixed assets": "asset",
    "income": "income",
    "long term liabilities": "liability",
    "other current assets": "asset",
    "other current liabilities": "liability",
    "other expense": "expense",
    "other income": "income",
}

ACCOUNT_TYPES_BY_CONCEPT = {
    "accounts_receivable": "accounts receivable (a/r)",
    "accounts_payable": "accounts payable (a/p)",
    "asset": "other current assets",
    "liability": "other current liabilities",
    "income": "income",
    "expense": "expenses",
}

BASE_ACCOUNTS = {
    "Accounts Receivable": "accounts receivable (a/r)",
    "Accounts Payable": "accounts payable (a/p)",
    "Sales Income": "income",
    "Consulting Income": "income",
    "Cost of Goods Sold": "expenses",
    "Office Expense": "expenses",
    "Checking": "bank",
    "Savings": "bank",
    "Visa Credit Card": "credit card",
    "Inventory Asset": "other current assets",
    "Equipment": "fixed assets",
    "Payroll Liabilities": "other current liabilities",
    "Bank Loan": "long term liabilities",
    "Owner Equity": "equity",
}

IDENTIFIER_COLUMNS = {
    "Customers": "customer",
    "customer_name": "customer",
    "customer_full_name": "customer",
    "Vendor": "vendor",
    "Vendor_name": "vendor",
    "Product_Service": "product",
    "payment_method": "payment_method",
    "Payment_method": "payment_method",
    "Account": "account",
}

TEXT_LITERAL_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")
COUNT_FUNCTION_RE = re.compile(r"\bcount\s*\(", re.IGNORECASE)


@dataclass
class SqlEvidence:
    sql: str
    columns: set[str] = field(default_factory=set)
    tables: set[str] = field(default_factory=set)
    literals_by_column: dict[str, set[str]] = field(default_factory=dict)
    all_literals: set[str] = field(default_factory=set)
    parse_error: str | None = None

    def literals_for(self, *columns: str) -> set[str]:
        wanted = {column.lower() for column in columns}
        values: set[str] = set()
        for column, literals in self.literals_by_column.items():
            if column.lower() in wanted:
                values.update(literals)
        return values


@dataclass(frozen=True)
class LiteralDebug:
    literal_value: str
    column: str | None
    table: str | None
    template_name: str
    appears_in_schema_value_concepts: bool
    appears_in_fixture_seed_values: bool
    debug_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "literal_value": self.literal_value,
            "column": self.column,
            "table": self.table,
            "template_name": self.template_name,
            "appears_in_schema_value_concepts": self.appears_in_schema_value_concepts,
            "appears_in_fixture_seed_values": self.appears_in_fixture_seed_values,
            "debug_reason": self.debug_reason,
        }


@dataclass
class FixtureBuildResult:
    db_path: Path
    seed_values: set[str]
    account_types_by_name: dict[str, str | None]


def safe_rate(numerator: int | float, denominator: int | float) -> float:
    return numerator / denominator if denominator else 0.0


def extract_sql_evidence(sql: str | None) -> SqlEvidence:
    text = str(sql or "")
    evidence = SqlEvidence(sql=text)
    evidence.all_literals.update(
        match.group(1) if match.group(1) is not None else match.group(2)
        for match in TEXT_LITERAL_RE.finditer(text)
    )
    try:
        tree = sqlglot.parse_one(text, read="sqlite")
    except Exception as exc:
        evidence.parse_error = str(exc)
        return evidence

    for table in tree.find_all(exp.Table):
        if table.name:
            evidence.tables.add(table.name)
    for column in tree.find_all(exp.Column):
        if column.name:
            evidence.columns.add(column.name)

    for predicate in tree.find_all(exp.EQ):
        left, right = predicate.left, predicate.right
        column_expr: exp.Column | None = None
        literal_expr: exp.Literal | None = None
        if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
            column_expr = left
            literal_expr = right
        elif isinstance(right, exp.Column) and isinstance(left, exp.Literal):
            column_expr = right
            literal_expr = left
        if column_expr is not None and literal_expr is not None and literal_expr.is_string:
            evidence.literals_by_column.setdefault(column_expr.name, set()).add(str(literal_expr.this))

    for predicate in tree.find_all(exp.In):
        if not isinstance(predicate.this, exp.Column):
            continue
        values = {
            str(item.this)
            for item in predicate.expressions
            if isinstance(item, exp.Literal) and item.is_string
        }
        if values:
            evidence.literals_by_column.setdefault(predicate.this.name, set()).update(values)

    return evidence


def template_applicability(evidence: SqlEvidence, account_types_by_name: dict[str, str | None]) -> dict[str, str]:
    columns = {column.lower() for column in evidence.columns}
    account_literals = evidence.literals_for("Account", "Account_name")
    account_type_literals = evidence.literals_for("Account_type")
    transaction_type_literals = evidence.literals_for("Transaction_TYPE")
    has_count = COUNT_FUNCTION_RE.search(evidence.sql) is not None
    account_concepts = {
        ACCOUNT_TYPE_CONCEPTS.get((account_types_by_name.get(literal) or "").lower())
        for literal in account_literals
    }
    account_type_concepts = {
        ACCOUNT_TYPE_CONCEPTS.get(literal.lower())
        for literal in account_type_literals
    }
    concepts = {concept for concept in account_concepts | account_type_concepts if concept}

    applicable: dict[str, str] = {}
    if {"credit", "debit"} & columns:
        applicable["posting_side_debit_credit"] = "gold references Debit or Credit"
    if {"ar_paid", "ap_paid"} & columns or concepts & {"accounts_receivable", "accounts_payable"}:
        applicable["ar_ap_scope"] = "gold references AR/AP status or account class"
    if concepts & {"income", "expense"}:
        applicable["income_expense_scope"] = "gold references income/expense account class"
    if concepts & {"asset", "liability", "accounts_receivable", "accounts_payable"}:
        applicable["asset_liability_scope"] = "gold references asset/liability account class"
    if {"open_balance", "balance", "ar_paid", "ap_paid"} & columns or has_count:
        applicable["balance_count_status_proxy"] = "gold references balance/status/count"
    if "quantity" in columns or "transaction_id" in columns or has_count:
        applicable["quantity_transaction_count"] = "gold references quantity or transaction count"
    if transaction_type_literals & ALLOWED_TRANSACTION_TYPES:
        applicable["transaction_type_scope"] = "gold references observed transaction type literal"
    if {"customers", "customer_name", "vendor", "vendor_name"} & columns:
        applicable["customer_vendor_scope"] = "gold references customer/vendor scope"
    return applicable


def schema_value_concepts() -> set[str]:
    return set(ALLOWED_TRANSACTION_TYPES) | set(ACCOUNT_TYPE_CONCEPTS)


def make_connection(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    create_schema(conn)
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE chart_of_accounts (
          id INTEGER,
          businessID INTEGER,
          Account_name TEXT,
          Account_type TEXT,
          PRIMARY KEY (id, businessID, Account_name),
          UNIQUE (businessID, Account_name)
        );
        CREATE TABLE customers (
          id INTEGER,
          businessID INTEGER,
          customer_name TEXT,
          customer_full_name TEXT,
          Billing_address TEXT,
          Billing_city TEXT,
          Billing_state TEXT,
          Billing_ZIP_code INTEGER,
          Shipping_address TEXT,
          Shipping_city TEXT,
          Shipping_state TEXT,
          Shipping_ZIP_code INTEGER,
          Balance DOUBLE,
          PRIMARY KEY (id, businessID, customer_name),
          UNIQUE (businessID, customer_name)
        );
        CREATE TABLE vendors (
          id INTEGER,
          businessID INTEGER,
          Vendor_name TEXT,
          Billing_address TEXT,
          Billing_city TEXT,
          Billing_state TEXT,
          Billing_ZIP_code INTEGER,
          Balance DOUBLE,
          PRIMARY KEY (id, businessID, Vendor_name),
          UNIQUE (businessID, Vendor_name)
        );
        CREATE TABLE products (
          id INTEGER,
          businessID TEXT,
          Product_Service TEXT,
          Product_Service_type TEXT,
          PRIMARY KEY (id, businessID, Product_Service)
        );
        CREATE TABLE payment_method (
          id INTEGER,
          businessID TEXT,
          Payment_method TEXT,
          Credit_card TEXT,
          PRIMARY KEY (id, businessID, Payment_method)
        );
        CREATE TABLE employees (
          id INTEGER,
          businessID TEXT,
          Employee_name TEXT,
          Employee_ID TEXT,
          Hire_date DATE,
          Billing_rate DOUBLE,
          Deleted TEXT,
          PRIMARY KEY (id, businessID, Employee_name)
        );
        CREATE TABLE master_txn_table (
          id INTEGER PRIMARY KEY,
          businessID INTEGER,
          Transaction_ID INTEGER,
          Transaction_DATE DATE,
          Transaction_TYPE TEXT,
          Amount DOUBLE,
          CreatedDATE DATE,
          CreatedUSER TEXT,
          Account TEXT,
          AR_paid TEXT,
          AP_paid TEXT,
          Due_DATE DATE,
          Open_balance DOUBLE,
          Customers TEXT,
          Vendor TEXT,
          Product_Service TEXT,
          Quantity INTEGER,
          Rate DOUBLE,
          Credit DOUBLE,
          Debit DOUBLE,
          payment_method TEXT,
          Misc TEXT,
          FOREIGN KEY (businessID, Account) REFERENCES chart_of_accounts(businessID, Account_name),
          FOREIGN KEY (businessID, Customers) REFERENCES customers(businessID, customer_name),
          FOREIGN KEY (businessID, Vendor) REFERENCES vendors(businessID, Vendor_name)
        );
        """
    )


def build_fixture(gold_sql: str, fixture_date: str = "2026-06-23") -> FixtureBuildResult:
    evidence = extract_sql_evidence(gold_sql)
    tmp = tempfile.NamedTemporaryFile(prefix="accounting_adversarial_", suffix=".sqlite", delete=False)
    tmp.close()
    path = Path(tmp.name)
    conn = make_connection(path)
    seed_values: set[str] = set()
    account_types: dict[str, str | None] = dict(BASE_ACCOUNTS)

    seed_support_rows(conn, evidence, seed_values, account_types)
    seed_base_transactions(conn, fixture_date)
    seed_gold_literals(conn, evidence, seed_values, account_types)
    seed_template_stress_rows(conn, fixture_date)
    conn.commit()
    conn.close()
    return FixtureBuildResult(path, seed_values, account_types)


def add_unique_row(conn: sqlite3.Connection, sql: str, values: tuple[Any, ...]) -> None:
    conn.execute(sql, values)


def seed_support_rows(
    conn: sqlite3.Connection,
    evidence: SqlEvidence,
    seed_values: set[str],
    account_types: dict[str, str | None],
) -> None:
    for idx, (account_name, account_type) in enumerate(account_types.items(), start=1):
        conn.execute(
            "INSERT INTO chart_of_accounts VALUES (?, ?, ?, ?)",
            (idx, BUSINESS_ID, account_name, account_type),
        )
        seed_values.update({account_name, account_type})
    for idx, name in enumerate(["Acme Customer", "Bright Customer"], start=1):
        conn.execute(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (idx, BUSINESS_ID, name, name, "1 Main", "NYC", "NY", 10000 + idx, "1 Main", "NYC", "NY", 10000 + idx, 100.0 * idx),
        )
        seed_values.add(name)
    for idx, name in enumerate(["Supply Vendor", "Rent Vendor"], start=1):
        conn.execute(
            "INSERT INTO vendors VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (idx, BUSINESS_ID, name, "9 Market", "NYC", "NY", 20000 + idx, 120.0 * idx),
        )
        seed_values.add(name)
    for idx, name in enumerate(["Widget", "Service Plan"], start=1):
        conn.execute("INSERT INTO products VALUES (?, ?, ?, ?)", (idx, str(BUSINESS_ID), name, "service"))
        seed_values.add(name)
    for idx, name in enumerate(["Cash", "Credit Card"], start=1):
        conn.execute("INSERT INTO payment_method VALUES (?, ?, ?, ?)", (idx, str(BUSINESS_ID), name, "no"))
        seed_values.add(name)
    conn.execute(
        "INSERT INTO employees VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, str(BUSINESS_ID), "Staff One", "E-1", "2024-01-01", None, "no"),
    )
    seed_values.add("Staff One")


def seed_gold_literals(
    conn: sqlite3.Connection,
    evidence: SqlEvidence,
    seed_values: set[str],
    account_types: dict[str, str | None],
) -> None:
    next_ids = {"customer": 100, "vendor": 100, "product": 100, "payment_method": 100, "account": 100}
    for column, literals in evidence.literals_by_column.items():
        kind = IDENTIFIER_COLUMNS.get(column)
        for literal in sorted(literals):
            seed_values.add(literal)
            if kind == "customer":
                next_ids[kind] += 1
                conn.execute(
                    "INSERT OR IGNORE INTO customers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (next_ids[kind], BUSINESS_ID, literal, literal, "seed", "seed", "ST", 1, "seed", "seed", "ST", 1, 77.0),
                )
            elif kind == "vendor":
                next_ids[kind] += 1
                conn.execute(
                    "INSERT OR IGNORE INTO vendors VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (next_ids[kind], BUSINESS_ID, literal, "seed", "seed", "ST", 1, 88.0),
                )
            elif kind == "product":
                next_ids[kind] += 1
                conn.execute("INSERT OR IGNORE INTO products VALUES (?, ?, ?, ?)", (next_ids[kind], str(BUSINESS_ID), literal, "service"))
            elif kind == "payment_method":
                next_ids[kind] += 1
                conn.execute("INSERT OR IGNORE INTO payment_method VALUES (?, ?, ?, ?)", (next_ids[kind], str(BUSINESS_ID), literal, "no"))
            elif kind == "account":
                next_ids[kind] += 1
                account_type = account_types.get(literal)
                account_types[literal] = account_type
                conn.execute(
                    "INSERT OR IGNORE INTO chart_of_accounts VALUES (?, ?, ?, ?)",
                    (next_ids[kind], BUSINESS_ID, literal, account_type),
                )
    for literal in evidence.literals_for("Account_type"):
        if literal.lower() in ACCOUNT_TYPE_CONCEPTS:
            seed_values.add(literal)
            synthetic_name = f"Seed {literal}"
            if synthetic_name not in account_types:
                account_types[synthetic_name] = literal
                conn.execute(
                    "INSERT OR IGNORE INTO chart_of_accounts VALUES (?, ?, ?, ?)",
                    (next_ids["account"] + 1, BUSINESS_ID, synthetic_name, literal),
                )
    for literal in evidence.literals_for("Transaction_TYPE"):
        if literal in ALLOWED_TRANSACTION_TYPES:
            seed_values.add(literal)


def txn_line(
    row_id: int,
    transaction_id: int,
    date: str,
    txn_type: str,
    amount: float,
    account: str,
    *,
    ar_paid: str | None = None,
    ap_paid: str | None = None,
    open_balance: float = 0.0,
    customer: str | None = None,
    vendor: str | None = None,
    product: str | None = None,
    quantity: int | None = None,
    rate: float | None = None,
    credit: float = 0.0,
    debit: float = 0.0,
    payment_method: str | None = "Cash",
    misc: str | None = None,
) -> tuple[Any, ...]:
    return (
        row_id,
        BUSINESS_ID,
        transaction_id,
        date,
        txn_type,
        amount,
        date,
        "fixture",
        account,
        ar_paid,
        ap_paid,
        date,
        open_balance,
        customer,
        vendor,
        product,
        quantity,
        rate,
        credit,
        debit,
        payment_method,
        misc,
    )


def insert_txn_lines(conn: sqlite3.Connection, rows: Iterable[tuple[Any, ...]]) -> None:
    conn.executemany(
        """
        INSERT INTO master_txn_table (
          id, businessID, Transaction_ID, Transaction_DATE, Transaction_TYPE,
          Amount, CreatedDATE, CreatedUSER, Account, AR_paid, AP_paid, Due_DATE,
          Open_balance, Customers, Vendor, Product_Service, Quantity, Rate,
          Credit, Debit, payment_method, Misc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        list(rows),
    )


def seed_base_transactions(conn: sqlite3.Connection, fixture_date: str) -> None:
    insert_txn_lines(
        conn,
        [
            txn_line(1, 1001, fixture_date, "invoice", 300, "Accounts Receivable", ar_paid=PAID_STATUS, open_balance=210, customer="Acme Customer", product="Widget", quantity=3, rate=100, debit=300),
            txn_line(2, 1001, fixture_date, "invoice", 300, "Sales Income", ar_paid=PAID_STATUS, customer="Acme Customer", product="Widget", quantity=3, rate=100, credit=300),
            txn_line(3, 1002, fixture_date, "bill", 140, "Office Expense", ap_paid=PAID_STATUS, vendor="Supply Vendor", product="Service Plan", quantity=2, rate=70, debit=140),
            txn_line(4, 1002, fixture_date, "bill", 140, "Accounts Payable", ap_paid=PAID_STATUS, open_balance=60, vendor="Supply Vendor", product="Service Plan", quantity=2, rate=70, credit=140),
            txn_line(5, 1003, fixture_date, "deposit", 90, "Checking", open_balance=90, debit=90),
            txn_line(6, 1003, fixture_date, "deposit", 90, "Owner Equity", credit=90),
        ],
    )


def seed_template_stress_rows(conn: sqlite3.Connection, fixture_date: str) -> None:
    insert_txn_lines(
        conn,
        [
            txn_line(101, 2001, fixture_date, "invoice", 410, "Accounts Receivable", ar_paid=PAID_STATUS, open_balance=410, customer="Bright Customer", product="Widget", quantity=5, rate=82, debit=410, misc="posting_ar"),
            txn_line(102, 2001, fixture_date, "invoice", 410, "Sales Income", ar_paid=PAID_STATUS, customer="Bright Customer", product="Widget", quantity=5, rate=82, credit=410, misc="posting_income"),
            txn_line(103, 2002, fixture_date, "bill", 175, "Cost of Goods Sold", ap_paid=PAID_STATUS, vendor="Rent Vendor", product="Service Plan", quantity=7, rate=25, debit=175, misc="expense_side"),
            txn_line(104, 2002, fixture_date, "bill", 175, "Accounts Payable", ap_paid=PAID_STATUS, open_balance=175, vendor="Rent Vendor", product="Service Plan", quantity=7, rate=25, credit=175, misc="ap_side"),
            txn_line(105, 2003, fixture_date, "deposit", 65, "Checking", open_balance=65, debit=65, misc="deposit_bank"),
            txn_line(106, 2003, fixture_date, "deposit", 65, "Payroll Liabilities", credit=65, misc="deposit_liability"),
            txn_line(107, 2004, fixture_date, "invoice", 33, "Inventory Asset", ar_paid=MISSING_STATUS, open_balance=33, customer="Acme Customer", product="Widget", quantity=11, rate=3, debit=33, misc="status_missing_not_unpaid"),
            txn_line(108, 2004, fixture_date, "invoice", 33, "Consulting Income", ar_paid=MISSING_STATUS, customer="Acme Customer", product="Widget", quantity=11, rate=3, credit=33, misc="status_missing_not_unpaid"),
        ],
    )


def assert_fixture_integrity(conn: sqlite3.Connection) -> None:
    txn_types = {row[0] for row in conn.execute("SELECT DISTINCT Transaction_TYPE FROM master_txn_table")}
    if not txn_types <= ALLOWED_TRANSACTION_TYPES:
        raise AssertionError(f"Unexpected transaction types: {sorted(txn_types - ALLOWED_TRANSACTION_TYPES)}")
    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        raise AssertionError(f"Fixture foreign key errors: {fk_errors}")
    imbalanced = conn.execute(
        """
        SELECT Transaction_ID, SUM(Debit), SUM(Credit)
        FROM master_txn_table
        GROUP BY Transaction_ID
        HAVING ROUND(COALESCE(SUM(Debit), 0), 6) != ROUND(COALESCE(SUM(Credit), 0), 6)
        """
    ).fetchall()
    if imbalanced:
        raise AssertionError(f"Imbalanced fixture transaction groups: {imbalanced}")


def preflight_template(conn: sqlite3.Connection, template_name: str) -> tuple[bool, dict[str, Any]]:
    probes: dict[str, Any]
    if template_name == "posting_side_debit_credit":
        debit, credit = conn.execute("SELECT SUM(Debit), SUM(Credit) FROM master_txn_table WHERE Misc = 'posting_ar'").fetchone()
        probes = {"sum_debit": debit, "sum_credit": credit}
        return debit != credit, probes
    if template_name == "ar_ap_scope":
        ar, ap = conn.execute(
            "SELECT SUM(CASE WHEN Account='Accounts Receivable' THEN Open_balance ELSE 0 END), "
            "SUM(CASE WHEN Account='Accounts Payable' THEN Open_balance ELSE 0 END) FROM master_txn_table"
        ).fetchone()
        probes = {"ar_probe": ar, "ap_probe": ap}
        return ar != ap, probes
    if template_name == "income_expense_scope":
        income, expense = conn.execute(
            "SELECT SUM(CASE WHEN Account IN ('Sales Income','Consulting Income') THEN Credit ELSE 0 END), "
            "SUM(CASE WHEN Account IN ('Office Expense','Cost of Goods Sold') THEN Debit ELSE 0 END) FROM master_txn_table"
        ).fetchone()
        probes = {"income_total": income, "expense_total": expense}
        return income != expense, probes
    if template_name == "asset_liability_scope":
        asset, liability = conn.execute(
            "SELECT SUM(CASE WHEN Account IN ('Checking','Inventory Asset','Accounts Receivable') THEN Open_balance ELSE 0 END), "
            "SUM(CASE WHEN Account IN ('Accounts Payable','Payroll Liabilities') THEN Open_balance ELSE 0 END) FROM master_txn_table"
        ).fetchone()
        probes = {"asset_probe": asset, "liability_probe": liability}
        return asset != liability, probes
    if template_name == "balance_count_status_proxy":
        balance, count_rows, paid = conn.execute(
            "SELECT SUM(Open_balance), COUNT(*), SUM(CASE WHEN AR_paid='paid' OR AP_paid='paid' THEN 1 ELSE 0 END) FROM master_txn_table"
        ).fetchone()
        probes = {"balance_total": balance, "row_count": count_rows, "paid_status_count": paid}
        return balance not in {count_rows, paid}, probes
    if template_name == "quantity_transaction_count":
        quantity, count_rows, count_txn = conn.execute(
            "SELECT SUM(Quantity), COUNT(*), COUNT(DISTINCT Transaction_ID) FROM master_txn_table"
        ).fetchone()
        probes = {"sum_quantity": quantity, "row_count": count_rows, "distinct_transaction_count": count_txn}
        return quantity != count_rows and quantity != count_txn, probes
    if template_name == "transaction_type_scope":
        rows = conn.execute(
            "SELECT Transaction_TYPE, COUNT(*), SUM(Amount) FROM master_txn_table GROUP BY Transaction_TYPE ORDER BY Transaction_TYPE"
        ).fetchall()
        probes = {row[0]: {"count": row[1], "amount": row[2]} for row in rows}
        observed = set(probes)
        amounts = {value["amount"] for value in probes.values()}
        return ALLOWED_TRANSACTION_TYPES <= observed and len(amounts) == len(probes), probes
    if template_name == "customer_vendor_scope":
        customer, vendor = conn.execute(
            "SELECT SUM(CASE WHEN Customers IS NOT NULL THEN Amount ELSE 0 END), "
            "SUM(CASE WHEN Vendor IS NOT NULL THEN Amount ELSE 0 END) FROM master_txn_table"
        ).fetchone()
        probes = {"customer_probe": customer, "vendor_probe": vendor}
        return customer > 0 and vendor > 0 and customer != vendor, probes
    raise ValueError(f"Unknown template: {template_name}")


def generated_literal_debug(
    generated: SqlEvidence,
    fixture: FixtureBuildResult,
    template_name: str,
    reason: str,
) -> list[dict[str, Any]]:
    records: list[LiteralDebug] = []
    concepts = schema_value_concepts()
    for column, literals in generated.literals_by_column.items():
        for literal in sorted(literals):
            if literal in fixture.seed_values:
                continue
            records.append(
                LiteralDebug(
                    literal_value=literal,
                    column=column,
                    table=None,
                    template_name=template_name,
                    appears_in_schema_value_concepts=literal in concepts,
                    appears_in_fixture_seed_values=False,
                    debug_reason=reason,
                )
            )
    return [record.to_dict() for record in records]


def evaluate_sql_pair_on_fixture(
    *,
    gold_sql: str,
    generated_sql: str,
    fixture_date: str = "2026-06-23",
    max_progress_steps: int = 2_000_000,
    progress_check_interval: int = 1000,
) -> dict[str, Any]:
    gold_evidence = extract_sql_evidence(gold_sql)
    fixture = build_fixture(gold_sql, fixture_date)
    generated_evidence = extract_sql_evidence(generated_sql)
    applicable = template_applicability(gold_evidence, fixture.account_types_by_name)

    conn = sqlite3.connect(fixture.db_path)
    conn.execute("PRAGMA query_only = ON")
    try:
        gold_rows, gold_error = execute_sql(conn, gold_sql, max_progress_steps, progress_check_interval)
        row_result: dict[str, Any] = {
            "applicable_templates": sorted(applicable),
            "tested_templates": [],
            "failed_templates": [],
            "gold_error_excluded_templates": [],
            "gold_empty_excluded_templates": [],
            "preflight_non_discriminative_templates": [],
            "template_errors": {},
            "failed_template_previews": {},
            "generated_only_literal_debug": [],
            "adversarial_pass": None,
        }
        if gold_error is not None:
            row_result["gold_error_excluded_templates"] = sorted(applicable)
            row_result["adversarial_pass"] = None
            row_result["template_errors"]["gold"] = gold_error
            return row_result
        if not gold_rows:
            row_result["gold_empty_excluded_templates"] = sorted(applicable)
            row_result["adversarial_pass"] = None
            return row_result

        any_failure = False
        for template_name in sorted(applicable):
            discriminative, probes = preflight_template(conn, template_name)
            if not discriminative:
                row_result["preflight_non_discriminative_templates"].append(template_name)
                row_result["template_errors"][template_name] = {"preflight": probes}
                continue
            row_result["tested_templates"].append(template_name)
            generated_rows, generated_error = execute_sql(
                conn,
                generated_sql,
                max_progress_steps,
                progress_check_interval,
            )
            debug_reason = "generated_unseeded_literal_failure" if generated_error else "generated_literal_not_in_fixture"
            row_result["generated_only_literal_debug"].extend(
                generated_literal_debug(generated_evidence, fixture, template_name, debug_reason)
            )
            generated_empty = generated_error is None and not generated_rows
            matches = False
            if generated_error is None and generated_rows is not None:
                matches = compare_results(gold_rows, generated_rows, order_sensitive=has_order_by(gold_sql))
            if generated_error is not None or generated_empty or not matches:
                any_failure = True
                row_result["failed_templates"].append(template_name)
                if generated_error is not None:
                    row_result["template_errors"][template_name] = generated_error
                row_result["failed_template_previews"][template_name] = {
                    "gold": result_preview(gold_rows, MAX_PREVIEW_ROWS),
                    "generated": None if generated_error else result_preview(generated_rows or [], MAX_PREVIEW_ROWS),
                    "preflight": probes,
                }
        row_result["adversarial_pass"] = (not any_failure) if row_result["tested_templates"] else None
        return row_result
    finally:
        conn.close()
        fixture.db_path.unlink(missing_ok=True)


def original_execution_accuracy(rows: list[dict[str, Any]]) -> tuple[int, int, float]:
    metric_rows = [
        row
        for row in rows
        if row.get("excluded_from_primary_metrics") is not True and row.get("execution_match") is not None
    ]
    correct = sum(1 for row in metric_rows if row.get("execution_match") is True)
    return correct, len(metric_rows), safe_rate(correct, len(metric_rows))


def evaluate_rows(
    rows: list[dict[str, Any]],
    *,
    set_label: str,
    fixture_date: str = "2026-06-23",
    max_progress_steps: int = 2_000_000,
    progress_check_interval: int = 1000,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    outputs: list[dict[str, Any]] = []
    template_counts: dict[str, Counter[str]] = {name: Counter() for name in TEMPLATE_NAMES}
    exclusion_counts: Counter[str] = Counter()
    not_testable_reason_counts: Counter[str] = Counter()
    tested_rows = 0
    adversarial_pass_rows = 0
    original_pass_adv_fail = 0

    for idx, row in enumerate(rows):
        gold_sql = row.get("gold_sql") or ""
        generated_sql = row.get("generated_sql") or row.get("pred_sql") or ""
        result = evaluate_sql_pair_on_fixture(
            gold_sql=gold_sql,
            generated_sql=generated_sql,
            fixture_date=fixture_date,
            max_progress_steps=max_progress_steps,
            progress_check_interval=progress_check_interval,
        )
        tested = bool(result["tested_templates"])
        if tested:
            tested_rows += 1
            if result["adversarial_pass"] is True:
                adversarial_pass_rows += 1
            elif row.get("execution_match") is True:
                original_pass_adv_fail += 1
        else:
            if not result["applicable_templates"]:
                not_testable_reason_counts["no_gold_schema_grounded_template_evidence"] += 1
            if result["gold_error_excluded_templates"]:
                not_testable_reason_counts["gold_error"] += 1
            if result["gold_empty_excluded_templates"]:
                not_testable_reason_counts["gold_empty"] += 1
            if result["preflight_non_discriminative_templates"]:
                not_testable_reason_counts["preflight_non_discriminative"] += 1

        for template in result["applicable_templates"]:
            template_counts[template]["applicable"] += 1
        for template in result["tested_templates"]:
            template_counts[template]["tested"] += 1
        for template in result["failed_templates"]:
            template_counts[template]["failure"] += 1
        exclusion_counts["gold_error_excluded_templates"] += len(result["gold_error_excluded_templates"])
        exclusion_counts["gold_empty_excluded_templates"] += len(result["gold_empty_excluded_templates"])
        exclusion_counts["preflight_non_discriminative_templates"] += len(result["preflight_non_discriminative_templates"])

        outputs.append(
            {
                "question_id": row.get("question_id", idx),
                "set": set_label,
                "gold_sql": gold_sql,
                "generated_sql": generated_sql,
                "execution_match": row.get("execution_match"),
                **result,
            }
        )

    orig_correct, orig_total, orig_acc = original_execution_accuracy(rows)
    metrics = {
        "label": set_label,
        "total_rows": len(rows),
        "original_execution_accuracy": orig_acc,
        "original_execution_correct_rows": orig_correct,
        "original_execution_metric_rows": orig_total,
        "accounting_adversarial_test_suite_accuracy": safe_rate(adversarial_pass_rows, tested_rows),
        "adversarial_pass_rows": adversarial_pass_rows,
        "adversarial_tested_rows": tested_rows,
        "original_ex_pass_adversarial_fail_rate": safe_rate(original_pass_adv_fail, orig_correct),
        "adversarial_testability_rate": safe_rate(tested_rows, len(rows)),
        "template_counts": {
            name: {
                "applicable": template_counts[name]["applicable"],
                "tested": template_counts[name]["tested"],
                "failure": template_counts[name]["failure"],
            }
            for name in TEMPLATE_NAMES
        },
        "gold_error_excluded_templates": exclusion_counts["gold_error_excluded_templates"],
        "gold_empty_excluded_templates": exclusion_counts["gold_empty_excluded_templates"],
        "preflight_non_discriminative_templates": exclusion_counts["preflight_non_discriminative_templates"],
        "not_testable_reason_counts": dict(not_testable_reason_counts),
    }
    return metrics, outputs
