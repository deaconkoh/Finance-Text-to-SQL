""" 
D3/Repair not finalized yet. Current implementation focuses on D1 and D2 rule checking and routing logic. D3 will require additional labelled data for computation logic errors, which is pending Group B annotation. The verify_sql function returns a VerificationReport that includes detected violations, their details, and the recommended route for repair or clarification.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from src.finverisql.intent import FinancialIntent
from src.finverisql.rule_maps import D1_OBJECT_RULES, D2_MEASURE_RULES
from src.finverisql.schema_loader import SchemaAnnotationStore
from src.finverisql.sql_parser import ParsedSQL, parse_sql


@dataclass
class Violation:
    dimension: str
    violation_type: str
    detail: str
    repair_hint: str | None = None
    ambiguous: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VerificationReport:
    violation_detected: bool
    violations: list[Violation]
    route: str
    parsed_sql: ParsedSQL
    intent: FinancialIntent
    column_annotations: list[dict[str, Any]]
    unannotated_columns: list[dict[str, Any]]
    d3_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "violation_detected": self.violation_detected,
            "violations": [violation.to_dict() for violation in self.violations],
            "route": self.route,
            "parsed_sql": self.parsed_sql.to_dict(),
            "intent": self.intent.to_dict(),
            "column_annotations": self.column_annotations,
            "unannotated_columns": self.unannotated_columns,
            "d3_status": self.d3_status,
        }


def annotate_column_refs(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> list[dict[str, Any]]:
    annotations: list[dict[str, Any]] = []

    candidate_tables = parsed_sql.tables

    column_refs = []

    for selected in parsed_sql.selected_columns:
        column_refs.append(selected)

    for filter_ref in parsed_sql.filters:
        column_refs.append(filter_ref)

    for aggregation in parsed_sql.aggregations:
        if aggregation.column:
            column_refs.append(aggregation)

    for column_ref in column_refs:
        matches = schema_store.annotate_column_reference(
            column=column_ref.column,
            table=column_ref.table,
            candidate_tables=candidate_tables,
        )

        annotations.extend(matches)

    unique_annotations = []
    seen = set()

    for annotation in annotations:
        key = (annotation.get("table"), annotation.get("column"))

        if key in seen:
            continue

        seen.add(key)
        unique_annotations.append(annotation)

    return unique_annotations

def find_unannotated_column_refs(
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> list[dict[str, Any]]:
    unannotated: list[dict[str, Any]] = []
    candidate_tables = parsed_sql.tables

    column_refs = []

    for selected in parsed_sql.selected_columns:
        column_refs.append(("selected_column", selected.column, selected.table))

    for filter_ref in parsed_sql.filters:
        column_refs.append(("filter_column", filter_ref.column, filter_ref.table))

    for aggregation in parsed_sql.aggregations:
        if aggregation.column:
            column_refs.append(("aggregation_column", aggregation.column, aggregation.table))

    seen = set()

    for source, column, table in column_refs:
        key = (source, table, column)

        if key in seen:
            continue

        seen.add(key)

        matches = schema_store.annotate_column_reference(
            column=column,
            table=table,
            candidate_tables=candidate_tables,
        )

        if not matches:
            unannotated.append(
                {
                    "source": source,
                    "table": table,
                    "column": column,
                    "flag": "no_annotation_found",
                }
            )

    return unannotated


def check_d1_financial_object(
    intent: FinancialIntent,
    parsed_sql: ParsedSQL,
    column_annotations: list[dict[str, Any]],
) -> list[Violation]:
    violations: list[Violation] = []

    rule = D1_OBJECT_RULES.get(intent.financial_object)

    if rule is None:
        return violations

    expected_entity_scope = rule["expected_entity_scope"]
    expected_account_type = rule["expected_account_type"]

    used_product_service_filter = any(
        annotation.get("entity_scope") == "product_service"
        for annotation in column_annotations
    )

    if expected_entity_scope == "account" and used_product_service_filter:
        violations.append(
            Violation(
                dimension="D1",
                violation_type="financial_object_error",
                detail=(
                    f"Intent expects an account-scoped financial object "
                    f"({intent.financial_object}), but SQL references a "
                    f"product_service-scoped column."
                ),
                repair_hint=(
                    "Use account-scoped columns such as Account or Account_type "
                    "instead of Product_Service when the question asks about "
                    "account categories."
                ),
            )
        )

    account_type_columns = [
        annotation
        for annotation in column_annotations
        if annotation.get("column", "").lower() == "account_type"
    ]

    if expected_account_type and account_type_columns:
        return violations

    # Conservative signal only. Do not force every SQL to join chart_of_accounts.
    return violations


def check_d2_financial_measure(
    intent: FinancialIntent,
    parsed_sql: ParsedSQL,
    schema_store: SchemaAnnotationStore,
) -> list[Violation]:
    violations: list[Violation] = []

    rule = D2_MEASURE_RULES.get(intent.financial_measure)

    if rule is None or intent.financial_measure == "none":
        return violations

    if intent.financial_measure == "count":
        has_count = any(agg.func == "count" for agg in parsed_sql.aggregations)

        if not has_count:
            violations.append(
                Violation(
                    dimension="D2",
                    violation_type="financial_measure_error",
                    detail="Intent expects a count measure, but SQL does not use COUNT.",
                    repair_hint="Use COUNT for count-based questions.",
                )
            )

        return violations

    if rule.get("ambiguous"):
        violations.append(
            Violation(
                dimension="D2",
                violation_type="financial_measure_ambiguous",
                detail=(
                    f"Intent financial_measure is {intent.financial_measure}, "
                    "which is ambiguous because it has no directional sign convention."
                ),
                repair_hint=(
                    "Clarify whether the question requires Credit, Debit, "
                    "Open_balance, or a generic Amount."
                ),
                ambiguous=True,
            )
        )
        return violations

    expected_sign_convention = rule["expected_sign_convention"]
    expected_measure_type = rule["expected_measure_type"]

    if parsed_sql.aggregations:
        measure_refs = parsed_sql.aggregations
    else:
        measure_refs = parsed_sql.selected_columns

    for measure_ref in measure_refs:
        column = getattr(measure_ref, "column", None)

        if not column:
            continue

        matches = schema_store.annotate_column_reference(
            column=column,
            table=getattr(measure_ref, "table", None),
            candidate_tables=parsed_sql.tables,
        )

        for annotation in matches:
            actual_sign_convention = annotation.get("sign_convention")
            actual_measure_type = annotation.get("measure_type")

            # Ignore non-measure columns such as dates, transaction type,
            # account names, product/service fields, and IDs.
            if actual_measure_type in {None, "none"}:
                continue

            sign_matches = actual_sign_convention == expected_sign_convention
            measure_matches = actual_measure_type == expected_measure_type

            if sign_matches and measure_matches:
                continue

            violations.append(
                Violation(
                    dimension="D2",
                    violation_type="financial_measure_error",
                    detail=(
                        f"Intent expects financial_measure={intent.financial_measure} "
                        f"with sign_convention={expected_sign_convention} and "
                        f"measure_type={expected_measure_type}, but SQL uses "
                        f"{annotation.get('table')}.{annotation.get('column')} "
                        f"with sign_convention={actual_sign_convention} and "
                        f"measure_type={actual_measure_type}."
                    ),
                    repair_hint=(
                        f"Use a column with sign_convention={expected_sign_convention} "
                        f"and measure_type={expected_measure_type}."
                    ),
                )
            )

    return violations


def determine_route(violations: list[Violation]) -> str:
    if not violations:
        return "no_repair"

    if any(violation.ambiguous for violation in violations):
        return "abstain_or_clarify"

    dimensions = {violation.dimension for violation in violations}

    if len(dimensions) == 1:
        return "single_rule_repair"

    return "multi_dimension_repair"


def run_d3_placeholder() -> str:
    return (
        "not_run: D3 requires labelled computation_logic_error examples. "
        "Implement after Group B labels are available."
    )


def verify_sql(
    question: str,
    generated_sql: str,
    intent: FinancialIntent,
    schema_store: SchemaAnnotationStore,
) -> VerificationReport:
    parsed_sql = parse_sql(generated_sql)

    if parsed_sql.parse_error:
        violation = Violation(
            dimension="parse",
            violation_type="sql_parse_error",
            detail=parsed_sql.parse_error,
            repair_hint=None,
        )

        return VerificationReport(
            violation_detected=True,
            violations=[violation],
            route="non_executable_or_parse_error",
            parsed_sql=parsed_sql,
            intent=intent,
            column_annotations=[],
            unannotated_columns=[],
            d3_status="not_run",
        )

    column_annotations = annotate_column_refs(
        parsed_sql=parsed_sql,
        schema_store=schema_store,
    )
    
    unannotated_columns = find_unannotated_column_refs(
        parsed_sql=parsed_sql,
        schema_store=schema_store,
    )

    violations: list[Violation] = []

    violations.extend(
        check_d1_financial_object(
            intent=intent,
            parsed_sql=parsed_sql,
            column_annotations=column_annotations,
        )
    )

    violations.extend(
        check_d2_financial_measure(
            intent=intent,
            parsed_sql=parsed_sql,
            schema_store=schema_store,
        )
    )

    if violations:
        d3_status = "not_run: D1/D2 violation detected"
    else:
        d3_status = run_d3_placeholder()

    return VerificationReport(
        violation_detected=bool(violations),
        violations=violations,
        route=determine_route(violations),
        parsed_sql=parsed_sql,
        intent=intent,
        column_annotations=column_annotations,
        unannotated_columns=unannotated_columns,
        d3_status=d3_status,
    )