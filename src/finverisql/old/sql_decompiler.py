"""Render schema-grounded SQL semantics as a legacy text profile.

This module converts `SQLFinancialSemantics` into a human-readable execution
profile with status, data-source, measure, object-scope, temporal, filter, and
structure sections. It remains useful for debugging and backward-compatible
verifier flows alongside the newer JSON FSIR path.

Main input is `SQLFinancialSemantics`; main output is descriptive profile text.
The decompiler does not inspect the natural-language question or judge SQL
correctness.
"""

from __future__ import annotations

from src.finverisql.sql_semantic_mapping import SQLFinancialSemantics


def _join_or_none(values: list[str]) -> str:
    cleaned = [str(v) for v in values if v not in (None, "", "none")]
    return ", ".join(sorted(set(cleaned))) if cleaned else "none detected"


def _format_sign_conventions(sign_conventions: list[str]) -> list[str]:
    """
    Make raw sign convention labels clearer for the LLM judge.
    """
    formatted = []

    for sign in sign_conventions:
        if sign == "ambiguous":
            formatted.append("gross amount with no debit/credit direction")
        elif sign == "debit_normal":
            formatted.append("debit-normal monetary value")
        elif sign == "credit_normal":
            formatted.append("credit-normal monetary value")
        elif sign == "context_dependent":
            formatted.append("context-dependent sign convention")
        else:
            formatted.append(str(sign))

    return formatted


def _get_aggregation_expressions(semantics: SQLFinancialSemantics) -> list[str]:
    expressions = []

    for col_use in semantics.measure_usage.aggregated_columns:
        if col_use.expression:
            expressions.append(col_use.expression)

    return sorted(set(expressions))


def _get_selected_columns(semantics: SQLFinancialSemantics) -> list[str]:
    columns = []

    for col_use in semantics.measure_usage.selected_columns:
        if col_use.table and col_use.column:
            columns.append(f"{col_use.table}.{col_use.column}")
        elif col_use.column:
            columns.append(col_use.column)

    return sorted(set(columns))


def _get_group_by_columns(semantics: SQLFinancialSemantics) -> list[str]:
    columns = []

    for col_use in semantics.logic.group_by_columns:
        if col_use.table and col_use.column:
            columns.append(f"{col_use.table}.{col_use.column}")
        elif col_use.column:
            columns.append(col_use.column)

    return sorted(set(columns))


def _get_ambiguous_columns(semantics: SQLFinancialSemantics) -> list[str]:
    ambiguous_cols = []

    ambiguous_cols.extend(semantics.object_scope.ambiguous_filter_columns)
    ambiguous_cols.extend(semantics.measure_usage.ambiguous_measure_columns)

    for condition in semantics.logic.filter_conditions:
        if condition.is_ambiguous:
            for col_use in condition.columns:
                if col_use.column:
                    ambiguous_cols.append(col_use.column)

    return sorted(set(ambiguous_cols))


def _condition_key(condition) -> tuple[str, str | None]:
    """
    Used to avoid printing date filters again under non-temporal filters.
    """
    return (condition.expression, condition.operator)


def decompile_semantics(semantics: SQLFinancialSemantics) -> str:
    """Translate `SQLFinancialSemantics` into a structured text profile.

    Args:
        semantics: Schema-grounded semantic representation of a candidate SQL
            query.

    Returns:
        Human-readable execution profile string. Fatal extraction states such as
        parse errors, unsupported lineage, and ambiguous mappings are rendered
        as explicit `[Status] ...` profiles.

    Assumptions:
        The profile describes what the candidate SQL appears to compute based
        only on parsed SQL structure and schema annotations. It does not use the
        natural-language question and does not judge correctness.
    """

    # Fatal guards: legacy verifier flows should abstain on these statuses
    # instead of asking the LLM to infer from unsafe or incomplete semantics.
    if semantics.parse_error:
        return (
            "[Status] PARSE_ERROR\n"
            "The candidate SQL could not be parsed, so its financial meaning "
            "could not be safely decompiled.\n"
            f"Error details: {semantics.parse_error}"
        )

    if semantics.unsupported_lineage:
        return (
            "[Status] UNSUPPORTED_LINEAGE\n"
            "The query contains CTEs, subqueries, or derived tables whose output "
            "columns cannot be safely traced back to base schema columns in v1. "
            "Strict semantic verification should abstain."
        )

    ambiguous_cols = _get_ambiguous_columns(semantics)

    if ambiguous_cols:
        return (
            "[Status] AMBIGUOUS_SEMANTIC_MAPPING\n"
            "The query uses unqualified columns that collide across multiple "
            "annotated tables. The decompiled SQL meaning is therefore not safe "
            "for strict verification.\n"
            f"Ambiguous columns: {', '.join(ambiguous_cols)}\n"
            "Strict semantic verification should abstain."
        )

    lines: list[str] = []

    lines.append("[Status] MAPPED_WITHOUT_AMBIGUITY")
    lines.append(
        "The candidate SQL was parsed and mapped to schema-grounded semantic "
        "annotations without detected column ambiguity."
    )

    # Data sources
    lines.append("[Data Sources]")

    if semantics.tables:
        lines.append(f"- Queried table(s): {', '.join(semantics.tables)}")
    else:
        lines.append("- No tables detected.")

    if semantics.joins:
        lines.append("- Join(s):")

        for join in semantics.joins:
            table = join.get("table") or "unknown_table"
            on_expression = join.get("on_expression") or "unknown join condition"
            lines.append(f"  - joined `{table}` on `{on_expression}`")
    else:
        lines.append("- No joins detected.")

    # Measure usage
    aggregation_functions = semantics.measure_usage.aggregation_functions
    aggregation_expressions = _get_aggregation_expressions(semantics)
    selected_columns = _get_selected_columns(semantics)
    sign_conventions = _format_sign_conventions(
        semantics.measure_usage.sign_conventions
    )

    lines.append("[Measure Usage]")

    if aggregation_functions:
        lines.append(
            f"- Aggregation function(s): {_join_or_none(aggregation_functions)}"
        )

        if aggregation_expressions:
            lines.append(
                f"- Aggregation expression(s): {', '.join(aggregation_expressions)}"
            )

        lines.append(
            f"- Measure type(s): {_join_or_none(semantics.measure_usage.measure_types)}"
        )
        lines.append(
            f"- Sign convention(s): {_join_or_none(sign_conventions)}"
        )

    elif selected_columns:
        lines.append("- No aggregation detected.")
        lines.append(f"- Selected column(s): {', '.join(selected_columns)}")
        lines.append(
            f"- Measure type(s): {_join_or_none(semantics.measure_usage.measure_types)}"
        )
        lines.append(
            f"- Sign convention(s): {_join_or_none(sign_conventions)}"
        )

    else:
        lines.append("- No schema-grounded financial measure was detected.")

    # Object / transaction / entity scope
    lines.append("[Object Scope]")

    has_scope = False

    if semantics.object_scope.account_type_concepts:
        has_scope = True
        lines.append(
            "- Account type concept(s): "
            f"{', '.join(semantics.object_scope.account_type_concepts)}"
        )

    if semantics.object_scope.account_type_values:
        has_scope = True
        lines.append(
            "- Account type value(s): "
            f"{', '.join(semantics.object_scope.account_type_values)}"
        )

    if semantics.object_scope.transaction_type_concepts:
        has_scope = True
        lines.append(
            "- Transaction concept(s): "
            f"{', '.join(semantics.object_scope.transaction_type_concepts)}"
        )

    if semantics.object_scope.transaction_type_values:
        has_scope = True
        lines.append(
            "- Transaction type value(s): "
            f"{', '.join(semantics.object_scope.transaction_type_values)}"
        )

    if semantics.object_scope.entity_scopes_detected:
        has_scope = True
        lines.append(
            "- Entity scope(s): "
            f"{', '.join(semantics.object_scope.entity_scopes_detected)}"
        )

    if semantics.object_scope.entity_filter_values:
        has_scope = True
        lines.append(
            "- Entity filter value(s): "
            f"{', '.join(semantics.object_scope.entity_filter_values)}"
        )

    if not has_scope:
        lines.append(
            "- No schema-grounded account type, transaction type, or entity "
            "scope filter was detected."
        )

    # Temporal scope
    lines.append("[Temporal Scope]")

    if semantics.logic.temporal_roles:
        lines.append(
            "- Date role(s) used for filtering: "
            f"{', '.join(semantics.logic.temporal_roles)}"
        )

    if semantics.logic.date_conditions:
        lines.append("- Date filter condition(s):")
        for condition in semantics.logic.date_conditions:
            lines.append(f"  - `{condition.expression}`")

    if not semantics.logic.temporal_roles and not semantics.logic.date_conditions:
        lines.append("- No schema-grounded WHERE date filter was detected.")

    # Applied non-temporal filter logic
    lines.append("[Applied Non-Temporal Filters]")

    date_condition_keys = {
        _condition_key(condition)
        for condition in semantics.logic.date_conditions
    }

    non_temporal_conditions = [
        condition
        for condition in semantics.logic.filter_conditions
        if _condition_key(condition) not in date_condition_keys
    ]

    if non_temporal_conditions:
        for condition in non_temporal_conditions:
            context = []

            if condition.concepts:
                context.append(f"concepts={', '.join(condition.concepts)}")

            if condition.semantic_roles:
                context.append(f"semantic_roles={', '.join(condition.semantic_roles)}")

            if condition.measure_types:
                context.append(f"measure_types={', '.join(condition.measure_types)}")

            if condition.sign_conventions:
                formatted_condition_signs = _format_sign_conventions(
                    condition.sign_conventions
                )
                context.append(
                    f"sign_conventions={', '.join(formatted_condition_signs)}"
                )

            if condition.entity_scopes:
                context.append(f"entity_scopes={', '.join(condition.entity_scopes)}")

            context_text = f" | context: {'; '.join(context)}" if context else ""

            lines.append(f"- `{condition.expression}`{context_text}")
    else:
        lines.append("- None detected.")

    
    # 7. Structural logic
    lines.append("[Structure]")

    group_by_columns = _get_group_by_columns(semantics)

    if group_by_columns:
        lines.append(f"- Grouped by: {', '.join(group_by_columns)}")
    else:
        lines.append("- No GROUP BY detected.")

    if semantics.logic.order_by_expressions:
        lines.append(
            "- Ordered by: "
            f"{', '.join(semantics.logic.order_by_expressions)}"
        )
    else:
        lines.append("- No ORDER BY detected.")

    if semantics.logic.limit is not None:
        lines.append(f"- Limited to {semantics.logic.limit} row(s).")
    else:
        lines.append("- No LIMIT detected.")

    return "\n".join(lines)
