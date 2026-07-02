#!/usr/bin/env python3
"""Audit Eq_acct_v1 fixture-pipeline abstentions.

This script does not change ASA scoring. It reruns Eq_acct_v1 with debug
diagnostics for EX-passing ASA rows, then summarizes where rows leave the
pipeline: activation, fixture construction, support validation, mutant-suite
validation, or generated-SQL execution.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.asa_metrics.old.eq_acct_v1 import TEMPLATE_NAMES, evaluate_eq_acct_v1


IMPORTANT_REASONS = [
    "no_applicable_template",
    "no_valid_fixture_state",
    "gold_sql_execution_error_on_fixture",
    "gold_result_empty",
    "gold_result_all_null",
    "gold_literal_not_seeded",
    "fixture_schema_invalid",
    "accounting_constraint_violation",
    "fixture_not_discriminative",
    "mutant_generation_not_supported",
    "mutants_not_distinguished",
    "unsupported_sql_feature",
    "timeout",
]

ENTITY_LITERAL_COLUMNS = {
    "account",
    "account_name",
    "account_type",
    "customers",
    "customer_name",
    "customer_full_name",
    "vendor",
    "vendor_name",
    "product_service",
    "payment_method",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Debug why Eq_acct_v1 returns None / eq_acct_not_tested."
    )
    parser.add_argument(
        "--asa-rows",
        required=True,
        help="ASA rows JSONL with gold_sql, generated_sql, execution_match/EX, and optional ASA fields.",
    )
    parser.add_argument(
        "--schema-annotations",
        default="data/booksql/schema_annotations.json",
        help="Schema annotation JSON path.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/outputs/metrics_experiments/exp02_asa_fcr_tsa_v2/debug",
        help="Output directory. If it exists, a timestamped subdirectory is used unless --overwrite is set.",
    )
    parser.add_argument("--fixture-date", default="2026-06-23")
    parser.add_argument("--max-progress-steps", type=int, default=2_000_000)
    parser.add_argument("--progress-check-interval", type=int, default=1000)
    parser.add_argument("--max-examples-per-reason", type=int, default=20)
    parser.add_argument("--limit", type=int, default=None, help="Optional row limit for smoke/debug runs.")
    parser.add_argument("--overwrite", action="store_true", help="Allow writing directly into an existing output directory.")
    return parser.parse_args()


def read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object at {path}")
    return data


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_number}")
            rows.append(row)
    return rows


def resolve_output_dir(path: str | Path, overwrite: bool) -> Path:
    output_dir = Path(path)
    if output_dir.exists() and not overwrite:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = output_dir / f"run_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def ex_value(row: dict[str, Any]) -> int | None:
    if row.get("EX") in {0, 1}:
        return int(row["EX"])
    match = row.get("execution_match")
    if match is True:
        return 1
    if match is False:
        return 0
    return None


def truncate(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def pct(count: int | float, denominator: int | float) -> float:
    return count / denominator if denominator else 0.0


def sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def nested_sorted_counter(counter: Counter[str], limit: int | None = None) -> dict[str, int]:
    items = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    if limit is not None:
        items = items[:limit]
    return dict(items)


def build_debug_summary(result: dict[str, Any]) -> str:
    applicable = result.get("applicable_templates", [])
    validated = result.get("validated_templates", [])
    tested = result.get("tested_templates", [])
    if result.get("eq_acct_result") in {0, 1}:
        return f"Eq_acct decided after testing {result.get('tested_fixture_state_count', 0)} validated states."
    if not applicable:
        return "No accounting template activated from gold SQL/schema evidence."
    if not result.get("state_validation_records"):
        return "Templates activated, but no fixture state records were produced."
    if not result.get("usable_fixture_state_count"):
        reasons = Counter(
            str(record.get("reason"))
            for record in result.get("state_validation_records", [])
            if record.get("reason")
        )
        return f"Fixture states were built but none were support-valid; top state reasons: {nested_sorted_counter(reasons, 3)}."
    if not validated:
        reasons = Counter(
            str(item.get("not_testable_reason"))
            for item in result.get("template_results", {}).values()
            if item.get("not_testable_reason")
        )
        return f"Support-valid states existed, but no template suite passed mutant validation; top suite reasons: {nested_sorted_counter(reasons, 3)}."
    if not tested:
        return "At least one suite validated, but generated SQL execution was not reached."
    return "Eq_acct returned None after reaching an unexpected partial-test state."


def literal_domain_flags(evidence: dict[str, Any]) -> dict[str, Any]:
    literals = evidence.get("gold_literals_by_column", {}) or {}
    columns = {str(column).lower() for column in literals}
    return {
        "literal_columns": sorted(literals),
        "has_account_literal": bool(columns & {"account", "account_name", "account_type"}),
        "has_customer_literal": bool(columns & {"customers", "customer_name", "customer_full_name"}),
        "has_vendor_literal": bool(columns & {"vendor", "vendor_name"}),
        "has_product_literal": "product_service" in columns,
        "has_payment_literal": "payment_method" in columns,
        "entity_literals": {
            column: values
            for column, values in sorted(literals.items())
            if str(column).lower() in ENTITY_LITERAL_COLUMNS
        },
    }


def audit_rows(
    rows: list[dict[str, Any]],
    schema_annotations: dict[str, Any],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    audited: list[dict[str, Any]] = []
    selected_rows = rows[: args.limit] if args.limit is not None else rows
    ex_pass_seen = 0
    for index, row in enumerate(selected_rows):
        ex = ex_value(row)
        base = {
            "input_index": index,
            "question_id": row.get("question_id", index),
            "question": row.get("question"),
            "gold_sql": row.get("gold_sql") or "",
            "generated_sql": row.get("generated_sql") or row.get("pred_sql") or "",
            "execution_match": row.get("execution_match"),
            "EX": ex,
            "Inv": row.get("Inv"),
            "asa_strict": row.get("asa_strict"),
        }
        if ex != 1:
            audited.append({**base, "eq_acct_debug": None})
            continue

        ex_pass_seen += 1
        if ex_pass_seen % 100 == 0:
            print(f"Audited {ex_pass_seen} EX-pass rows...", flush=True)
        result = evaluate_eq_acct_v1(
            gold_sql=base["gold_sql"],
            generated_sql=base["generated_sql"],
            schema_annotations=schema_annotations,
            fixture_date=args.fixture_date,
            max_progress_steps=args.max_progress_steps,
            progress_check_interval=args.progress_check_interval,
            include_debug=True,
        )
        audited.append({**base, "eq_acct_debug": result})
    return audited


def aggregate_audit(audited: list[dict[str, Any]]) -> dict[str, Any]:
    total_rows = len(audited)
    ex_pass = [row for row in audited if row.get("EX") == 1]
    ex_fail = [row for row in audited if row.get("EX") == 0]
    debug_rows = [row for row in ex_pass if row.get("eq_acct_debug")]

    eq_counts: Counter[str] = Counter()
    inv_counts: Counter[str] = Counter()
    asa_counts: Counter[str] = Counter()
    for row in audited:
        result = row.get("eq_acct_debug") or {}
        eq_value = result.get("eq_acct_result", row.get("Eq_acct"))
        inv_value = row.get("Inv")
        asa_value = row.get("asa_strict")
        eq_counts["None" if eq_value is None else str(eq_value)] += 1
        inv_counts["None" if inv_value is None else str(inv_value)] += 1
        asa_counts["None" if asa_value is None else str(asa_value)] += 1

    semantic_testable = [
        row
        for row in audited
        if (row.get("eq_acct_debug") or {}).get("eq_acct_result") in {0, 1}
        and row.get("Inv") in {0, 1}
    ]

    funnel = {
        "ex_pass_rows": len(ex_pass),
        "rows_with_applicable_template": sum(1 for row in debug_rows if row["eq_acct_debug"].get("applicable_templates")),
        "rows_with_fixture_state_constructed": sum(1 for row in debug_rows if (row["eq_acct_debug"].get("debug") or {}).get("fixture_states")),
        "rows_with_usable_support_valid_state": sum(1 for row in debug_rows if row["eq_acct_debug"].get("usable_fixture_state_count", 0) > 0),
        "rows_with_mutant_validated_template_suite": sum(1 for row in debug_rows if row["eq_acct_debug"].get("validated_templates")),
        "rows_with_generated_sql_executed": sum(1 for row in debug_rows if row["eq_acct_debug"].get("tested_fixture_state_count", 0) > 0),
        "eq_acct_pass_rows": sum(1 for row in debug_rows if row["eq_acct_debug"].get("eq_acct_result") == 1),
        "eq_acct_fail_rows": sum(1 for row in debug_rows if row["eq_acct_debug"].get("eq_acct_result") == 0),
        "eq_acct_none_rows": sum(1 for row in debug_rows if row["eq_acct_debug"].get("eq_acct_result") is None),
    }

    row_reason_counts: Counter[str] = Counter()
    template_reason_counts: Counter[str] = Counter()
    state_reason_counts: Counter[str] = Counter()
    template_stats: dict[str, dict[str, Any]] = {
        template: {
            "template": template,
            "activated_row_count": 0,
            "fixture_states_attempted": 0,
            "support_valid_usable_states": 0,
            "invalid_states": 0,
            "mutant_validated_template_suites": 0,
            "generated_tested_states": 0,
            "eq_acct_failures": 0,
            "invalid_state_reasons": Counter(),
            "template_suite_failure_reasons": Counter(),
        }
        for template in TEMPLATE_NAMES
    }

    gold_support: dict[str, Any] = {
        "state_reason_counts": Counter(),
        "state_reason_by_template": defaultdict(Counter),
        "literal_columns_by_reason": defaultdict(Counter),
        "date_predicate_rows_by_reason": Counter(),
        "entity_literal_rows_by_reason": defaultdict(Counter),
        "join_or_parent_like_errors": Counter(),
    }
    mutant_failures: dict[str, Any] = {
        "by_template": defaultdict(Counter),
        "family_generation": defaultdict(Counter),
        "execution_status": defaultdict(Counter),
    }

    for row in debug_rows:
        result = row["eq_acct_debug"]
        reasons = result.get("not_testable_reason_counts", {}) or {}
        row_reason_counts.update(reasons.keys())
        evidence = (result.get("debug") or {}).get("evidence", {})
        literal_flags = literal_domain_flags(evidence)
        has_date = bool(evidence.get("date_predicates"))

        for template in result.get("applicable_templates", []):
            template_stats[template]["activated_row_count"] += 1
        for record in result.get("state_validation_records", []):
            template = record.get("template")
            reason = record.get("reason")
            if template in template_stats:
                template_stats[template]["fixture_states_attempted"] += 1
                if record.get("valid"):
                    template_stats[template]["support_valid_usable_states"] += 1
                else:
                    template_stats[template]["invalid_states"] += 1
                    if reason:
                        template_stats[template]["invalid_state_reasons"].update([str(reason)])
            if reason:
                reason = str(reason)
                state_reason_counts.update([reason])
                gold_support["state_reason_counts"].update([reason])
                gold_support["state_reason_by_template"][str(template)].update([reason])
                for column in literal_flags["literal_columns"]:
                    gold_support["literal_columns_by_reason"][reason].update([column])
                if has_date:
                    gold_support["date_predicate_rows_by_reason"].update([reason])
                for key in [
                    "has_account_literal",
                    "has_customer_literal",
                    "has_vendor_literal",
                    "has_product_literal",
                    "has_payment_literal",
                ]:
                    if literal_flags[key]:
                        gold_support["entity_literal_rows_by_reason"][reason].update([key])
                error = str(record.get("error") or "")
                if "foreign key" in error.lower() or "no such" in error.lower():
                    gold_support["join_or_parent_like_errors"].update([error])

        for template, template_result in (result.get("template_results") or {}).items():
            suite_reason = template_result.get("not_testable_reason")
            if template_result.get("validated"):
                template_stats[template]["mutant_validated_template_suites"] += 1
            elif suite_reason:
                template_reason_counts.update([str(suite_reason)])
                template_stats[template]["template_suite_failure_reasons"].update([str(suite_reason)])
                mutant_failures["by_template"][template].update([str(suite_reason)])
            summary = template_result.get("mutant_validation") or {}
            for generation_record in summary.get("mutant_generation_records", []):
                family = generation_record.get("family")
                if generation_record.get("generated"):
                    mutant_failures["family_generation"][str(family)].update(["generated"])
                else:
                    mutant_failures["family_generation"][str(family)].update([str(generation_record.get("skip_reason"))])
            for execution_record in summary.get("mutant_execution_records", []):
                mutant_failures["execution_status"][template].update([str(execution_record.get("status"))])

        for execution_record in (result.get("debug") or {}).get("generated_execution_records", []):
            template = execution_record.get("template")
            if template in template_stats:
                template_stats[template]["generated_tested_states"] += 1
        for template in result.get("failed_templates", []):
            template_stats[template]["eq_acct_failures"] += 1

    template_breakdown = {}
    for template, stats in template_stats.items():
        template_breakdown[template] = {
            key: (sorted_counter(value) if isinstance(value, Counter) else value)
            for key, value in stats.items()
        }

    return {
        "overall_row_counts": {
            "total_rows": total_rows,
            "ex_pass_rows": len(ex_pass),
            "ex_fail_rows": len(ex_fail),
            "eq_acct_counts": dict(eq_counts),
            "inv_counts": dict(inv_counts),
            "asa_strict_counts": dict(asa_counts),
            "semantic_testability_count": len(semantic_testable),
            "semantic_testability_rate": pct(len(semantic_testable), len(ex_pass)),
        },
        "eq_acct_pipeline_funnel": funnel,
        "template_breakdown": template_breakdown,
        "not_tested_reason_breakdown": {
            "row_level": sorted_counter(row_reason_counts),
            "template_level": sorted_counter(template_reason_counts),
            "state_level": sorted_counter(state_reason_counts),
            "important_reasons": {
                reason: {
                    "row_level": row_reason_counts.get(reason, 0),
                    "template_level": template_reason_counts.get(reason, 0),
                    "state_level": state_reason_counts.get(reason, 0),
                }
                for reason in IMPORTANT_REASONS
            },
        },
        "gold_support_failure_analysis": {
            "state_reason_counts": sorted_counter(gold_support["state_reason_counts"]),
            "state_reason_by_template": {
                template: sorted_counter(counter)
                for template, counter in sorted(gold_support["state_reason_by_template"].items())
            },
            "literal_columns_by_reason": {
                reason: sorted_counter(counter)
                for reason, counter in sorted(gold_support["literal_columns_by_reason"].items())
            },
            "date_predicate_rows_by_reason": sorted_counter(gold_support["date_predicate_rows_by_reason"]),
            "entity_literal_rows_by_reason": {
                reason: sorted_counter(counter)
                for reason, counter in sorted(gold_support["entity_literal_rows_by_reason"].items())
            },
            "join_or_parent_like_errors": sorted_counter(gold_support["join_or_parent_like_errors"]),
        },
        "mutant_validation_failure_analysis": {
            "by_template": {
                template: sorted_counter(counter)
                for template, counter in sorted(mutant_failures["by_template"].items())
            },
            "family_generation": {
                family: sorted_counter(counter)
                for family, counter in sorted(mutant_failures["family_generation"].items())
            },
            "execution_status": {
                template: sorted_counter(counter)
                for template, counter in sorted(mutant_failures["execution_status"].items())
            },
        },
    }


def bottleneck_for_template(stats: dict[str, Any]) -> str:
    if stats["activated_row_count"] == 0:
        return "not activated"
    if stats["fixture_states_attempted"] == 0:
        return "state construction"
    if stats["support_valid_usable_states"] == 0:
        return "support validation"
    if stats["mutant_validated_template_suites"] == 0:
        return "mutant validation"
    if stats["generated_tested_states"] == 0:
        return "generated execution"
    return "tested"


def select_examples(
    audited: list[dict[str, Any]],
    max_examples_per_reason: int,
) -> list[dict[str, Any]]:
    categories = [
        "gold_result_empty",
        "gold_result_all_null",
        "gold_literal_not_seeded",
        "mutants_not_distinguished",
        "mutant_generation_not_supported",
        "unsupported_sql_feature",
        "activated_but_none_validate",
        "customer_vendor_validates",
    ]
    selected: dict[tuple[Any, str], dict[str, Any]] = {}
    counts: Counter[str] = Counter()

    def add(row: dict[str, Any], category: str) -> None:
        if counts[category] >= max_examples_per_reason:
            return
        result = row.get("eq_acct_debug") or {}
        key = (row.get("question_id"), category)
        selected[key] = {
            "category": category,
            "question_id": row.get("question_id"),
            "question": row.get("question"),
            "gold_sql": row.get("gold_sql"),
            "generated_sql": row.get("generated_sql"),
            "execution_match": row.get("execution_match"),
            "eq_acct_result": result.get("eq_acct_result"),
            "applicable_templates": result.get("applicable_templates", []),
            "validated_templates": result.get("validated_templates", []),
            "tested_templates": result.get("tested_templates", []),
            "not_testable_reason_counts": result.get("not_testable_reason_counts", {}),
            "template_results": result.get("template_results", {}),
            "state_validation_records": result.get("state_validation_records", []),
            "mutant_validation_summary": result.get("mutant_validation_summary", {}),
            "gold_result_preview": result.get("gold_result_preview"),
            "mutant_result_previews": result.get("mutant_result_previews"),
            "evidence": (result.get("debug") or {}).get("evidence", {}),
            "debug_summary": build_debug_summary(result),
        }
        counts[category] += 1

    for row in audited:
        result = row.get("eq_acct_debug") or {}
        if not result:
            continue
        state_reasons = {
            record.get("reason")
            for record in result.get("state_validation_records", [])
            if record.get("reason")
        }
        template_reasons = {
            template_result.get("not_testable_reason")
            for template_result in (result.get("template_results") or {}).values()
            if template_result.get("not_testable_reason")
        }
        row_reasons = set((result.get("not_testable_reason_counts") or {}).keys())
        for category in categories:
            if category in state_reasons or category in template_reasons or category in row_reasons:
                add(row, category)
        if result.get("applicable_templates") and not result.get("validated_templates"):
            add(row, "activated_but_none_validate")
        if "customer_vendor_scope" in result.get("validated_templates", []):
            add(row, "customer_vendor_validates")

    return list(selected.values())


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_template_csv(path: Path, template_breakdown: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "template",
                "activated_row_count",
                "fixture_states_attempted",
                "support_valid_usable_states",
                "invalid_states",
                "mutant_validated_template_suites",
                "generated_tested_states",
                "eq_acct_failures",
                "top_invalid_state_reasons",
                "top_template_suite_failure_reasons",
                "likely_bottleneck",
            ],
        )
        writer.writeheader()
        for template in TEMPLATE_NAMES:
            stats = template_breakdown[template]
            writer.writerow(
                {
                    "template": template,
                    "activated_row_count": stats["activated_row_count"],
                    "fixture_states_attempted": stats["fixture_states_attempted"],
                    "support_valid_usable_states": stats["support_valid_usable_states"],
                    "invalid_states": stats["invalid_states"],
                    "mutant_validated_template_suites": stats["mutant_validated_template_suites"],
                    "generated_tested_states": stats["generated_tested_states"],
                    "eq_acct_failures": stats["eq_acct_failures"],
                    "top_invalid_state_reasons": json.dumps(stats["invalid_state_reasons"], sort_keys=True),
                    "top_template_suite_failure_reasons": json.dumps(stats["template_suite_failure_reasons"], sort_keys=True),
                    "likely_bottleneck": bottleneck_for_template(stats),
                }
            )


def markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(item) for item in row) + " |")
    return lines


def write_markdown(path: Path, audit: dict[str, Any], examples: list[dict[str, Any]]) -> None:
    overall = audit["overall_row_counts"]
    funnel = audit["eq_acct_pipeline_funnel"]
    template_breakdown = audit["template_breakdown"]
    reason_breakdown = audit["not_tested_reason_breakdown"]
    gold_support = audit["gold_support_failure_analysis"]
    mutant = audit["mutant_validation_failure_analysis"]
    ex_pass = funnel["ex_pass_rows"]

    lines: list[str] = [
        "# Eq_acct_v1 Fixture Audit",
        "",
        "## Overall Row Counts",
        "",
    ]
    lines.extend(
        markdown_table(
            ["Metric", "Value"],
            [
                ["total rows", overall["total_rows"]],
                ["EX pass rows", overall["ex_pass_rows"]],
                ["EX fail rows", overall["ex_fail_rows"]],
                ["Eq_acct = 1", overall["eq_acct_counts"].get("1", 0)],
                ["Eq_acct = 0", overall["eq_acct_counts"].get("0", 0)],
                ["Eq_acct = None", overall["eq_acct_counts"].get("None", 0)],
                ["Inv = 1", overall["inv_counts"].get("1", 0)],
                ["Inv = 0", overall["inv_counts"].get("0", 0)],
                ["Inv = None", overall["inv_counts"].get("None", 0)],
                ["ASA strict pass", overall["asa_strict_counts"].get("1", 0)],
                ["ASA strict fail", overall["asa_strict_counts"].get("0", 0)],
                ["ASA strict None", overall["asa_strict_counts"].get("None", 0)],
                ["semantic testability count", overall["semantic_testability_count"]],
                ["semantic testability rate among EX-pass", f"{overall['semantic_testability_rate']:.4f}"],
            ],
        )
    )

    lines.extend(["", "## Eq_acct Pipeline Funnel", ""])
    funnel_rows = []
    labels = [
        ("EX-pass rows", "ex_pass_rows"),
        ("rows with at least one applicable template", "rows_with_applicable_template"),
        ("rows with at least one fixture state constructed", "rows_with_fixture_state_constructed"),
        ("rows with at least one usable support-valid state", "rows_with_usable_support_valid_state"),
        ("rows with at least one mutant-validated template suite", "rows_with_mutant_validated_template_suite"),
        ("rows with generated SQL executed on at least one validated state", "rows_with_generated_sql_executed"),
        ("Eq_acct pass rows", "eq_acct_pass_rows"),
        ("Eq_acct fail rows", "eq_acct_fail_rows"),
        ("Eq_acct None rows", "eq_acct_none_rows"),
    ]
    for label, key in labels:
        count = funnel[key]
        funnel_rows.append([label, count, f"{pct(count, ex_pass):.4f}"])
    lines.extend(markdown_table(["Stage", "Rows", "Pct of EX-pass"], funnel_rows))

    lines.extend(["", "## Template-Level Breakdown", ""])
    template_rows = []
    for template in TEMPLATE_NAMES:
        stats = template_breakdown[template]
        template_rows.append(
            [
                template,
                stats["activated_row_count"],
                stats["fixture_states_attempted"],
                stats["support_valid_usable_states"],
                stats["invalid_states"],
                stats["mutant_validated_template_suites"],
                stats["generated_tested_states"],
                stats["eq_acct_failures"],
                nested_sorted_counter(Counter(stats["invalid_state_reasons"]), 3),
                nested_sorted_counter(Counter(stats["template_suite_failure_reasons"]), 3),
                bottleneck_for_template(stats),
            ]
        )
    lines.extend(
        markdown_table(
            [
                "Template",
                "Activated Rows",
                "States Attempted",
                "Usable States",
                "Invalid States",
                "Validated Suites",
                "Generated-Tested States",
                "Eq Failures",
                "Top Invalid Reasons",
                "Top Suite Reasons",
                "Likely Bottleneck",
            ],
            template_rows,
        )
    )

    lines.extend(["", "## Not-Tested Reason Breakdown", "", "### Row Level", ""])
    lines.extend(markdown_table(["Reason", "Count"], [[k, v] for k, v in reason_breakdown["row_level"].items()]))
    lines.extend(["", "### Template Level", ""])
    lines.extend(markdown_table(["Reason", "Count"], [[k, v] for k, v in reason_breakdown["template_level"].items()]))
    lines.extend(["", "### State Level", ""])
    lines.extend(markdown_table(["Reason", "Count"], [[k, v] for k, v in reason_breakdown["state_level"].items()]))
    lines.extend(["", "### Important Reasons", ""])
    lines.extend(
        markdown_table(
            ["Reason", "Row", "Template", "State"],
            [
                [reason, counts["row_level"], counts["template_level"], counts["state_level"]]
                for reason, counts in reason_breakdown["important_reasons"].items()
            ],
        )
    )

    lines.extend(["", "## Gold Support Failure Analysis", ""])
    lines.extend(["### State Failure Counts", ""])
    lines.extend(markdown_table(["Reason", "Count"], [[k, v] for k, v in gold_support["state_reason_counts"].items()]))
    lines.extend(["", "### State Failures By Template", ""])
    lines.extend(
        markdown_table(
            ["Template", "Reasons"],
            [[template, reasons] for template, reasons in gold_support["state_reason_by_template"].items()],
        )
    )
    lines.extend(["", "### Literal Columns By Failure Reason", ""])
    lines.extend(
        markdown_table(
            ["Reason", "Literal Columns"],
            [[reason, columns] for reason, columns in gold_support["literal_columns_by_reason"].items()],
        )
    )
    lines.extend(["", "### Date Predicate Rows By Failure Reason", ""])
    lines.extend(markdown_table(["Reason", "Count"], [[k, v] for k, v in gold_support["date_predicate_rows_by_reason"].items()]))
    lines.extend(["", "### Entity Literal Rows By Failure Reason", ""])
    lines.extend(
        markdown_table(
            ["Reason", "Entity Literal Flags"],
            [[reason, flags] for reason, flags in gold_support["entity_literal_rows_by_reason"].items()],
        )
    )

    lines.extend(["", "## Mutant Validation Failure Analysis", ""])
    lines.extend(["### Suite Failure Reasons By Template", ""])
    lines.extend(markdown_table(["Template", "Reasons"], [[k, v] for k, v in mutant["by_template"].items()]))
    lines.extend(["", "### Mutant Family Generation", ""])
    lines.extend(markdown_table(["Family", "Generated/Skipped"], [[k, v] for k, v in mutant["family_generation"].items()]))
    lines.extend(["", "### Mutant Execution Status", ""])
    lines.extend(markdown_table(["Template", "Execution Status"], [[k, v] for k, v in mutant["execution_status"].items()]))

    lines.extend(["", "## Activated-But-Never-Tested Templates", ""])
    activated_but_low = []
    for template in TEMPLATE_NAMES:
        stats = template_breakdown[template]
        if stats["activated_row_count"] and stats["generated_tested_states"] == 0:
            activated_but_low.append([template, stats["activated_row_count"], bottleneck_for_template(stats)])
    lines.extend(markdown_table(["Template", "Activated Rows", "Likely Bottleneck"], activated_but_low))

    lines.extend(["", "## Representative Row Examples", ""])
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for example in examples:
        by_category[example["category"]].append(example)
    for category, category_examples in sorted(by_category.items()):
        lines.extend(["", f"### {category}", ""])
        for example in category_examples[:10]:
            lines.append(
                "- `{qid}` templates={templates} reasons={reasons} summary={summary} gold=`{gold}`".format(
                    qid=example.get("question_id"),
                    templates=example.get("applicable_templates"),
                    reasons=example.get("not_testable_reason_counts"),
                    summary=truncate(example.get("debug_summary"), 220),
                    gold=truncate(example.get("gold_sql"), 180),
                )
            )

    lines.extend(["", "## Recommended Fix Order", ""])
    lines.extend(recommendations(audit))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def recommendations(audit: dict[str, Any]) -> list[str]:
    reason_state = audit["not_tested_reason_breakdown"]["state_level"]
    reason_template = audit["not_tested_reason_breakdown"]["template_level"]
    row_reasons = audit["not_tested_reason_breakdown"]["row_level"]
    recs: list[str] = []
    if row_reasons.get("unsupported_sql_feature", 0):
        recs.append(
            f"1. Add debug-only parsing lineage categories for unsupported SQL before broadening coverage; unsupported lineage currently affects {row_reasons['unsupported_sql_feature']} rows."
        )
    if reason_state.get("gold_result_empty", 0) or reason_state.get("gold_result_all_null", 0):
        recs.append(
            "2. Improve gold-support fixture seeding for activated templates, especially date/literal/account support, because empty or all-null gold results dominate state-level support failures."
        )
    if reason_template.get("mutant_generation_not_supported", 0):
        recs.append(
            "3. Expand mutant debug coverage and AST rewrite support for activated templates where no safe mutant is generated; keep generation gold-only."
        )
    if reason_template.get("mutants_not_distinguished", 0):
        recs.append(
            "4. Calibrate fixture states against existing mutant families so usable states actually distinguish at least one pre-specified mutant at suite level."
        )
    if not recs:
        recs.append("1. Inspect representative examples first; no single dominant failure bucket was detected.")
    recs.append(
        f"{len(recs) + 1}. Only after the above, consider activation changes; do not broaden activation until fixture and mutant bottlenecks are explained."
    )
    return [f"- {item}" for item in recs]


def main() -> None:
    args = parse_args()
    rows = read_jsonl(args.asa_rows)
    schema_annotations = read_json(args.schema_annotations)
    output_dir = resolve_output_dir(args.output_dir, args.overwrite)

    print(f"Input rows: {len(rows)}")
    print(f"Output directory: {output_dir}")
    audited = audit_rows(rows, schema_annotations, args)
    audit = aggregate_audit(audited)
    examples = select_examples(audited, args.max_examples_per_reason)

    json_path = output_dir / "eq_acct_v1_fixture_audit.json"
    md_path = output_dir / "eq_acct_v1_fixture_audit.md"
    examples_path = output_dir / "eq_acct_v1_not_tested_examples.jsonl"
    csv_path = output_dir / "eq_acct_v1_template_breakdown.csv"

    payload = {
        "inputs": {
            "asa_rows": str(args.asa_rows),
            "schema_annotations": str(args.schema_annotations),
            "fixture_date": args.fixture_date,
            "max_progress_steps": args.max_progress_steps,
            "progress_check_interval": args.progress_check_interval,
            "limit": args.limit,
        },
        **audit,
    }
    write_json(json_path, payload)
    write_markdown(md_path, payload, examples)
    write_jsonl(examples_path, examples)
    write_template_csv(csv_path, audit["template_breakdown"])

    print(f"Saved Markdown report: {md_path}")
    print(f"Saved JSON audit: {json_path}")
    print(f"Saved examples JSONL: {examples_path}")
    print(f"Saved template CSV: {csv_path}")


if __name__ == "__main__":
    main()
