"""Deterministic financial-semantic contradiction diagnostics for SQL pairs.

This v1 diagnostic is intentionally narrow. It compares schema-grounded
financial meaning in SELECT outputs and WHERE filters; it does not exact-match
SQL text, entity names, literal IDs, addresses, dates, or ordinary
non-financial business objects.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from src.finverisql.schema_loader import SchemaAnnotationStore, normalise_value
from src.finverisql.sql_parser import ColumnRef, ParsedSQL, parse_sql
from src.finverisql.sql_semantic_mapping import build_sql_financial_semantics


HARD = "hard_financial_contradiction"
WARNING = "warning"
NONE = "no_financial_contradiction"
NOT_EVALUABLE = "not_evaluable"

FINANCIAL_ROLES = {"financial_measure"}
RATE_ROLES = {"rate_measure"}
QUANTITY_ROLES = {"quantity_measure"}
STATUS_ROLES = {"settlement_status_flag", "status_flag"}
COUNT_FUNCTIONS = {"count"}
SUM_FUNCTIONS = {"sum"}
AVG_FUNCTIONS = {"avg", "average"}
MONEY_UNITS = {"money", "currency", "monetary"}
RATE_UNITS = {"monetary_per_unit"}
TRANSACTION_TYPE_ROLE = "transaction_type_classifier"
INVOICE_BILL_CONCEPTS = {"invoice", "bill"}

FINANCIAL_NAME_HINTS = {
    "amount",
    "balance",
    "open_balance",
    "credit",
    "debit",
    "quantity",
    "qty",
    "rate",
    "price",
    "cost",
    "expense",
    "revenue",
    "income",
    "asset",
    "liability",
    "ar",
    "ap",
}

OPPOSITE_CLASSES = {
    frozenset({"asset", "liability"}),
    frozenset({"accounts_receivable", "accounts_payable"}),
    frozenset({"ar", "ap"}),
    frozenset({"income", "expense"}),
    frozenset({"revenue", "expense"}),
    frozenset({"revenue", "cost"}),
}

ASSET_LIKE = {"asset", "accounts_receivable", "ar"}
LIABILITY_LIKE = {"liability", "accounts_payable", "ap"}
INCOME_LIKE = {"income", "revenue"}
EXPENSE_LIKE = {"expense", "cost"}


@dataclass(frozen=True)
class Term:
    expression: str
    function: str | None
    column: str | None
    table: str | None
    sign: int
    annotations: tuple[dict[str, Any], ...]


@dataclass
class OutputAtom:
    expression: str
    expression_index: int
    expression_sql: str
    function: str | None
    column: str | None
    table: str | None
    semantic_kind: str
    measure_type: str | None = None
    unit: str | None = None
    financial_element: str | None = None
    domain_object: str | None = None
    financial_role: str | None = None
    posting_side: str | None = None
    expression_contribution: str = "unknown"
    signed_posting_side: str | None = None
    formula_signed_posting_sides: set[str] = field(default_factory=set)
    formula_functions: set[str] = field(default_factory=set)
    classes: set[str] = field(default_factory=set)
    is_finance_bearing: bool = False
    is_missing_financial_annotation: bool = False
    annotations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["classes"] = sorted(self.classes)
        result["formula_signed_posting_sides"] = sorted(self.formula_signed_posting_sides)
        result["formula_functions"] = sorted(self.formula_functions)
        result["annotations"] = [
            {
                key: value
                for key, value in annotation.items()
                if key
                in {
                    "table",
                    "column",
                    "semantic_role",
                    "financial_role",
                    "measure_type",
                    "unit",
                    "financial_element",
                    "domain_object",
                    "posting_side",
                    "normal_balance",
                    "status_dimension",
                }
                and value not in (None, "", [], {}, "none")
            }
            for annotation in self.annotations
        ]
        return {
            key: value
            for key, value in result.items()
            if value not in (None, "", [], {}, set(), "none")
        }


@dataclass
class FilterAtom:
    expression: str
    operator: str | None
    column: str | None
    table: str | None
    values: list[Any]
    semantic_kind: str
    classes: set[str] = field(default_factory=set)
    is_finance_bearing: bool = False
    annotations: list[dict[str, Any]] = field(default_factory=list)
    value_semantics: list[dict[str, Any]] = field(default_factory=list)

    def signature(self) -> tuple[Any, ...]:
        return (
            self.column_key,
            self.operator,
            tuple(sorted(self.classes)),
        )

    @property
    def column_key(self) -> str:
        if self.table and self.column:
            return f"{self.table}.{self.column}".lower()
        return (self.column or "").lower()

    def to_dict(self) -> dict[str, Any]:
        return {
            "expression": self.expression,
            "operator": self.operator,
            "column": self.column,
            "table": self.table,
            "values": self.values,
            "semantic_kind": self.semantic_kind,
            "classes": sorted(self.classes),
            "is_finance_bearing": self.is_finance_bearing,
            "value_semantics": [
                {
                    key: value
                    for key, value in item.items()
                    if key
                    in {
                        "raw_value",
                        "clean_value",
                        "normalised_value",
                        "concept",
                        "value_status",
                        "exact_literal_match",
                    }
                    and value not in (None, "", [], {}, "none")
                }
                for item in self.value_semantics
            ],
        }


@dataclass
class Bundle:
    sql: str
    parsed: ParsedSQL
    semantics: Any
    select_atoms: list[OutputAtom]
    filter_atoms: list[FilterAtom]
    non_financial_atoms: list[dict[str, Any]]
    unsupported_financial_expressions: list[str]
    missing_financial_annotations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sql": self.sql,
            "select_atoms": [atom.to_dict() for atom in self.select_atoms],
            "filter_atoms": [atom.to_dict() for atom in self.filter_atoms],
            "non_financial_atoms": self.non_financial_atoms,
            "unsupported_financial_expressions": self.unsupported_financial_expressions,
            "missing_financial_annotations": self.missing_financial_annotations,
            "parse_error": self.parsed.parse_error,
            "unsupported_lineage": self.parsed.unsupported_lineage,
        }


def evaluate_financial_contradiction(
    gold_sql: str,
    generated_sql: str,
    schema_annotations: dict,
) -> dict:
    """Compare two SQL statements for deterministic financial contradictions."""
    schema_store = SchemaAnnotationStore(schema_annotations)
    findings: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    gold_bundle = _build_bundle(gold_sql, schema_store)
    generated_bundle = _build_bundle(generated_sql, schema_store)

    not_evaluable_reasons = _not_evaluable_reasons(gold_bundle, "gold") + _not_evaluable_reasons(
        generated_bundle,
        "generated",
    )
    if not_evaluable_reasons:
        findings.extend(not_evaluable_reasons)
        primary_status = NOT_EVALUABLE
    else:
        comparison_findings = _compare_select_outputs(gold_bundle, generated_bundle, schema_store)
        comparison_findings.extend(_compare_filters(gold_bundle, generated_bundle))

        findings.extend([finding for finding in comparison_findings if finding["status"] == HARD])
        warnings.extend([finding for finding in comparison_findings if finding["status"] == WARNING])

        if findings:
            primary_status = HARD
        else:
            primary_status = NONE

    return {
        "primary_status": primary_status,
        "findings": findings,
        "warnings": warnings,
        "gold_bundle": gold_bundle.to_dict(),
        "generated_bundle": generated_bundle.to_dict(),
        "debug": {
            "gold_semantics": gold_bundle.semantics.to_dict(),
            "generated_semantics": generated_bundle.semantics.to_dict(),
        },
    }


def _build_bundle(sql: str, schema_store: SchemaAnnotationStore) -> Bundle:
    parsed = parse_sql(sql)
    semantics = build_sql_financial_semantics(parsed, schema_store)
    select_atoms: list[OutputAtom] = []
    non_financial_atoms: list[dict[str, Any]] = []
    unsupported: list[str] = []
    missing: list[str] = []

    if not parsed.parse_error:
        tree = sqlglot.parse_one(sql or "", read="sqlite")
        select_expr = tree.find(exp.Select)
        expressions = list(select_expr.expressions) if select_expr is not None else []
        for index, select_item in enumerate(expressions):
            expression = select_item.this if isinstance(select_item, exp.Alias) else select_item
            expression_sql = expression.sql(dialect="sqlite")
            terms, is_supported = _extract_terms(expression, parsed, schema_store)
            if not is_supported:
                if _expression_has_financial_content(expression, parsed, schema_store):
                    unsupported.append(expression_sql)
                else:
                    non_financial_atoms.append(
                        {
                            "source": "select",
                            "expression": expression_sql,
                            "reason": "unsupported_non_financial_expression",
                        }
                    )
                continue

            if not terms and _is_count_expression(expression):
                select_atoms.append(
                    OutputAtom(
                        expression=expression_sql,
                        expression_index=index,
                        expression_sql=expression_sql,
                        function="count",
                        column=None,
                        table=None,
                        semantic_kind="count",
                        expression_contribution="neutral",
                    )
                )
                continue

            for term in terms:
                atom = _atom_from_term(term, index, expression_sql)
                if atom.is_missing_financial_annotation:
                    missing.append(atom.expression)
                if atom.is_finance_bearing or atom.semantic_kind in {"count", "status"}:
                    select_atoms.append(atom)
                else:
                    non_financial_atoms.append(
                        {
                            "source": "select",
                            "expression": atom.expression,
                            "column": atom.column,
                            "table": atom.table,
                            "semantic_kind": atom.semantic_kind,
                        }
                    )
            _attach_formula_context(select_atoms, expression_index=index)

    filter_atoms = _build_filter_atoms(parsed, schema_store)
    missing.extend(_missing_financial_filter_annotations(parsed, schema_store))

    return Bundle(
        sql=sql,
        parsed=parsed,
        semantics=semantics,
        select_atoms=select_atoms,
        filter_atoms=filter_atoms,
        non_financial_atoms=non_financial_atoms,
        unsupported_financial_expressions=sorted(set(unsupported)),
        missing_financial_annotations=sorted(set(missing)),
    )


def _extract_terms(
    expression: exp.Expression,
    parsed: ParsedSQL,
    schema_store: SchemaAnnotationStore,
    sign: int = 1,
) -> tuple[list[Term], bool]:
    if isinstance(expression, exp.Paren):
        return _extract_terms(expression.this, parsed, schema_store, sign)
    if isinstance(expression, exp.Alias):
        return _extract_terms(expression.this, parsed, schema_store, sign)
    if isinstance(expression, exp.Add):
        left, left_ok = _extract_terms(expression.this, parsed, schema_store, sign)
        right, right_ok = _extract_terms(expression.expression, parsed, schema_store, sign)
        return left + right, left_ok and right_ok
    if isinstance(expression, exp.Sub):
        left, left_ok = _extract_terms(expression.this, parsed, schema_store, sign)
        right, right_ok = _extract_terms(expression.expression, parsed, schema_store, -sign)
        return left + right, left_ok and right_ok
    if isinstance(expression, exp.Neg):
        return _extract_terms(expression.this, parsed, schema_store, -sign)
    if isinstance(expression, exp.Cast):
        return _extract_terms(expression.this, parsed, schema_store, sign)
    if isinstance(expression, exp.AggFunc):
        if _is_count_expression(expression):
            return [], True
        column = next(expression.find_all(exp.Column), None)
        if column is None:
            return [], False
        column_ref = _column_ref(column, parsed.aliases)
        return [
            Term(
                expression=expression.sql(dialect="sqlite"),
                function=_function_name(expression),
                column=column_ref.column,
                table=column_ref.table,
                sign=sign,
                annotations=tuple(_annotations(column_ref, parsed, schema_store)),
            )
        ], True
    if isinstance(expression, exp.Column):
        column_ref = _column_ref(expression, parsed.aliases)
        return [
            Term(
                expression=expression.sql(dialect="sqlite"),
                function=None,
                column=column_ref.column,
                table=column_ref.table,
                sign=sign,
                annotations=tuple(_annotations(column_ref, parsed, schema_store)),
            )
        ], True
    if isinstance(expression, exp.Literal):
        return [], True
    return [], False


def _atom_from_term(term: Term, expression_index: int, expression_sql: str) -> OutputAtom:
    annotation = term.annotations[0] if len(term.annotations) == 1 else {}
    role = _norm(annotation.get("semantic_role"))
    measure_type = _norm(annotation.get("measure_type"))
    unit = _norm(annotation.get("unit"))
    financial_element = _norm(annotation.get("financial_element"))
    domain_object = _norm(annotation.get("domain_object"))
    financial_role = _norm(annotation.get("financial_role"))
    posting_side = _norm(annotation.get("posting_side"))
    classes = _classes_from_annotation(annotation)
    contribution = "positive" if term.sign > 0 else "negative"

    is_missing_financial = not term.annotations and _has_financial_name_hint(term.column)
    function = _normalise_function(term.function)

    if function in COUNT_FUNCTIONS:
        semantic_kind = "count"
    elif role in FINANCIAL_ROLES or financial_role or unit in MONEY_UNITS:
        semantic_kind = "monetary" if unit in MONEY_UNITS or measure_type in {"flow", "stock"} else "financial"
    elif role in RATE_ROLES or measure_type == "rate" or unit in RATE_UNITS:
        semantic_kind = "rate"
    elif role in QUANTITY_ROLES or measure_type == "quantity" or unit == "quantity":
        semantic_kind = "quantity"
    elif role in STATUS_ROLES:
        semantic_kind = "status"
    elif is_missing_financial:
        semantic_kind = "unknown_financial"
    else:
        semantic_kind = "non_financial"

    signed_posting_side = None
    if posting_side in {"credit", "debit"}:
        signed_posting_side = f"{'+' if term.sign > 0 else '-'}{posting_side}"

    is_finance_bearing = semantic_kind in {"monetary", "financial", "rate", "quantity", "unknown_financial"}

    return OutputAtom(
        expression=term.expression,
        expression_index=expression_index,
        expression_sql=expression_sql,
        function=function,
        column=term.column,
        table=term.table,
        semantic_kind=semantic_kind,
        measure_type=measure_type,
        unit=unit,
        financial_element=financial_element,
        domain_object=domain_object,
        financial_role=financial_role,
        posting_side=posting_side,
        expression_contribution=contribution,
        signed_posting_side=signed_posting_side,
        classes=classes,
        is_finance_bearing=is_finance_bearing,
        is_missing_financial_annotation=is_missing_financial,
        annotations=list(term.annotations),
    )


def _build_filter_atoms(parsed: ParsedSQL, schema_store: SchemaAnnotationStore) -> list[FilterAtom]:
    atoms: list[FilterAtom] = []
    for filter_ref in parsed.filters:
        for column_ref in filter_ref.columns:
            annotations = _annotations(column_ref, parsed, schema_store)
            if not annotations:
                continue
            annotation = annotations[0] if len(annotations) == 1 else {}
            value_semantics: list[dict[str, Any]] = []
            classes = _classes_from_annotation(annotation)
            for item in annotations:
                resolved = schema_store.resolve_values_semantics(item, filter_ref.values)
                value_semantics.extend(resolved)
                for value_semantic in resolved:
                    classes.update(_classes_from_value_semantic(value_semantic))

            role = _norm(annotation.get("semantic_role"))
            measure_type = _norm(annotation.get("measure_type"))
            if role in FINANCIAL_ROLES or classes or measure_type in {"flow", "stock"}:
                semantic_kind = "financial"
            elif role == TRANSACTION_TYPE_ROLE:
                semantic_kind = "transaction_type"
            elif role in QUANTITY_ROLES:
                semantic_kind = "quantity"
            elif role in STATUS_ROLES:
                semantic_kind = "status"
            else:
                semantic_kind = "non_financial"

            atoms.append(
                FilterAtom(
                    expression=filter_ref.expression,
                    operator=filter_ref.operator,
                    column=column_ref.column,
                    table=column_ref.table,
                    values=filter_ref.values,
                    semantic_kind=semantic_kind,
                    classes=classes,
                    is_finance_bearing=semantic_kind in {"financial", "quantity", "transaction_type"} or bool(classes),
                    annotations=annotations,
                    value_semantics=value_semantics,
                )
            )
    return atoms


def _compare_select_outputs(
    gold: Bundle,
    generated: Bundle,
    schema_store: SchemaAnnotationStore,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    gold_financial = [atom for atom in gold.select_atoms if atom.is_finance_bearing]
    generated_financial = [atom for atom in generated.select_atoms if atom.is_finance_bearing]

    if not gold_financial and generated_financial:
        return [
            _warning(
                "output_shape_warning",
                "Generated SQL selects a finance-bearing output where gold selects no finance-bearing output.",
                detail_code="financial_selected_output_introduced",
                generated=[atom.to_dict() for atom in generated_financial],
            )
        ]

    if not gold_financial:
        return findings

    unmatched_gold = list(gold_financial)
    unmatched_generated = list(generated.select_atoms)
    for gold_atom in list(unmatched_gold):
        match = next(
            (
                generated_atom
                for generated_atom in unmatched_generated
                if _atoms_equivalent(gold_atom, generated_atom)
            ),
            None,
        )
        if match is not None:
            unmatched_gold.remove(gold_atom)
            unmatched_generated.remove(match)

    for gold_atom in list(unmatched_gold):
        best = _best_generated_match(gold_atom, unmatched_generated)
        if best is None:
            findings.append(
                _warning(
                    "output_shape_warning",
                    "Generated SQL is missing a required finance-bearing selected output.",
                    detail_code="required_financial_output_removed",
                    gold=[gold_atom.to_dict()],
                )
            )
            continue

        status, reason = _classify_atom_pair(gold_atom, best, schema_store)
        if status:
            findings.append(
                _finding(
                    status,
                    reason,
                    _message_for_reason(reason),
                    gold=[gold_atom.to_dict()],
                    generated=[best.to_dict()],
                )
            )
        unmatched_generated.remove(best)
        if reason == "deterministic_balance_reconstruction":
            unmatched_generated = [
                atom
                for atom in unmatched_generated
                if atom.expression_index != best.expression_index
                or atom.expression_sql != best.expression_sql
            ]

    for generated_atom in unmatched_generated:
        if not generated_atom.is_finance_bearing:
            continue
        if any(_is_incompatible(gold_atom, generated_atom) for gold_atom in gold_financial):
            findings.append(
                _finding(
                    HARD,
                    "extra_incompatible_finance_output_added",
                    "Generated SQL adds an incompatible finance-bearing selected output.",
                    generated=[generated_atom.to_dict()],
                )
            )
        else:
            findings.append(
                _warning(
                    "output_shape_warning",
                    "Generated SQL adds an extra compatible finance-bearing selected output.",
                    detail_code="extra_compatible_finance_output_added",
                    generated=[generated_atom.to_dict()],
                )
            )
    return findings


def _compare_filters(gold: Bundle, generated: Bundle) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    gold_filters = [atom for atom in gold.filter_atoms if atom.is_finance_bearing]
    generated_filters = [atom for atom in generated.filter_atoms if atom.is_finance_bearing]
    matched_generated: set[int] = set()

    for gold_filter in gold_filters:
        exact_index = next(
            (
                index
                for index, generated_filter in enumerate(generated_filters)
                if index not in matched_generated and gold_filter.signature() == generated_filter.signature()
            ),
            None,
        )
        if exact_index is not None:
            matched_generated.add(exact_index)
            continue

        related = [
            (index, generated_filter)
            for index, generated_filter in enumerate(generated_filters)
            if index not in matched_generated and gold_filter.column_key == generated_filter.column_key
        ]
        if related:
            index, generated_filter = related[0]
            matched_generated.add(index)
            if _is_invoice_bill_transaction_type_substitution(gold_filter, generated_filter):
                findings.append(
                    _finding(
                        HARD,
                        "invoice_bill_transaction_type_substitution",
                        "Generated SQL substitutes invoice and bill transaction-type filters.",
                        gold=[gold_filter.to_dict()],
                        generated=[generated_filter.to_dict()],
                    )
                )
            elif _classes_incompatible(gold_filter.classes, generated_filter.classes):
                findings.append(
                    _finding(
                        HARD,
                        "financial_filter_changed_incompatibly",
                        "Generated SQL changes a finance-bearing filter to an incompatible financial class.",
                        gold=[gold_filter.to_dict()],
                        generated=[generated_filter.to_dict()],
                    )
                )
            else:
                findings.append(
                    _warning(
                        "financial_scope_warning",
                        "Generated SQL changes a finance-bearing filter.",
                        detail_code="finance_bearing_filter_changed",
                        gold=[gold_filter.to_dict()],
                        generated=[generated_filter.to_dict()],
                    )
                )
        else:
            findings.append(
                _warning(
                    "financial_scope_warning",
                    "Generated SQL removes a finance-bearing filter.",
                    detail_code="finance_bearing_filter_removed",
                    gold=[gold_filter.to_dict()],
                )
            )

    for index, generated_filter in enumerate(generated_filters):
        if index in matched_generated:
            continue
        findings.append(
            _warning(
                "financial_scope_warning",
                "Generated SQL adds a finance-bearing filter.",
                detail_code="finance_bearing_filter_added",
                generated=[generated_filter.to_dict()],
            )
        )
    return findings


def _is_invoice_bill_transaction_type_substitution(
    gold_filter: FilterAtom,
    generated_filter: FilterAtom,
) -> bool:
    if not _is_transaction_type_filter(gold_filter) or not _is_transaction_type_filter(generated_filter):
        return False
    gold_concepts = _mapped_filter_concepts(gold_filter)
    generated_concepts = _mapped_filter_concepts(generated_filter)
    return (
        len(gold_concepts) == 1
        and len(generated_concepts) == 1
        and gold_concepts | generated_concepts == INVOICE_BILL_CONCEPTS
    )


def _is_transaction_type_filter(filter_atom: FilterAtom) -> bool:
    return any(
        _norm(annotation.get("semantic_role")) == TRANSACTION_TYPE_ROLE
        for annotation in filter_atom.annotations
    )


def _mapped_filter_concepts(filter_atom: FilterAtom) -> set[str]:
    concepts: set[str] = set()
    for value_semantic in filter_atom.value_semantics:
        if value_semantic.get("value_status") not in {"exact_match", "normalised_match"}:
            continue
        concept = _norm(value_semantic.get("concept"))
        if concept:
            concepts.add(concept)
    return concepts


def _classify_atom_pair(
    gold: OutputAtom,
    generated: OutputAtom,
    schema_store: SchemaAnnotationStore,
) -> tuple[str | None, str]:
    if _atoms_equivalent(gold, generated):
        return None, "equivalent_financial_output"
    if _is_balance_count_or_status_proxy(gold, generated):
        return HARD, "balance_replaced_by_count_or_status_proxy"
    if _is_rate_as_total_amount_substitution(gold, generated):
        return HARD, "rate_as_total_amount_substitution"
    if _posting_side_mismatch(gold, generated):
        return HARD, "posting_side_reversal"
    if _classes_incompatible(gold.classes, generated.classes):
        return HARD, "incompatible_financial_output"
    if generated.semantic_kind in {"non_financial", "status", "count"}:
        if generated.semantic_kind == "count" and gold.semantic_kind == "quantity":
            return WARNING, "aggregation_or_grain_error"
        if generated.semantic_kind == "count" and gold.semantic_kind in {"monetary", "financial"}:
            return WARNING, "aggregation_or_grain_error"
        return WARNING, "output_shape_warning"
    if _is_stock_balance_reconstruction(gold, generated, schema_store):
        return None, "deterministic_balance_reconstruction"
    if _is_balance_stock_replaced_by_flow_amount(gold, generated):
        return HARD, "balance_stock_replaced_by_flow_amount"
    if _is_stock_to_plausible_flow(gold, generated):
        return WARNING, "financial_measure_mismatch"
    if _function_changed_softly(gold, generated):
        return WARNING, "aggregation_or_grain_error"
    if _resolved_posting_side_replaced_by_unresolved_amount(gold, generated):
        return WARNING, "unresolved_measure_warning"
    if gold.semantic_kind != generated.semantic_kind or gold.measure_type != generated.measure_type:
        return WARNING, "financial_measure_mismatch"
    return WARNING, "financial_measure_mismatch"


def _atoms_equivalent(gold: OutputAtom, generated: OutputAtom) -> bool:
    return (
        gold.semantic_kind == generated.semantic_kind
        and _normalise_function(gold.function) == _normalise_function(generated.function)
        and gold.measure_type == generated.measure_type
        and gold.unit == generated.unit
        and gold.financial_element == generated.financial_element
        and gold.domain_object == generated.domain_object
        and gold.signed_posting_side == generated.signed_posting_side
        and gold.classes == generated.classes
    )


def _best_generated_match(gold: OutputAtom, generated_atoms: list[OutputAtom]) -> OutputAtom | None:
    if not generated_atoms:
        return None
    same_column = [
        atom
        for atom in generated_atoms
        if atom.column and gold.column and atom.column.lower() == gold.column.lower()
    ]
    if same_column:
        return same_column[0]
    same_kind = [atom for atom in generated_atoms if atom.semantic_kind == gold.semantic_kind]
    if same_kind:
        return same_kind[0]
    return generated_atoms[0]


def _is_incompatible(gold: OutputAtom, generated: OutputAtom) -> bool:
    if _posting_side_mismatch(gold, generated):
        return True
    if _classes_incompatible(gold.classes, generated.classes):
        return True
    if gold.measure_type == "stock" and _has_any_class(generated.classes, INCOME_LIKE | EXPENSE_LIKE):
        return True
    return False


def _posting_side_mismatch(gold: OutputAtom, generated: OutputAtom) -> bool:
    if gold.signed_posting_side and generated.signed_posting_side:
        return gold.signed_posting_side != generated.signed_posting_side
    if gold.posting_side in {"credit", "debit"} and generated.posting_side in {"credit", "debit"}:
        return gold.posting_side != generated.posting_side
    return False


def _classes_incompatible(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return False
    for pair in OPPOSITE_CLASSES:
        if left & pair and right & pair and not (left & right):
            return True
    if _has_any_class(left, ASSET_LIKE) and _has_any_class(right, LIABILITY_LIKE):
        return True
    if _has_any_class(left, LIABILITY_LIKE) and _has_any_class(right, ASSET_LIKE):
        return True
    if _has_any_class(left, INCOME_LIKE) and _has_any_class(right, EXPENSE_LIKE):
        return True
    if _has_any_class(left, EXPENSE_LIKE) and _has_any_class(right, INCOME_LIKE):
        return True
    if _has_any_class(left, ASSET_LIKE | LIABILITY_LIKE) and _has_any_class(right, INCOME_LIKE | EXPENSE_LIKE):
        return True
    if _has_any_class(left, INCOME_LIKE | EXPENSE_LIKE) and _has_any_class(right, ASSET_LIKE | LIABILITY_LIKE):
        return True
    return False


def _is_balance_count_or_status_proxy(gold: OutputAtom, generated: OutputAtom) -> bool:
    return _is_monetary_balance_or_stock(gold) and generated.semantic_kind in {"count", "status"}


def _is_monetary_balance_or_stock(atom: OutputAtom) -> bool:
    if atom.semantic_kind not in {"monetary", "financial"}:
        return False
    if atom.unit not in MONEY_UNITS and atom.measure_type != "stock":
        return False
    column_name = normalise_value(atom.column or "")
    return atom.measure_type == "stock" or atom.financial_role == "balance" or "balance" in column_name


def _is_rate_as_total_amount_substitution(gold: OutputAtom, generated: OutputAtom) -> bool:
    return _is_total_monetary_flow_sum(gold) and _is_rate_answer_measure(generated)


def _is_total_monetary_flow_sum(atom: OutputAtom) -> bool:
    if atom.function not in SUM_FUNCTIONS:
        return False
    if atom.measure_type != "flow" or atom.semantic_kind not in {"monetary", "financial"}:
        return False
    # Amount may trigger only the rate_as_total_amount_substitution rule because
    # this rule compares total monetary amount vs unit rate, not debit/credit direction.
    return normalise_value(atom.column or "") in {"credit", "debit", "amount"}


def _is_rate_answer_measure(atom: OutputAtom) -> bool:
    return (
        atom.semantic_kind == "rate"
        or atom.measure_type == "rate"
        or atom.unit in RATE_UNITS
        or any(_norm(annotation.get("semantic_role")) in RATE_ROLES for annotation in atom.annotations)
    ) and atom.function in {None, "sum", "average"}


def _is_balance_stock_replaced_by_flow_amount(gold: OutputAtom, generated: OutputAtom) -> bool:
    return (
        _is_direct_selected_measure(gold)
        and _is_direct_selected_measure(generated)
        and _is_monetary_balance_or_stock(gold)
        and generated.measure_type == "flow"
        and generated.semantic_kind in {"monetary", "financial"}
        and normalise_value(generated.column or "") in {"credit", "debit", "amount"}
    )


def _is_direct_selected_measure(atom: OutputAtom) -> bool:
    return atom.expression_sql == atom.expression


def _function_changed_softly(gold: OutputAtom, generated: OutputAtom) -> bool:
    return (
        gold.semantic_kind in {"monetary", "financial", "rate"}
        and generated.semantic_kind in {"monetary", "financial", "rate"}
        and {gold.function, generated.function} & SUM_FUNCTIONS
        and {gold.function, generated.function} & AVG_FUNCTIONS
    )


def _resolved_posting_side_replaced_by_unresolved_amount(gold: OutputAtom, generated: OutputAtom) -> bool:
    return (
        gold.semantic_kind in {"monetary", "financial"}
        and generated.semantic_kind in {"monetary", "financial"}
        and bool(gold.posting_side)
        and not generated.posting_side
    ) or (
        generated.semantic_kind in {"monetary", "financial"}
        and gold.semantic_kind in {"monetary", "financial"}
        and bool(generated.posting_side)
        and not gold.posting_side
    )


def _is_stock_to_plausible_flow(gold: OutputAtom, generated: OutputAtom) -> bool:
    return (
        gold.measure_type == "stock"
        and generated.measure_type == "flow"
        and generated.semantic_kind in {"monetary", "financial"}
    )


def _is_stock_balance_reconstruction(
    gold: OutputAtom,
    generated: OutputAtom,
    schema_store: SchemaAnnotationStore,
) -> bool:
    if not _is_stock_to_plausible_flow(gold, generated):
        return False
    metadata = schema_store.get_schema_metadata()
    if not metadata.get("full_history_balance_reconstruction_supported"):
        return False
    if not metadata.get("as_of_balance_reconstruction_supported"):
        return False
    if not gold.classes or _classes_incompatible(gold.classes, generated.classes):
        return False

    if not generated.formula_functions <= SUM_FUNCTIONS:
        return False
    sides = generated.formula_signed_posting_sides
    if _has_any_class(gold.classes, ASSET_LIKE):
        return sides == {"+debit", "-credit"}
    if _has_any_class(gold.classes, LIABILITY_LIKE):
        return sides == {"+credit", "-debit"}
    return False


def _attach_formula_context(atoms: list[OutputAtom], expression_index: int) -> None:
    peers = [atom for atom in atoms if atom.expression_index == expression_index]
    sides = {
        atom.signed_posting_side
        for atom in peers
        if atom.signed_posting_side in {"+debit", "-debit", "+credit", "-credit"}
    }
    functions = {atom.function for atom in peers if atom.function}
    for atom in peers:
        atom.formula_signed_posting_sides = set(sides)
        atom.formula_functions = set(functions)


def _not_evaluable_reasons(bundle: Bundle, side: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if bundle.parsed.parse_error:
        findings.append(
            _finding(
                NOT_EVALUABLE,
                "parse_failure",
                f"{side} SQL could not be parsed.",
                parse_error=bundle.parsed.parse_error,
            )
        )
    if bundle.parsed.unsupported_lineage and _bundle_has_financial_content(bundle):
        findings.append(
            _finding(
                NOT_EVALUABLE,
                "unsupported_finance_bearing_lineage",
                f"{side} SQL uses unsupported lineage needed for financial comparison.",
            )
        )
    for expression in bundle.unsupported_financial_expressions:
        findings.append(
            _finding(
                NOT_EVALUABLE,
                "unsupported_finance_bearing_expression",
                f"{side} SQL uses an unsupported finance-bearing expression.",
                expression=expression,
            )
        )
    if bundle.missing_financial_annotations:
        findings.append(
            _finding(
                NOT_EVALUABLE,
                "missing_financial_annotation",
                f"{side} SQL references a financial-looking field without schema annotation.",
                expressions=bundle.missing_financial_annotations,
            )
        )
    return findings


def _bundle_has_financial_content(bundle: Bundle) -> bool:
    return any(atom.is_finance_bearing for atom in bundle.select_atoms) or any(
        atom.is_finance_bearing for atom in bundle.filter_atoms
    )


def _missing_financial_filter_annotations(
    parsed: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> list[str]:
    missing = []
    for filter_ref in parsed.filters:
        for column_ref in filter_ref.columns:
            if _has_financial_name_hint(column_ref.column) and not _annotations(column_ref, parsed, schema_store):
                missing.append(filter_ref.expression)
    return missing


def _expression_has_financial_content(
    expression: exp.Expression,
    parsed: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> bool:
    for column in expression.find_all(exp.Column):
        column_ref = _column_ref(column, parsed.aliases)
        annotations = _annotations(column_ref, parsed, schema_store)
        if not annotations and _has_financial_name_hint(column_ref.column):
            return True
        for annotation in annotations:
            if _annotation_is_finance_bearing(annotation):
                return True
    return False


def _annotation_is_finance_bearing(annotation: dict[str, Any]) -> bool:
    role = _norm(annotation.get("semantic_role"))
    measure_type = _norm(annotation.get("measure_type"))
    unit = _norm(annotation.get("unit"))
    return (
        role in FINANCIAL_ROLES | QUANTITY_ROLES | RATE_ROLES
        or measure_type in {"flow", "stock", "quantity", "rate"}
        or unit in MONEY_UNITS | RATE_UNITS
        or bool(annotation.get("financial_role"))
        or bool(annotation.get("financial_element"))
    )


def _annotations(
    column_ref: ColumnRef,
    parsed: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> list[dict[str, Any]]:
    return schema_store.annotate_column_reference(
        column=column_ref.column,
        table=column_ref.table,
        candidate_tables=parsed.tables,
    )


def _column_ref(column: exp.Column, aliases: dict[str, str]) -> ColumnRef:
    table = column.table or None
    if table is not None:
        table = aliases.get(table, table)
    return ColumnRef(column=column.name, table=table)


def _function_name(expression: exp.Expression) -> str:
    name = expression.key or expression.__class__.__name__
    return _normalise_function(str(name))


def _normalise_function(function: str | None) -> str | None:
    if function is None:
        return None
    function = function.lower()
    if function == "avg":
        return "average"
    return function


def _is_count_expression(expression: exp.Expression) -> bool:
    return isinstance(expression, exp.Count) or _function_name(expression) == "count"


def _classes_from_annotation(annotation: dict[str, Any]) -> set[str]:
    classes = {
        _norm(annotation.get("financial_element")),
        _norm(annotation.get("domain_object")),
        _norm(annotation.get("financial_role")),
    }
    return {item for item in classes if item}


def _classes_from_value_semantic(value_semantic: dict[str, Any]) -> set[str]:
    metadata = value_semantic.get("concept_metadata") or {}
    classes = {
        _norm(value_semantic.get("concept")),
        _norm(metadata.get("financial_element")),
        _norm(metadata.get("domain_object")),
        _norm(metadata.get("financial_statement")),
    }
    return {item for item in classes if item}


def _has_any_class(classes: set[str], candidates: set[str]) -> bool:
    return bool(classes & candidates)


def _has_financial_name_hint(column: str | None) -> bool:
    normalised = normalise_value(column or "")
    parts = set(normalised.replace("-", "_").split("_"))
    return normalised in FINANCIAL_NAME_HINTS or bool(parts & FINANCIAL_NAME_HINTS)


def _norm(value: Any) -> str | None:
    normalised = normalise_value(value)
    return normalised or None


def _finding(status: str, code: str, message: str, **evidence: Any) -> dict[str, Any]:
    return {
        "status": status,
        "code": code,
        "message": message,
        "evidence": {key: value for key, value in evidence.items() if value not in (None, [], {})},
    }


def _warning(code: str, message: str, **evidence: Any) -> dict[str, Any]:
    return _finding(WARNING, code, message, **evidence)


def _message_for_reason(reason: str) -> str:
    return {
        "balance_replaced_by_count_or_status_proxy": "Generated SQL replaces a monetary balance/stock measure with a count or status proxy.",
        "rate_as_total_amount_substitution": "Generated SQL replaces a total monetary amount with a unit rate measure.",
        "invoice_bill_transaction_type_substitution": "Generated SQL substitutes invoice and bill transaction-type filters.",
        "balance_stock_replaced_by_flow_amount": "Generated SQL replaces a point-in-time balance/stock measure with a transaction-level flow amount.",
        "posting_side_reversal": "Generated SQL reverses debit/credit posting-side meaning.",
        "incompatible_financial_output": "Generated SQL uses an incompatible financial selected output.",
        "aggregation_or_grain_error": "Generated SQL changes aggregation or analytical grain.",
        "financial_measure_mismatch": "Generated SQL changes financial measure semantics without a deterministic ontology contradiction.",
        "output_shape_warning": "Generated SQL changes selected output shape without a deterministic ontology contradiction.",
        "financial_scope_warning": "Generated SQL changes financial scope without a deterministic ontology contradiction.",
        "unresolved_measure_warning": "Generated SQL uses unresolved or broader financial measure meaning.",
    }.get(reason, reason)
