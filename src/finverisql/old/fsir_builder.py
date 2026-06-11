"""Build the Financial Semantic Intermediate Representation (FSIR).

This module converts `SQLFinancialSemantics` into the verifier-facing JSON
profile used by FinVeriSQL. The FSIR describes what candidate SQL appears to
compute through a financial concept layer, measurement layer, and reporting
topology layer.

Main input is `SQLFinancialSemantics` from `sql_semantic_mapping.py`. Main
outputs are the FSIR dictionary from `build_fsir` and deterministic verifier JSON
from `render_fsir_for_verifier`. The FSIR is descriptive, not evaluative: it
does not inspect the question, gold SQL, or execution result.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from ..sql_semantic_mapping import (
    AnnotatedColumnUse,
    AnnotatedFilterCondition,
    SQLFinancialSemantics,
)


_EMPTY_VALUES = {None, "", "none", "null", "n/a", "unknown"}
DATE_ROLES = {
    "transaction_date",
    "due_date",
    "system_created_date",
    "date_field",
}

TEMPORAL_SOURCE_DIALECT = "sqlite"
TEMPORAL_PARSER_SCOPE = "sqlite_date_arithmetic"
TEMPORAL_REPRESENTATION_LEVEL = "symbolic_temporal_boundary"

_CALENDAR_PERIOD_REGISTRY = {
    # Prior month interval:
    # date(current_date, 'start of month', '-1 months')
    # to date(current_date, 'start of month', '-1 days')
    (
        "start_of_current_month",
        ("-1 month",),
        "start_of_current_month",
        ("-1 day",),
    ): "prior_month",

    # Current month to date:
    # date(current_date, 'start of month') to current_date
    (
        "start_of_current_month",
        (),
        "current_date",
        (),
    ): "current_month_to_date",

    # Current year to date:
    # date(current_date, 'start of year') to current_date
    (
        "start_of_current_year",
        (),
        "current_date",
        (),
    ): "current_year_to_date",

    # Prior calendar year:
    # date(current_date, 'start of year', '-1 year')
    # to date(current_date, 'start of year', '-1 day')
    (
        "start_of_current_year",
        ("-1 year",),
        "start_of_current_year",
        ("-1 day",),
    ): "prior_calendar_year",
}


# Basic helpers
def _clean_value(value: Any) -> str:
    if value in _EMPTY_VALUES:
        return ""

    return str(value).strip().strip("'").strip('"')


def _clean_sql_string(value: Any) -> str:
    return str(value).strip().strip("'").strip('"').strip()


def _normalise_values(values: list[Any] | None) -> list[str]:
    cleaned: list[str] = []

    for value in values or []:
        text = _clean_value(value)

        if text:
            cleaned.append(text)

    return _unique(cleaned)


def _unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()

    for value in values:
        key = json.dumps(value, sort_keys=True, default=str)

        if key not in seen:
            seen.add(key)
            result.append(value)

    return result


def _qualified_column(table: str | None, column: str | None) -> str | None:
    if not column:
        return None

    if table:
        return f"{table}.{column}"

    return column


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], "unknown"):
            return value

    return None


def _safe_lower(value: Any) -> str:
    return str(value or "").lower()


def _looks_like_absolute_date(value: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", str(value).strip()))


def _get_annotation_values(
    annotations: list[dict[str, Any]],
    key: str,
) -> list[str]:
    values: list[str] = []

    for annotation in annotations or []:
        value = annotation.get(key)

        if value in _EMPTY_VALUES or value == []:
            continue

        if isinstance(value, list):
            values.extend(_clean_value(item) for item in value if _clean_value(item))
        else:
            cleaned = _clean_value(value)
            if cleaned:
                values.append(cleaned)

    return _unique(values)


def _first_annotation_value(
    annotations: list[dict[str, Any]],
    key: str,
    default: str | None = "unknown",
) -> str | None:
    values = _get_annotation_values(annotations, key)
    return values[0] if values else default


def _mapping_status(column_use: AnnotatedColumnUse | None) -> str:
    if column_use is None:
        return "unmapped"

    if column_use.is_ambiguous:
        return "ambiguous"

    if not column_use.annotations:
        return "unmapped"

    return "mapped"


def _derivation_source(column_use: AnnotatedColumnUse | None) -> str:
    if column_use is None:
        return "unmapped"

    if column_use.annotations:
        return "schema_column_annotation"

    if column_use.column:
        return "column_name_heuristic"

    if column_use.expression:
        return "expression_pattern"

    return "unmapped"


# Measurement layer
def _build_measurements(semantics: SQLFinancialSemantics) -> list[dict[str, Any]]:
    """
    Build object-level measurement records.

    Important:
    - This describes the physical computation used by SQL.
    - It does not infer the expected measure from the user question.
    - column_normal_balance is metadata about the selected physical column,
      not a judgement that the query uses the correct financial sign.
    """
    measurements: list[dict[str, Any]] = []

    aggregated_groups = _group_column_uses_by_expression(
        semantics.measure_usage.aggregated_columns
    )

    for index, column_uses in enumerate(aggregated_groups, start=1):
        measurements.append(
            _measurement_from_column_uses(
                column_uses=column_uses,
                measurement_id=f"m{index}",
                default_source="aggregation",
            )
        )

    if measurements:
        return measurements

    selected_groups = _group_column_uses_by_expression(
        semantics.measure_usage.selected_columns
    )

    for index, column_uses in enumerate(selected_groups, start=1):
        measurements.append(
            _measurement_from_column_uses(
                column_uses=column_uses,
                measurement_id=f"m{index}",
                default_source="select",
            )
        )

    return measurements


def _group_column_uses_by_expression(
    column_uses: list[AnnotatedColumnUse],
) -> list[list[AnnotatedColumnUse]]:
    grouped: dict[tuple[str, str, str], list[AnnotatedColumnUse]] = defaultdict(list)

    for column_use in column_uses:
        key = (
            column_use.source or "unknown",
            column_use.expression or _qualified_column(column_use.table, column_use.column) or "",
            column_use.function or "NONE",
        )
        grouped[key].append(column_use)

    return list(grouped.values())


def _measurement_from_column_uses(
    column_uses: list[AnnotatedColumnUse],
    measurement_id: str,
    default_source: str,
) -> dict[str, Any]:
    primary = column_uses[0] if column_uses else None
    expression = primary.expression if primary else None
    function_name = (primary.function if primary else None) or "NONE"

    components = [
        _measure_component_from_column_use(
            column_use=column_use,
            component_id=f"{measurement_id}_c{component_index}",
            parent_expression=expression,
        )
        for component_index, column_use in enumerate(column_uses, start=1)
    ]

    conditional_modifiers = _extract_conditional_modifiers(expression)
    expression_type = _infer_metric_expression_type(
        expression=expression,
        function_name=function_name,
        components=components,
        default_source=default_source,
    )

    return {
        "measurement_id": measurement_id,
        "metric_alias": None,
        "metric_expression": {
            "expression_type": expression_type,
            "raw_expression": expression,
            "components": components,
            "conditional_modifiers": conditional_modifiers,
        },
        "source": default_source,
        "mapping_status": _combined_mapping_status(components),
    }


def _measure_component_from_column_use(
    column_use: AnnotatedColumnUse,
    component_id: str,
    parent_expression: str | None,
) -> dict[str, Any]:
    annotations = column_use.annotations or []
    column = _qualified_column(column_use.table, column_use.column)
    aggregation_function = (column_use.function or "NONE").upper()

    return {
        "component_id": component_id,
        "column": column,
        "aggregation_function": aggregation_function,
        "extracted_vector": _infer_extracted_vector(column_use),
        "column_normal_balance": _first_annotation_value(
            annotations,
            "sign_convention",
            default="unknown",
        ),
        "measure_type": _infer_measure_type(column_use),
        "measure_family": _infer_measure_family(column_use),
        "semantic_role": _first_annotation_value(
            annotations,
            "semantic_role",
            default="unknown",
        ),
        "unit": _infer_unit(column_use),
        "algebraic_sign": _infer_algebraic_sign(
            column=column_use.column,
            expression=parent_expression,
        ),
        "derivation_source": _derivation_source(column_use),
        "mapping_status": _mapping_status(column_use),
    }


def _infer_metric_expression_type(
    expression: str | None,
    function_name: str,
    components: list[dict[str, Any]],
    default_source: str,
) -> str:
    lowered = _safe_lower(expression)
    function_lower = _safe_lower(function_name)

    if function_lower == "count" or "count(*)" in lowered:
        return "row_count"

    if default_source == "select" and function_lower in {"", "none"}:
        return "raw_column_selection"

    if len(components) > 1:
        return "composite_expression"

    if _contains_arithmetic_expression(lowered):
        return "composite_expression"

    return "single_column_aggregate"


def _contains_arithmetic_expression(expression: str) -> bool:
    if not expression:
        return False

    # Avoid treating SQLite date modifiers such as '-1 month' as financial arithmetic.
    cleaned = re.sub(r"date\s*\(.*?\)", "", expression, flags=re.IGNORECASE)
    return bool(re.search(r"[A-Za-z_)0-9]\s*[+\-*/]\s*[A-Za-z_(0-9]", cleaned))


def _infer_extracted_vector(column_use: AnnotatedColumnUse) -> str:
    column_name = _safe_lower(column_use.column)
    expression = _safe_lower(column_use.expression)
    function_name = _safe_lower(column_use.function)

    if function_name == "count" or "count(*)" in expression:
        return "row_count"

    if column_name == "credit":
        return "credit"

    if column_name == "debit":
        return "debit"

    if column_name in {"amount", "total_amount", "txn_amount"}:
        return "raw_numeric"

    if column_name in {"balance", "open_balance", "ending_balance"}:
        return "balance"

    if column_name in {"quantity", "qty"}:
        return "quantity"

    semantic_roles = set(_get_annotation_values(column_use.annotations, "semantic_role"))
    measure_types = set(_get_annotation_values(column_use.annotations, "measure_type"))

    if "quantity_measure" in semantic_roles or "quantity" in measure_types:
        return "quantity"

    if "financial_measure" in semantic_roles:
        return "raw_numeric"

    return "unknown"


def _infer_measure_type(column_use: AnnotatedColumnUse) -> str:
    annotations = column_use.annotations or []
    measure_type = _first_annotation_value(annotations, "measure_type", default=None)

    if measure_type:
        return measure_type

    extracted_vector = _infer_extracted_vector(column_use)

    if extracted_vector in {"credit", "debit", "raw_numeric"}:
        return "flow"

    if extracted_vector == "balance":
        return "stock"

    if extracted_vector == "quantity":
        return "quantity"

    if extracted_vector == "row_count":
        return "count"

    return "unknown"


def _infer_measure_family(column_use: AnnotatedColumnUse) -> str:
    extracted_vector = _infer_extracted_vector(column_use)
    units = set(_get_annotation_values(column_use.annotations, "unit"))

    if extracted_vector == "row_count":
        return "row_count"

    if extracted_vector == "quantity":
        return "quantity"

    if extracted_vector == "balance":
        return "balance"

    if extracted_vector in {"credit", "debit", "raw_numeric"}:
        if "ratio" in units:
            return "ratio"
        return "monetary_amount" if ("monetary" in units or "currency" in units or not units) else "numeric_measure"

    return "unknown"


def _infer_unit(column_use: AnnotatedColumnUse) -> str:
    units = set(_get_annotation_values(column_use.annotations, "unit"))
    extracted_vector = _infer_extracted_vector(column_use)

    if "monetary" in units or "currency" in units:
        return "currency"

    if "ratio" in units:
        return "ratio"

    if extracted_vector == "quantity":
        return "units"

    if extracted_vector == "row_count":
        return "records"

    if units:
        return sorted(units)[0]

    if extracted_vector in {"credit", "debit", "raw_numeric", "balance"}:
        return "currency"

    return "unknown"


def _infer_algebraic_sign(column: str | None, expression: str | None) -> str:
    if not column or not expression:
        return "+"

    escaped_column = re.escape(column)

    negative_patterns = [
        rf"-\s*(?:[A-Za-z_][\w]*\.)?{escaped_column}\b",
        rf"-\s*[A-Za-z_][\w]*\s*\(\s*(?:[A-Za-z_][\w]*\.)?{escaped_column}\b",
    ]

    for pattern in negative_patterns:
        if re.search(pattern, expression, flags=re.IGNORECASE):
            return "-"

    return "+"


def _combined_mapping_status(components: list[dict[str, Any]]) -> str:
    statuses = {component.get("mapping_status") for component in components}

    if "ambiguous" in statuses:
        return "ambiguous"

    if statuses == {"mapped"}:
        return "mapped"

    if "mapped" in statuses:
        return "partially_mapped"

    return "unmapped"


def _extract_conditional_modifiers(expression: str | None) -> list[dict[str, Any]]:
    """
    V0 heuristic for CASE WHEN expressions.

    This preserves descriptive SQL behaviour without judging whether the
    condition is correct for the user's question.
    """
    if not expression:
        return []

    lowered = expression.lower()

    if "case" not in lowered or "when" not in lowered:
        return []

    case_match = re.search(
        r"case\s+when\s+(.*?)\s+then\s+(.*?)\s+(?:else\s+(.*?))?\s*end",
        expression,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if not case_match:
        return [
            {
                "condition_expression": "CASE expression detected but not normalized",
                "then_expression_summary": None,
                "else_expression_summary": None,
                "algebraic_impact": "conditional_transformation",
                "normalization_status": "partially_normalized",
            }
        ]

    condition = " ".join(case_match.group(1).split())
    then_expr = " ".join((case_match.group(2) or "").split())
    else_expr = " ".join((case_match.group(3) or "").split())

    algebraic_impact = "conditional_transformation"

    if re.search(r"(^|\W)-\s*[A-Za-z_]", then_expr) or re.search(r"(^|\W)-\s*\d", then_expr):
        algebraic_impact = "invert_sign_polarity"

    elif else_expr in {"0", "0.0"}:
        algebraic_impact = "include_only"

    parsed_condition = _parse_simple_condition_expression(condition)

    return [
        {
            "condition_expression": condition,
            "condition_scope_role": parsed_condition.get("scope_role"),
            "condition_column": parsed_condition.get("mapped_column"),
            "condition_operator": parsed_condition.get("operator"),
            "condition_values": parsed_condition.get("values", []),
            "then_expression_summary": then_expr or None,
            "else_expression_summary": else_expr or None,
            "algebraic_impact": algebraic_impact,
            "normalization_status": "partially_normalized",
        }
    ]


# Financial concept layer
def _build_financial_concept_layer(
    semantics: SQLFinancialSemantics,
    measurement_ids: list[str],
) -> dict[str, Any]:
    constraints = _build_scope_constraints(
        semantics=semantics,
        measurement_ids=measurement_ids,
    )

    return {
        "scope_constraints": constraints,
        "scope_coverage": _build_scope_coverage(
            semantics=semantics,
            constraints=constraints,
        ),
    }

def _build_scope_summary(constraints: list[dict[str, Any]]) -> dict[str, Any]:
    present_scope_roles = []
    present_financial_classes = []
    present_transaction_events = []
    present_payment_statuses = []
    present_entity_roles = []

    for constraint in constraints:
        role = constraint.get("scope_role")
        if role:
            present_scope_roles.append(role)

        for value in constraint.get("derived_financial_classes") or []:
            present_financial_classes.append(value)

        for value in constraint.get("mapped_concepts") or []:
            if role == "transaction_event":
                present_transaction_events.append(value)
            elif role == "payment_status":
                present_payment_statuses.append(value)

        if role in {"customer", "vendor", "employee", "product_service", "account"}:
            present_entity_roles.append(role)

    return {
        "present_scope_roles": _unique(present_scope_roles),
        "present_financial_classes": _unique(present_financial_classes),
        "present_transaction_events": _unique(present_transaction_events),
        "present_payment_statuses": _unique(present_payment_statuses),
        "present_entity_roles": _unique(present_entity_roles),
    }


def _build_measurement_summary(measurements: list[dict[str, Any]]) -> dict[str, Any]:
    measure_families = []
    extracted_vectors = []
    aggregation_functions = []
    expression_types = []

    for measurement in measurements:
        metric_expression = measurement.get("metric_expression") or {}
        expression_type = metric_expression.get("expression_type")
        if expression_type:
            expression_types.append(expression_type)

        for component in metric_expression.get("components") or []:
            if component.get("measure_family"):
                measure_families.append(component["measure_family"])
            if component.get("extracted_vector"):
                extracted_vectors.append(component["extracted_vector"])
            if component.get("aggregation_function"):
                aggregation_functions.append(component["aggregation_function"])

    return {
        "present_measure_families": _unique(measure_families),
        "present_extracted_vectors": _unique(extracted_vectors),
        "present_aggregation_functions": _unique(aggregation_functions),
        "present_expression_types": _unique(expression_types),
    }


def _build_topology_summary(
    grouping_dimensions: list[dict[str, Any]],
    temporal_resolution: dict[str, Any],
) -> dict[str, Any]:
    grouping_roles = [
        dimension.get("grouping_role")
        for dimension in grouping_dimensions
        if dimension.get("grouping_role")
    ]

    date_predicates = temporal_resolution.get("date_predicates") or []

    temporal_filter_labels = [
        predicate.get("normalized_label")
        for predicate in date_predicates
        if predicate.get("normalized_label")
    ]

    return {
        "present_grouping_roles": _unique(grouping_roles),
        "has_temporal_grouping": "temporal_period" in grouping_roles,
        "has_temporal_filter": bool(date_predicates),
        "temporal_filter_labels": _unique(temporal_filter_labels),
    }

def _build_scope_constraints(
    semantics: SQLFinancialSemantics,
    measurement_ids: list[str],
) -> list[dict[str, Any]]:
    constraints: list[dict[str, Any]] = []
    scope_index = 1

    for condition in semantics.logic.filter_conditions:
        if condition.is_ambiguous:
            continue

        # Date predicates are represented in reporting_topology_layer.temporal_resolution.
        if _is_date_condition(condition):
            continue

        first_column = _first_condition_column(condition)
        mapped_column = _qualified_column(first_column.table, first_column.column) if first_column else None
        mapped_concepts = _normalise_values(condition.concepts)
        # Scope classes come from frozen schema/value annotations only. The
        # builder does not infer expected concepts from the question.
        derived_classes, class_source = _derive_scope_financial_classes(
            condition=condition,
            first_column=first_column,
        )

        constraints.append(
            {
                "scope_id": f"s{scope_index}",
                "scope_role": _scope_role_from_condition(condition),
                "mapped_column": mapped_column,
                "operator": condition.operator,
                "values": _normalise_values(condition.values),
                "mapped_concepts": mapped_concepts,
                "derived_financial_classes": derived_classes,
                "derivation_source": class_source,
                "enforcement_location": "global_filter",
                "applies_to_measurement_ids": measurement_ids,
                "condition_expression": condition.expression,
                "mapping_status": _mapping_status(first_column),
            }
        )

        scope_index += 1

    constraints.extend(
        _build_measurement_condition_scopes(
            semantics=semantics,
            start_index=scope_index,
        )
    )

    return constraints


def _derive_scope_financial_classes(
    condition: AnnotatedFilterCondition,
    first_column: AnnotatedColumnUse | None,
) -> tuple[list[str], str]:
    """
    Return financial classes/concepts exposed by frozen metadata.

    This is not correctness evaluation. It is provenance-labelled metadata
    lookup from value annotations and column annotations.
    """
    concepts = _normalise_values(condition.concepts)

    if concepts:
        return concepts, "schema_value_annotation"

    if first_column and first_column.annotations:
        candidate_keys = [
            "account_class",
            "account_type",
            "statement_family",
            "financial_statement",
        ]

        values: list[str] = []
        for key in candidate_keys:
            values.extend(_get_annotation_values(first_column.annotations, key))

        if values:
            return _unique(values), "schema_column_annotation"

    return [], "unmapped"


def _build_scope_coverage(
    semantics: SQLFinancialSemantics,
    constraints: list[dict[str, Any]],
) -> dict[str, Any]:
    ambiguous_count = sum(
        1
        for condition in semantics.logic.filter_conditions
        if condition.is_ambiguous and not _is_date_condition(condition)
    )

    if semantics.unsupported_lineage:
        status = "unknown_due_to_extraction_limit"
    elif ambiguous_count:
        status = "unknown_due_to_ambiguous_filter"
    elif constraints:
        status = "explicit_scope"
    else:
        status = "no_scope_in_sql"

    return {
        "has_scope_constraints": bool(constraints),
        "status": status,
        "ambiguous_scope_count": ambiguous_count,
        "note": "Absence of scope constraints means no non-temporal scope was extracted from the SQL, not that the question did not require one.",
    }


def _build_measurement_condition_scopes(
    semantics: SQLFinancialSemantics,
    start_index: int,
) -> list[dict[str, Any]]:
    """
    Extract CASE WHEN conditions as scope constraints enforced inside measurements.

    This lets the verifier treat equivalent financial scopes similarly even when
    one SQL uses WHERE and another uses CASE WHEN inside an aggregate.
    """
    constraints: list[dict[str, Any]] = []
    scope_index = start_index

    aggregated_groups = _group_column_uses_by_expression(
        semantics.measure_usage.aggregated_columns
    )

    for measurement_index, column_uses in enumerate(aggregated_groups, start=1):
        expression = column_uses[0].expression if column_uses else ""

        if "case" not in _safe_lower(expression):
            continue

        condition_match = re.search(
            r"case\s+when\s+(.*?)\s+then",
            expression or "",
            flags=re.IGNORECASE | re.DOTALL,
        )

        condition_expression = (
            " ".join(condition_match.group(1).split())
            if condition_match
            else "CASE condition detected but not normalized"
        )

        parsed_condition = _parse_simple_condition_expression(condition_expression)

        constraints.append(
            {
                "scope_id": f"s{scope_index}",
                "scope_role": parsed_condition.get("scope_role", "conditional_scope"),
                "mapped_column": parsed_condition.get("mapped_column"),
                "operator": parsed_condition.get("operator"),
                "values": parsed_condition.get("values", []),
                "mapped_concepts": [],
                "derived_financial_classes": [],
                "derivation_source": "case_condition_parser",
                "enforcement_location": "measurement_condition",
                "applies_to_measurement_ids": [f"m{measurement_index}"],
                "condition_expression": condition_expression,
                "mapping_status": "partially_mapped",
            }
        )

        scope_index += 1

    return constraints


def _parse_simple_condition_expression(condition_expression: str) -> dict[str, Any]:
    """
    Conservative parser for simple CASE WHEN conditions.

    Examples:
    account IN ('Income', 'Other Income')
    transaction_type = 'Credit Memo'
    open_balance > 0
    """
    text = condition_expression.strip()

    in_match = re.search(
        r"(?i)\b([A-Za-z_][\w.]*)\s+in\s*\((.*?)\)",
        text,
    )

    if in_match:
        column = in_match.group(1)
        values = _split_function_args(in_match.group(2))
        cleaned_values = [_clean_value(value) for value in values if _clean_value(value)]

        return {
            "scope_role": _scope_role_from_column_name(column),
            "mapped_column": column,
            "operator": "IN",
            "values": cleaned_values,
        }

    comparison_match = re.search(
        r"(?i)\b([A-Za-z_][\w.]*)\s*(=|!=|<>|>|>=|<|<=|like)\s*('.*?'|\".*?\"|[A-Za-z0-9_.-]+)",
        text,
    )

    if comparison_match:
        column = comparison_match.group(1)
        operator = comparison_match.group(2).upper()
        value = _clean_value(comparison_match.group(3))

        return {
            "scope_role": _scope_role_from_column_name(column),
            "mapped_column": column,
            "operator": operator,
            "values": [value] if value else [],
        }

    return {
        "scope_role": "conditional_scope",
        "mapped_column": None,
        "operator": None,
        "values": [],
    }


def _scope_role_from_column_name(column_name: str) -> str:
    lowered = column_name.lower()

    if "account" in lowered:
        return "account"

    if "transaction_type" in lowered or lowered.endswith(".type") or lowered == "type":
        return "transaction_event"

    if "customer" in lowered:
        return "customer"

    if "vendor" in lowered:
        return "vendor"

    if "employee" in lowered:
        return "employee"

    if "product" in lowered or "service" in lowered:
        return "product_service"

    if "paid" in lowered or "payment" in lowered or "open_balance" in lowered:
        return "payment_status"
    
    if "payment_method" in lowered:
        return "payment_method"

    return "conditional_scope"


def _is_date_condition(condition: AnnotatedFilterCondition) -> bool:
    return any(role in DATE_ROLES for role in condition.semantic_roles or [])


def _scope_role_from_condition(condition: AnnotatedFilterCondition) -> str:
    roles = set(condition.semantic_roles or [])
    scopes = set(condition.entity_scopes or [])

    if "account_type_classifier" in roles:
        return "account"

    if "transaction_type_classifier" in roles:
        return "transaction_event"

    if "payment_status_flag" in roles:
        return "payment_status"

    if "customer" in scopes:
        return "customer"

    if "vendor" in scopes:
        return "vendor"

    if "employee" in scopes:
        return "employee"

    if "product_service" in scopes:
        return "product_service"

    if "account" in scopes:
        return "account"

    if "financial_measure" in roles:
        return "measure_filter"
    
    if "payment_method_identifier" in roles:
        return "payment_method"

    return "other"


def _first_condition_column(
    condition: AnnotatedFilterCondition,
) -> AnnotatedColumnUse | None:
    if not condition.columns:
        return None

    return condition.columns[0]


# Reporting topology layer
def _build_grouping_dimensions(
    semantics: SQLFinancialSemantics,
) -> list[dict[str, Any]]:
    dimensions: list[dict[str, Any]] = []

    for column_use in semantics.logic.group_by_columns:
        annotations = column_use.annotations or []

        grouping_role = _infer_grouping_role(column_use)
        temporal_grain = _first_annotation_value(
            annotations,
            "temporal_grain",
            default=None,
        )

        dimensions.append(
            {
                "grouping_role": grouping_role,
                "grouping_column": _qualified_column(column_use.table, column_use.column),
                "grain_role": "primary",
                "temporal_grain": temporal_grain,
                "derivation_source": _derivation_source(column_use),
                "mapping_status": _mapping_status(column_use),
            }
        )

    return dimensions


def _infer_grouping_role(column_use: AnnotatedColumnUse) -> str:
    annotations = column_use.annotations or []

    semantic_roles = set(_get_annotation_values(annotations, "semantic_role"))
    entity_scopes = set(_get_annotation_values(annotations, "entity_scope"))

    column_name = _safe_lower(column_use.column)
    expression = _safe_lower(column_use.expression)

    if column_use.is_ambiguous:
        return "ambiguous_column"

    if not annotations and column_use.column:
        if "customer" in column_name:
            return "customer"
        if "vendor" in column_name:
            return "vendor"
        if "employee" in column_name:
            return "employee"
        if "account" in column_name:
            return "account"
        if "product" in column_name or "service" in column_name:
            return "product_service"
        if "date" in column_name or "strftime" in expression:
            return "temporal_period"
        return "unannotated_column"

    if "transaction_date" in semantic_roles or "date_field" in semantic_roles:
        return "temporal_period"

    if "strftime" in expression:
        return "temporal_period"

    if "customer" in entity_scopes or "customer" in column_name:
        return "customer"

    if "vendor" in entity_scopes or "vendor" in column_name:
        return "vendor"

    if "employee" in entity_scopes or "employee" in column_name:
        return "employee"

    if "account" in entity_scopes or "account" in column_name:
        return "account"

    if "product_service" in entity_scopes or "product" in column_name or "service" in column_name:
        return "product_service"

    if "entity_identifier" in semantic_roles:
        return "entity"

    return "other"


def _derive_analytical_grain(
    semantics: SQLFinancialSemantics,
    grouping_dimensions: list[dict[str, Any]],
) -> str:
    """
    Analytical grain is a derived summary from grouping_dimensions.
    It is verifier-friendly but not the source of truth.
    """
    has_aggregation = bool(semantics.measure_usage.aggregated_columns)

    if not grouping_dimensions:
        return "global_summary" if has_aggregation else "raw_transaction"

    primary_roles = [
        item["grouping_role"]
        for item in grouping_dimensions
        if item.get("grain_role") == "primary"
    ]

    if not primary_roles:
        return "grouped_unknown"

    if len(primary_roles) == 1:
        return f"{primary_roles[0]}_level"

    return "_by_".join(primary_roles)


# Temporal resolution


def _build_temporal_resolution(
    semantics: SQLFinancialSemantics,
) -> dict[str, Any]:
    """
    Build a conservative symbolic temporal representation.

    Methodological note:
    - The physical parser currently targets SQLite date arithmetic because
      BookSQL is SQLite-based.
    - The FSIR representation itself is dialect-independent: temporal logic
      is decomposed into anchor, boundary, offsets, period grain, and
      normalization status.
    - Other SQL dialects can be supported later by adding dialect-specific
      physical parsers that map into this same symbolic structure.
    """
    if not semantics.logic.date_conditions:
        return {
            "source_dialect": TEMPORAL_SOURCE_DIALECT,
            "parser_scope": TEMPORAL_PARSER_SCOPE,
            "representation_level": TEMPORAL_REPRESENTATION_LEVEL,
            "date_predicates": [],
            "normalization_status": "no_temporal_filter",
            "extension_note": (
                "No temporal predicate was extracted from the SQL. Absence here means "
                "no schema-grounded date filter was found, not that the question did not require one."
            ),
        }

    date_predicates = [
        _normalise_temporal_predicate(condition)
        for condition in semantics.logic.date_conditions
    ]

    statuses = {
        predicate.get("normalization_status", "unknown")
        for predicate in date_predicates
    }

    if statuses == {"normalized"}:
        normalization_status = "normalized"
    elif "normalized" in statuses or "partially_normalized" in statuses:
        normalization_status = "partially_normalized"
    else:
        normalization_status = "unknown"

    return {
        "source_dialect": TEMPORAL_SOURCE_DIALECT,
        "parser_scope": TEMPORAL_PARSER_SCOPE,
        "representation_level": TEMPORAL_REPRESENTATION_LEVEL,
        "date_predicates": date_predicates,
        "normalization_status": normalization_status,
        "extension_note": (
            "Physical temporal parsing is currently implemented for SQLite date arithmetic. "
            "Extension to other SQL dialects should reuse the same symbolic boundary decomposition: "
            "anchor, boundary, offsets, period grain, and normalization status."
        ),
    }


def _normalise_temporal_predicate(
    condition: AnnotatedFilterCondition,
) -> dict[str, Any]:
    expression = condition.expression or ""
    operator = (condition.operator or "UNKNOWN").upper()

    first_column = _first_condition_column(condition)
    date_column = (
        _qualified_column(first_column.table, first_column.column)
        if first_column
        else None
    )

    lowered = expression.lower()

    # The temporal normalizer recognizes a small set of SQLite physical forms
    # and maps them to symbolic boundaries. Unknown forms remain explicit rather
    # than being forced into a calendar label.
    if operator == "BETWEEN" and len(condition.values) >= 2:
        return _normalise_between_temporal_predicate(
            condition=condition,
            date_column=date_column,
        )

    if "strftime" in lowered:
        return _normalise_strftime_temporal_predicate(
            condition=condition,
            date_column=date_column,
        )

    if operator in {">", ">=", "<", "<=", "="} and condition.values:
        boundary = _normalise_date_boundary(condition.values[0])

        return {
            "source_dialect": TEMPORAL_SOURCE_DIALECT,
            "physical_expression_family": boundary.get("physical_expression_family"),
            "normalization_rule_id": boundary.get("normalization_rule_id"),
            "normalization_rule_basis": "calendar_boundary_decomposition",
            "date_column": date_column,
            "predicate_type": _predicate_type_from_operator(operator),
            "evaluation_mode": "date_comparison",
            "anchor": boundary.get("anchor"),
            "start_boundary": boundary if operator in {">", ">=", "="} else None,
            "end_boundary": boundary if operator in {"<", "<=", "="} else None,
            "period_grain": boundary.get("period_grain", "unknown"),
            "normalized_label": None,
            "normalization_status": boundary.get("normalization_status", "unknown"),
            "raw_expression_summary": _summarise_temporal_expression(expression),
        }

    return {
        "source_dialect": TEMPORAL_SOURCE_DIALECT,
        "physical_expression_family": "unknown_temporal_expression",
        "normalization_rule_id": None,
        "normalization_rule_basis": "not_normalized",
        "date_column": date_column,
        "predicate_type": "UNKNOWN",
        "evaluation_mode": "unknown",
        "anchor": None,
        "start_boundary": None,
        "end_boundary": None,
        "period_grain": "unknown",
        "normalized_label": None,
        "normalization_status": "unknown",
        "raw_expression_summary": _summarise_temporal_expression(expression),
    }


def _normalise_between_temporal_predicate(
    condition: AnnotatedFilterCondition,
    date_column: str | None,
) -> dict[str, Any]:
    start_boundary = _normalise_date_boundary(condition.values[0])
    end_boundary = _normalise_date_boundary(condition.values[1])

    normalized_label = _infer_period_label_from_boundaries(
        start_boundary=start_boundary,
        end_boundary=end_boundary,
    )

    period_grain = _infer_period_grain_from_boundaries(
        start_boundary=start_boundary,
        end_boundary=end_boundary,
        normalized_label=normalized_label,
    )

    normalization_rule_id = _infer_temporal_rule_id(
        normalized_label=normalized_label,
        start_boundary=start_boundary,
        end_boundary=end_boundary,
    )

    # A registered label such as prior_month is verifier-friendly shorthand.
    # The raw start/end boundary objects remain in the FSIR as the source facts.
    if normalized_label is not None:
        normalization_status = "normalized"
        evaluation_mode = "relative_period"

    elif (
        start_boundary.get("normalization_status") != "unknown"
        or end_boundary.get("normalization_status") != "unknown"
    ):
        normalization_status = "partially_normalized"
        evaluation_mode = "absolute_or_relative_range"

    else:
        normalization_status = "unknown"
        evaluation_mode = "unknown"

    return {
        "source_dialect": TEMPORAL_SOURCE_DIALECT,
        "physical_expression_family": _infer_predicate_physical_expression_family(condition),
        "normalization_rule_id": normalization_rule_id,
        "normalization_rule_basis": _normalization_rule_basis(normalization_rule_id),
        "date_column": date_column,
        "predicate_type": "BETWEEN",
        "evaluation_mode": evaluation_mode,
        "anchor": _first_non_empty(
            start_boundary.get("anchor"),
            end_boundary.get("anchor"),
        ),
        "start_boundary": start_boundary,
        "end_boundary": end_boundary,
        "period_grain": period_grain,
        "normalized_label": normalized_label,
        "normalization_status": normalization_status,
        "raw_expression_summary": _summarise_temporal_expression(condition.expression),
    }


def _normalise_strftime_temporal_predicate(
    condition: AnnotatedFilterCondition,
    date_column: str | None,
) -> dict[str, Any]:
    expression = condition.expression or ""
    lowered = expression.lower()

    if "strftime" in lowered and "%y" in lowered:
        year_offset = _extract_integer_offset(lowered)

        if year_offset == -1:
            normalized_label = "prior_calendar_year"
            normalization_rule_id = "sqlite_strftime_prior_calendar_year_component"
            anchor = "current_date"
        elif year_offset == 0:
            normalized_label = "current_calendar_year"
            normalization_rule_id = "sqlite_strftime_current_calendar_year_component"
            anchor = "current_date"
        else:
            normalized_label = None
            normalization_rule_id = "sqlite_strftime_year_component_partial"
            anchor = "current_date" if "current_date" in lowered or "'now'" in lowered or '"now"' in lowered else None

        return {
            "source_dialect": TEMPORAL_SOURCE_DIALECT,
            "physical_expression_family": "sqlite_strftime_component_filter",
            "normalization_rule_id": normalization_rule_id,
            "normalization_rule_basis": "calendar_component_decomposition",
            "date_column": date_column,
            "predicate_type": "YEAR_EQUALS",
            "evaluation_mode": "calendar_component_filter",
            "anchor": anchor,
            "start_boundary": None,
            "end_boundary": None,
            "period_grain": "year",
            "normalized_label": normalized_label,
            "normalization_status": "partially_normalized",
            "raw_expression_summary": _summarise_temporal_expression(expression),
        }

    if "strftime" in lowered and "%m" in lowered:
        return {
            "source_dialect": TEMPORAL_SOURCE_DIALECT,
            "physical_expression_family": "sqlite_strftime_component_filter",
            "normalization_rule_id": "sqlite_strftime_month_component_partial",
            "normalization_rule_basis": "calendar_component_decomposition",
            "date_column": date_column,
            "predicate_type": "MONTH_EQUALS",
            "evaluation_mode": "calendar_component_filter",
            "anchor": "current_date" if "current_date" in lowered or "'now'" in lowered or '"now"' in lowered else None,
            "start_boundary": None,
            "end_boundary": None,
            "period_grain": "month",
            "normalized_label": None,
            "normalization_status": "partially_normalized",
            "raw_expression_summary": _summarise_temporal_expression(expression),
        }

    return {
        "source_dialect": TEMPORAL_SOURCE_DIALECT,
        "physical_expression_family": "sqlite_strftime_component_filter",
        "normalization_rule_id": "sqlite_strftime_unknown_component_partial",
        "normalization_rule_basis": "calendar_component_decomposition",
        "date_column": date_column,
        "predicate_type": "CALENDAR_COMPONENT_FILTER",
        "evaluation_mode": "calendar_component_filter",
        "anchor": "current_date" if "current_date" in lowered or "'now'" in lowered or '"now"' in lowered else None,
        "start_boundary": None,
        "end_boundary": None,
        "period_grain": "unknown",
        "normalized_label": None,
        "normalization_status": "partially_normalized",
        "raw_expression_summary": _summarise_temporal_expression(expression),
    }


def _normalise_date_boundary(value: Any) -> dict[str, Any]:
    raw = _clean_value(value)
    lowered = raw.lower()

    if not raw:
        return {
            "raw": None,
            "source_dialect": TEMPORAL_SOURCE_DIALECT,
            "physical_expression_family": "empty_boundary",
            "normalization_rule_id": None,
            "anchor": None,
            "base": None,
            "offsets": [],
            "period_grain": "unknown",
            "normalization_status": "unknown",
        }

    if lowered in {"current_date", "date('now')", 'date("now")'}:
        return {
            "raw": raw,
            "source_dialect": TEMPORAL_SOURCE_DIALECT,
            "physical_expression_family": "current_date_literal",
            "normalization_rule_id": "calendar_current_date_anchor",
            "anchor": "current_date",
            "base": "current_date",
            "offsets": [],
            "period_grain": "day",
            "normalization_status": "normalized",
        }

    parsed_date_call = _parse_sqlite_date_call(raw)

    if parsed_date_call is not None:
        return parsed_date_call

    if _looks_like_absolute_date(raw):
        return {
            "raw": raw,
            "source_dialect": TEMPORAL_SOURCE_DIALECT,
            "physical_expression_family": "absolute_date_literal",
            "normalization_rule_id": "absolute_date_boundary",
            "anchor": "absolute",
            "base": raw,
            "offsets": [],
            "period_grain": "day",
            "normalization_status": "normalized",
        }

    return {
        "raw": raw,
        "source_dialect": TEMPORAL_SOURCE_DIALECT,
        "physical_expression_family": "unknown_boundary_expression",
        "normalization_rule_id": None,
        "anchor": None,
        "base": None,
        "offsets": [],
        "period_grain": "unknown",
        "normalization_status": "unknown",
    }


def _parse_sqlite_date_call(raw: str) -> dict[str, Any] | None:
    """
    Conservative parser for SQLite DATE(...) expressions.

    This parser is SQLite-specific. The output representation is not:
    it decomposes the expression into symbolic calendar components.
    """
    match = re.match(r"(?i)date\s*\((.*)\)\s*$", raw.strip())

    if not match:
        return None

    args = _split_function_args(match.group(1))

    if not args:
        return None

    anchor = _normalise_temporal_anchor(args[0])
    modifiers = [_clean_sql_string(arg) for arg in args[1:]]

    base = anchor
    offsets: list[str] = []
    period_grain = "day"

    for modifier in modifiers:
        normalized_modifier = _normalise_sqlite_date_modifier(modifier)

        # SQLite's "start of month/year" modifiers change the symbolic base
        # boundary; numeric modifiers become offsets from that base.
        if normalized_modifier == "start_of_month":
            base = "start_of_current_month"
            period_grain = "month"

        elif normalized_modifier == "start_of_year":
            base = "start_of_current_year"
            period_grain = "year"

        elif normalized_modifier:
            offsets.append(normalized_modifier)

            if "month" in normalized_modifier:
                period_grain = "month"

            elif "year" in normalized_modifier:
                period_grain = "year"

    return {
        "raw": raw,
        "source_dialect": TEMPORAL_SOURCE_DIALECT,
        "physical_expression_family": "sqlite_date_modifier",
        "normalization_rule_id": _infer_boundary_rule_id(
            base=base,
            offsets=offsets,
            period_grain=period_grain,
        ),
        "anchor": anchor,
        "base": base,
        "offsets": offsets,
        "period_grain": period_grain,
        "normalization_status": "normalized",
    }


def _split_function_args(argument_string: str) -> list[str]:
    """
    Split function arguments while respecting quotes and nested parentheses.
    """
    args: list[str] = []
    current: list[str] = []
    quote_char: str | None = None
    depth = 0

    for char in argument_string:
        if quote_char:
            current.append(char)
            if char == quote_char:
                quote_char = None
            continue

        if char in {"'", '"'}:
            quote_char = char
            current.append(char)
            continue

        if char == "(":
            depth += 1
            current.append(char)
            continue

        if char == ")":
            depth -= 1
            current.append(char)
            continue

        if char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
            continue

        current.append(char)

    if current:
        args.append("".join(current).strip())

    return args


def _normalise_temporal_anchor(value: Any) -> str:
    cleaned = _clean_sql_string(value).lower()

    if cleaned in {"current_date", "now", "date('now')", 'date("now")'}:
        return "current_date"

    if _looks_like_absolute_date(cleaned):
        return "absolute"

    return cleaned or "unknown"


def _normalise_sqlite_date_modifier(modifier: str) -> str | None:
    cleaned = _clean_sql_string(modifier).lower()

    if cleaned == "start of month":
        return "start_of_month"

    if cleaned == "start of year":
        return "start_of_year"

    offset_match = re.match(
        r"^([+-]?\d+)\s+(day|days|month|months|year|years)$",
        cleaned,
    )

    if offset_match:
        number = int(offset_match.group(1))
        unit = offset_match.group(2).rstrip("s")
        return f"{number:+d} {unit}"

    return cleaned or None


def _infer_period_label_from_boundaries(
    start_boundary: dict[str, Any],
    end_boundary: dict[str, Any],
) -> str | None:
    """
    Infer optional logical reporting-period labels from symbolic boundaries.

    This is a conservative registry, not a general temporal reasoner.
    If the boundary pattern is not registered, return None and allow the
    temporal predicate to degrade to partially_normalized.
    """
    start_base = start_boundary.get("base")
    start_offsets = tuple(start_boundary.get("offsets") or ())

    end_base = end_boundary.get("base")
    end_offsets = tuple(end_boundary.get("offsets") or ())

    lookup_key = (
        start_base,
        start_offsets,
        end_base,
        end_offsets,
    )

    return _CALENDAR_PERIOD_REGISTRY.get(lookup_key)


def _infer_period_grain_from_boundaries(
    start_boundary: dict[str, Any],
    end_boundary: dict[str, Any],
    normalized_label: str | None,
) -> str:
    if normalized_label and "month" in normalized_label:
        return "month"

    if normalized_label and "year" in normalized_label:
        return "year"

    grains = {
        start_boundary.get("period_grain"),
        end_boundary.get("period_grain"),
    }

    if "year" in grains:
        return "year"

    if "month" in grains:
        return "month"

    if "day" in grains:
        return "day"

    return "unknown"


def _infer_boundary_rule_id(
    base: str | None,
    offsets: list[str],
    period_grain: str,
) -> str:
    offset_set = set(offsets or [])

    if base == "start_of_current_month" and not offset_set:
        return "sqlite_start_of_current_month_boundary"

    if base == "start_of_current_month" and "-1 month" in offset_set:
        return "sqlite_start_of_current_month_minus_one_month_boundary"

    if base == "start_of_current_month" and "-1 day" in offset_set:
        return "sqlite_start_of_current_month_minus_one_day_boundary"

    if base == "start_of_current_year" and not offset_set:
        return "sqlite_start_of_current_year_boundary"

    if base == "start_of_current_year" and "-1 year" in offset_set:
        return "sqlite_start_of_current_year_minus_one_year_boundary"

    if base == "start_of_current_year" and "-1 day" in offset_set:
        return "sqlite_start_of_current_year_minus_one_day_boundary"

    if period_grain != "unknown":
        return f"sqlite_symbolic_{period_grain}_boundary"

    return "sqlite_symbolic_boundary"


def _infer_temporal_rule_id(
    normalized_label: str | None,
    start_boundary: dict[str, Any],
    end_boundary: dict[str, Any],
) -> str | None:
    if normalized_label == "prior_month":
        return "calendar_prior_month_from_month_boundaries"

    if normalized_label == "current_month_to_date":
        return "calendar_current_month_to_date_from_month_start"

    if normalized_label == "current_year_to_date":
        return "calendar_current_year_to_date_from_year_start"

    if normalized_label == "prior_calendar_year":
        return "calendar_prior_year_from_year_boundaries"

    if (
        start_boundary.get("normalization_status") != "unknown"
        or end_boundary.get("normalization_status") != "unknown"
    ):
        return "symbolic_boundary_decomposition_partial"

    return None


def _normalization_rule_basis(normalization_rule_id: str | None) -> str:
    if normalization_rule_id is None:
        return "not_normalized"

    if normalization_rule_id.startswith("calendar_"):
        return "calendar_reporting_period_logic"

    if normalization_rule_id.startswith("symbolic_"):
        return "partial_symbolic_boundary_decomposition"

    if normalization_rule_id.startswith("sqlite_"):
        return "sqlite_physical_parser_to_symbolic_boundary"

    return "unknown"


def _infer_predicate_physical_expression_family(
    condition: AnnotatedFilterCondition,
) -> str:
    expression = (condition.expression or "").lower()
    values = " ".join(str(value).lower() for value in condition.values or [])

    combined = f"{expression} {values}"

    if "date(" in combined:
        return "sqlite_date_modifier"

    if "strftime" in combined:
        return "sqlite_strftime_component_filter"

    if any(_looks_like_absolute_date(str(value)) for value in condition.values or []):
        return "absolute_date_literal"

    return "unknown_temporal_expression"


def _predicate_type_from_operator(operator: str) -> str:
    if operator == "=":
        return "DATE_EQUALS"

    if operator in {">", ">="}:
        return "DATE_LOWER_BOUND"

    if operator in {"<", "<="}:
        return "DATE_UPPER_BOUND"

    return "DATE_COMPARE"


def _extract_integer_offset(text: str) -> int | None:
    """
    Safely extract relative temporal integer offsets.

    Handles:
    - SQLite modifier strings such as '-1 year', '+2 months'
    - Calendar component arithmetic such as:
      strftime('%Y', current_date) - 1

    Does not use broad substring matching.
    """
    original = str(text or "")
    lowered = original.lower()
    compact = re.sub(r"\s+", "", lowered)

    modifier_match = re.search(
        r"""['\"](?P<sign>[+-])(?P<value>\d+)\s*(?P<unit>day|days|month|months|year|years)['\"]""",
        lowered,
        flags=re.IGNORECASE,
    )

    if modifier_match:
        sign = -1 if modifier_match.group("sign") == "-" else 1
        return sign * int(modifier_match.group("value"))

    arithmetic_match = re.search(
        r"strftime\s*\([^)]*\)\s*(?P<sign>[+-])\s*(?P<value>\d+)\b",
        lowered,
        flags=re.IGNORECASE,
    )

    if arithmetic_match:
        sign = -1 if arithmetic_match.group("sign") == "-" else 1
        return sign * int(arithmetic_match.group("value"))

    if "current_date" in compact or "'now'" in compact or '"now"' in compact:
        return 0

    return None


def _summarise_temporal_expression(expression: str | None) -> str | None:
    if not expression:
        return None

    compact = " ".join(str(expression).split())

    if len(compact) > 240:
        return compact[:237] + "..."

    return compact

def _is_numeric_measure_column_use(column_use: AnnotatedColumnUse | None) -> bool:
    if column_use is None:
        return False

    column_name = (column_use.column or "").lower()
    annotations = column_use.annotations or []

    semantic_roles = set(_get_annotation_values(annotations, "semantic_role"))
    measure_types = set(_get_annotation_values(annotations, "measure_type"))
    units = set(_get_annotation_values(annotations, "unit"))

    if column_name in {
        "debit",
        "credit",
        "amount",
        "balance",
        "open_balance",
        "quantity",
        "qty",
        "rate",
    }:
        return True

    if "financial_measure" in semantic_roles:
        return True

    if measure_types.intersection({"flow", "stock", "quantity"}):
        return True

    if units.intersection({"monetary", "currency", "units", "count"}):
        return True

    return False


def _is_numeric_threshold_filter(condition: AnnotatedFilterCondition) -> bool:
    operator = (condition.operator or "").upper()

    if operator not in {">", ">=", "<", "<=", "BETWEEN"}:
        return False

    if _is_date_condition(condition):
        return False

    return any(
        _is_numeric_measure_column_use(column_use)
        for column_use in condition.columns
    )


def _build_filter_topology(
    semantics: SQLFinancialSemantics,
) -> dict[str, Any]:
    """
    Describe WHERE-level threshold filters that may be relevant to aggregation logic.

    Important:
    FSIR v0 cannot extract true HAVING clauses because the current parser does
    not expose HAVING separately. This function does not pretend otherwise.
    It only surfaces WHERE-level numeric threshold filters so the verifier can
    notice possible pre-aggregation filtering.
    """
    where_measure_threshold_filters: list[dict[str, Any]] = []

    for condition in semantics.logic.filter_conditions:
        if condition.is_ambiguous:
            continue

        if not _is_numeric_threshold_filter(condition):
            continue

        first_column = _first_condition_column(condition)

        where_measure_threshold_filters.append(
            {
                "filter_expression": condition.expression,
                "mapped_column": (
                    _qualified_column(first_column.table, first_column.column)
                    if first_column
                    else None
                ),
                "operator": condition.operator,
                "values": _normalise_values(condition.values),
                "enforcement_location": "global_filter",
                "filter_stage": "pre_aggregation_where",
                "mapping_status": _mapping_status(first_column),
            }
        )

    has_aggregation = bool(semantics.measure_usage.aggregated_columns)

    if has_aggregation and where_measure_threshold_filters:
        threshold_filtering_risk = "possible_pre_aggregation_threshold_filter"
    else:
        threshold_filtering_risk = "none_detected"

    return {
        "where_measure_threshold_filters": where_measure_threshold_filters,
        "post_aggregation_filters": [],
        "post_aggregation_filter_extraction_status": "not_supported_in_fsir_v0",
        "threshold_filtering_risk": threshold_filtering_risk,
        "note": (
            "FSIR v0 surfaces numeric threshold filters found in WHERE. "
            "True HAVING clauses are not available unless the SQL parser exposes them."
        ),
    }

# Extraction metadata
def _build_profile_extraction(
    semantics: SQLFinancialSemantics,
) -> dict[str, Any]:
    warnings: list[str] = []
    unsupported_features: list[str] = []

    if semantics.unsupported_lineage:
        unsupported_features.append("unsupported_lineage")

    for column_use in semantics.measure_usage.aggregated_columns:
        expression = column_use.expression or ""

        if "case" in expression.lower():
            warnings.append(
                "CASE expression detected. Conditional modifier extraction is heuristic in FSIR v0."
            )

    if any(condition.is_ambiguous for condition in semantics.logic.filter_conditions):
        warnings.append("Ambiguous filter column mapping detected.")

    if semantics.measure_usage.ambiguous_measure_columns:
        warnings.append(
            "Ambiguous measure column mapping detected: "
            + ", ".join(semantics.measure_usage.ambiguous_measure_columns)
        )

    if semantics.logic.date_conditions:
        warnings.append(
            "Temporal predicate normalization uses a SQLite physical parser for BookSQL "
            "and maps expressions into a dialect-independent symbolic boundary representation."
        )

    # NOTE: Do not add a general HAVING warning here. HAVING support is a known
    # FSIR v0 limitation, not a per-query extraction warning unless the parser
    # later exposes HAVING clauses explicitly.

    status = "OK"

    if unsupported_features:
        status = "UNSUPPORTED_LINEAGE"

    elif warnings:
        status = "PARTIAL"

    return {
        "status": status,
        "unsupported_features": unsupported_features,
        "extraction_warnings": _unique(warnings),
    }

# Public API
def build_fsir(semantics: SQLFinancialSemantics) -> dict[str, Any]:
    """Build a Financial Semantic Intermediate Representation.

    Args:
        semantics: Schema-grounded SQL semantics produced by
            `build_sql_financial_semantics`.

    Returns:
        Nested FSIR dictionary with extraction metadata, summaries, financial
        concept layer, measurement layer, and reporting topology layer.

    Important assumptions:
        This function uses only `SQLFinancialSemantics`. It does not inspect the
        question, gold SQL, or execution results, and it does not decide whether
        the generated SQL is correct.

    Edge cases:
        Parse errors return a structured `PARSE_ERROR` FSIR. Unsupported lineage
        still returns available parsed facts but marks status as
        `UNSUPPORTED_LINEAGE` so the verifier can abstain.
    """
    if semantics.parse_error:
        return {
            "status": "PARSE_ERROR",
            "profile_extraction": {
                "status": "PARSE_ERROR",
                "unsupported_features": [],
                "extraction_warnings": [semantics.parse_error],
            },
            "fsir_summary": {
                "scope_summary": {
                    "present_scope_roles": [],
                    "present_financial_classes": [],
                    "present_transaction_events": [],
                    "present_payment_statuses": [],
                    "present_entity_roles": [],
                },
                "measurement_summary": {
                    "present_measure_families": [],
                    "present_extracted_vectors": [],
                    "present_aggregation_functions": [],
                    "present_expression_types": [],
                },
                "topology_summary": {
                    "present_grouping_roles": [],
                    "has_temporal_grouping": False,
                    "has_temporal_filter": False,
                    "temporal_filter_labels": [],
                },
            },
            "financial_concept_layer": {
                "scope_constraints": [],
                "scope_coverage": {
                    "has_scope_constraints": False,
                    "status": "unknown_due_to_parse_error",
                    "ambiguous_scope_count": 0,
                    "note": "No scope constraints can be extracted from a parse error.",
                },
            },
            "measurement_layer": {
                "measurements": [],
            },
            "reporting_topology_layer": {
                "analytical_grain": "unknown",
                "grouping_dimensions": [],
                "temporal_resolution": {
                    "source_dialect": TEMPORAL_SOURCE_DIALECT,
                    "parser_scope": TEMPORAL_PARSER_SCOPE,
                    "representation_level": TEMPORAL_REPRESENTATION_LEVEL,
                    "date_predicates": [],
                    "normalization_status": "unknown",
                },
                "filter_topology": {
                    "where_measure_threshold_filters": [],
                    "post_aggregation_filters": [],
                    "post_aggregation_filter_extraction_status": "not_supported_in_fsir_v0",
                    "threshold_filtering_risk": "none_detected",
                    "note": "No filter topology can be extracted from a parse error.",
                },
                "ordering": [],
                "limit": None,
            },
        }

    measurements = _build_measurements(semantics)

    measurement_ids = [
        measurement["measurement_id"]
        for measurement in measurements
    ]

    grouping_dimensions = _build_grouping_dimensions(semantics)

    # Build the three FSIR layers separately so the verifier can inspect object
    # scope, physical measures, and computation topology without parsing prose.
    financial_concept_layer = _build_financial_concept_layer(
        semantics=semantics,
        measurement_ids=measurement_ids,
    )

    temporal_resolution = _build_temporal_resolution(semantics)

    measurement_layer = {
        "measurements": measurements,
    }

    reporting_topology_layer = {
        "analytical_grain": _derive_analytical_grain(
            semantics=semantics,
            grouping_dimensions=grouping_dimensions,
        ),
        "grouping_dimensions": grouping_dimensions,
        "temporal_resolution": temporal_resolution,
        "filter_topology": _build_filter_topology(semantics),
        "ordering": semantics.logic.order_by_expressions,
        "limit": semantics.logic.limit,
    }

    return {
        "status": "OK" if not semantics.unsupported_lineage else "UNSUPPORTED_LINEAGE",
        "profile_extraction": _build_profile_extraction(semantics),
        "fsir_summary": {
            "scope_summary": _build_scope_summary(
                financial_concept_layer["scope_constraints"]
            ),
            "measurement_summary": _build_measurement_summary(measurements),
            "topology_summary": _build_topology_summary(
                grouping_dimensions=grouping_dimensions,
                temporal_resolution=temporal_resolution,
            ),
        },
        "financial_concept_layer": financial_concept_layer,
        "measurement_layer": measurement_layer,
        "reporting_topology_layer": reporting_topology_layer,
    }

def render_fsir_for_verifier(fsir: dict[str, Any]) -> str:
    """Render an FSIR dictionary as deterministic verifier input JSON.

    Args:
        fsir: FSIR dictionary returned by `build_fsir`.

    Returns:
        Pretty-printed JSON string with stable key ordering.

    Assumption:
        The verifier prompt expects stable, inspectable JSON text rather than a
        lossy natural-language decompilation.
    """
    return json.dumps(
        fsir,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
