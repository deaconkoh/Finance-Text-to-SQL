"""Map parsed SQL to compact, schema-grounded financial semantics.

This module decorates a parsed SQL AST with BookSQL financial semantics from
`SchemaAnnotationStore`. It does not decide whether SQL answers the natural
language question. It only reports what the SQL actually does, with grounding,
ambiguity, value-status warnings, table grain, and measure evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.finverisql.schema_loader import SchemaAnnotationStore, normalise_value
from src.finverisql.sql_parser import AggregationRef, ColumnRef, ParsedSQL


ACCOUNT_TYPE_ROLE = "account_type_classifier"
TRANSACTION_TYPE_ROLE = "transaction_type_classifier"
ENTITY_IDENTIFIER_ROLE = "entity_identifier"
FINANCIAL_MEASURE_ROLE = "financial_measure"

SCOPE_ROLES = {
    "account_type_classifier",
    "transaction_type_classifier",
    "entity_identifier",
    "account_identifier",
    "product_service_identifier",
    "product_service_classifier",
    "payment_method_identifier",
    "payment_method_attribute",
    "settlement_status_flag",
    "status_flag",
}

DATE_ROLES = {
    "transaction_date",
    "due_date",
    "system_created_date",
    "date_field",
    "entity_date",
}

COMPACT_ANNOTATION_KEYS = [
    "table",
    "column",
    "resolution_status",
    "is_ambiguous",
    "candidate_count",
    "description",
    "semantic_role",
    "financial_role",
    "entity_scope",
    "domain_object",
    "measure_type",
    "unit",
    "financial_element",
    "sign_convention",
    "normal_balance",
    "posting_side",
    "time_behavior",
    "status_dimension",
    "transaction_family",
    "related_party_role",
    "related_entity_column",
    "related_status_column",
    "related_measure",
    "related_account",
    "related_financial_object",
    "linked_table",
    "linked_column",
    "classification_column",
    "requires_account_context",
    "usable_as_measure",
    "data_availability",
    "groups_rows",
    "table_grain",
    "balance_rule",
    "warning",
    "grounding_level",
    "grounding_rule",
]


def _unique(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen = set()

    for value in values:
        if value in (None, "", [], {}):
            continue

        key = str(value)

        if key not in seen:
            seen.add(key)
            result.append(value)

    return result


def _normalise_filter_values(values: list[Any]) -> list[str]:
    return [
        normalise_value(value)
        for value in values
        if normalise_value(value)
    ]


def _annotation_summary(annotation: dict[str, Any]) -> dict[str, Any]:
    """Return a compact verifier-facing view of an annotation."""
    return {
        key: annotation[key]
        for key in COMPACT_ANNOTATION_KEYS
        if key in annotation and annotation[key] not in (None, "", [], {}, "none")
    }


def _annotation_summaries(annotations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_annotation_summary(annotation) for annotation in annotations or []]


def _get_all_annotation_attrs(
    annotations: list[dict[str, Any]],
    key: str,
) -> list[str]:
    values: list[str] = []

    for annotation in annotations or []:
        value = annotation.get(key)

        if value not in (None, "", [], {}, "none"):
            values.append(str(value))

    return _unique(values)


def _is_ambiguous(annotations: list[dict[str, Any]]) -> bool:
    return any(annotation.get("is_ambiguous") for annotation in annotations or [])


def _is_temporal_annotation(annotation: dict[str, Any]) -> bool:
    return annotation.get("semantic_role") in DATE_ROLES


def _resolve_annotations(
    schema_store: SchemaAnnotationStore,
    parsed_sql: ParsedSQL,
    column: str | None,
    table: str | None,
) -> list[dict[str, Any]]:
    return schema_store.annotate_column_reference(
        column=column,
        table=table,
        candidate_tables=parsed_sql.tables,
    )


def _resolve_value_semantics(
    schema_store: SchemaAnnotationStore,
    annotations: list[dict[str, Any]],
    values: list[Any],
    allow_when_ambiguous: bool = False,
) -> list[dict[str, Any]]:
    """Resolve filter values for each annotation.

    For ambiguous columns, value semantics are normally withheld so the mapper
    does not treat possible meanings as confirmed evidence.
    """
    if not values:
        return []

    if _is_ambiguous(annotations) and not allow_when_ambiguous:
        return [
            {
                "raw_value": value,
                "concept": None,
                "value_status": "withheld_due_to_ambiguous_column",
                "warning": "Value semantics withheld because the column mapping is ambiguous.",
            }
            for value in values
        ]

    resolutions: list[dict[str, Any]] = []

    for annotation in annotations:
        for value in values:
            resolved = schema_store.resolve_value_semantics(annotation, value)
            resolved["annotation_column"] = f"{annotation.get('table')}.{annotation.get('column')}"
            resolutions.append(resolved)

    return resolutions


def _concepts_from_value_semantics(value_semantics: list[dict[str, Any]]) -> list[str]:
    return _unique([
        str(item.get("concept"))
        for item in value_semantics or []
        if item.get("concept")
    ])


def _warnings_from_value_semantics(value_semantics: list[dict[str, Any]]) -> list[str]:
    return _unique([
        str(item.get("warning"))
        for item in value_semantics or []
        if item.get("warning")
    ])


def _annotation_warnings(annotations: list[dict[str, Any]]) -> list[str]:
    return _unique([
        str(annotation.get("warning"))
        for annotation in annotations or []
        if annotation.get("warning")
    ])


def _column_signature(annotation: dict[str, Any]) -> str:
    table = annotation.get("table")
    column = annotation.get("column")

    if table and column:
        return f"{table}.{column}"

    return str(column or table or "unknown")


def _first_singleton(values: list[Any]) -> Any | None:
    unique_values = _unique(values)
    return unique_values[0] if len(unique_values) == 1 else None


@dataclass
class AnnotatedColumnUse:
    """A SQL column occurrence enriched with compact schema semantics."""

    source: str
    column: str | None
    table: str | None
    expression: str | None
    function: str | None
    operator: str | None
    values: list[Any]
    annotations: list[dict[str, Any]]
    value_semantics: list[dict[str, Any]] = field(default_factory=list)
    derived_semantics: dict[str, Any] = field(default_factory=dict)
    is_ambiguous: bool = False

    @property
    def concepts(self) -> list[str]:
        return _concepts_from_value_semantics(self.value_semantics)

    def to_dict(self) -> dict[str, Any]:
        """Return a compact JSON-serializable evidence record."""
        annotation_summaries = _annotation_summaries(self.annotations)
        warnings = _unique(
            _annotation_warnings(self.annotations)
            + _warnings_from_value_semantics(self.value_semantics)
            + self.derived_semantics.get("warnings", [])
        )

        result = {
            "source": self.source,
            "column": self.column,
            "table": self.table,
            "expression": self.expression,
            "function": self.function,
            "operator": self.operator,
            "values": self.values,
            "annotation_summaries": annotation_summaries,
            "value_semantics": self.value_semantics,
            "concepts": self.concepts,
            "is_ambiguous": self.is_ambiguous,
            "warnings": warnings,
        }

        if self.derived_semantics:
            result["derived_semantics"] = self.derived_semantics

        return {
            key: value
            for key, value in result.items()
            if value not in (None, "", [], {}, "none")
        }

    def to_debug_dict(self) -> dict[str, Any]:
        """Return full internal data, including raw annotations, for debugging."""
        return {
            "source": self.source,
            "column": self.column,
            "table": self.table,
            "expression": self.expression,
            "function": self.function,
            "operator": self.operator,
            "values": self.values,
            "annotations": self.annotations,
            "value_semantics": self.value_semantics,
            "concepts": self.concepts,
            "derived_semantics": self.derived_semantics,
            "is_ambiguous": self.is_ambiguous,
        }


@dataclass
class ObjectScopeSemantics:
    """Business and financial scope facts extracted from SQL filters."""

    has_account_type_filter: bool
    account_type_values: list[str]
    account_type_concepts: list[str]

    has_transaction_type_filter: bool
    transaction_type_values: list[str]
    transaction_type_concepts: list[str]

    entity_filter_columns: list[str]
    entity_filter_values: list[str]
    entity_scopes_detected: list[str]

    scope_constraints: list[dict[str, Any]]
    ambiguous_filter_columns: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "has_account_type_filter": self.has_account_type_filter,
            "account_type_values": self.account_type_values,
            "account_type_concepts": self.account_type_concepts,
            "has_transaction_type_filter": self.has_transaction_type_filter,
            "transaction_type_values": self.transaction_type_values,
            "transaction_type_concepts": self.transaction_type_concepts,
            "entity_filter_columns": self.entity_filter_columns,
            "entity_filter_values": self.entity_filter_values,
            "entity_scopes_detected": self.entity_scopes_detected,
            "scope_constraints": self.scope_constraints,
            "ambiguous_filter_columns": self.ambiguous_filter_columns,
            "warnings": self.warnings,
        }


@dataclass
class MeasureSemantics:
    """Measurement facts from SELECT columns and aggregate expressions."""

    aggregated_columns: list[AnnotatedColumnUse]
    selected_columns: list[AnnotatedColumnUse]
    aggregation_functions: list[str]
    measure_types: list[str]
    sign_conventions: list[str]
    posting_sides: list[str]
    financial_roles: list[str]
    units: list[str]
    requires_account_context_columns: list[str]
    unusable_measure_columns: list[str]
    ambiguous_measure_columns: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "aggregated_columns": [item.to_dict() for item in self.aggregated_columns],
            "selected_columns": [item.to_dict() for item in self.selected_columns],
            "aggregation_functions": self.aggregation_functions,
            "measure_types": self.measure_types,
            "sign_conventions": self.sign_conventions,
            "posting_sides": self.posting_sides,
            "financial_roles": self.financial_roles,
            "units": self.units,
            "requires_account_context_columns": self.requires_account_context_columns,
            "unusable_measure_columns": self.unusable_measure_columns,
            "ambiguous_measure_columns": self.ambiguous_measure_columns,
            "warnings": self.warnings,
        }


@dataclass
class AnnotatedFilterCondition:
    """A WHERE predicate enriched with schema and value-level semantics."""

    expression: str
    operator: str | None
    values: list[Any]
    columns: list[AnnotatedColumnUse]
    is_ambiguous: bool
    semantic_roles: list[str]
    measure_types: list[str]
    sign_conventions: list[str]
    entity_scopes: list[str]
    concepts: list[str]
    value_semantics: list[dict[str, Any]]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "expression": self.expression,
            "operator": self.operator,
            "values": self.values,
            "columns": [col.to_dict() for col in self.columns],
            "is_ambiguous": self.is_ambiguous,
            "semantic_roles": self.semantic_roles,
            "measure_types": self.measure_types,
            "sign_conventions": self.sign_conventions,
            "entity_scopes": self.entity_scopes,
            "concepts": self.concepts,
            "value_semantics": self.value_semantics,
            "warnings": self.warnings,
        }


@dataclass
class LogicSemantics:
    """Computation-logic facts extracted from SQL clauses."""

    group_by_columns: list[AnnotatedColumnUse]
    order_by_expressions: list[str]
    limit: int | None
    date_conditions: list[AnnotatedFilterCondition]
    temporal_roles: list[str]
    filter_conditions: list[AnnotatedFilterCondition]
    structural_warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_by_columns": [item.to_dict() for item in self.group_by_columns],
            "order_by_expressions": self.order_by_expressions,
            "limit": self.limit,
            "date_conditions": [item.to_dict() for item in self.date_conditions],
            "temporal_roles": self.temporal_roles,
            "filter_conditions": [item.to_dict() for item in self.filter_conditions],
            "structural_warnings": self.structural_warnings,
        }


@dataclass
class SQLFinancialSemantics:
    """Complete schema-grounded semantic profile for one parsed SQL query."""

    tables: list[str]
    aliases: dict[str, str]
    joins: list[dict[str, Any]]
    table_context: dict[str, Any]
    object_scope: ObjectScopeSemantics
    measure_usage: MeasureSemantics
    logic: LogicSemantics
    warnings: list[str]
    parse_error: str | None = None
    unsupported_lineage: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tables": self.tables,
            "aliases": self.aliases,
            "joins": self.joins,
            "table_context": self.table_context,
            "object_scope": self.object_scope.to_dict(),
            "measure_usage": self.measure_usage.to_dict(),
            "logic": self.logic.to_dict(),
            "warnings": self.warnings,
            "parse_error": self.parse_error,
            "unsupported_lineage": self.unsupported_lineage,
        }


def _make_annotated_column_use(
    source: str,
    column: str | None,
    table: str | None,
    expression: str | None,
    function: str | None,
    operator: str | None,
    values: list[Any],
    annotations: list[dict[str, Any]],
    value_semantics: list[dict[str, Any]] | None = None,
    derived_semantics: dict[str, Any] | None = None,
) -> AnnotatedColumnUse:
    return AnnotatedColumnUse(
        source=source,
        column=column,
        table=table,
        expression=expression,
        function=function,
        operator=operator,
        values=values,
        annotations=annotations,
        value_semantics=value_semantics or [],
        derived_semantics=derived_semantics or {},
        is_ambiguous=_is_ambiguous(annotations),
    )


def _column_use_from_column_ref(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
    column_ref: ColumnRef,
    source: str,
    expression: str | None = None,
    function: str | None = None,
    operator: str | None = None,
    values: list[Any] | None = None,
    derived_semantics: dict[str, Any] | None = None,
) -> AnnotatedColumnUse:
    annotations = _resolve_annotations(
        schema_store=schema_store,
        parsed_sql=parsed_sql,
        column=column_ref.column,
        table=column_ref.table,
    )

    value_semantics = _resolve_value_semantics(
        schema_store=schema_store,
        annotations=annotations,
        values=values or [],
    )

    return _make_annotated_column_use(
        source=source,
        column=column_ref.column,
        table=column_ref.table,
        expression=expression,
        function=function,
        operator=operator,
        values=values or [],
        annotations=annotations,
        value_semantics=value_semantics,
        derived_semantics=derived_semantics,
    )


def _table_context_for_sql(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> dict[str, Any]:
    table_context: dict[str, Any] = {}

    for table in parsed_sql.tables:
        real_table = schema_store.get_table_name(table) or table
        metadata = schema_store.get_table_metadata(real_table)

        if metadata:
            table_context[real_table] = metadata

    return table_context


def _count_star_semantics(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
    aggregation_ref: AggregationRef,
) -> dict[str, Any]:
    warnings: list[str] = []
    table_grains: list[str] = []
    transaction_group_keys: list[str] = []

    for table in parsed_sql.tables:
        metadata = schema_store.get_table_metadata(table)

        if not metadata:
            continue

        if metadata.get("table_grain"):
            table_grains.append(str(metadata["table_grain"]))

        if metadata.get("transaction_group_key"):
            transaction_group_keys.append(str(metadata["transaction_group_key"]))

        if aggregation_ref.func.lower() == "count" and aggregation_ref.expression.upper() == "COUNT(*)":
            if metadata.get("count_star_warning"):
                warnings.append(str(metadata["count_star_warning"]))

    return {
        "semantic_operation": "row_count" if aggregation_ref.func.lower() == "count" else "aggregate_no_column",
        "table_grains": _unique(table_grains),
        "transaction_group_keys": _unique(transaction_group_keys),
        "warnings": _unique(warnings),
    }


def _infer_financial_element_context(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> dict[str, Any]:
    """Infer a single account/financial-element context from filters, if clear."""
    contexts: list[dict[str, Any]] = []

    for filter_ref in parsed_sql.filters:
        for col_ref in filter_ref.columns:
            annotations = _resolve_annotations(schema_store, parsed_sql, col_ref.column, col_ref.table)

            if _is_ambiguous(annotations):
                continue

            for annotation in annotations:
                if annotation.get("semantic_role") != ACCOUNT_TYPE_ROLE:
                    continue

                value_semantics = _resolve_value_semantics(schema_store, [annotation], filter_ref.values)

                for value_semantic in value_semantics:
                    concept_metadata = value_semantic.get("concept_metadata") or {}
                    financial_element = concept_metadata.get("financial_element")

                    if financial_element:
                        contexts.append(
                            {
                                "source_expression": filter_ref.expression,
                                "value_concept": value_semantic.get("concept"),
                                "financial_element": financial_element,
                                "normal_balance": concept_metadata.get("normal_balance"),
                                "domain_object": concept_metadata.get("domain_object"),
                            }
                        )

    unique_elements = _unique([context.get("financial_element") for context in contexts])

    if len(unique_elements) == 1:
        return contexts[0]

    return {}


def _with_measure_derived_semantics(
    column_use: AnnotatedColumnUse,
    financial_context: dict[str, Any],
    schema_store: SchemaAnnotationStore,
) -> AnnotatedColumnUse:
    derived = dict(column_use.derived_semantics or {})
    warnings = list(derived.get("warnings", []))

    for annotation in column_use.annotations:
        if annotation.get("semantic_role") != FINANCIAL_MEASURE_ROLE:
            continue

        signature = _column_signature(annotation)

        if annotation.get("usable_as_measure") is False:
            warnings.append(
                f"{signature} is marked usable_as_measure=false and should not be used for numeric computation."
            )

        if annotation.get("requires_account_context"):
            posting_side = annotation.get("posting_side")
            context_element = financial_context.get("financial_element")

            if posting_side and context_element:
                posting_effect = schema_store.get_posting_effect(
                    financial_element=context_element,
                    posting_side=posting_side,
                )

                if posting_effect:
                    derived["posting_effect"] = posting_effect
                    derived["account_context"] = financial_context
            else:
                warnings.append(
                    f"{signature} requires account/account type context to interpret its debit/credit effect."
                )

    if warnings:
        derived["warnings"] = _unique(warnings)

    column_use.derived_semantics = derived
    return column_use


def _column_uses_from_aggregation(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
    aggregation_ref: AggregationRef,
    financial_context: dict[str, Any],
) -> list[AnnotatedColumnUse]:
    if not aggregation_ref.columns:
        return [
            _make_annotated_column_use(
                source="aggregation",
                column=None,
                table=None,
                expression=aggregation_ref.expression,
                function=aggregation_ref.func,
                operator=None,
                values=[],
                annotations=[],
                derived_semantics=_count_star_semantics(parsed_sql, schema_store, aggregation_ref),
            )
        ]

    column_uses: list[AnnotatedColumnUse] = []

    for column_ref in aggregation_ref.columns:
        column_use = _column_use_from_column_ref(
            parsed_sql=parsed_sql,
            schema_store=schema_store,
            column_ref=column_ref,
            source="aggregation",
            expression=aggregation_ref.expression,
            function=aggregation_ref.func,
        )
        column_uses.append(
            _with_measure_derived_semantics(column_use, financial_context, schema_store)
        )

    return column_uses


def _build_scope_constraint(
    filter_expression: str,
    operator: str | None,
    values: list[Any],
    annotation: dict[str, Any],
    value_semantics: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = _annotation_summary(annotation)
    concepts = _concepts_from_value_semantics(value_semantics)
    warnings = _unique(_annotation_warnings([annotation]) + _warnings_from_value_semantics(value_semantics))

    constraint = {
        "source_clause": "WHERE",
        "expression": filter_expression,
        "operator": operator,
        "column": _column_signature(annotation),
        "raw_values": values,
        "semantic_role": annotation.get("semantic_role"),
        "entity_scope": annotation.get("entity_scope"),
        "domain_object": annotation.get("domain_object"),
        "measure_type": annotation.get("measure_type"),
        "financial_element": annotation.get("financial_element"),
        "normal_balance": annotation.get("normal_balance"),
        "status_dimension": annotation.get("status_dimension"),
        "related_party_role": annotation.get("related_party_role"),
        "related_entity_column": annotation.get("related_entity_column"),
        "related_measure": annotation.get("related_measure"),
        "concepts": concepts,
        "value_semantics": value_semantics,
        "annotation_summary": summary,
        "warnings": warnings,
    }

    return {
        key: value
        for key, value in constraint.items()
        if value not in (None, "", [], {}, "none")
    }


def build_object_scope_semantics(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> ObjectScopeSemantics:
    account_type_values, account_type_concepts = [], []
    transaction_type_values, transaction_type_concepts = [], []
    entity_filter_columns, entity_filter_values, entity_scopes_detected = [], [], []
    scope_constraints: list[dict[str, Any]] = []
    ambiguous_filter_columns: list[str] = []
    warnings: list[str] = []

    for filter_ref in parsed_sql.filters:
        for col_ref in filter_ref.columns:
            annotations = _resolve_annotations(
                schema_store=schema_store,
                parsed_sql=parsed_sql,
                column=col_ref.column,
                table=col_ref.table,
            )

            if not annotations:
                continue

            if _is_ambiguous(annotations):
                if col_ref.column:
                    ambiguous_filter_columns.append(col_ref.column)
                warnings.append(
                    f"Filter column {col_ref.column!r} is ambiguous; confirmed scope semantics withheld."
                )
                continue

            semantic_roles = _get_all_annotation_attrs(annotations, "semantic_role")
            entity_scopes = _get_all_annotation_attrs(annotations, "entity_scope")
            values = _normalise_filter_values(filter_ref.values)

            for annotation in annotations:
                value_semantics = _resolve_value_semantics(schema_store, [annotation], filter_ref.values)
                concepts = _concepts_from_value_semantics(value_semantics)
                warnings.extend(_warnings_from_value_semantics(value_semantics))
                warnings.extend(_annotation_warnings([annotation]))

                if annotation.get("semantic_role") in SCOPE_ROLES:
                    scope_constraints.append(
                        _build_scope_constraint(
                            filter_expression=filter_ref.expression,
                            operator=filter_ref.operator,
                            values=filter_ref.values,
                            annotation=annotation,
                            value_semantics=value_semantics,
                        )
                    )

                if annotation.get("semantic_role") == ACCOUNT_TYPE_ROLE:
                    account_type_values.extend(values)
                    account_type_concepts.extend(concepts)

                elif annotation.get("semantic_role") == TRANSACTION_TYPE_ROLE:
                    transaction_type_values.extend(values)
                    transaction_type_concepts.extend(concepts)

                elif annotation.get("semantic_role") == ENTITY_IDENTIFIER_ROLE:
                    if col_ref.column:
                        entity_filter_columns.append(col_ref.column)
                    entity_filter_values.extend(values)
                    entity_scopes_detected.extend(entity_scopes)

    return ObjectScopeSemantics(
        has_account_type_filter=bool(account_type_values),
        account_type_values=sorted(set(account_type_values)),
        account_type_concepts=sorted(set(account_type_concepts)),
        has_transaction_type_filter=bool(transaction_type_values),
        transaction_type_values=sorted(set(transaction_type_values)),
        transaction_type_concepts=sorted(set(transaction_type_concepts)),
        entity_filter_columns=sorted(set(entity_filter_columns)),
        entity_filter_values=sorted(set(entity_filter_values)),
        entity_scopes_detected=sorted(set(entity_scopes_detected)),
        scope_constraints=scope_constraints,
        ambiguous_filter_columns=sorted(set(ambiguous_filter_columns)),
        warnings=sorted(set(warnings)),
    )


def build_measure_semantics(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> MeasureSemantics:
    financial_context = _infer_financial_element_context(parsed_sql, schema_store)
    aggregated_columns: list[AnnotatedColumnUse] = []

    for aggregation_ref in parsed_sql.aggregations:
        aggregated_columns.extend(
            _column_uses_from_aggregation(
                parsed_sql=parsed_sql,
                schema_store=schema_store,
                aggregation_ref=aggregation_ref,
                financial_context=financial_context,
            )
        )

    selected_columns = [
        _with_measure_derived_semantics(
            _column_use_from_column_ref(
                parsed_sql=parsed_sql,
                schema_store=schema_store,
                column_ref=column_ref,
                source="select",
            ),
            financial_context,
            schema_store,
        )
        for column_ref in parsed_sql.selected_columns
    ]

    all_column_uses = aggregated_columns + selected_columns

    aggregation_functions = _unique([
        aggregation_ref.func
        for aggregation_ref in parsed_sql.aggregations
    ])

    measure_types: list[str] = []
    sign_conventions: list[str] = []
    posting_sides: list[str] = []
    financial_roles: list[str] = []
    units: list[str] = []
    requires_account_context_columns: list[str] = []
    unusable_measure_columns: list[str] = []
    ambiguous_measure_columns: list[str] = []
    warnings: list[str] = []

    for column_use in all_column_uses:
        warnings.extend(column_use.to_dict().get("warnings", []))

        if column_use.is_ambiguous:
            if column_use.column:
                ambiguous_measure_columns.append(column_use.column)
            continue

        for annotation in column_use.annotations:
            if annotation.get("semantic_role") != FINANCIAL_MEASURE_ROLE:
                continue

            measure_types.extend(_get_all_annotation_attrs([annotation], "measure_type"))
            sign_conventions.extend(_get_all_annotation_attrs([annotation], "sign_convention"))
            posting_sides.extend(_get_all_annotation_attrs([annotation], "posting_side"))
            financial_roles.extend(_get_all_annotation_attrs([annotation], "financial_role"))
            units.extend(_get_all_annotation_attrs([annotation], "unit"))

            if annotation.get("requires_account_context"):
                requires_account_context_columns.append(_column_signature(annotation))

            if annotation.get("usable_as_measure") is False:
                unusable_measure_columns.append(_column_signature(annotation))

    return MeasureSemantics(
        aggregated_columns=aggregated_columns,
        selected_columns=selected_columns,
        aggregation_functions=aggregation_functions,
        measure_types=sorted(set(measure_types)),
        sign_conventions=sorted(set(sign_conventions)),
        posting_sides=sorted(set(posting_sides)),
        financial_roles=sorted(set(financial_roles)),
        units=sorted(set(units)),
        requires_account_context_columns=sorted(set(requires_account_context_columns)),
        unusable_measure_columns=sorted(set(unusable_measure_columns)),
        ambiguous_measure_columns=sorted(set(ambiguous_measure_columns)),
        warnings=sorted(set(warnings)),
    )


def build_logic_semantics(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> LogicSemantics:
    group_by_columns = [
        _column_use_from_column_ref(
            parsed_sql=parsed_sql,
            schema_store=schema_store,
            column_ref=column_ref,
            source="group_by",
        )
        for column_ref in parsed_sql.group_by
    ]

    date_conditions: list[AnnotatedFilterCondition] = []
    temporal_roles: list[str] = []
    filter_conditions: list[AnnotatedFilterCondition] = []
    structural_warnings: list[str] = []
    seen_date_condition_keys = set()

    for filter_ref in parsed_sql.filters:
        annotated_columns_in_filter: list[AnnotatedColumnUse] = []
        is_filter_ambiguous = False
        is_date_condition = False

        roles: list[str] = []
        measures: list[str] = []
        signs: list[str] = []
        scopes: list[str] = []
        concepts: list[str] = []
        all_value_semantics: list[dict[str, Any]] = []
        warnings: list[str] = []

        for col_ref in filter_ref.columns:
            annotations = _resolve_annotations(
                schema_store=schema_store,
                parsed_sql=parsed_sql,
                column=col_ref.column,
                table=col_ref.table,
            )

            if not annotations:
                continue

            is_col_ambiguous = _is_ambiguous(annotations)
            is_filter_ambiguous = is_filter_ambiguous or is_col_ambiguous

            value_semantics = _resolve_value_semantics(
                schema_store=schema_store,
                annotations=annotations,
                values=filter_ref.values,
                allow_when_ambiguous=False,
            )

            col_use = _make_annotated_column_use(
                source="filter",
                column=col_ref.column,
                table=col_ref.table,
                expression=filter_ref.expression,
                function=None,
                operator=filter_ref.operator,
                values=filter_ref.values,
                annotations=annotations,
                value_semantics=value_semantics,
            )

            annotated_columns_in_filter.append(col_use)
            all_value_semantics.extend(value_semantics)
            warnings.extend(col_use.to_dict().get("warnings", []))

            if is_col_ambiguous:
                continue

            roles.extend(_get_all_annotation_attrs(annotations, "semantic_role"))
            measures.extend(_get_all_annotation_attrs(annotations, "measure_type"))
            signs.extend(_get_all_annotation_attrs(annotations, "sign_convention"))
            scopes.extend(_get_all_annotation_attrs(annotations, "entity_scope"))
            concepts.extend(_concepts_from_value_semantics(value_semantics))

            if any(_is_temporal_annotation(annotation) for annotation in annotations):
                is_date_condition = True
                temporal_roles.extend(_get_all_annotation_attrs(annotations, "semantic_role"))

        condition = AnnotatedFilterCondition(
            expression=filter_ref.expression,
            operator=filter_ref.operator,
            values=filter_ref.values,
            columns=annotated_columns_in_filter,
            is_ambiguous=is_filter_ambiguous,
            semantic_roles=sorted(set(roles)),
            measure_types=sorted(set(measures)),
            sign_conventions=sorted(set(signs)),
            entity_scopes=sorted(set(scopes)),
            concepts=sorted(set(concepts)),
            value_semantics=all_value_semantics,
            warnings=sorted(set(warnings)),
        )

        filter_conditions.append(condition)

        date_key = (condition.expression, condition.operator)

        if is_date_condition and date_key not in seen_date_condition_keys:
            date_conditions.append(condition)
            seen_date_condition_keys.add(date_key)

    return LogicSemantics(
        group_by_columns=group_by_columns,
        order_by_expressions=parsed_sql.order_by,
        limit=parsed_sql.limit,
        date_conditions=date_conditions,
        temporal_roles=sorted(set(temporal_roles)),
        filter_conditions=filter_conditions,
        structural_warnings=sorted(set(structural_warnings)),
    )


def _empty_object_scope() -> ObjectScopeSemantics:
    return ObjectScopeSemantics(
        has_account_type_filter=False,
        account_type_values=[],
        account_type_concepts=[],
        has_transaction_type_filter=False,
        transaction_type_values=[],
        transaction_type_concepts=[],
        entity_filter_columns=[],
        entity_filter_values=[],
        entity_scopes_detected=[],
        scope_constraints=[],
        ambiguous_filter_columns=[],
        warnings=[],
    )


def _empty_measure_usage() -> MeasureSemantics:
    return MeasureSemantics(
        aggregated_columns=[],
        selected_columns=[],
        aggregation_functions=[],
        measure_types=[],
        sign_conventions=[],
        posting_sides=[],
        financial_roles=[],
        units=[],
        requires_account_context_columns=[],
        unusable_measure_columns=[],
        ambiguous_measure_columns=[],
        warnings=[],
    )


def _empty_logic() -> LogicSemantics:
    return LogicSemantics(
        group_by_columns=[],
        order_by_expressions=[],
        limit=None,
        date_conditions=[],
        temporal_roles=[],
        filter_conditions=[],
        structural_warnings=[],
    )


def build_sql_financial_semantics(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> SQLFinancialSemantics:
    """Build a complete schema-grounded semantic representation."""
    if parsed_sql.parse_error:
        return SQLFinancialSemantics(
            tables=[],
            aliases={},
            joins=[],
            table_context={},
            object_scope=_empty_object_scope(),
            measure_usage=_empty_measure_usage(),
            logic=_empty_logic(),
            warnings=[f"Parse error: {parsed_sql.parse_error}"],
            parse_error=parsed_sql.parse_error,
            unsupported_lineage=getattr(parsed_sql, "unsupported_lineage", False),
        )

    table_context = _table_context_for_sql(parsed_sql, schema_store)
    object_scope = build_object_scope_semantics(parsed_sql, schema_store)
    measure_usage = build_measure_semantics(parsed_sql, schema_store)
    logic = build_logic_semantics(parsed_sql, schema_store)

    warnings = _unique(
        object_scope.warnings
        + measure_usage.warnings
        + logic.structural_warnings
        + [warning for condition in logic.filter_conditions for warning in condition.warnings]
    )

    return SQLFinancialSemantics(
        tables=parsed_sql.tables,
        aliases=parsed_sql.aliases,
        joins=[join.to_dict() for join in parsed_sql.joins],
        table_context=table_context,
        object_scope=object_scope,
        measure_usage=measure_usage,
        logic=logic,
        warnings=warnings,
        parse_error=None,
        unsupported_lineage=parsed_sql.unsupported_lineage,
    )
