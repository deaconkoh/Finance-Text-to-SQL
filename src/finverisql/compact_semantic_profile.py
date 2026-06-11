from __future__ import annotations

import json
import re
from typing import Any


def build_verifier_payload(semantic_profile: Any) -> dict[str, Any]:
    """
    Build a compact verifier-facing payload from SQLFinancialSemantics.

    This is not a new semantic representation layer.
    It is a deterministic projection/cleanup step that:
    - removes empty/debug-heavy fields
    - removes repeated filter information
    - preserves verifier-critical semantic evidence
    - preserves important absence signals
    - preserves warnings
    - adds lightweight deterministic temporal period hints

    Input can be either:
    - SQLFinancialSemantics object with .to_dict()
    - already-serialized semantic profile dict
    """
    profile = _to_dict(semantic_profile)

    if profile.get("parse_error"):
        return {
            "status": "parse_error",
            "parse_error": profile.get("parse_error"),
            "warnings": _dedupe(profile.get("warnings", [])),
        }

    payload = {
        "tables": _compact_tables(profile),
        "scope": _compact_scope(profile),
        "measurement": _compact_measurement(profile),
        "topology": _compact_topology(profile),
        "absence_signals": _compact_absence_signals(profile),
        "warnings": _collect_warnings(profile),
    }

    if profile.get("unsupported_lineage") is True:
        payload["unsupported_lineage"] = True

    return _prune_empty(payload)


def build_compact_semantic_profile(semantic_profile: Any) -> dict[str, Any]:
    """
    Alias for build_verifier_payload.

    This avoids naming confusion in testing scripts.
    """
    return build_verifier_payload(semantic_profile)


def render_verifier_payload(payload: dict[str, Any]) -> str:
    """Render payload as stable JSON for LLM verifier input."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def _to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()

    if isinstance(value, dict):
        return value

    raise TypeError(
        "Expected SQLFinancialSemantics object with .to_dict() or dict, "
        f"got {type(value).__name__}."
    )


def _compact_tables(profile: dict[str, Any]) -> dict[str, Any]:
    """
    Keep table grain and transaction key.

    Only include balancing_rule if the SQL appears to use double-entry/accounting
    context such as Debit, Credit, Account, or Account_type.
    """
    table_context = profile.get("table_context") or {}
    result: dict[str, Any] = {}
    include_balancing_rule = _uses_double_entry_context(profile)

    for table_name in profile.get("tables", []):
        metadata = table_context.get(table_name, {})

        table_payload = {
            "grain": metadata.get("table_grain"),
            "transaction_key": metadata.get("transaction_group_key"),
        }

        if include_balancing_rule:
            table_payload["balancing_rule"] = metadata.get("balancing_rule")

        result[table_name] = _prune_empty(table_payload)

    return result


def _uses_double_entry_context(profile: dict[str, Any]) -> bool:
    """
    Detect whether the compact payload should preserve the table balancing rule.

    The balancing rule is useful for Debit/Credit/account logic, but noisy for
    simple product/customer row-count questions.
    """
    measure_usage = profile.get("measure_usage") or {}
    object_scope = profile.get("object_scope") or {}

    column_uses = (
        (measure_usage.get("aggregated_columns") or [])
        + (measure_usage.get("selected_columns") or [])
    )

    for item in column_uses:
        item_text = " ".join(
            str(value or "").lower()
            for value in [
                item.get("expression"),
                item.get("column"),
                item.get("function"),
            ]
        )

        if "debit" in item_text or "credit" in item_text:
            return True

        for summary in item.get("annotation_summaries") or []:
            summary_text = " ".join(
                str(value or "").lower()
                for value in [
                    summary.get("column"),
                    summary.get("posting_side"),
                    summary.get("financial_role"),
                    summary.get("semantic_role"),
                    summary.get("domain_object"),
                ]
            )

            if "debit" in summary_text or "credit" in summary_text:
                return True

    if object_scope.get("has_account_type_filter"):
        return True

    for constraint in object_scope.get("scope_constraints") or []:
        semantic_role = str(constraint.get("semantic_role") or "").lower()
        column = str(constraint.get("column") or "").lower()
        domain_object = str(constraint.get("domain_object") or "").lower()

        if semantic_role in {"account_type_classifier", "account_identifier"}:
            return True

        if "account" in column or "account" in domain_object:
            return True

    return False


def _compact_scope(profile: dict[str, Any]) -> list[dict[str, Any]]:
    object_scope = profile.get("object_scope") or {}
    constraints = object_scope.get("scope_constraints") or []

    compact_constraints = []

    for constraint in constraints:
        value_semantics = constraint.get("value_semantics") or []

        value_statuses = _dedupe([
            item.get("value_status")
            for item in value_semantics
            if item.get("value_status")
        ])

        value_warnings = _dedupe([
            item.get("warning")
            for item in value_semantics
            if item.get("warning")
        ])

        concepts = _dedupe(
            (constraint.get("concepts") or [])
            + [
                item.get("concept")
                for item in value_semantics
                if item.get("concept")
            ]
        )

        compact_constraints.append(
            _prune_empty({
                "role": _scope_role(constraint),
                "column": constraint.get("column"),
                "operator": constraint.get("operator"),
                "values": constraint.get("raw_values"),
                "semantic_role": constraint.get("semantic_role"),
                "entity_scope": constraint.get("entity_scope"),
                "domain_object": constraint.get("domain_object"),
                "status_dimension": constraint.get("status_dimension"),
                "financial_element": constraint.get("financial_element"),
                "concepts": concepts,
                "value_status": _single_or_list(value_statuses),
                "warnings": _dedupe(
                    (constraint.get("warnings") or []) + value_warnings
                ),
            })
        )

    return compact_constraints


def _compact_measurement(profile: dict[str, Any]) -> list[dict[str, Any]]:
    measure_usage = profile.get("measure_usage") or {}

    # Prefer aggregates. If no aggregate exists, fall back to selected columns.
    column_uses = (
        measure_usage.get("aggregated_columns")
        or measure_usage.get("selected_columns")
        or []
    )

    measurements = []

    for item in column_uses:
        derived = item.get("derived_semantics") or {}
        summaries = item.get("annotation_summaries") or []

        expression = item.get("expression")
        function = item.get("function")

        if not expression and item.get("column"):
            expression = _qualified_column(item.get("table"), item.get("column"))

        measurement = {
            "source": item.get("source"),
            "expression": expression,
            "function": function.upper() if isinstance(function, str) else function,
            "semantic_operation": derived.get("semantic_operation"),
            "table_grain": _single_or_list(derived.get("table_grains")),
            "transaction_key": _single_or_list(derived.get("transaction_group_keys")),
            "distinct": _is_distinct_expression(expression),
            "columns": _compact_measure_columns(summaries),
            "warnings": _dedupe(
                (item.get("warnings") or []) + (derived.get("warnings") or [])
            ),
        }

        measurements.append(_prune_empty(measurement))

    return measurements


def _compact_measure_columns(
    annotation_summaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    columns = []

    for summary in annotation_summaries:
        columns.append(
            _prune_empty({
                "column": _qualified_column(summary.get("table"), summary.get("column")),
                "semantic_role": summary.get("semantic_role"),
                "financial_role": summary.get("financial_role"),
                "measure_type": summary.get("measure_type"),
                "domain_object": summary.get("domain_object"),
                "unit": summary.get("unit"),
                "posting_side": summary.get("posting_side"),
                "sign_convention": summary.get("sign_convention"),
                "requires_account_context": summary.get("requires_account_context"),
                "usable_as_measure": summary.get("usable_as_measure"),
            })
        )

    return columns


def _compact_topology(profile: dict[str, Any]) -> dict[str, Any]:
    logic = profile.get("logic") or {}

    group_by_columns = logic.get("group_by_columns") or []
    date_conditions = logic.get("date_conditions") or []

    group_by = [_compact_group_by(item) for item in group_by_columns]
    temporal_filters = [_compact_temporal_filter(item) for item in date_conditions]

    return {
        "analytical_grain": _derive_analytical_grain(
            has_measurement=bool(
                (profile.get("measure_usage") or {}).get("aggregated_columns")
            ),
            group_by=group_by,
        ),
        "group_by": group_by if group_by else "none",
        "order_by": logic.get("order_by_expressions") or "none",
        "limit": logic.get("limit") if logic.get("limit") is not None else "none",
        "temporal_filter": temporal_filters if temporal_filters else "none",
    }


def _compact_group_by(group_item: dict[str, Any]) -> dict[str, Any]:
    summaries = group_item.get("annotation_summaries") or []
    first_summary = summaries[0] if summaries else {}

    return _prune_empty({
        "column": _qualified_column(group_item.get("table"), group_item.get("column")),
        "role": _grouping_role(first_summary, group_item),
        "semantic_role": first_summary.get("semantic_role"),
        "entity_scope": first_summary.get("entity_scope"),
        "domain_object": first_summary.get("domain_object"),
    })


def _compact_temporal_filter(condition: dict[str, Any]) -> dict[str, Any]:
    """
    Build a lightweight temporal summary for verifier context.

    This does not fully normalise dates.
    It only extracts raw boundaries and adds deterministic period hints such as:
    - year_to_date
    - month_to_date
    - week_to_date
    - trailing_12_months
    - trailing_1_year
    - prior_calendar_month
    - relative_month_range
    """
    expression = condition.get("expression")
    operator = str(condition.get("operator") or "").upper()
    values = condition.get("values") or []
    semantic_roles = condition.get("semantic_roles") or []

    period_hint = _infer_period_hint(operator, values, expression)

    temporal_payload = {
        "expression": expression,
        "operator": operator,
        "date_column": _infer_date_column(condition, expression),
        "semantic_role": _single_or_list(semantic_roles),
        "period_hint": period_hint,
        "normalization_status": (
            "raw_expression_with_period_hint"
            if period_hint != "temporal_filter"
            else "raw_expression_only"
        ),
    }

    if operator == "BETWEEN" and len(values) >= 2:
        temporal_payload["raw_start"] = values[0]
        temporal_payload["raw_end"] = values[1]

    elif values:
        temporal_payload["raw_value"] = values[0]

    return _prune_empty(temporal_payload)


def _infer_date_column(
    condition: dict[str, Any],
    expression: Any,
) -> str | None:
    """
    Recover the date column deterministically.

    Prefer structured column metadata if available.
    Fall back to conservative regex extraction from the raw expression.
    """
    columns = condition.get("columns") or []

    if columns:
        first = columns[0]
        table = first.get("table")
        column = first.get("column")

        if table and column:
            return f"{table}.{column}"

        if column:
            return str(column)

    text = str(expression or "")

    # Handles:
    # Transaction_DATE BETWEEN ...
    # master_txn_table.Transaction_DATE BETWEEN ...
    between_match = re.search(
        r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s+BETWEEN\b",
        text,
        flags=re.IGNORECASE,
    )

    if between_match:
        return between_match.group(1)

    # Handles:
    # DATE(Transaction_DATE)
    date_func_match = re.search(
        r"\bDATE\s*\(\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)",
        text,
        flags=re.IGNORECASE,
    )

    if date_func_match:
        return date_func_match.group(1)

    # Handles:
    # strftime('%Y', Transaction_DATE)
    strftime_match = re.search(
        r"\bstrftime\s*\(\s*['\"][^'\"]+['\"]\s*,\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)",
        text,
        flags=re.IGNORECASE,
    )

    if strftime_match:
        return strftime_match.group(1)

    # Fallback: return first identifier that looks date-like, but avoid returning
    # the SQL function name DATE itself.
    identifiers = re.findall(
        r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?\b",
        text,
    )

    for identifier in identifiers:
        if identifier.lower() != "date" and "date" in identifier.lower():
            return identifier

    return None


def _infer_period_hint(
    operator: str,
    values: list[Any],
    expression: Any,
) -> str:
    """
    Deterministic period hint only.

    This does not claim to compute exact calendar dates.
    It classifies common finance/reporting period shapes.
    """
    start = str(values[0]).lower() if len(values) >= 1 else ""
    end = str(values[1]).lower() if len(values) >= 2 else ""
    text = " ".join([start, end, str(expression or "").lower()])

    if operator == "BETWEEN":
        if _looks_like_ytd(start, end):
            return "year_to_date"

        if _looks_like_mtd(start, end):
            return "month_to_date"

        if _looks_like_wtd(start, end):
            return "week_to_date"

        if _looks_like_trailing_12_months(start, end):
            return "trailing_12_months"

        if _looks_like_trailing_1_year(start, end):
            return "trailing_1_year"

        if _looks_like_prior_month_range(start, end):
            return "prior_calendar_month"

        if _looks_like_relative_week_range(start, end):
            return "relative_week_range"

        if "start of year" in text and "month" in text:
            return "relative_month_range"

        if "start of year" in text:
            return "relative_year_range"

        if "start of month" in text:
            return "relative_month_range"

        if re.search(r"\d{4}-\d{2}-\d{2}", text):
            return "absolute_date_range"

        return "date_range"

    if operator in {">", ">="}:
        if "start of year" in text:
            return "year_to_date_lower_bound"

        if "start of month" in text:
            return "month_to_date_lower_bound"

        if "weekday" in text:
            return "week_to_date_lower_bound"

        if "-12 month" in text or "-12 months" in text:
            return "trailing_12_months_lower_bound"

        if "-1 year" in text:
            return "trailing_1_year_lower_bound"

        if re.search(r"\d{4}-\d{2}-\d{2}", text):
            return "absolute_date_lower_bound"

        return "date_lower_bound"

    if operator in {"<", "<="}:
        if re.search(r"\d{4}-\d{2}-\d{2}", text):
            return "absolute_date_upper_bound"

        return "date_upper_bound"

    if operator == "=":
        if "strftime" in text and "%y" in text:
            return "year_component_filter"

        if "strftime" in text and "%m" in text:
            return "month_component_filter"

        if re.search(r"\d{4}-\d{2}-\d{2}", text):
            return "absolute_date_match"

        return "date_match"

    return "temporal_filter"


def _looks_like_ytd(start: str, end: str) -> bool:
    return (
        "start of year" in start
        and _looks_like_now_boundary(end)
    )


def _looks_like_mtd(start: str, end: str) -> bool:
    return (
        "start of month" in start
        and _looks_like_now_boundary(end)
    )


def _looks_like_wtd(start: str, end: str) -> bool:
    return (
        "weekday" in start
        and _looks_like_now_boundary(end)
    )


def _looks_like_trailing_12_months(start: str, end: str) -> bool:
    return (
        ("-12 month" in start or "-12 months" in start)
        and _looks_like_now_boundary(end)
    )


def _looks_like_trailing_1_year(start: str, end: str) -> bool:
    return (
        "-1 year" in start
        and _looks_like_now_boundary(end)
    )


def _looks_like_prior_month_range(start: str, end: str) -> bool:
    return (
        "start of month" in start
        and "-1 month" in start
        and "start of month" in end
        and (
            "-1 day" in end
            or "-1 second" in end
            or "-1 minute" in end
            or "-1 month" not in end
        )
    )


def _looks_like_relative_week_range(start: str, end: str) -> bool:
    return (
        "weekday" in start
        and "weekday" in end
    )


def _looks_like_now_boundary(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text).lower())

    return compact in {
        "date('now')",
        'date("now")',
        "current_date",
        "date(current_date)",
        "'now'",
        '"now"',
    }


def _compact_absence_signals(profile: dict[str, Any]) -> dict[str, Any]:
    object_scope = profile.get("object_scope") or {}
    logic = profile.get("logic") or {}

    return {
        "transaction_type_filter": (
            "present" if object_scope.get("has_transaction_type_filter") else "missing"
        ),
        "account_type_filter": (
            "present" if object_scope.get("has_account_type_filter") else "missing"
        ),
        "grouping": (
            "present" if logic.get("group_by_columns") else "none"
        ),
        "temporal_filter": (
            "present" if logic.get("date_conditions") else "none"
        ),
        "ordering": (
            "present" if logic.get("order_by_expressions") else "none"
        ),
        "limit": (
            "present" if logic.get("limit") is not None else "none"
        ),
    }


def _collect_warnings(profile: dict[str, Any]) -> list[str]:
    warnings = []

    warnings.extend(profile.get("warnings") or [])

    object_scope = profile.get("object_scope") or {}
    warnings.extend(object_scope.get("warnings") or [])

    measure_usage = profile.get("measure_usage") or {}
    warnings.extend(measure_usage.get("warnings") or [])

    logic = profile.get("logic") or {}
    warnings.extend(logic.get("structural_warnings") or [])

    for constraint in object_scope.get("scope_constraints") or []:
        warnings.extend(constraint.get("warnings") or [])

        for value_semantic in constraint.get("value_semantics") or []:
            if value_semantic.get("warning"):
                warnings.append(value_semantic["warning"])

    for item in measure_usage.get("aggregated_columns") or []:
        warnings.extend(item.get("warnings") or [])
        derived = item.get("derived_semantics") or {}
        warnings.extend(derived.get("warnings") or [])

    for condition in logic.get("filter_conditions") or []:
        warnings.extend(condition.get("warnings") or [])

    return _dedupe(warnings)


def _scope_role(constraint: dict[str, Any]) -> str:
    semantic_role = constraint.get("semantic_role")
    entity_scope = constraint.get("entity_scope")
    status_dimension = constraint.get("status_dimension")

    if entity_scope in {"customer", "vendor", "employee", "account", "product_service"}:
        return entity_scope

    if semantic_role == "transaction_type_classifier":
        return "transaction_type"

    if semantic_role == "account_type_classifier":
        return "account_type"

    if semantic_role in {"payment_method_identifier", "payment_method_attribute"}:
        return "payment_method"

    if semantic_role in {"settlement_status_flag", "status_flag"}:
        if status_dimension == "payment_status":
            return "payment_status"
        return "status"

    if semantic_role == "account_identifier":
        return "account"

    if semantic_role in {"product_service_identifier", "product_service_classifier"}:
        return "product_service"

    return semantic_role or "other"


def _grouping_role(summary: dict[str, Any], group_item: dict[str, Any]) -> str:
    entity_scope = summary.get("entity_scope")
    semantic_role = summary.get("semantic_role")
    column = str(group_item.get("column") or "").lower()

    if entity_scope:
        return entity_scope

    if semantic_role == "transaction_date" or "date" in column:
        return "temporal_period"

    if "customer" in column:
        return "customer"

    if "vendor" in column:
        return "vendor"

    if "account" in column:
        return "account"

    if "product" in column or "service" in column:
        return "product_service"

    return semantic_role or "other"


def _derive_analytical_grain(
    has_measurement: bool,
    group_by: list[dict[str, Any]],
) -> str:
    if not group_by:
        return "global_summary" if has_measurement else "raw_rows"

    roles = _dedupe([
        item.get("role")
        for item in group_by
        if item.get("role")
    ])

    if len(roles) == 1:
        return f"{roles[0]}_level"

    if roles:
        return "_by_".join(roles)

    return "grouped_unknown"


def _is_distinct_expression(expression: Any) -> bool:
    if not isinstance(expression, str):
        return False

    return "distinct" in expression.lower()


def _qualified_column(table: Any, column: Any) -> str | None:
    if not column:
        return None

    if table:
        return f"{table}.{column}"

    return str(column)


def _single_or_list(values: Any) -> Any:
    if values in (None, "", [], {}):
        return None

    if isinstance(values, list):
        cleaned = _dedupe(values)
        if len(cleaned) == 1:
            return cleaned[0]
        return cleaned

    return values


def _dedupe(values: list[Any]) -> list[Any]:
    result = []
    seen = set()

    for value in values or []:
        if value in (None, "", [], {}, "none"):
            continue

        key = json.dumps(value, sort_keys=True, default=str)

        if key not in seen:
            seen.add(key)
            result.append(value)

    return result


def _prune_empty(value: Any) -> Any:
    """
    Recursively remove empty fields.

    Important:
    - False is preserved because it can be meaningful, e.g. distinct = false.
    - Strings such as 'none' and 'missing' are preserved as explicit absence signals.
    """
    if isinstance(value, dict):
        cleaned = {
            key: _prune_empty(item)
            for key, item in value.items()
        }

        return {
            key: item
            for key, item in cleaned.items()
            if item not in (None, "", [], {})
        }

    if isinstance(value, list):
        cleaned_items = [_prune_empty(item) for item in value]
        return [
            item
            for item in cleaned_items
            if item not in (None, "", [], {})
        ]

    return value