from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from src.finverisql.schema_loader import SchemaAnnotationStore, normalise_value
from src.finverisql.sql_parser import AggregationRef, ColumnRef, FilterRef, ParsedSQL


ACCOUNT_TYPE_ROLE = "account_type_classifier"
TRANSACTION_TYPE_ROLE = "transaction_type_classifier"
ENTITY_IDENTIFIER_ROLE = "entity_identifier"
FINANCIAL_MEASURE_ROLE = "financial_measure"

DATE_ROLES = {
    "transaction_date",
    "due_date",
    "system_created_date",
    "date_field",
}


@dataclass
class AnnotatedColumnUse:
    source: str
    column: str | None
    table: str | None
    expression: str | None
    function: str | None
    operator: str | None
    values: list[Any]
    annotations: list[dict[str, Any]]
    concepts: list[str]
    is_ambiguous: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ObjectScopeSemantics:
    has_account_type_filter: bool
    account_type_values: list[str]
    account_type_concepts: list[str]

    has_transaction_type_filter: bool
    transaction_type_values: list[str]
    transaction_type_concepts: list[str]

    entity_filter_columns: list[str]
    entity_filter_values: list[str]
    entity_scopes_detected: list[str]

    ambiguous_filter_columns: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MeasureSemantics:
    aggregated_columns: list[AnnotatedColumnUse]
    selected_columns: list[AnnotatedColumnUse]
    aggregation_functions: list[str]
    measure_types: list[str]
    sign_conventions: list[str]
    ambiguous_measure_columns: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "aggregated_columns": [item.to_dict() for item in self.aggregated_columns],
            "selected_columns": [item.to_dict() for item in self.selected_columns],
            "aggregation_functions": self.aggregation_functions,
            "measure_types": self.measure_types,
            "sign_conventions": self.sign_conventions,
            "ambiguous_measure_columns": self.ambiguous_measure_columns,
        }
        

@dataclass
class AnnotatedFilterCondition:
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
        }

@dataclass
class LogicSemantics:
    group_by_columns: list[AnnotatedColumnUse]
    order_by_expressions: list[str]
    limit: int | None
    date_conditions: list[AnnotatedFilterCondition]
    temporal_roles: list[str]
    filter_conditions: list[AnnotatedFilterCondition]  # NEW ADDITION

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_by_columns": [item.to_dict() for item in self.group_by_columns],
            "order_by_expressions": self.order_by_expressions,
            "limit": self.limit,
            "date_conditions": [item.to_dict() for item in self.date_conditions],
            "temporal_roles": self.temporal_roles,
            "filter_conditions": [item.to_dict() for item in self.filter_conditions],
        }


@dataclass
class SQLFinancialSemantics:
    tables: list[str]
    aliases: dict[str, str]
    joins: list[dict[str, Any]]
    object_scope: ObjectScopeSemantics
    measure_usage: MeasureSemantics
    logic: LogicSemantics
    parse_error: str | None = None
    unsupported_lineage: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "tables": self.tables,
            "aliases": self.aliases,
            "joins": self.joins,
            "object_scope": self.object_scope.to_dict(),
            "measure_usage": self.measure_usage.to_dict(),
            "logic": self.logic.to_dict(),
            "parse_error": self.parse_error,
            "unsupported_lineage": self.unsupported_lineage,
        }


def _unique(values: list[Any]) -> list[Any]:
    result = []
    seen = set()

    for value in values:
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


def _get_annotation_attr(
    annotations: list[dict[str, Any]],
    key: str,
    default: Any = None,
) -> Any:
    for annotation in annotations or []:
        value = annotation.get(key)

        if value not in (None, "", [], "none"):
            return value

    return default


def _get_all_annotation_attrs(
    annotations: list[dict[str, Any]],
    key: str,
) -> list[str]:
    values = []

    for annotation in annotations or []:
        value = annotation.get(key)

        if value not in (None, "", [], "none"):
            values.append(str(value))

    return _unique(values)


def _is_ambiguous(annotations: list[dict[str, Any]]) -> bool:
    return any(annotation.get("is_ambiguous") for annotation in annotations or [])


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


def _resolve_value_concepts(
    schema_store: SchemaAnnotationStore,
    annotations: list[dict[str, Any]],
    values: list[Any],
) -> list[str]:
    concepts: list[str] = []

    for annotation in annotations:
        for value in values:
            concept = schema_store.resolve_value_concept(annotation, value)

            if concept:
                concepts.append(str(concept))

    return _unique(concepts)


def _make_annotated_column_use(
    source: str,
    column: str | None,
    table: str | None,
    expression: str | None,
    function: str | None,
    operator: str | None,
    values: list[Any],
    annotations: list[dict[str, Any]],
    concepts: list[str] | None = None,
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
        concepts=concepts or [],
        is_ambiguous=_is_ambiguous(annotations),
    )

def build_object_scope_semantics(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> ObjectScopeSemantics:
    account_type_values, account_type_concepts = [], []
    transaction_type_values, transaction_type_concepts = [], []
    entity_filter_columns, entity_filter_values, entity_scopes_detected = [], [], []
    ambiguous_filter_columns = []

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

            # AMBIGUITY GUARD
            if _is_ambiguous(annotations):
                if col_ref.column:
                    ambiguous_filter_columns.append(col_ref.column)
                continue  # Don't process ambiguous semantics.

            semantic_roles = _get_all_annotation_attrs(annotations, "semantic_role")
            entity_scopes = _get_all_annotation_attrs(annotations, "entity_scope")
            values = _normalise_filter_values(filter_ref.values)
            concepts = _resolve_value_concepts(schema_store, annotations, values)

            if ACCOUNT_TYPE_ROLE in semantic_roles:
                account_type_values.extend(values)
                account_type_concepts.extend(concepts)

            elif TRANSACTION_TYPE_ROLE in semantic_roles:
                transaction_type_values.extend(values)
                transaction_type_concepts.extend(concepts)

            elif ENTITY_IDENTIFIER_ROLE in semantic_roles:
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
        ambiguous_filter_columns=sorted(set(ambiguous_filter_columns)),
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
) -> AnnotatedColumnUse:
    annotations = _resolve_annotations(
        schema_store=schema_store,
        parsed_sql=parsed_sql,
        column=column_ref.column,
        table=column_ref.table,
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
    )


def _column_uses_from_aggregation(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
    aggregation_ref: AggregationRef,
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
            )
        ]

    return [
        _column_use_from_column_ref(
            parsed_sql=parsed_sql,
            schema_store=schema_store,
            column_ref=column_ref,
            source="aggregation",
            expression=aggregation_ref.expression,
            function=aggregation_ref.func,
        )
        for column_ref in aggregation_ref.columns
    ]


def build_measure_semantics(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> MeasureSemantics:
    aggregated_columns: list[AnnotatedColumnUse] = []

    for aggregation_ref in parsed_sql.aggregations:
        aggregated_columns.extend(
            _column_uses_from_aggregation(
                parsed_sql=parsed_sql,
                schema_store=schema_store,
                aggregation_ref=aggregation_ref,
            )
        )

    selected_columns = [
        _column_use_from_column_ref(
            parsed_sql=parsed_sql,
            schema_store=schema_store,
            column_ref=column_ref,
            source="select",
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
    ambiguous_measure_columns: list[str] = []

    for column_use in all_column_uses:
        if column_use.is_ambiguous:
            if column_use.column:
                ambiguous_measure_columns.append(column_use.column)
            continue

        semantic_roles = _get_all_annotation_attrs(column_use.annotations, "semantic_role")

        if FINANCIAL_MEASURE_ROLE in semantic_roles:
            measure_types.extend(
                _get_all_annotation_attrs(column_use.annotations, "measure_type")
            )
            sign_conventions.extend(
                _get_all_annotation_attrs(column_use.annotations, "sign_convention")
            )

    return MeasureSemantics(
        aggregated_columns=aggregated_columns,
        selected_columns=selected_columns,
        aggregation_functions=aggregation_functions,
        measure_types=sorted(set(measure_types)),
        sign_conventions=sorted(set(sign_conventions)),
        ambiguous_measure_columns=sorted(set(ambiguous_measure_columns)),
    )


def _is_temporal_annotation(annotation: dict[str, Any]) -> bool:
    return annotation.get("semantic_role") in DATE_ROLES


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

            col_concepts = [] if is_col_ambiguous else _resolve_value_concepts(
                schema_store=schema_store,
                annotations=annotations,
                values=filter_ref.values,
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
                concepts=col_concepts,
            )

            annotated_columns_in_filter.append(col_use)

            if is_col_ambiguous:
                is_filter_ambiguous = True
                continue

            roles.extend(_get_all_annotation_attrs(annotations, "semantic_role"))
            measures.extend(_get_all_annotation_attrs(annotations, "measure_type"))
            signs.extend(_get_all_annotation_attrs(annotations, "sign_convention"))
            scopes.extend(_get_all_annotation_attrs(annotations, "entity_scope"))
            concepts.extend(col_concepts)

            if any(_is_temporal_annotation(annotation) for annotation in annotations):
                is_date_condition = True
                temporal_roles.extend(
                    _get_all_annotation_attrs(annotations, "semantic_role")
                )

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
    )
    
def build_sql_financial_semantics(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> SQLFinancialSemantics:
    if parsed_sql.parse_error:
        empty_object_scope = ObjectScopeSemantics(
            has_account_type_filter=False,
            account_type_values=[],
            account_type_concepts=[],
            has_transaction_type_filter=False,
            transaction_type_values=[],
            transaction_type_concepts=[],
            entity_filter_columns=[],
            entity_filter_values=[],
            entity_scopes_detected=[],
            ambiguous_filter_columns=[],
        )

        empty_measure_usage = MeasureSemantics(
            aggregated_columns=[],
            selected_columns=[],
            aggregation_functions=[],
            measure_types=[],
            sign_conventions=[],
            ambiguous_measure_columns=[],
        )

        empty_logic = LogicSemantics(
            group_by_columns=[],
            order_by_expressions=[],
            limit=None,
            date_conditions=[],
            temporal_roles=[],
            filter_conditions=[],
        )

        return SQLFinancialSemantics(
            tables=[],
            aliases={},
            joins=[],
            object_scope=empty_object_scope,
            measure_usage=empty_measure_usage,
            logic=empty_logic,
            parse_error=parsed_sql.parse_error,
        )

    object_scope = build_object_scope_semantics(
        parsed_sql=parsed_sql,
        schema_store=schema_store,
    )

    measure_usage = build_measure_semantics(
        parsed_sql=parsed_sql,
        schema_store=schema_store,
    )

    logic = build_logic_semantics(
        parsed_sql=parsed_sql,
        schema_store=schema_store,
    )

    return SQLFinancialSemantics(
        tables=parsed_sql.tables,
        aliases=parsed_sql.aliases,
        joins=[join.to_dict() for join in parsed_sql.joins],
        object_scope=object_scope,
        measure_usage=measure_usage,
        logic=logic,
        parse_error=None,
        unsupported_lineage=parsed_sql.unsupported_lineage, 
    )