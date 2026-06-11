"""Parse generated SQL into a compact structural representation.

This module uses `sqlglot` to extract selected columns, aggregate expressions,
tables, aliases, joins, filters, grouping, ordering, limits, parse errors, and
unsupported-lineage markers from candidate SQL.

Main input is SQL text from a baseline Text-to-SQL model. Main output is
`ParsedSQL`, which describes SQL structure only; schema-grounded financial
meaning is added later by `sql_semantic_mapping.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

try:
    import sqlglot
    from sqlglot import exp
except ImportError as exc:
    raise ImportError(
        "sqlglot is required for FinVeriSQL SQL parsing. "
        "Install it with: pip install sqlglot"
    ) from exc


@dataclass(frozen=True)
class ColumnRef:
    """Reference to a SQL column, optionally qualified by a resolved table.

    Args:
        column: Column name as parsed from the SQL expression.
        table: Resolved table name when a qualifier or alias is available.

    Assumption:
        Table aliases have already been expanded to base table names when
        possible.
    """

    column: str
    table: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dictionary representation."""
        return asdict(self)


@dataclass
class AggregationRef:
    """Aggregate expression extracted from the SELECT tree.

    Args:
        func: Normalized aggregate function name, such as `sum` or `count`.
        expression: SQLite-rendered aggregate expression.
        columns: Column references found inside the aggregate expression.

    Edge cases:
        `COUNT(*)` and other column-free aggregates may have an empty `columns`
        list.
    """

    func: str
    expression: str
    columns: list[ColumnRef]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dictionary representation."""
        return {
            "func": self.func,
            "expression": self.expression,
            "columns": [column.to_dict() for column in self.columns],
        }


@dataclass
class FilterRef:
    """WHERE-predicate reference extracted from a comparison expression.

    Args:
        columns: All columns found in the predicate, not only the leftmost one.
        primary_column: First deduplicated column, retained for legacy callers.
        operator: Normalized comparison operator.
        values: Literal, function, or subquery values from the predicate.
        expression: SQLite-rendered predicate expression.

    Important:
        All predicate columns are preserved so computed conditions such as
        `Quantity * Rate > 10000` keep both column references for semantic
        mapping.
    """

    columns: list[ColumnRef]
    primary_column: ColumnRef | None
    operator: str | None
    values: list[Any]
    expression: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dictionary representation."""
        return {
            "columns": [col.to_dict() for col in self.columns],
            "primary_column": self.primary_column.to_dict() if self.primary_column else None,
            "operator": self.operator,
            "values": self.values,
            "expression": self.expression,
        }

@dataclass
class ParsedSQL:
    """Serializable SQL structure extracted from a candidate query.

    Args:
        selected_columns: Column references used by SELECT expressions.
        aggregations: Aggregate function references in the query.
        tables: Base tables found in the query.
        aliases: Mapping from SQL aliases to base table names.
        joins: Join targets and ON expressions.
        filters: WHERE comparison predicates.
        group_by: GROUP BY column references.
        order_by: SQLite-rendered ORDER BY expressions.
        limit: Integer LIMIT value, if statically available.
        raw_sql: Original SQL text.
        parse_error: Parser error string when `sqlglot` cannot parse the SQL.
        unsupported_lineage: Whether the query uses CTEs or subqueries that v1
            cannot trace through safely.

    Assumption:
        This object describes SQL structure only. Schema-level financial meaning
        is added later in `sql_semantic_mapping.py`.
    """

    selected_columns: list[ColumnRef]
    aggregations: list[AggregationRef]
    tables: list[str]
    aliases: dict[str, str]
    joins: list[JoinRef]
    filters: list[FilterRef]
    group_by: list[ColumnRef]
    order_by: list[str]
    limit: int | None
    raw_sql: str
    parse_error: str | None = None
    unsupported_lineage: bool = False  # Added flag

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dictionary representation."""
        return {
            "selected_columns": [item.to_dict() for item in self.selected_columns],
            "aggregations": [item.to_dict() for item in self.aggregations],
            "tables": self.tables,
            "aliases": self.aliases,
            "joins": [item.to_dict() for item in self.joins],
            "filters": [item.to_dict() for item in self.filters],
            "group_by": [item.to_dict() for item in self.group_by],
            "order_by": self.order_by,
            "limit": self.limit,
            "raw_sql": self.raw_sql,
            "parse_error": self.parse_error,
            "unsupported_lineage": self.unsupported_lineage,
        }

@dataclass
class JoinRef:
    """Join target and ON condition extracted from a SQL query.

    Args:
        table: Joined table name, if `sqlglot` exposes a table target.
        on_expression: SQLite-rendered ON expression, if present.
    """

    table: str | None
    on_expression: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dictionary representation."""
        return asdict(self)


COMPARISON_OPERATORS = {
    exp.EQ: "=",
    exp.NEQ: "!=",
    exp.GT: ">",
    exp.GTE: ">=",
    exp.LT: "<",
    exp.LTE: "<=",
    exp.Like: "LIKE",
    exp.ILike: "ILIKE",
    exp.In: "IN",
    exp.Between: "BETWEEN",
    exp.Is: "IS",
}


def _dedupe_columns(columns: list[ColumnRef]) -> list[ColumnRef]:
    seen: set[tuple[str | None, str]] = set()
    result: list[ColumnRef] = []

    for column in columns:
        key = (column.table, column.column)

        if key not in seen:
            seen.add(key)
            result.append(column)

    return result


def _resolve_table_name(table: str | None, aliases: dict[str, str]) -> str | None:
    if table is None:
        return None

    return aliases.get(table, table)


def _parse_column(column_expr: exp.Column, aliases: dict[str, str]) -> ColumnRef:
    table = column_expr.table or None

    return ColumnRef(
        column=column_expr.name,
        table=_resolve_table_name(table, aliases),
    )


def _extract_aliases(tree: exp.Expression) -> dict[str, str]:
    aliases: dict[str, str] = {}

    for table_expr in tree.find_all(exp.Table):
        table_name = table_expr.name

        if not table_name:
            continue

        alias = table_expr.alias

        if alias:
            aliases[alias] = table_name

    return aliases


def _extract_tables(tree: exp.Expression) -> list[str]:
    tables: list[str] = []

    for table_expr in tree.find_all(exp.Table):
        table_name = table_expr.name

        if table_name and table_name not in tables:
            tables.append(table_name)

    return tables


def _extract_selected_columns(tree: exp.Expression, aliases: dict[str, str]) -> list[ColumnRef]:
    selected_columns: list[ColumnRef] = []

    select_expr = tree.find(exp.Select)

    if select_expr is None:
        return selected_columns

    for expression in select_expr.expressions:
        for column in expression.find_all(exp.Column):
            selected_columns.append(_parse_column(column, aliases))

    return _dedupe_columns(selected_columns)


def _aggregation_name(agg_expr: exp.Expression) -> str:
    name = agg_expr.key or agg_expr.__class__.__name__
    name = str(name).lower()

    if name == "avg":
        return "average"

    return name


def _extract_aggregations(tree: exp.Expression, aliases: dict[str, str]) -> list[AggregationRef]:
    aggregations: list[AggregationRef] = []

    for agg_expr in tree.find_all(exp.AggFunc):
        columns = [
            _parse_column(column, aliases)
            for column in agg_expr.find_all(exp.Column)
        ]

        aggregations.append(
            AggregationRef(
                func=_aggregation_name(agg_expr),
                expression=agg_expr.sql(dialect="sqlite"),
                columns=_dedupe_columns(columns),
            )
        )

    return aggregations


def _extract_literal_value(expression: exp.Expression | None) -> Any:
    if expression is None:
        return None

    if isinstance(expression, exp.Literal):
        return expression.this

    if isinstance(expression, exp.Boolean):
        return expression.this

    if isinstance(expression, exp.Null):
        return None

    if isinstance(expression, exp.Date):
        return expression.sql(dialect="sqlite")

    if isinstance(expression, exp.Anonymous):
        return expression.sql(dialect="sqlite")

    return expression.sql(dialect="sqlite")


def _extract_values_from_predicate(predicate: exp.Expression) -> list[Any]:
    if isinstance(predicate, exp.In):
        values = []

        expressions = predicate.expressions or []

        for expression in expressions:
            values.append(_extract_literal_value(expression))

        query = predicate.args.get("query")

        if query is not None:
            values.append(query.sql(dialect="sqlite"))

        return values

    if isinstance(predicate, exp.Between):
        low = predicate.args.get("low")
        high = predicate.args.get("high")

        return [
            _extract_literal_value(low),
            _extract_literal_value(high),
        ]

    if isinstance(predicate, exp.Is):
        return [_extract_literal_value(predicate.expression)]

    right = predicate.expression

    return [_extract_literal_value(right)]







def _extract_group_by(tree: exp.Expression, aliases: dict[str, str]) -> list[ColumnRef]:
    group_columns: list[ColumnRef] = []

    group_expr = tree.find(exp.Group)

    if group_expr is None:
        return group_columns

    for expression in group_expr.expressions:
        for column in expression.find_all(exp.Column):
            group_columns.append(_parse_column(column, aliases))

    return _dedupe_columns(group_columns)


def _extract_order_by(tree: exp.Expression) -> list[str]:
    order_by: list[str] = []

    order_expr = tree.find(exp.Order)

    if order_expr is None:
        return order_by

    for ordered in order_expr.expressions:
        order_by.append(ordered.sql(dialect="sqlite"))

    return order_by


def _extract_limit(tree: exp.Expression) -> int | None:
    limit_expr = tree.find(exp.Limit)

    if limit_expr is None:
        return None

    expression = limit_expr.expression

    if isinstance(expression, exp.Literal):
        try:
            return int(expression.this)
        except ValueError:
            return None

    return None


def _extract_joins(tree: exp.Expression) -> list[JoinRef]:
    joins: list[JoinRef] = []

    for join_expr in tree.find_all(exp.Join):
        table_expr = join_expr.this
        table_name = table_expr.name if isinstance(table_expr, exp.Table) else None

        on_expr = join_expr.args.get("on")

        joins.append(
            JoinRef(
                table=table_name,
                on_expression=on_expr.sql(dialect="sqlite") if on_expr is not None else None,
            )
        )

    return joins

def _detect_unsupported_lineage(tree: exp.Expression) -> bool:
    """
    Detects CTEs (WITH) or subqueries that break flat alias tracking.
    """
    if tree.find(exp.With):
        return True
    if tree.find(exp.Subquery):
        return True
    return False

def _extract_filters(tree: exp.Expression, aliases: dict[str, str]) -> list[FilterRef]:
    filters: list[FilterRef] = []
    where_expr = tree.find(exp.Where)

    if where_expr is None:
        return filters

    for predicate_class, operator in COMPARISON_OPERATORS.items():
        for predicate in where_expr.find_all(predicate_class):
            # Capture every column in the predicate, not just a left-hand side.
            # This is important for FSIR because computed filters can carry
            # financial meaning through multiple fields.
            columns = [
                _parse_column(column_expr, aliases) 
                for column_expr in predicate.find_all(exp.Column)
            ]
            
            deduped_columns = _dedupe_columns(columns)
            primary_col = deduped_columns[0] if deduped_columns else None

            filters.append(
                FilterRef(
                    columns=deduped_columns,
                    primary_column=primary_col,
                    operator=operator,
                    values=_extract_values_from_predicate(predicate),
                    expression=predicate.sql(dialect="sqlite"),
                )
            )

    return filters

def parse_sql(sql: str) -> ParsedSQL:
    """Parse SQL text into FinVeriSQL's structural representation.

    Args:
        sql: Candidate SQL generated by a baseline model.

    Returns:
        `ParsedSQL` with extracted structural fields. If parsing fails, the
        returned object contains empty structural lists and `parse_error`.

    Edge cases:
        CTEs and subqueries are parseable but marked as `unsupported_lineage`
        because the current mapper cannot safely resolve derived columns back
        to annotated base schema columns.
    """
    raw_sql = sql or ""

    try:
        tree = sqlglot.parse_one(raw_sql, read="sqlite")
    except Exception as exc:
        return ParsedSQL(
            selected_columns=[], aggregations=[], tables=[], aliases={}, joins=[],
            filters=[], group_by=[], order_by=[], limit=None, raw_sql=raw_sql,
            parse_error=str(exc), unsupported_lineage=False
        )

    aliases = _extract_aliases(tree)
    has_unsupported_lineage = _detect_unsupported_lineage(tree)

    # Keep parsing flat facts even when lineage is unsupported; downstream FSIR
    # construction can surface available evidence while the verifier abstains.
    return ParsedSQL(
        selected_columns=_extract_selected_columns(tree, aliases),
        aggregations=_extract_aggregations(tree, aliases),
        tables=_extract_tables(tree),
        aliases=aliases,
        joins=_extract_joins(tree),
        filters=_extract_filters(tree, aliases),
        group_by=_extract_group_by(tree, aliases),
        order_by=_extract_order_by(tree),
        limit=_extract_limit(tree),
        raw_sql=raw_sql,
        parse_error=None,
        unsupported_lineage=has_unsupported_lineage,
    )
