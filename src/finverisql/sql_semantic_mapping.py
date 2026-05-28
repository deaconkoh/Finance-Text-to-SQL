from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .schema_loader import SchemaAnnotationStore
from .sql_parser import AggregationRef, ColumnRef, FilterRef, OrderByRef, ParsedSQL


# Note:
# These dataclasses use list fields for JSON-friendly output.
# They are treated as immutable by convention after construction.


@dataclass(frozen=True)
class AnnotatedColumnUse:
    """
    A SQL column reference enriched with schema annotation.

    source examples:
    - selected_column
    - aggregation
    - filter
    - group_by
    - order_by
    """

    source: str
    column: str
    table: str | None
    annotations: list[dict[str, Any]]
    func: str | None = None
    operator: str | None = None
    values: list[str] | None = None
    expression: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ObjectScopeSemantics:
    """
    Object-level scope detected from SQL filters.

    This supports D1:
    - Does SQL restrict account_type?
    - Does SQL restrict transaction_type?
    - Does SQL restrict customer/vendor/employee/entity?
    """

    has_account_type_filter: bool
    account_type_values: list[str]
    has_transaction_type_filter: bool
    transaction_type_values: list[str]
    entity_filter_columns: list[str]
    entity_filter_values: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MeasureSemantics:
    """
    Measure usage detected from selected and aggregated columns.

    This supports D2:
    - Does SQL aggregate debit?
    - Does SQL aggregate credit?
    - Does SQL aggregate amount?
    - Does SQL use balance/open_balance?
    """

    aggregated_columns: list[AnnotatedColumnUse]
    selected_columns: list[AnnotatedColumnUse]
    aggregated_column_names: list[str]
    aggregation_functions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "aggregated_columns": [
                item.to_dict() for item in self.aggregated_columns
            ],
            "selected_columns": [
                item.to_dict() for item in self.selected_columns
            ],
            "aggregated_column_names": self.aggregated_column_names,
            "aggregation_functions": self.aggregation_functions,
        }


@dataclass(frozen=True)
class LogicSemantics:
    """
    SQL logic detected from parsed structure.

    This supports D3:
    - GROUP BY
    - ORDER BY
    - LIMIT
    - date conditions
    """

    group_by_columns: list[AnnotatedColumnUse]
    order_by_columns: list[AnnotatedColumnUse]
    limit: int | None
    date_conditions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_by_columns": [
                item.to_dict() for item in self.group_by_columns
            ],
            "order_by_columns": [
                item.to_dict() for item in self.order_by_columns
            ],
            "limit": self.limit,
            "date_conditions": self.date_conditions,
        }


@dataclass(frozen=True)
class SQLFinancialSemantics:
    """
    Financial meaning of the predicted SQL.

    This is the actual SQL side used by FinVeriSQL.
    It should be compared against expected requirements from the user intent.
    """

    tables: list[str]
    joins: list[dict[str, Any]]
    object_scope: ObjectScopeSemantics
    measure_usage: MeasureSemantics
    logic: LogicSemantics
    parse_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tables": self.tables,
            "joins": self.joins,
            "object_scope": self.object_scope.to_dict(),
            "measure_usage": self.measure_usage.to_dict(),
            "logic": self.logic.to_dict(),
            "parse_error": self.parse_error,
        }


def get_annotation_attr(
    annotations: list[dict[str, Any]],
    key: str,
    default: Any = None,
) -> Any:
    """
    Safely retrieve an annotation attribute.

    Use this in verifier rules instead of direct dictionary access.

    Example:
        sign_convention = get_annotation_attr(
            item.annotations,
            "sign_convention",
        )
    """
    for annotation in annotations:
        if key in annotation:
            return annotation[key]

    return default


def annotate_column(
    column: str,
    table: str | None,
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> list[dict[str, Any]]:
    """
    Resolve a column reference to schema annotations.

    If table is known:
        use direct lookup.

    If table is unknown:
        search candidate tables used in the SQL first.
    """
    return schema_store.annotate_column_reference(
        column=column,
        table=table,
        candidate_tables=parsed_sql.tables,
    )


def annotated_column_use_from_column_ref(
    source: str,
    column_ref: ColumnRef,
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> AnnotatedColumnUse:
    annotations = annotate_column(
        column=column_ref.column,
        table=column_ref.table,
        parsed_sql=parsed_sql,
        schema_store=schema_store,
    )

    return AnnotatedColumnUse(
        source=source,
        column=column_ref.column,
        table=column_ref.table,
        annotations=annotations,
    )


def annotated_column_use_from_aggregation(
    aggregation: AggregationRef,
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> AnnotatedColumnUse:
    annotations = []

    if aggregation.column is not None:
        annotations = annotate_column(
            column=aggregation.column,
            table=aggregation.table,
            parsed_sql=parsed_sql,
            schema_store=schema_store,
        )

    return AnnotatedColumnUse(
        source="aggregation",
        column=aggregation.column or "*",
        table=aggregation.table,
        annotations=annotations,
        func=aggregation.func,
        expression=aggregation.expression,
    )


def annotated_column_use_from_filter(
    filter_ref: FilterRef,
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> AnnotatedColumnUse:
    annotations = annotate_column(
        column=filter_ref.column,
        table=filter_ref.table,
        parsed_sql=parsed_sql,
        schema_store=schema_store,
    )

    return AnnotatedColumnUse(
        source="filter",
        column=filter_ref.column,
        table=filter_ref.table,
        annotations=annotations,
        operator=filter_ref.operator,
        values=filter_ref.values or [],
        expression=filter_ref.expression,
    )


def annotated_column_use_from_order_by(
    order_by: OrderByRef,
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> AnnotatedColumnUse:
    annotations = []

    if order_by.column is not None:
        annotations = annotate_column(
            column=order_by.column,
            table=order_by.table,
            parsed_sql=parsed_sql,
            schema_store=schema_store,
        )

    return AnnotatedColumnUse(
        source="order_by",
        column=order_by.column or "",
        table=order_by.table,
        annotations=annotations,
        expression=order_by.expression,
    )


def normalise_values(values: list[str] | None) -> list[str]:
    if not values:
        return []

    cleaned_values = []

    for value in values:
        cleaned = str(value).strip()

        if cleaned and cleaned not in cleaned_values:
            cleaned_values.append(cleaned)

    return cleaned_values


def get_filter_values(
    filters: list[FilterRef],
    column_names: set[str],
) -> list[str]:
    values: list[str] = []

    normalised_column_names = {column.lower() for column in column_names}

    for filter_ref in filters:
        if filter_ref.column.lower() in normalised_column_names:
            for value in normalise_values(filter_ref.values):
                if value not in values:
                    values.append(value)

    return values


def has_filter(
    filters: list[FilterRef],
    column_names: set[str],
) -> bool:
    normalised_column_names = {column.lower() for column in column_names}

    return any(
        filter_ref.column.lower() in normalised_column_names
        for filter_ref in filters
    )


def build_object_scope_semantics(parsed_sql: ParsedSQL) -> ObjectScopeSemantics:
    account_type_columns = {"account_type", "accounttype"}

    transaction_type_columns = {"transaction_type", "transactiontype"}

    entity_columns = {
        "customer",
        "customers",
        "customer_name",
        "customer_full_name",
        "vendor",
        "vendors",
        "vendor_name",
        "supplier",
        "supplier_name",
        "employee",
        "employee_name",
    }

    account_type_values = get_filter_values(
        parsed_sql.filters,
        account_type_columns,
    )

    transaction_type_values = get_filter_values(
        parsed_sql.filters,
        transaction_type_columns,
    )

    entity_filter_columns: list[str] = []
    entity_filter_values: list[str] = []

    for filter_ref in parsed_sql.filters:
        column_name = filter_ref.column.lower()

        if column_name in entity_columns:
            if column_name not in entity_filter_columns:
                entity_filter_columns.append(column_name)

            for value in normalise_values(filter_ref.values):
                if value not in entity_filter_values:
                    entity_filter_values.append(value)

    return ObjectScopeSemantics(
        has_account_type_filter=has_filter(
            parsed_sql.filters,
            account_type_columns,
        ),
        account_type_values=account_type_values,
        has_transaction_type_filter=has_filter(
            parsed_sql.filters,
            transaction_type_columns,
        ),
        transaction_type_values=transaction_type_values,
        entity_filter_columns=entity_filter_columns,
        entity_filter_values=entity_filter_values,
    )


def build_measure_semantics(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> MeasureSemantics:
    aggregated_columns = [
        annotated_column_use_from_aggregation(
            aggregation=aggregation,
            parsed_sql=parsed_sql,
            schema_store=schema_store,
        )
        for aggregation in parsed_sql.aggregations
    ]

    selected_columns = [
        annotated_column_use_from_column_ref(
            source="selected_column",
            column_ref=column_ref,
            parsed_sql=parsed_sql,
            schema_store=schema_store,
        )
        for column_ref in parsed_sql.selected_columns
    ]

    aggregated_column_names: list[str] = []
    aggregation_functions: list[str] = []

    for item in aggregated_columns:
        if item.column not in aggregated_column_names:
            aggregated_column_names.append(item.column)

        if item.func and item.func not in aggregation_functions:
            aggregation_functions.append(item.func)

    return MeasureSemantics(
        aggregated_columns=aggregated_columns,
        selected_columns=selected_columns,
        aggregated_column_names=aggregated_column_names,
        aggregation_functions=aggregation_functions,
    )


def build_logic_semantics(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> LogicSemantics:
    group_by_columns = [
        annotated_column_use_from_column_ref(
            source="group_by",
            column_ref=column_ref,
            parsed_sql=parsed_sql,
            schema_store=schema_store,
        )
        for column_ref in parsed_sql.group_by
    ]

    order_by_columns = [
        annotated_column_use_from_order_by(
            order_by=order_by,
            parsed_sql=parsed_sql,
            schema_store=schema_store,
        )
        for order_by in parsed_sql.order_by
    ]

    return LogicSemantics(
        group_by_columns=group_by_columns,
        order_by_columns=order_by_columns,
        limit=parsed_sql.limit,
        date_conditions=parsed_sql.date_conditions,
    )


def build_empty_object_scope_semantics() -> ObjectScopeSemantics:
    return ObjectScopeSemantics(
        has_account_type_filter=False,
        account_type_values=[],
        has_transaction_type_filter=False,
        transaction_type_values=[],
        entity_filter_columns=[],
        entity_filter_values=[],
    )


def build_empty_measure_semantics() -> MeasureSemantics:
    return MeasureSemantics(
        aggregated_columns=[],
        selected_columns=[],
        aggregated_column_names=[],
        aggregation_functions=[],
    )


def build_empty_logic_semantics() -> LogicSemantics:
    return LogicSemantics(
        group_by_columns=[],
        order_by_columns=[],
        limit=None,
        date_conditions=[],
    )


def build_sql_financial_semantics(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> SQLFinancialSemantics:
    """
    Build the actual SQL financial semantics used by FinVeriSQL.

    This maps parsed SQL components to their financial meaning using schema
    annotations. The output is the actual SQL side that D1/D2/D3 compare
    against expected requirements from the user intent.
    """
    if parsed_sql.parse_error:
        return SQLFinancialSemantics(
            tables=parsed_sql.tables,
            joins=[join.to_dict() for join in parsed_sql.joins],
            object_scope=build_empty_object_scope_semantics(),
            measure_usage=build_empty_measure_semantics(),
            logic=build_empty_logic_semantics(),
            parse_error=parsed_sql.parse_error,
        )

    object_scope = build_object_scope_semantics(parsed_sql)
    measure = build_measure_semantics(parsed_sql, schema_store)
    logic = build_logic_semantics(parsed_sql, schema_store)

    return SQLFinancialSemantics(
        tables=parsed_sql.tables,
        joins=[join.to_dict() for join in parsed_sql.joins],
        object_scope=object_scope,
        measure_usage=measure,
        logic=logic,
        parse_error=None,
    )