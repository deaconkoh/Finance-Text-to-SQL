"""Deprecated schema-grounded accounting stress equivalence research code.

Eq_acct_v1 is retained for historical experiments, direct unit coverage, and
debug tooling. It is not imported, invoked, counted, or emitted by the active
ASA evaluator, which now uses the invariant-only EX/Inv definition.

Eq_acct_v1 builds deterministic BookSQL fixture states from gold SQL and schema
annotations only. Mutants are used only to validate that an activated template
suite has accounting-stress coverage; generated SQL is then compared directly
against gold SQL on usable states from validated suites.
"""

from __future__ import annotations

import sqlite3
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import sqlglot
from sqlglot import exp

from src.asa_metrics.old.accounting_adversarial import (
    ACCOUNT_TYPE_CONCEPTS,
    ACCOUNT_TYPES_BY_CONCEPT,
    ALLOWED_TRANSACTION_TYPES,
    BASE_ACCOUNTS,
    BUSINESS_ID,
    MAX_PREVIEW_ROWS,
    TEMPLATE_NAMES,
    FixtureBuildResult,
    assert_fixture_integrity,
    build_fixture,
    insert_txn_lines,
    make_connection,
    seed_gold_literals,
    txn_line,
)
from src.eval.evaluate_outputs import compare_results, execute_sql, has_order_by, result_preview
from src.finverisql.schema_loader import SchemaAnnotationStore, normalise_identifier, normalise_value
from src.finverisql.sql_parser import parse_sql
from src.finverisql.sql_semantic_mapping import AnnotatedColumnUse, build_sql_financial_semantics


POSTING_TEMPLATE = "posting_side_debit_credit"
AR_AP_TEMPLATE = "ar_ap_scope"
INCOME_EXPENSE_TEMPLATE = "income_expense_scope"
ASSET_LIABILITY_TEMPLATE = "asset_liability_scope"
BALANCE_COUNT_TEMPLATE = "balance_count_status_proxy"
QUANTITY_COUNT_TEMPLATE = "quantity_transaction_count"
TRANSACTION_TYPE_TEMPLATE = "transaction_type_scope"
CUSTOMER_VENDOR_TEMPLATE = "customer_vendor_scope"

MUTANT_FAMILIES_BY_TEMPLATE = {
    POSTING_TEMPLATE: ["swap_credit_debit"],
    AR_AP_TEMPLATE: ["swap_ar_ap_status", "swap_ar_ap_concepts"],
    INCOME_EXPENSE_TEMPLATE: ["swap_income_expense_values", "remove_account_type_predicate"],
    ASSET_LIABILITY_TEMPLATE: ["swap_asset_liability_values", "remove_asset_liability_predicate"],
    BALANCE_COUNT_TEMPLATE: ["replace_balance_with_count", "replace_count_with_balance", "replace_count_with_status", "replace_open_balance_with_amount"],
    QUANTITY_COUNT_TEMPLATE: ["replace_quantity_with_count", "replace_quantity_with_distinct_transactions", "count_rows_to_distinct_transactions", "count_distinct_to_rows"],
    TRANSACTION_TYPE_TEMPLATE: ["swap_transaction_type_literals", "remove_transaction_type_predicate"],
    CUSTOMER_VENDOR_TEMPLATE: ["swap_customer_vendor_columns", "remove_party_predicate"],
}

STATE_NAMES_BY_TEMPLATE = {
    POSTING_TEMPLATE: [
        "filtered_debit_credit_difference",
        "global_balance_filtered_difference",
        "posting_filter_distractors",
    ],
    AR_AP_TEMPLATE: [
        "invoice_ar_bill_ap_open_balance_difference",
        "ar_ap_paid_status_difference",
        "party_status_swap_distractors",
    ],
    INCOME_EXPENSE_TEMPLATE: [
        "income_credit_expense_debit_difference",
        "same_context_income_expense",
        "asset_liability_filter_distractors",
    ],
    ASSET_LIABILITY_TEMPLATE: [
        "asset_liability_balance_difference",
        "ar_ap_asset_liability_subtypes",
        "bank_credit_card_distractors",
    ],
    BALANCE_COUNT_TEMPLATE: [
        "open_balance_differs_from_row_count",
        "balance_differs_from_paid_status_count",
        "missing_status_marker_rows",
    ],
    QUANTITY_COUNT_TEMPLATE: [
        "quantity_differs_from_row_count",
        "quantity_differs_from_distinct_transaction_count",
        "multi_line_transaction_grain",
    ],
    TRANSACTION_TYPE_TEMPLATE: [
        "distinct_transaction_type_counts_and_sums",
        "same_account_multiple_transaction_types",
        "invoice_customer_bill_vendor_swaps",
    ],
    CUSTOMER_VENDOR_TEMPLATE: [
        "customer_vendor_amount_difference",
        "separate_customer_vendor_literal_domains",
        "party_balance_transaction_amount_difference",
    ],
}


@dataclass(frozen=True)
class DatePredicateEvidence:
    column: str
    operator: str | None
    values: tuple[Any, ...]
    expression: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "operator": self.operator,
            "values": list(self.values),
            "expression": self.expression,
        }


@dataclass
class EqAcctEvidence:
    parse_error: str | None
    unsupported_lineage: bool
    tables: list[str]
    selected_columns: list[AnnotatedColumnUse]
    aggregate_functions: list[str]
    filter_columns: list[AnnotatedColumnUse]
    filter_value_concepts: list[str]
    account_type_concepts: set[str]
    transaction_type_concepts: set[str]
    entity_scopes: set[str]
    measure_roles: set[str]
    gold_literals_by_column: dict[str, set[str]]
    date_predicates: list[DatePredicateEvidence]
    raw_columns: set[str] = field(default_factory=set)
    count_used: bool = False


@dataclass(frozen=True)
class SupportRequirement:
    kind: str
    values: tuple[str, ...] = ()
    status: str = "not_required"
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "values": list(self.values),
            "status": self.status,
            "reason": self.reason,
        }


@dataclass
class GoldSupportRequirements:
    selected_measures: set[str] = field(default_factory=set)
    account_type_literals: set[str] = field(default_factory=set)
    account_type_concepts: set[str] = field(default_factory=set)
    account_literals: set[str] = field(default_factory=set)
    account_substrings: set[str] = field(default_factory=set)
    transaction_types: set[str] = field(default_factory=set)
    unsupported_transaction_types: set[str] = field(default_factory=set)
    customers: set[str] = field(default_factory=set)
    vendors: set[str] = field(default_factory=set)
    products: set[str] = field(default_factory=set)
    payment_methods: set[str] = field(default_factory=set)
    settlement_statuses: dict[str, set[str]] = field(default_factory=dict)
    fixture_date: str | None = None
    date_lower: str | None = None
    date_upper: str | None = None
    count_used: bool = False
    requirements: list[SupportRequirement] = field(default_factory=list)

    @property
    def unsupported(self) -> list[SupportRequirement]:
        return [item for item in self.requirements if item.status == "unsupported"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_measures": sorted(self.selected_measures),
            "account_type_literals": sorted(self.account_type_literals),
            "account_type_concepts": sorted(self.account_type_concepts),
            "account_literals": sorted(self.account_literals),
            "account_substrings": sorted(self.account_substrings),
            "transaction_types": sorted(self.transaction_types),
            "unsupported_transaction_types": sorted(self.unsupported_transaction_types),
            "customers": sorted(self.customers),
            "vendors": sorted(self.vendors),
            "products": sorted(self.products),
            "payment_methods": sorted(self.payment_methods),
            "settlement_statuses": {
                column: sorted(values)
                for column, values in sorted(self.settlement_statuses.items())
            },
            "fixture_date": self.fixture_date,
            "date_lower": self.date_lower,
            "date_upper": self.date_upper,
            "count_used": self.count_used,
            "requirements": [item.to_dict() for item in self.requirements],
        }


@dataclass
class FixtureState:
    template: str
    state_name: str
    purpose: str
    db_path: Path
    seed_values: set[str]
    required_literals: set[str]
    support_requirements: GoldSupportRequirements | None = None
    support_probe_sql: str | None = None
    support_rows_inserted: int = 0
    preserve_double_entry: bool = True


def _empty_result() -> dict[str, Any]:
    return {
        "eq_acct_version": "v1",
        "eq_acct_result": None,
        "adversarial_pass": None,
        "applicable_templates": [],
        "validated_templates": [],
        "tested_templates": [],
        "failed_templates": [],
        "usable_fixture_state_count": 0,
        "invalid_fixture_state_count": 0,
        "tested_fixture_state_count": 0,
        "not_testable_reason_counts": {},
        "template_results": {},
        "failed_states": [],
        "state_validation_records": [],
        "gold_result_preview": None,
        "generated_result_preview": None,
        "mutant_result_previews": {},
        "mutant_validation_summary": {},
    }


def _compat_result(value: int | None) -> bool | None:
    if value == 1:
        return True
    if value == 0:
        return False
    return None


def _debug_evidence_dict(evidence: EqAcctEvidence) -> dict[str, Any]:
    return {
        "parse_error": evidence.parse_error,
        "unsupported_lineage": evidence.unsupported_lineage,
        "tables": evidence.tables,
        "selected_columns": [item.to_dict() for item in evidence.selected_columns],
        "aggregate_functions": evidence.aggregate_functions,
        "filter_columns": [item.to_dict() for item in evidence.filter_columns],
        "filter_value_concepts": evidence.filter_value_concepts,
        "account_type_concepts": sorted(evidence.account_type_concepts),
        "transaction_type_concepts": sorted(evidence.transaction_type_concepts),
        "entity_scopes": sorted(evidence.entity_scopes),
        "measure_roles": sorted(evidence.measure_roles),
        "gold_literals_by_column": {
            column: sorted(values)
            for column, values in sorted(evidence.gold_literals_by_column.items())
        },
        "date_predicates": [item.to_dict() for item in evidence.date_predicates],
        "raw_columns": sorted(evidence.raw_columns),
        "count_used": evidence.count_used,
    }


def _debug_state_dict(state: FixtureState) -> dict[str, Any]:
    return {
        "template": state.template,
        "state_name": state.state_name,
        "purpose": state.purpose,
        "required_literals": sorted(state.required_literals),
        "seeded_required_literals": sorted(state.required_literals & state.seed_values),
        "missing_required_literals": sorted(state.required_literals - state.seed_values),
        "support_requirements": (
            state.support_requirements.to_dict() if state.support_requirements else None
        ),
        "support_probe_sql": state.support_probe_sql,
        "support_rows_inserted": state.support_rows_inserted,
        "preserve_double_entry": state.preserve_double_entry,
    }


def _unique_sorted(values: Iterable[Any]) -> list[str]:
    return sorted({str(value) for value in values if value not in (None, "", [], {}, "none")})


def _column_key(column: str | None) -> str:
    return normalise_identifier(column)


def _column_matches(column: str | None, *wanted: str) -> bool:
    return _column_key(column) in {_column_key(item) for item in wanted}


def _gold_literals_by_column(sql: str) -> dict[str, set[str]]:
    parsed = parse_sql(sql)
    literals: dict[str, set[str]] = {}
    for filter_ref in parsed.filters:
        for col_ref in filter_ref.columns:
            if not col_ref.column:
                continue
            values = {
                str(value).strip("'").strip('"')
                for value in filter_ref.values
                if isinstance(value, str) and value.strip("'").strip('"')
            }
            if values:
                literals.setdefault(col_ref.column, set()).update(values)
    return literals


def _literal_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]
    text = text.strip()
    return text or None


def _literal_values(values: Iterable[Any]) -> set[str]:
    return {
        text
        for value in values
        if (text := _literal_text(value))
    }


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _first_sorted(values: Iterable[str], default: str | None = None) -> str | None:
    ordered = sorted({value for value in values if value})
    return ordered[0] if ordered else default


def _extract_instr_account_literals(gold_sql: str) -> set[str]:
    try:
        tree = sqlglot.parse_one(gold_sql, read="sqlite")
    except Exception:
        return set()

    literals: set[str] = set()
    for node in tree.find_all(exp.StrPosition):
        source = node.this
        if not isinstance(source, exp.Column) or not _column_matches(source.name, "Account", "Account_name"):
            continue
        substr = node.args.get("substr")
        value: str | None = None
        if isinstance(substr, exp.Literal) and substr.is_string:
            value = str(substr.this)
        elif isinstance(substr, exp.Column):
            identifier = substr.this
            if isinstance(identifier, exp.Identifier) and identifier.args.get("quoted"):
                value = str(identifier.this)
        if value:
            literals.add(value)
    return literals


def _account_type_concept_for_literal(literal: str) -> str | None:
    normalized = literal.lower()
    if normalized == "expense":
        normalized = "expenses"
    return ACCOUNT_TYPE_CONCEPTS.get(normalized)


def _concept_is_ar_ap(concept: str) -> bool:
    return concept in {"accounts_receivable", "accounts_payable"}


def _concept_is_asset_or_liability(concept: str) -> bool:
    return concept in {
        "asset",
        "liability",
        "current_asset",
        "current_liability",
        "fixed_asset",
        "long_term_liability",
        "credit_card_payable",
        "cash_or_bank",
        "accounts_receivable",
        "accounts_payable",
    }


def _annotation_attrs(column_use: AnnotatedColumnUse, key: str) -> list[str]:
    return _unique_sorted(annotation.get(key) for annotation in column_use.annotations)


DATE_EXPR_RE = re.compile(r"""^[A-Za-z0-9_(),"'\-+\s]+$""")


def _resolve_sqlite_date_expression(value: Any, fixture_date: str) -> str | None:
    text = _literal_text(value)
    if not text:
        return None
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    if not DATE_EXPR_RE.match(text) or ";" in text:
        return None
    expr = re.sub(r"\bCURRENT_DATE\b", _sql_literal(fixture_date), text, flags=re.IGNORECASE)
    if not re.search(r"\bDATE\s*\(", expr, flags=re.IGNORECASE):
        return None
    conn = sqlite3.connect(":memory:")
    try:
        row = conn.execute(f"SELECT {expr}").fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    resolved = str(row[0]) if row and row[0] is not None else ""
    if len(resolved) >= 10 and resolved[4:5] == "-" and resolved[7:8] == "-":
        return resolved[:10]
    return None


def _date_inside_bounds(candidate: str, lower: str | None, upper: str | None) -> bool:
    if lower and candidate < lower:
        return False
    if upper and candidate > upper:
        return False
    return True


def _date_for_bounds(fixture_date: str, lower: str | None, upper: str | None) -> str:
    if _date_inside_bounds(fixture_date, lower, upper):
        return fixture_date
    if lower and (upper is None or lower <= upper):
        return lower
    if upper:
        return upper
    return fixture_date


def build_gold_support_requirements(
    gold_sql: str,
    evidence: EqAcctEvidence,
    fixture_date: str,
) -> GoldSupportRequirements:
    """Extract gold-only support constraints used to seed fixture rows."""
    support = GoldSupportRequirements()
    support.count_used = evidence.count_used
    for column in evidence.selected_columns:
        key = _column_key(column.column)
        if key:
            support.selected_measures.add(key)
    support.selected_measures.update(
        _column_key(column.column)
        for column in evidence.selected_columns
        if column.column
    )

    for column, literals in evidence.gold_literals_by_column.items():
        key = _column_key(column)
        clean_literals = _literal_values(literals)
        if key in {"account", "account_name"}:
            support.account_literals.update(clean_literals)
        elif key == "account_type":
            for literal in clean_literals:
                concept = _account_type_concept_for_literal(literal)
                if concept:
                    support.account_type_literals.add(literal)
                    support.account_type_concepts.add(concept)
                else:
                    support.requirements.append(
                        SupportRequirement("account_type", (literal,), "unsupported", "unknown_account_type_literal")
                    )
        elif key == "transaction_type":
            supported = {literal for literal in clean_literals if literal in ALLOWED_TRANSACTION_TYPES}
            unsupported = clean_literals - supported
            support.transaction_types.update(supported)
            support.unsupported_transaction_types.update(unsupported)
            for literal in unsupported:
                support.requirements.append(
                    SupportRequirement("transaction_type", (literal,), "unsupported", "unknown_transaction_type_literal")
                )
        elif key in {"customers", "customer_name", "customer_full_name"}:
            support.customers.update(clean_literals)
        elif key in {"vendor", "vendor_name"}:
            support.vendors.update(clean_literals)
        elif key == "product_service":
            support.products.update(clean_literals)
        elif key == "payment_method":
            support.payment_methods.update(clean_literals)
        elif key in {"ar_paid", "ap_paid"}:
            support.settlement_statuses.setdefault(key, set()).update(clean_literals)

    support.account_substrings.update(_extract_instr_account_literals(gold_sql))

    for concept in sorted(evidence.account_type_concepts):
        if concept in ACCOUNT_TYPES_BY_CONCEPT:
            support.account_type_concepts.add(concept)

    for predicate in evidence.date_predicates:
        if not _column_matches(predicate.column, "Transaction_DATE"):
            support.requirements.append(
                SupportRequirement("date", (predicate.expression,), "unsupported", "date_predicate_not_transaction_date")
            )
            continue
        operator = str(predicate.operator or "").upper()
        if operator == "BETWEEN" and len(predicate.values) == 2:
            lower = _resolve_sqlite_date_expression(predicate.values[0], fixture_date)
            upper = _resolve_sqlite_date_expression(predicate.values[1], fixture_date)
            if not lower or not upper:
                support.requirements.append(
                    SupportRequirement("date", (predicate.expression,), "unsupported", "unsupported_date_boundary")
                )
                continue
            support.date_lower = lower
            support.date_upper = upper
            support.fixture_date = _date_for_bounds(fixture_date, lower, upper)
        elif operator in {"=", ">=", ">", "<=", "<"} and predicate.values:
            boundary = _resolve_sqlite_date_expression(predicate.values[0], fixture_date)
            if not boundary:
                support.requirements.append(
                    SupportRequirement("date", (predicate.expression,), "unsupported", "unsupported_date_boundary")
                )
                continue
            if operator == "=":
                support.fixture_date = boundary
                support.date_lower = boundary
                support.date_upper = boundary
            elif operator in {">=", ">"}:
                support.date_lower = boundary
                support.fixture_date = _date_for_bounds(fixture_date, boundary, support.date_upper)
            else:
                support.date_upper = boundary
                support.fixture_date = _date_for_bounds(fixture_date, support.date_lower, boundary)
        else:
            support.requirements.append(
                SupportRequirement("date", (predicate.expression,), "unsupported", "unsupported_date_predicate")
            )

    if support.fixture_date is None:
        support.fixture_date = _date_for_bounds(fixture_date, support.date_lower, support.date_upper)

    requirement_specs = [
        ("selected_measure", support.selected_measures),
        ("account_type", support.account_type_literals or support.account_type_concepts),
        ("account_identifier", support.account_literals),
        ("account_instr_identifier", support.account_substrings),
        ("transaction_type", support.transaction_types),
        ("customer", support.customers),
        ("vendor", support.vendors),
        ("product_service", support.products),
        ("payment_method", support.payment_methods),
    ]
    for kind, values in requirement_specs:
        status = "supported" if values else "not_required"
        support.requirements.append(SupportRequirement(kind, tuple(sorted(values)), status))
    if support.date_lower or support.date_upper:
        support.requirements.append(
            SupportRequirement(
                "date",
                tuple(value for value in (support.date_lower, support.date_upper) if value),
                "supported",
            )
        )
    else:
        support.requirements.append(SupportRequirement("date", (), "not_required"))
    for column, values in sorted(support.settlement_statuses.items()):
        support.requirements.append(SupportRequirement(f"settlement_status:{column}", tuple(sorted(values)), "supported"))

    return support


def build_eq_acct_evidence(gold_sql: str, schema_annotations: dict[str, Any]) -> EqAcctEvidence:
    """Extract schema-grounded accounting evidence from gold SQL only."""
    parsed = parse_sql(gold_sql)
    schema_store = SchemaAnnotationStore(schema_annotations or {})
    semantics = build_sql_financial_semantics(parsed, schema_store)

    selected_columns = list(semantics.measure_usage.selected_columns)
    selected_columns.extend(semantics.measure_usage.aggregated_columns)
    filter_columns = [
        column
        for condition in semantics.logic.filter_conditions
        for column in condition.columns
    ]
    group_columns = list(semantics.logic.group_by_columns)
    all_uses = selected_columns + filter_columns + group_columns

    account_type_concepts = set(semantics.object_scope.account_type_concepts)
    transaction_type_concepts = set(semantics.object_scope.transaction_type_concepts)
    entity_scopes: set[str] = set(semantics.object_scope.entity_scopes_detected)
    measure_roles: set[str] = set(semantics.measure_usage.financial_roles)
    filter_value_concepts: list[str] = []

    for condition in semantics.logic.filter_conditions:
        filter_value_concepts.extend(condition.concepts)
        for column in condition.columns:
            entity_scopes.update(_annotation_attrs(column, "entity_scope"))
            for annotation in column.annotations:
                if annotation.get("semantic_role") == "settlement_status_flag":
                    account_type_concepts.add(str(annotation.get("domain_object") or ""))

    for column in all_uses:
        entity_scopes.update(_annotation_attrs(column, "entity_scope"))
        measure_roles.update(_annotation_attrs(column, "financial_role"))
        for annotation in column.annotations:
            role = annotation.get("semantic_role")
            if role == "account_type_classifier":
                for value in column.value_semantics:
                    if value.get("concept"):
                        account_type_concepts.add(str(value["concept"]))
            elif role == "transaction_type_classifier":
                for value in column.value_semantics:
                    if value.get("concept"):
                        transaction_type_concepts.add(str(value["concept"]))
            elif role == "settlement_status_flag":
                domain = str(annotation.get("domain_object") or "")
                if "receivable" in domain:
                    account_type_concepts.add("accounts_receivable")
                if "payable" in domain:
                    account_type_concepts.add("accounts_payable")

    raw_columns = {
        col.column
        for col in parsed.selected_columns + parsed.group_by
        if col.column
    }
    for agg in parsed.aggregations:
        for col in agg.columns:
            if col.column:
                raw_columns.add(col.column)
    for filter_ref in parsed.filters:
        for col in filter_ref.columns:
            if col.column:
                raw_columns.add(col.column)

    date_predicates = [
        DatePredicateEvidence(
            column=condition.columns[0].column or "unknown",
            operator=condition.operator,
            values=tuple(condition.values),
            expression=condition.expression,
        )
        for condition in semantics.logic.date_conditions
        if condition.columns
    ]

    return EqAcctEvidence(
        parse_error=parsed.parse_error or semantics.parse_error,
        unsupported_lineage=bool(parsed.unsupported_lineage or semantics.unsupported_lineage),
        tables=list(parsed.tables),
        selected_columns=selected_columns,
        aggregate_functions=list(semantics.measure_usage.aggregation_functions),
        filter_columns=filter_columns,
        filter_value_concepts=_unique_sorted(filter_value_concepts),
        account_type_concepts={item for item in account_type_concepts if item},
        transaction_type_concepts={item for item in transaction_type_concepts if item},
        entity_scopes={item for item in entity_scopes if item},
        measure_roles={item for item in measure_roles if item},
        gold_literals_by_column=_gold_literals_by_column(gold_sql),
        date_predicates=date_predicates,
        raw_columns={_column_key(column) for column in raw_columns},
        count_used=any(agg.func.lower() == "count" for agg in parsed.aggregations),
    )


def activate_templates(evidence: EqAcctEvidence) -> dict[str, str]:
    """Return template activation reasons from gold evidence only."""
    if evidence.parse_error or evidence.unsupported_lineage:
        return {}

    columns = evidence.raw_columns
    account_concepts = evidence.account_type_concepts
    transaction_concepts = evidence.transaction_type_concepts
    scopes = evidence.entity_scopes
    applicable: dict[str, str] = {}

    if {"credit", "debit"} & columns:
        applicable[POSTING_TEMPLATE] = "gold references annotated Debit or Credit"
    if {"ar_paid", "ap_paid"} & columns or any(_concept_is_ar_ap(c) for c in account_concepts):
        applicable[AR_AP_TEMPLATE] = "gold references AR/AP status or account class"
    if {"income", "expense"} & account_concepts:
        applicable[INCOME_EXPENSE_TEMPLATE] = "gold references income/expense account class"
    if any(_concept_is_asset_or_liability(c) for c in account_concepts):
        applicable[ASSET_LIABILITY_TEMPLATE] = "gold references asset/liability account class"
    if {"open_balance", "balance", "ar_paid", "ap_paid"} & columns or evidence.count_used:
        applicable[BALANCE_COUNT_TEMPLATE] = "gold references stock balance, status, or count"
    if "quantity" in columns or "transaction_id" in columns or evidence.count_used:
        applicable[QUANTITY_COUNT_TEMPLATE] = "gold references quantity or transaction count"
    if transaction_concepts & set(ALLOWED_TRANSACTION_TYPES):
        applicable[TRANSACTION_TYPE_TEMPLATE] = "gold references transaction type value concept"
    if {"customer", "vendor"} & scopes or {"customers", "customer_name", "customer_full_name", "vendor", "vendor_name"} & columns:
        applicable[CUSTOMER_VENDOR_TEMPLATE] = "gold references customer/vendor scope"
    return applicable


def _insert_template_rows(conn: sqlite3.Connection, fixture_date: str) -> None:
    """Add v1 stress rows while preserving double-entry balance."""
    insert_txn_lines(
        conn,
        [
            txn_line(1001, 9101, fixture_date, "invoice", 520, "Accounts Receivable", ar_paid="paid", open_balance=520, customer="Acme Customer", product="Widget", quantity=13, rate=40, debit=520, misc="v1_ar_invoice"),
            txn_line(1002, 9101, fixture_date, "invoice", 520, "Sales Income", ar_paid="paid", customer="Acme Customer", product="Widget", quantity=13, rate=40, credit=520, misc="v1_income_invoice"),
            txn_line(1003, 9102, fixture_date, "bill", 190, "Office Expense", ap_paid="paid", vendor="Supply Vendor", product="Service Plan", quantity=2, rate=95, debit=190, misc="v1_expense_bill"),
            txn_line(1004, 9102, fixture_date, "bill", 190, "Accounts Payable", ap_paid="paid", open_balance=190, vendor="Supply Vendor", product="Service Plan", quantity=2, rate=95, credit=190, misc="v1_ap_bill"),
            txn_line(1005, 9103, fixture_date, "deposit", 75, "Checking", open_balance=75, quantity=1, debit=75, misc="v1_asset_deposit"),
            txn_line(1006, 9103, fixture_date, "deposit", 75, "Payroll Liabilities", quantity=1, credit=75, misc="v1_liability_deposit"),
            txn_line(1007, 9104, fixture_date, "invoice", 44, "Inventory Asset", ar_paid="--", open_balance=44, customer="Bright Customer", product="Widget", quantity=22, rate=2, debit=44, misc="v1_missing_status"),
            txn_line(1008, 9104, fixture_date, "invoice", 44, "Consulting Income", ar_paid="--", customer="Bright Customer", product="Widget", quantity=22, rate=2, credit=44, misc="v1_missing_status"),
            txn_line(1009, 9105, fixture_date, "bill", 305, "Cost of Goods Sold", ap_paid="paid", vendor="Rent Vendor", product="Service Plan", quantity=5, rate=61, debit=305, misc="v1_vendor_bill"),
            txn_line(1010, 9105, fixture_date, "bill", 305, "Visa Credit Card", ap_paid="paid", open_balance=305, vendor="Rent Vendor", product="Service Plan", quantity=5, rate=61, credit=305, misc="v1_credit_card_bill"),
        ],
    )


def _seed_gold_literal_transactions(
    conn: sqlite3.Connection,
    evidence: EqAcctEvidence,
    account_types: dict[str, str | None],
    fixture_date: str,
) -> None:
    """Seed conservative transaction rows for supported gold literals."""
    next_row = 2000
    next_txn = 9900
    for account_type_literal in sorted(evidence.gold_literals_by_column.get("Account_type", set())):
        concept = ACCOUNT_TYPE_CONCEPTS.get(account_type_literal.lower())
        if not concept:
            continue
        account_type = account_type_literal
        account_name = f"Seed {account_type_literal}"
        if account_name not in account_types:
            account_types[account_name] = account_type
            conn.execute(
                "INSERT OR IGNORE INTO chart_of_accounts VALUES (?, ?, ?, ?)",
                (next_row, BUSINESS_ID, account_name, account_type),
            )
        opposite = "Owner Equity" if concept in {"asset", "accounts_receivable"} else "Checking"
        debit_account = account_name if concept in {"asset", "accounts_receivable", "expense"} else opposite
        credit_account = opposite if debit_account == account_name else account_name
        next_row += 1
        next_txn += 1
        insert_txn_lines(
            conn,
            [
                txn_line(next_row, next_txn, fixture_date, "invoice", 123, debit_account, debit=123, open_balance=123 if debit_account == account_name else 0, customer="Acme Customer"),
                txn_line(next_row + 1, next_txn, fixture_date, "invoice", 123, credit_account, credit=123, open_balance=123 if credit_account == account_name else 0, customer="Acme Customer"),
            ],
        )
        next_row += 1


def _ensure_account(
    conn: sqlite3.Connection,
    account_types: dict[str, str | None],
    account_name: str,
    account_type: str | None,
    row_id: int,
) -> None:
    if account_type and account_types.get(account_name) != account_type:
        account_types[account_name] = account_type
        conn.execute(
            "INSERT OR REPLACE INTO chart_of_accounts VALUES (?, ?, ?, ?)",
            (row_id, BUSINESS_ID, account_name, account_type),
        )
        return
    if account_name not in account_types:
        account_types[account_name] = account_type
        conn.execute(
            "INSERT OR IGNORE INTO chart_of_accounts VALUES (?, ?, ?, ?)",
            (row_id, BUSINESS_ID, account_name, account_type),
        )


def _ensure_parent_rows(conn: sqlite3.Connection, support: GoldSupportRequirements) -> set[str]:
    seeded: set[str] = set()
    for idx, customer in enumerate(sorted(support.customers), start=8000):
        conn.execute(
            "INSERT OR IGNORE INTO customers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (idx, BUSINESS_ID, customer, customer, "support", "support", "ST", 1, "support", "support", "ST", 1, 101.0),
        )
        seeded.add(customer)
    for idx, vendor in enumerate(sorted(support.vendors), start=8100):
        conn.execute(
            "INSERT OR IGNORE INTO vendors VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (idx, BUSINESS_ID, vendor, "support", "support", "ST", 1, 202.0),
        )
        seeded.add(vendor)
    for idx, product in enumerate(sorted(support.products), start=8200):
        conn.execute("INSERT OR IGNORE INTO products VALUES (?, ?, ?, ?)", (idx, str(BUSINESS_ID), product, "service"))
        seeded.add(product)
    for idx, method in enumerate(sorted(support.payment_methods), start=8300):
        conn.execute("INSERT OR IGNORE INTO payment_method VALUES (?, ?, ?, ?)", (idx, str(BUSINESS_ID), method, "no"))
        seeded.add(method)
    return seeded


def _account_type_literal_for_support(support: GoldSupportRequirements) -> str | None:
    literal = _first_sorted(support.account_type_literals)
    if literal:
        return literal
    concept = _first_sorted(support.account_type_concepts)
    return ACCOUNT_TYPES_BY_CONCEPT.get(concept or "")


def _support_account_name(support: GoldSupportRequirements, account_type_literal: str | None) -> str:
    explicit = _first_sorted(support.account_literals)
    if explicit:
        return explicit
    substring = _first_sorted(support.account_substrings)
    if substring:
        return substring
    if account_type_literal:
        return f"Support {account_type_literal}"
    return "Sales Income"


def _support_posting_side(support: GoldSupportRequirements) -> str:
    measures = support.selected_measures
    if "credit" in measures:
        return "credit"
    if "debit" in measures:
        return "debit"
    concept = _first_sorted(support.account_type_concepts)
    if concept in {"income", "liability", "accounts_payable"}:
        return "credit"
    return "debit"


def _opposite_account_for_side(side: str) -> str:
    return "Checking" if side == "credit" else "Owner Equity"


def _base_line_kwargs(support: GoldSupportRequirements) -> dict[str, Any]:
    customer = _first_sorted(support.customers, "Acme Customer")
    vendor = _first_sorted(support.vendors)
    product = _first_sorted(support.products, "Widget")
    payment_method = _first_sorted(support.payment_methods, "Cash")
    kwargs = {
        "customer": customer,
        "vendor": vendor,
        "product": product,
        "payment_method": payment_method,
        "quantity": 5,
        "rate": 31,
        "ar_paid": "paid",
        "ap_paid": "paid",
        "open_balance": 155.0,
    }
    for column, statuses in support.settlement_statuses.items():
        status = _first_sorted(statuses)
        if column == "ar_paid":
            kwargs["ar_paid"] = status
        elif column == "ap_paid":
            kwargs["ap_paid"] = status
    return kwargs


def _matching_transaction_types(support: GoldSupportRequirements) -> list[str]:
    if support.transaction_types:
        return sorted(support.transaction_types)
    return ["invoice"]


def _contrast_transaction_types(support: GoldSupportRequirements) -> list[str]:
    matching = set(_matching_transaction_types(support))
    return sorted(set(ALLOWED_TRANSACTION_TYPES) - matching)


def _support_probe_sql(support: GoldSupportRequirements) -> str:
    predicates: list[str] = []
    if support.account_type_literals or support.account_type_concepts:
        account_types = sorted(
            support.account_type_literals
            or {
                ACCOUNT_TYPES_BY_CONCEPT[concept]
                for concept in support.account_type_concepts
                if concept in ACCOUNT_TYPES_BY_CONCEPT
            }
        )
        if account_types:
            predicates.append(
                "c.Account_type IN (" + ", ".join(_sql_literal(value) for value in account_types) + ")"
            )
    if support.account_literals:
        predicates.append(
            "m.Account IN (" + ", ".join(_sql_literal(value) for value in sorted(support.account_literals)) + ")"
        )
    for substring in sorted(support.account_substrings):
        predicates.append(f"INSTR(m.Account, {_sql_literal(substring)})")
    if support.transaction_types:
        predicates.append(
            "m.Transaction_TYPE IN (" + ", ".join(_sql_literal(value) for value in sorted(support.transaction_types)) + ")"
        )
    if support.customers:
        predicates.append(
            "m.Customers IN (" + ", ".join(_sql_literal(value) for value in sorted(support.customers)) + ")"
        )
    if support.vendors:
        predicates.append(
            "m.Vendor IN (" + ", ".join(_sql_literal(value) for value in sorted(support.vendors)) + ")"
        )
    if support.products:
        predicates.append(
            "m.Product_Service IN (" + ", ".join(_sql_literal(value) for value in sorted(support.products)) + ")"
        )
    if support.payment_methods:
        predicates.append(
            "m.payment_method IN (" + ", ".join(_sql_literal(value) for value in sorted(support.payment_methods)) + ")"
        )
    for column, values in sorted(support.settlement_statuses.items()):
        predicates.append(
            f"m.{column} IN (" + ", ".join(_sql_literal(value) for value in sorted(values)) + ")"
        )
    if support.date_lower:
        predicates.append(f"m.Transaction_DATE >= {_sql_literal(support.date_lower)}")
    if support.date_upper:
        predicates.append(f"m.Transaction_DATE <= {_sql_literal(support.date_upper)}")
    where = " WHERE " + " AND ".join(predicates) if predicates else ""
    return (
        "SELECT COUNT(*) FROM master_txn_table m "
        "LEFT JOIN chart_of_accounts c ON m.businessID = c.businessID AND m.Account = c.Account_name"
        f"{where}"
    )


def _seed_support_transactions(
    conn: sqlite3.Connection,
    support: GoldSupportRequirements,
    account_types: dict[str, str | None],
) -> tuple[set[str], int, str]:
    seeded = _ensure_parent_rows(conn, support)
    account_type_literal = _account_type_literal_for_support(support)
    account_name = _support_account_name(support, account_type_literal)
    _ensure_account(conn, account_types, account_name, account_type_literal, 8400)
    seeded.add(account_name)
    if account_type_literal:
        seeded.add(account_type_literal)

    side = _support_posting_side(support)
    opposite = _opposite_account_for_side(side)
    amount = 155.0
    date = support.fixture_date or "2026-06-23"
    kwargs = _base_line_kwargs(support)
    rows: list[tuple[Any, ...]] = []
    row_id = 8500
    txn_id = 9500

    for txn_type in _matching_transaction_types(support):
        first = txn_line(
            row_id,
            txn_id,
            date,
            txn_type,
            amount,
            account_name,
            credit=amount if side == "credit" else 0.0,
            debit=amount if side == "debit" else 0.0,
            misc="v1_gold_support",
            **kwargs,
        )
        second = txn_line(
            row_id + 1,
            txn_id,
            date,
            txn_type,
            amount,
            account_name if support.count_used else opposite,
            credit=amount if side == "debit" else 0.0,
            debit=amount if side == "credit" else 0.0,
            misc="v1_gold_support_pair",
            **{**kwargs, "quantity": 8},
        )
        rows.extend([first, second])
        seeded.add(txn_type)
        row_id += 2
        txn_id += 1

    for txn_type in _contrast_transaction_types(support):
        contrast_amount = amount + (37 * (len(rows) + 1))
        rows.extend(
            [
                txn_line(
                    row_id,
                    txn_id,
                    date,
                    txn_type,
                    contrast_amount,
                    account_name,
                    credit=contrast_amount if side == "credit" else 0.0,
                    debit=contrast_amount if side == "debit" else 0.0,
                    misc="v1_transaction_type_contrast",
                    **kwargs,
                ),
                txn_line(
                    row_id + 1,
                    txn_id,
                    date,
                    txn_type,
                    contrast_amount,
                    account_name if support.count_used else opposite,
                    credit=contrast_amount if side == "debit" else 0.0,
                    debit=contrast_amount if side == "credit" else 0.0,
                    misc="v1_transaction_type_contrast_pair",
                    **{**kwargs, "quantity": 9},
                ),
            ]
        )
        row_id += 2
        txn_id += 1

    if rows:
        insert_txn_lines(conn, rows)
    return seeded, len(rows), _support_probe_sql(support)


def _build_fixture_state(
    gold_sql: str,
    evidence: EqAcctEvidence,
    support: GoldSupportRequirements,
    template: str,
    state_name: str,
    fixture_date: str,
) -> FixtureState:
    fixture = build_fixture(gold_sql, fixture_date)
    conn = sqlite3.connect(fixture.db_path)
    try:
        _insert_template_rows(conn, fixture_date)
        _seed_gold_literal_transactions(conn, evidence, fixture.account_types_by_name, fixture_date)
        support_seed_values, support_rows_inserted, support_probe_sql = _seed_support_transactions(
            conn,
            support,
            fixture.account_types_by_name,
        )
        conn.commit()
    finally:
        conn.close()
    required_literals = {
        literal
        for literals in evidence.gold_literals_by_column.values()
        for literal in literals
    }
    return FixtureState(
        template=template,
        state_name=state_name,
        purpose=state_name.replace("_", " "),
        db_path=fixture.db_path,
        seed_values=set(fixture.seed_values) | required_literals | support_seed_values,
        required_literals=required_literals,
        support_requirements=support,
        support_probe_sql=support_probe_sql,
        support_rows_inserted=support_rows_inserted,
        preserve_double_entry=True,
    )


def _fixture_date_for_evidence(evidence: EqAcctEvidence, default_date: str) -> str:
    for predicate in evidence.date_predicates:
        for value in predicate.values:
            text = str(value).strip("'").strip('"')
            if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
                return text[:10]
    return default_date


def build_fixture_states(
    gold_sql: str,
    evidence: EqAcctEvidence,
    applicable_templates: Iterable[str],
    *,
    fixture_date: str,
) -> list[FixtureState]:
    states: list[FixtureState] = []
    support = build_gold_support_requirements(gold_sql, evidence, fixture_date)
    state_fixture_date = support.fixture_date or _fixture_date_for_evidence(evidence, fixture_date)
    for template in applicable_templates:
        for state_name in STATE_NAMES_BY_TEMPLATE.get(template, []):
            states.append(_build_fixture_state(gold_sql, evidence, support, template, state_name, state_fixture_date))
    return states


def _result_all_null(rows: list[tuple[Any, ...]]) -> bool:
    return bool(rows) and all(all(value is None for value in row) for row in rows)


def _is_scalar_zero(rows: list[tuple[Any, ...]]) -> bool:
    return len(rows) == 1 and len(rows[0]) == 1 and rows[0][0] == 0


def _blocking_support_reasons(support: GoldSupportRequirements | None) -> list[str]:
    if support is None:
        return []
    reasons: list[str] = []
    for item in support.unsupported:
        if item.kind == "transaction_type" and support.transaction_types:
            continue
        reasons.append(item.reason or f"unsupported_{item.kind}")
    return reasons


def _is_timeout_error(error: str | None) -> bool:
    return bool(error) and ("query_timeout" in error.lower() or "interrupted" in error.lower())


def _validate_fixture_state(
    state: FixtureState,
    gold_sql: str,
    max_progress_steps: int,
    progress_check_interval: int,
) -> tuple[bool, dict[str, Any], list[tuple[Any, ...]] | None]:
    record: dict[str, Any] = {
        "template": state.template,
        "state_name": state.state_name,
        "valid": False,
        "reason": None,
        "gold_result_preview": None,
        "support_probe_sql": state.support_probe_sql,
        "support_rows_inserted": state.support_rows_inserted,
    }
    if not state.required_literals <= state.seed_values:
        record["reason"] = "gold_literal_not_seeded"
        return False, record, None
    blocking_support_reasons = _blocking_support_reasons(state.support_requirements)
    if blocking_support_reasons:
        record["reason"] = "unsupported_support_requirement"
        record["support_reasons"] = blocking_support_reasons
        return False, record, None

    conn = sqlite3.connect(state.db_path)
    conn.execute("PRAGMA query_only = ON")
    try:
        try:
            assert_fixture_integrity(conn)
        except AssertionError as exc:
            record["reason"] = "accounting_constraint_violation"
            record["error"] = str(exc)
            return False, record, None
        rows, error = execute_sql(conn, gold_sql, max_progress_steps, progress_check_interval)
        if error:
            record["reason"] = "timeout" if _is_timeout_error(error) else "gold_sql_execution_error_on_fixture"
            record["error"] = error
            return False, record, None
        rows = rows or []
        if not rows:
            record["reason"] = "gold_result_empty"
            return False, record, rows
        if (
            state.support_requirements
            and state.support_requirements.count_used
            and _is_scalar_zero(rows)
            and state.support_probe_sql
        ):
            probe_count = conn.execute(state.support_probe_sql).fetchone()[0]
            record["support_probe_count"] = probe_count
            if state.support_rows_inserted and probe_count <= 0:
                record["reason"] = "support_probe_empty"
                return False, record, rows
            if state.support_rows_inserted and probe_count > 0:
                record["reason"] = "gold_count_zero_despite_supported_rows"
                return False, record, rows
        if _result_all_null(rows):
            record["reason"] = "gold_result_all_null"
            return False, record, rows
        record["valid"] = True
        record["gold_result_preview"] = result_preview(rows, MAX_PREVIEW_ROWS)
        return True, record, rows
    finally:
        conn.close()


def _replace_column_names(tree: exp.Expression, mapping: dict[str, str]) -> exp.Expression:
    def transform(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column):
            replacement = mapping.get(_column_key(node.name))
            if replacement:
                node.set("this", exp.to_identifier(replacement))
        return node

    return tree.copy().transform(transform)


def _replace_string_literals(tree: exp.Expression, mapping: dict[str, str]) -> exp.Expression:
    norm_mapping = {normalise_value(key): value for key, value in mapping.items()}

    def transform(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Literal) and node.is_string:
            replacement = norm_mapping.get(normalise_value(node.this))
            if replacement:
                return exp.Literal.string(replacement)
        return node

    return tree.copy().transform(transform)


def _replace_sum_column_with_count(tree: exp.Expression, target_columns: set[str]) -> exp.Expression | None:
    changed = False

    def transform(node: exp.Expression) -> exp.Expression:
        nonlocal changed
        if isinstance(node, exp.Sum):
            cols = list(node.find_all(exp.Column))
            if any(_column_key(col.name) in target_columns for col in cols):
                changed = True
                return exp.Count(this=exp.Star())
        return node

    mutated = tree.copy().transform(transform)
    return mutated if changed else None


def _replace_sum_column_with_distinct_transaction_count(tree: exp.Expression, target_columns: set[str]) -> exp.Expression | None:
    changed = False

    def transform(node: exp.Expression) -> exp.Expression:
        nonlocal changed
        if isinstance(node, exp.Sum):
            cols = list(node.find_all(exp.Column))
            if any(_column_key(col.name) in target_columns for col in cols):
                changed = True
                return exp.Count(this=exp.Distinct(expressions=[exp.column("Transaction_ID")]))
        return node

    mutated = tree.copy().transform(transform)
    return mutated if changed else None


def _replace_count_with_count_star(tree: exp.Expression) -> exp.Expression | None:
    changed = False

    def transform(node: exp.Expression) -> exp.Expression:
        nonlocal changed
        if isinstance(node, exp.Count) and not isinstance(node.this, exp.Star):
            changed = True
            return exp.Count(this=exp.Star())
        return node

    mutated = tree.copy().transform(transform)
    return mutated if changed else None


def _replace_count_star_with_distinct_transaction(tree: exp.Expression) -> exp.Expression | None:
    changed = False

    def transform(node: exp.Expression) -> exp.Expression:
        nonlocal changed
        if isinstance(node, exp.Count) and isinstance(node.this, exp.Star):
            changed = True
            return exp.Count(this=exp.Distinct(expressions=[exp.column("Transaction_ID")]))
        return node

    mutated = tree.copy().transform(transform)
    return mutated if changed else None


def _replace_count_with_sum_column(tree: exp.Expression, column: str) -> exp.Expression | None:
    changed = False

    def transform(node: exp.Expression) -> exp.Expression:
        nonlocal changed
        if isinstance(node, exp.Count):
            changed = True
            return exp.Sum(this=exp.column(column))
        return node

    mutated = tree.copy().transform(transform)
    return mutated if changed else None


def _replace_count_with_status_count(tree: exp.Expression) -> exp.Expression | None:
    changed = False

    def transform(node: exp.Expression) -> exp.Expression:
        nonlocal changed
        if isinstance(node, exp.Count):
            changed = True
            return sqlglot.parse_one(
                "SUM(CASE WHEN AR_paid = 'paid' OR AP_paid = 'paid' THEN 1 ELSE 0 END)",
                read="sqlite",
            )
        return node

    mutated = tree.copy().transform(transform)
    return mutated if changed else None


def _remove_template_predicates(tree: exp.Expression, template: str) -> exp.Expression | None:
    tied_columns = {
        POSTING_TEMPLATE: {"credit", "debit"},
        AR_AP_TEMPLATE: {"ar_paid", "ap_paid", "account_type"},
        INCOME_EXPENSE_TEMPLATE: {"account_type"},
        ASSET_LIABILITY_TEMPLATE: {"account_type"},
        BALANCE_COUNT_TEMPLATE: {"open_balance", "ar_paid", "ap_paid"},
        QUANTITY_COUNT_TEMPLATE: {"quantity", "transaction_id"},
        TRANSACTION_TYPE_TEMPLATE: {"transaction_type"},
        CUSTOMER_VENDOR_TEMPLATE: {"customers", "customer_name", "customer_full_name", "vendor", "vendor_name"},
    }.get(template, set())
    where = tree.args.get("where")
    if where is None or where.this is None:
        return None

    def strip_predicate(node: exp.Expression) -> exp.Expression | None:
        if isinstance(node, exp.And):
            left = strip_predicate(node.left)
            right = strip_predicate(node.right)
            if left is None:
                return right
            if right is None:
                return left
            node.set("this", left)
            node.set("expression", right)
            return node
        cols = {_column_key(col.name) for col in node.find_all(exp.Column)}
        if cols & tied_columns:
            return None
        return node

    new_condition = strip_predicate(where.this.copy())
    if new_condition is None:
        return None
    mutated = tree.copy()
    mutated.set("where", exp.Where(this=new_condition))
    return mutated


def _safe_sql(tree: exp.Expression | None, original_sql: str) -> str | None:
    if tree is None:
        return None
    sql = tree.sql(dialect="sqlite")
    return sql if sql and sql.strip() != original_sql.strip() else None


def generate_template_mutants_with_debug(
    gold_sql: str,
    template: str,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Generate safe pre-specified mutants plus per-family debug records."""
    expected_families = MUTANT_FAMILIES_BY_TEMPLATE.get(template, [])
    try:
        tree = sqlglot.parse_one(gold_sql, read="sqlite")
    except Exception as exc:
        return [], [
            {
                "family": family,
                "generated": False,
                "skip_reason": "parse_error",
                "error": str(exc),
            }
            for family in expected_families
        ]

    mutants: list[dict[str, str]] = []
    records: list[dict[str, Any]] = []

    def add(name: str, maybe_tree: exp.Expression | None) -> None:
        sql = _safe_sql(maybe_tree, gold_sql)
        if sql and sql not in {item["sql"] for item in mutants}:
            mutants.append({"family": name, "sql": sql})
            records.append({"family": name, "generated": True, "skip_reason": None, "sql": sql})
        else:
            records.append(
                {
                    "family": name,
                    "generated": False,
                    "skip_reason": "no_safe_rewrite_or_no_change",
                }
            )

    if template == POSTING_TEMPLATE:
        add("swap_credit_debit", _replace_column_names(tree, {"credit": "Debit", "debit": "Credit"}))
    elif template == AR_AP_TEMPLATE:
        add("swap_ar_ap_status", _replace_column_names(tree, {"ar_paid": "AP_paid", "ap_paid": "AR_paid"}))
        add("swap_ar_ap_concepts", _replace_string_literals(tree, {"accounts receivable (a/r)": "Accounts Payable (A/P)", "accounts payable (a/p)": "Accounts Receivable (A/R)"}))
    elif template == INCOME_EXPENSE_TEMPLATE:
        add("swap_income_expense_values", _replace_string_literals(tree, {"income": "Expenses", "other income": "Other Expense", "expenses": "Income", "expense": "Income", "other expense": "Other Income"}))
        add("remove_account_type_predicate", _remove_template_predicates(tree, template))
    elif template == ASSET_LIABILITY_TEMPLATE:
        add("swap_asset_liability_values", _replace_string_literals(tree, {"other current assets": "Other Current Liabilities", "fixed assets": "Long Term Liabilities", "bank": "Credit Card", "credit card": "Bank", "accounts receivable (a/r)": "Accounts Payable (A/P)", "accounts payable (a/p)": "Accounts Receivable (A/R)"}))
        add("remove_asset_liability_predicate", _remove_template_predicates(tree, template))
    elif template == BALANCE_COUNT_TEMPLATE:
        add("replace_balance_with_count", _replace_sum_column_with_count(tree, {"open_balance", "balance"}))
        add("replace_count_with_balance", _replace_count_with_sum_column(tree, "Open_balance"))
        add("replace_count_with_status", _replace_count_with_status_count(tree))
        add("replace_open_balance_with_amount", _replace_column_names(tree, {"open_balance": "Amount"}))
    elif template == QUANTITY_COUNT_TEMPLATE:
        add("replace_quantity_with_count", _replace_sum_column_with_count(tree, {"quantity"}))
        add("replace_quantity_with_distinct_transactions", _replace_sum_column_with_distinct_transaction_count(tree, {"quantity"}))
        add("count_rows_to_distinct_transactions", _replace_count_star_with_distinct_transaction(tree))
        add("count_distinct_to_rows", _replace_count_with_count_star(tree))
    elif template == TRANSACTION_TYPE_TEMPLATE:
        add("swap_transaction_type_literals", _replace_string_literals(tree, {"invoice": "bill", "bill": "deposit", "deposit": "invoice"}))
        add("remove_transaction_type_predicate", _remove_template_predicates(tree, template))
    elif template == CUSTOMER_VENDOR_TEMPLATE:
        add("swap_customer_vendor_columns", _replace_column_names(tree, {"customers": "Vendor", "customer_name": "Vendor_name", "customer_full_name": "Vendor_name", "vendor": "Customers", "vendor_name": "customer_name"}))
        add("remove_party_predicate", _remove_template_predicates(tree, template))

    generated_or_skipped = {record["family"] for record in records}
    for family in expected_families:
        if family not in generated_or_skipped:
            records.append(
                {
                    "family": family,
                    "generated": False,
                    "skip_reason": "not_attempted_for_template",
                }
            )

    return mutants, records


def generate_template_mutants(gold_sql: str, template: str) -> list[dict[str, str]]:
    """Generate safe pre-specified mutants for one activated template."""
    mutants, _ = generate_template_mutants_with_debug(gold_sql, template)
    return mutants


def _validate_template_suite(
    template: str,
    usable_states: list[tuple[FixtureState, list[tuple[Any, ...]]]],
    gold_sql: str,
    max_progress_steps: int,
    progress_check_interval: int,
    include_debug: bool = False,
) -> tuple[bool, str | None, dict[str, Any], dict[str, Any]]:
    if not usable_states:
        return False, "no_valid_fixture_state", {"mutants_generated": 0, "mutants_distinguished": 0}, {}
    mutants, generation_records = generate_template_mutants_with_debug(gold_sql, template)
    if not mutants:
        summary = {
            "mutants_generated": 0,
            "mutants_distinguished": 0,
            "mutant_generation_records": generation_records,
        }
        return False, "mutant_generation_not_supported", summary, {}

    distinguished = 0
    previews: dict[str, Any] = {}
    execution_records: list[dict[str, Any]] = []
    for mutant in mutants:
        mutant_distinguished = False
        for state, gold_rows in usable_states:
            conn = sqlite3.connect(state.db_path)
            conn.execute("PRAGMA query_only = ON")
            try:
                mutant_rows, mutant_error = execute_sql(conn, mutant["sql"], max_progress_steps, progress_check_interval)
            finally:
                conn.close()
            if mutant_error:
                if include_debug:
                    execution_records.append(
                        {
                            "family": mutant["family"],
                            "state_name": state.state_name,
                            "status": "error",
                            "error": mutant_error,
                        }
                    )
                continue
            mutant_rows = mutant_rows or []
            if not compare_results(gold_rows, mutant_rows, order_sensitive=has_order_by(gold_sql)):
                mutant_distinguished = True
                if include_debug:
                    execution_records.append(
                        {
                            "family": mutant["family"],
                            "state_name": state.state_name,
                            "status": "distinguished",
                            "gold_result_preview": result_preview(gold_rows, MAX_PREVIEW_ROWS),
                            "mutant_result_preview": result_preview(mutant_rows, MAX_PREVIEW_ROWS),
                        }
                    )
                previews[f"{template}:{mutant['family']}:{state.state_name}"] = {
                    "mutant_sql": mutant["sql"],
                    "gold": result_preview(gold_rows, MAX_PREVIEW_ROWS),
                    "mutant": result_preview(mutant_rows, MAX_PREVIEW_ROWS),
                }
                break
            if include_debug:
                execution_records.append(
                    {
                        "family": mutant["family"],
                        "state_name": state.state_name,
                        "status": "equal",
                        "gold_result_preview": result_preview(gold_rows, MAX_PREVIEW_ROWS),
                        "mutant_result_preview": result_preview(mutant_rows, MAX_PREVIEW_ROWS),
                    }
                )
        if mutant_distinguished:
            distinguished += 1

    summary = {
        "mutants_generated": len(mutants),
        "mutants_distinguished": distinguished,
        "mutant_families": [mutant["family"] for mutant in mutants],
        "mutant_generation_records": generation_records,
    }
    if include_debug:
        summary["mutants_executed"] = len(execution_records)
        summary["mutant_execution_records"] = execution_records
    if distinguished:
        return True, None, summary, previews
    return False, "mutants_not_distinguished", summary, previews


def _evaluate_generated_on_state(
    state: FixtureState,
    gold_rows: list[tuple[Any, ...]],
    gold_sql: str,
    generated_sql: str,
    max_progress_steps: int,
    progress_check_interval: int,
) -> tuple[bool, dict[str, Any], list[tuple[Any, ...]] | None]:
    conn = sqlite3.connect(state.db_path)
    conn.execute("PRAGMA query_only = ON")
    try:
        generated_rows, generated_error = execute_sql(conn, generated_sql, max_progress_steps, progress_check_interval)
    finally:
        conn.close()

    failed_state = {
        "template": state.template,
        "state_name": state.state_name,
        "gold_result_preview": result_preview(gold_rows, MAX_PREVIEW_ROWS),
        "generated_result_preview": None,
        "generated_error": generated_error,
        "failed_reason": None,
    }
    if generated_error:
        failed_state["failed_reason"] = "timeout" if _is_timeout_error(generated_error) else "generated_execution_error"
        return False, failed_state, None
    generated_rows = generated_rows or []
    failed_state["generated_result_preview"] = result_preview(generated_rows, MAX_PREVIEW_ROWS)
    if not compare_results(gold_rows, generated_rows, order_sensitive=has_order_by(gold_sql)):
        failed_state["failed_reason"] = "generated_mismatch"
        return False, failed_state, generated_rows
    return True, failed_state, generated_rows


def _cleanup_states(states: Iterable[FixtureState]) -> None:
    for state in states:
        state.db_path.unlink(missing_ok=True)


def evaluate_eq_acct_v1(
    gold_sql: str,
    generated_sql: str,
    schema_annotations: dict[str, Any],
    *,
    fixture_date: str = "2026-06-23",
    max_progress_steps: int = 2_000_000,
    progress_check_interval: int = 1000,
    include_debug: bool = False,
) -> dict[str, Any]:
    """Evaluate canonical Eq_acct_v1 result for one SQL pair."""
    result = _empty_result()
    evidence = build_eq_acct_evidence(gold_sql, schema_annotations)
    if include_debug:
        result["debug"] = {
            "evidence": _debug_evidence_dict(evidence),
            "activated_templates": {},
            "fixture_states": [],
            "generated_execution_records": [],
        }

    if evidence.parse_error:
        result["not_testable_reason_counts"] = {"unsupported_sql_feature": 1}
        result["mutant_validation_summary"] = {"parse_error": evidence.parse_error}
        return result
    if evidence.unsupported_lineage:
        result["not_testable_reason_counts"] = {"unsupported_sql_feature": 1}
        result["mutant_validation_summary"] = {"unsupported_lineage": True}
        return result

    applicable = activate_templates(evidence)
    result["applicable_templates"] = sorted(applicable)
    if include_debug:
        result["debug"]["activated_templates"] = dict(sorted(applicable.items()))
    if not applicable:
        result["not_testable_reason_counts"] = {"no_applicable_template": 1}
        return result

    states = build_fixture_states(gold_sql, evidence, sorted(applicable), fixture_date=fixture_date)
    if include_debug:
        result["debug"]["fixture_states"] = [_debug_state_dict(state) for state in states]
    not_testable_reason_counts: Counter[str] = Counter()
    state_validation_records: list[dict[str, Any]] = []
    usable_by_template: dict[str, list[tuple[FixtureState, list[tuple[Any, ...]]]]] = {
        template: [] for template in applicable
    }
    template_results: dict[str, Any] = {}
    mutant_result_previews: dict[str, Any] = {}
    mutant_validation_summary: dict[str, Any] = {}

    try:
        for state in states:
            valid, record, gold_rows = _validate_fixture_state(
                state,
                gold_sql,
                max_progress_steps,
                progress_check_interval,
            )
            state_validation_records.append(record)
            if valid and gold_rows is not None:
                usable_by_template[state.template].append((state, gold_rows))
                if result["gold_result_preview"] is None:
                    result["gold_result_preview"] = result_preview(gold_rows, MAX_PREVIEW_ROWS)
            else:
                not_testable_reason_counts.update([str(record.get("reason") or "fixture_schema_invalid")])

        validated_templates: list[str] = []
        for template in sorted(applicable):
            usable_states = usable_by_template.get(template, [])
            validated, reason, summary, previews = _validate_template_suite(
                template,
                usable_states,
                gold_sql,
                max_progress_steps,
                progress_check_interval,
                include_debug=include_debug,
            )
            mutant_validation_summary[template] = summary
            mutant_result_previews.update(previews)
            template_results[template] = {
                "activation_reason": applicable[template],
                "usable_fixture_state_count": len(usable_states),
                "validated": validated,
                "not_testable_reason": reason,
                "state_names": [state.state_name for state, _ in usable_states],
                "mutant_validation": summary,
            }
            if validated:
                validated_templates.append(template)
            elif reason:
                not_testable_reason_counts.update([reason])

        tested_templates: list[str] = []
        failed_templates: set[str] = set()
        failed_states: list[dict[str, Any]] = []
        tested_state_count = 0
        first_generated_preview = None

        for template in validated_templates:
            template_tested = False
            for state, gold_rows in usable_by_template[template]:
                template_tested = True
                tested_state_count += 1
                ok, failed_state, generated_rows = _evaluate_generated_on_state(
                    state,
                    gold_rows,
                    gold_sql,
                    generated_sql,
                    max_progress_steps,
                    progress_check_interval,
                )
                if include_debug:
                    result["debug"]["generated_execution_records"].append(
                        {
                            "template": state.template,
                            "state_name": state.state_name,
                            "executed": True,
                            "passed": ok,
                            "failed_reason": failed_state.get("failed_reason"),
                            "generated_error": failed_state.get("generated_error"),
                            "generated_result_preview": failed_state.get("generated_result_preview"),
                        }
                    )
                if generated_rows is not None and first_generated_preview is None:
                    first_generated_preview = result_preview(generated_rows, MAX_PREVIEW_ROWS)
                if not ok:
                    failed_templates.add(template)
                    failed_states.append(failed_state)
            if template_tested:
                tested_templates.append(template)

        if failed_states:
            eq_value: int | None = 0
        elif tested_state_count:
            eq_value = 1
        else:
            eq_value = None

        result.update(
            {
                "eq_acct_result": eq_value,
                "adversarial_pass": _compat_result(eq_value),
                "validated_templates": sorted(validated_templates),
                "tested_templates": sorted(tested_templates),
                "failed_templates": sorted(failed_templates),
                "usable_fixture_state_count": sum(len(items) for items in usable_by_template.values()),
                "invalid_fixture_state_count": sum(1 for record in state_validation_records if not record["valid"]),
                "tested_fixture_state_count": tested_state_count,
                "not_testable_reason_counts": dict(sorted(not_testable_reason_counts.items())),
                "template_results": template_results,
                "failed_states": failed_states,
                "state_validation_records": state_validation_records,
                "generated_result_preview": first_generated_preview,
                "mutant_result_previews": mutant_result_previews,
                "mutant_validation_summary": mutant_validation_summary,
            }
        )
        return result
    finally:
        _cleanup_states(states)


def evaluate_sql_pair_on_fixture_v1(
    *,
    gold_sql: str,
    generated_sql: str,
    schema_annotations: dict[str, Any],
    fixture_date: str = "2026-06-23",
    max_progress_steps: int = 2_000_000,
    progress_check_interval: int = 1000,
) -> dict[str, Any]:
    """Compatibility alias for callers that still use fixture terminology."""
    return evaluate_eq_acct_v1(
        gold_sql=gold_sql,
        generated_sql=generated_sql,
        schema_annotations=schema_annotations,
        fixture_date=fixture_date,
        max_progress_steps=max_progress_steps,
        progress_check_interval=progress_check_interval,
    )
