from __future__ import annotations

from dataclasses import asdict, dataclass
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
    column: str
    table: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AggregationRef:
    func: str
    column: str | None
    table: str | None = None
    expression: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FilterRef:
    column: str
    table: str | None
    expression: str
    operator: str | None = None
    values: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class JoinRef:
    table: str
    alias: str | None
    join_type: str | None
    expression: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OrderByRef:
    expression: str
    direction: str | None
    column: str | None = None
    table: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParsedSQL:
    selected_columns: list[ColumnRef]
    aggregations: list[AggregationRef]
    tables: list[str]
    aliases: dict[str, str]
    joins: list[JoinRef]
    filters: list[FilterRef]
    date_conditions: list[str]
    group_by: list[ColumnRef]
    order_by: list[OrderByRef]
    limit: int | None
    raw_sql: str | None = None
    parse_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_columns": [item.to_dict() for item in self.selected_columns],
            "aggregations": [item.to_dict() for item in self.aggregations],
            "tables": self.tables,
            "aliases": self.aliases,
            "joins": [item.to_dict() for item in self.joins],
            "filters": [item.to_dict() for item in self.filters],
            "date_conditions": self.date_conditions,
            "group_by": [item.to_dict() for item in self.group_by],
            "order_by": [item.to_dict() for item in self.order_by],
            "limit": self.limit,
            "raw_sql": self.raw_sql,
            "parse_error": self.parse_error,
        }


def normalize_identifier(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = str(value).strip().strip('"').strip("'").strip("`")

    if not cleaned:
        return None

    return cleaned.lower()


def resolve_table(table_or_alias: str | None, aliases: dict[str, str]) -> str | None:
    if not table_or_alias:
        return None

    key = normalize_identifier(table_or_alias)

    if key in aliases:
        return aliases[key]

    return key


def parse_column(
    column_expr: exp.Column,
    aliases: dict[str, str] | None = None,
) -> ColumnRef:
    aliases = aliases or {}

    return ColumnRef(
        column=normalize_identifier(column_expr.name) or "",
        table=resolve_table(column_expr.table or None, aliases),
    )


def extract_aliases(tree: exp.Expression) -> dict[str, str]:
    aliases: dict[str, str] = {}

    for table in tree.find_all(exp.Table):
        table_name = normalize_identifier(table.name)
        alias = normalize_identifier(table.alias)

        if table_name and alias:
            aliases[alias] = table_name

    return aliases


def extract_tables(tree: exp.Expression) -> list[str]:
    tables: list[str] = []

    for table in tree.find_all(exp.Table):
        table_name = normalize_identifier(table.name)

        if table_name and table_name not in tables:
            tables.append(table_name)

    return tables


def extract_selected_columns(
    tree: exp.Expression,
    aliases: dict[str, str],
) -> list[ColumnRef]:
    selected_columns: list[ColumnRef] = []
    select_expr = tree.find(exp.Select)

    if select_expr is None:
        return selected_columns

    for expression in select_expr.expressions:
        for column in expression.find_all(exp.Column):
            col_ref = parse_column(column, aliases)

            if col_ref not in selected_columns:
                selected_columns.append(col_ref)

    return selected_columns


def extract_aggregations(
    tree: exp.Expression,
    aliases: dict[str, str],
) -> list[AggregationRef]:
    aggregations: list[AggregationRef] = []
    seen: set[tuple[str, str | None, str | None]] = set()

    for agg_expr in tree.find_all(exp.AggFunc):
        agg_name = agg_expr.sql_name().lower()
        first_column = next(agg_expr.find_all(exp.Column), None)

        column = None
        table = None

        if first_column is not None:
            parsed_column = parse_column(first_column, aliases)
            column = parsed_column.column
            table = parsed_column.table

        key = (agg_name, column, table)

        if key in seen:
            continue

        seen.add(key)

        aggregations.append(
            AggregationRef(
                func=agg_name,
                column=column,
                table=table,
                expression=agg_expr.sql(dialect="sqlite"),
            )
        )

    return aggregations


FILTER_PREDICATE_CLASSES = (
    exp.In,
    exp.EQ,
    exp.Between,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.Like,
    exp.Is,
)

if hasattr(exp, "NEQ"):
    FILTER_PREDICATE_CLASSES = FILTER_PREDICATE_CLASSES + (exp.NEQ,)


OPERATOR_MAP = {
    exp.In: "IN",
    exp.EQ: "=",
    exp.Between: "BETWEEN",
    exp.GT: ">",
    exp.GTE: ">=",
    exp.LT: "<",
    exp.LTE: "<=",
    exp.Like: "LIKE",
    exp.Is: "IS",
}

if hasattr(exp, "NEQ"):
    OPERATOR_MAP[exp.NEQ] = "!="


def infer_filter_operator(expression: exp.Expression) -> str | None:
    for operator_class, operator_symbol in OPERATOR_MAP.items():
        if isinstance(expression, operator_class):
            return operator_symbol

    return None


def extract_literal_values(expression: exp.Expression) -> list[str]:
    values: list[str] = []

    for literal in expression.find_all(exp.Literal):
        raw_value = literal.this

        if raw_value is not None:
            values.append(str(raw_value).strip("'").strip('"'))

    return values


def iter_filter_predicates(where_expr: exp.Where) -> list[exp.Expression]:
    predicates: list[exp.Expression] = []

    for predicate_class in FILTER_PREDICATE_CLASSES:
        for predicate in where_expr.find_all(predicate_class):
            predicates.append(predicate)

    deduped: list[exp.Expression] = []
    seen: set[str] = set()

    for predicate in predicates:
        predicate_sql = predicate.sql(dialect="sqlite")

        if predicate_sql not in seen:
            seen.add(predicate_sql)
            deduped.append(predicate)

    return deduped


def extract_filters(
    tree: exp.Expression,
    aliases: dict[str, str],
) -> list[FilterRef]:
    filters: list[FilterRef] = []

    for where_expr in tree.find_all(exp.Where):
        predicates = iter_filter_predicates(where_expr)

        for predicate in predicates:
            operator = infer_filter_operator(predicate)
            values = extract_literal_values(predicate)
            predicate_sql = predicate.sql(dialect="sqlite")

            for column in predicate.find_all(exp.Column):
                parsed_column = parse_column(column, aliases)

                filters.append(
                    FilterRef(
                        column=parsed_column.column,
                        table=parsed_column.table,
                        expression=predicate_sql,
                        operator=operator,
                        values=values,
                    )
                )

    deduped: list[FilterRef] = []
    seen: set[tuple[str, str | None, str, str | None, tuple[str, ...]]] = set()

    for item in filters:
        key = (
            item.column,
            item.table,
            item.expression,
            item.operator,
            tuple(item.values or []),
        )

        if key not in seen:
            seen.add(key)
            deduped.append(item)

    return deduped


def extract_date_conditions(filters: list[FilterRef]) -> list[str]:
    date_conditions: list[str] = []

    for filter_ref in filters:
        if "date" in filter_ref.column.lower():
            if filter_ref.expression not in date_conditions:
                date_conditions.append(filter_ref.expression)

    return date_conditions


def extract_joins(
    tree: exp.Expression,
    aliases: dict[str, str],
) -> list[JoinRef]:
    joins: list[JoinRef] = []

    for join in tree.find_all(exp.Join):
        table_expr = join.this

        table_name = None
        alias = None

        if isinstance(table_expr, exp.Table):
            table_name = normalize_identifier(table_expr.name)
            alias = normalize_identifier(table_expr.alias)

        join_kind = join.args.get("kind")

        joins.append(
            JoinRef(
                table=table_name or "",
                alias=alias,
                join_type=str(join_kind).lower() if join_kind else None,
                expression=join.sql(dialect="sqlite"),
            )
        )

    return joins


def extract_group_by(
    tree: exp.Expression,
    aliases: dict[str, str],
) -> list[ColumnRef]:
    group_by_columns: list[ColumnRef] = []
    group_expr = tree.find(exp.Group)

    if group_expr is None:
        return group_by_columns

    for column in group_expr.find_all(exp.Column):
        col_ref = parse_column(column, aliases)

        if col_ref not in group_by_columns:
            group_by_columns.append(col_ref)

    return group_by_columns


def extract_order_by(
    tree: exp.Expression,
    aliases: dict[str, str],
) -> list[OrderByRef]:
    order_items: list[OrderByRef] = []
    order_expr = tree.find(exp.Order)

    if order_expr is None:
        return order_items

    for ordered in order_expr.expressions:
        direction = "DESC" if ordered.args.get("desc") else "ASC"

        first_column = next(ordered.find_all(exp.Column), None)
        column = None
        table = None

        if first_column is not None:
            parsed_column = parse_column(first_column, aliases)
            column = parsed_column.column
            table = parsed_column.table

        order_items.append(
            OrderByRef(
                expression=ordered.sql(dialect="sqlite"),
                direction=direction,
                column=column,
                table=table,
            )
        )

    return order_items


def extract_limit(tree: exp.Expression) -> int | None:
    limit_expr = tree.find(exp.Limit)

    if limit_expr is None:
        return None

    literal = limit_expr.expression

    if isinstance(literal, exp.Literal):
        try:
            return int(literal.this)
        except Exception:
            return None

    return None


def empty_parsed_sql(
    sql: str | None = None,
    parse_error: str | None = None,
) -> ParsedSQL:
    return ParsedSQL(
        selected_columns=[],
        aggregations=[],
        tables=[],
        aliases={},
        joins=[],
        filters=[],
        date_conditions=[],
        group_by=[],
        order_by=[],
        limit=None,
        raw_sql=sql,
        parse_error=parse_error,
    )


def parse_sql(sql: str) -> ParsedSQL:
    try:
        tree = sqlglot.parse_one(sql, read="sqlite")
    except Exception as exc:
        return empty_parsed_sql(sql=sql, parse_error=str(exc))

    aliases = extract_aliases(tree)
    filters = extract_filters(tree, aliases)

    return ParsedSQL(
        selected_columns=extract_selected_columns(tree, aliases),
        aggregations=extract_aggregations(tree, aliases),
        tables=extract_tables(tree),
        aliases=aliases,
        joins=extract_joins(tree, aliases),
        filters=filters,
        date_conditions=extract_date_conditions(filters),
        group_by=extract_group_by(tree, aliases),
        order_by=extract_order_by(tree, aliases),
        limit=extract_limit(tree),
        raw_sql=sql,
        parse_error=None,
    )