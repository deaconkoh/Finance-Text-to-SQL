#!/usr/bin/env python3
"""Evaluate deterministic financial contradiction metrics for SQL outputs.

This script compares each row's ``gold_sql`` against its ``generated_sql`` using
``evaluate_financial_contradiction``. When both --before-jsonl and --after-jsonl
are provided, metrics are computed on the same question_id intersection.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.asa_metrics.financial_contradiction import evaluate_financial_contradiction


STATUSES = [
    "hard_financial_contradiction",
    "no_financial_contradiction",
    "not_evaluable",
]

WARNING_CODES = [
    "aggregation_or_grain_error",
    "financial_measure_mismatch",
    "output_shape_warning",
    "financial_scope_warning",
    "unresolved_measure_warning",
]

NEW_HARD_FINDING_CODES = [
    "rate_as_total_amount_substitution",
    "invoice_bill_transaction_type_substitution",
    "balance_stock_replaced_by_flow_amount",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compute financial contradiction metrics for one JSONL file, or "
            "before/after JSONL files joined by question_id."
        )
    )
    parser.add_argument(
        "--before-jsonl",
        required=True,
        help="Baseline/evaluated JSONL with question_id, gold_sql, generated_sql.",
    )
    parser.add_argument(
        "--after-jsonl",
        default=None,
        help=(
            "Optional after-repair evaluated JSONL. If provided, before/after "
            "metrics use only intersecting question_id values."
        ),
    )
    parser.add_argument(
        "--schema-path",
        default="data/booksql/schema_annotations.json",
        help="Schema annotations JSON path.",
    )
    parser.add_argument(
        "--output-json",
        required=True,
        help="Aggregate metrics JSON output path.",
    )
    parser.add_argument(
        "--output-md",
        default=None,
        help="Optional aggregate metrics Markdown output path.",
    )
    parser.add_argument(
        "--row-output-jsonl",
        default=None,
        help="Optional row-level contradiction diagnostics JSONL output path.",
    )
    parser.add_argument(
        "--dedupe",
        choices=["first", "last", "error"],
        default="last",
        help="How to handle duplicate question_id rows in each input.",
    )
    parser.add_argument(
        "--include-debug",
        action="store_true",
        help="Include full gold/generated bundles and debug semantics in row output.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit after joining/deduping, useful for smoke tests.",
    )
    parser.add_argument(
        "--audit-output-json",
        default="reports/fcr_new_rules_audit.json",
        help="Audit JSON output with examples for newly added hard FCR rules.",
    )
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


def dedupe_by_question_id(
    rows: list[dict[str, Any]],
    policy: str,
    label: str,
) -> tuple[dict[str, dict[str, Any]], int]:
    deduped: dict[str, dict[str, Any]] = {}
    duplicate_count = 0

    for index, row in enumerate(rows):
        question_id = row.get("question_id")
        if question_id in (None, ""):
            raise ValueError(f"{label} row {index} is missing question_id")
        key = str(question_id)
        if key in deduped:
            duplicate_count += 1
            if policy == "error":
                raise ValueError(f"{label} has duplicate question_id: {key}")
            if policy == "first":
                continue
        deduped[key] = row

    return deduped, duplicate_count


def safe_rate(numerator: int | float, denominator: int | float) -> float:
    return numerator / denominator if denominator else 0.0


def evaluate_rows(
    rows_by_id: dict[str, dict[str, Any]],
    question_ids: list[str],
    schema_annotations: dict[str, Any],
    label: str,
    include_debug: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counts: Counter[str] = Counter()
    finding_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    row_outputs: list[dict[str, Any]] = []
    execution_metric_rows = 0
    execution_correct_rows = 0
    esm_2x2: Counter[str] = Counter()
    esm_missing = 0

    for question_id in question_ids:
        row = rows_by_id[question_id]
        result = evaluate_financial_contradiction(
            gold_sql=row.get("gold_sql") or "",
            generated_sql=row.get("generated_sql") or "",
            schema_annotations=schema_annotations,
        )
        status = result["primary_status"]
        counts[status] += 1
        finding_codes = [finding.get("code") for finding in result.get("findings", [])]
        finding_counts.update(
            finding.get("code")
            for finding in result.get("findings", [])
            if finding.get("status") == "hard_financial_contradiction" and finding.get("code")
        )
        warning_codes = [warning.get("code") for warning in result.get("warnings", [])]
        warning_counts.update(code for code in warning_codes if code)

        if row.get("excluded_from_primary_metrics") is not True and row.get("execution_match") is not None:
            execution_metric_rows += 1
            if row.get("execution_match") is True:
                execution_correct_rows += 1

        execution_match = row.get("execution_match")
        if status != "not_evaluable":
            if execution_match is True:
                esm_suffix = "esm_pass"
            elif execution_match is False:
                esm_suffix = "esm_fail"
            else:
                esm_suffix = None
                esm_missing += 1
            if esm_suffix is not None and status in {
                "hard_financial_contradiction",
                "no_financial_contradiction",
            }:
                esm_2x2[f"{status}__{esm_suffix}"] += 1

        row_output = {
            "set": label,
            "question_id": question_id,
            "gold_sql": row.get("gold_sql"),
            "generated_sql": row.get("generated_sql"),
            "execution_match": row.get("execution_match"),
            "evaluation_group": row.get("evaluation_group"),
            "primary_status": status,
            "finding_codes": finding_codes,
            "findings": result.get("findings", []),
            "warning_codes": warning_codes,
            "warnings": result.get("warnings", []),
        }
        if include_debug:
            row_output["result"] = result
        row_outputs.append(row_output)

    total = len(question_ids)
    evaluable = total - counts["not_evaluable"]
    hard = counts["hard_financial_contradiction"]
    no_contradiction = counts["no_financial_contradiction"]

    metrics = {
        "label": label,
        "total_rows": total,
        "evaluable_rows": evaluable,
        "status_counts": {status: counts[status] for status in STATUSES},
        "hard_finding_counts": dict(sorted(finding_counts.items())),
        "hard_financial_contradiction_rate": safe_rate(hard, evaluable),
        "no_contradiction_rate": safe_rate(no_contradiction, evaluable),
        "not_evaluable_rate": safe_rate(counts["not_evaluable"], total),
        "warning_counts": {code: warning_counts[code] for code in WARNING_CODES},
        "warning_rates": {code: safe_rate(warning_counts[code], evaluable) for code in WARNING_CODES},
        "esm_2x2": {
            "hard_financial_contradiction__esm_pass": esm_2x2[
                "hard_financial_contradiction__esm_pass"
            ],
            "hard_financial_contradiction__esm_fail": esm_2x2[
                "hard_financial_contradiction__esm_fail"
            ],
            "no_financial_contradiction__esm_pass": esm_2x2[
                "no_financial_contradiction__esm_pass"
            ],
            "no_financial_contradiction__esm_fail": esm_2x2[
                "no_financial_contradiction__esm_fail"
            ],
            "not_evaluable_excluded": counts["not_evaluable"],
            "missing_execution_match_excluded": esm_missing,
        },
        "execution_accuracy": safe_rate(execution_correct_rows, execution_metric_rows),
        "execution_correct_rows": execution_correct_rows,
        "execution_metric_rows": execution_metric_rows,
    }
    return metrics, row_outputs


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def pct(value: float) -> str:
    return f"{value:.4f}"


def write_markdown(path: str | Path, metrics: dict[str, Any]) -> None:
    lines = [
        "# Financial Contradiction Metrics",
        "",
        f"- Join mode: `{metrics['join_mode']}`",
        f"- Question IDs evaluated: {metrics['joined_question_ids']}",
        f"- Dedupe policy: `{metrics['dedupe_policy']}`",
        "",
        "| Set | Rows | Evaluable | Hard FCR | No Contradiction | Not Evaluable | Execution Accuracy |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for set_metrics in metrics["sets"]:
        lines.append(
            "| {label} | {total_rows} | {evaluable_rows} | {hard} | {none} | {not_eval} | {exec_acc} |".format(
                label=set_metrics["label"],
                total_rows=set_metrics["total_rows"],
                evaluable_rows=set_metrics["evaluable_rows"],
                hard=pct(set_metrics["hard_financial_contradiction_rate"]),
                none=pct(set_metrics["no_contradiction_rate"]),
                not_eval=pct(set_metrics["not_evaluable_rate"]),
                exec_acc=pct(set_metrics["execution_accuracy"]),
            )
        )
    lines.extend(["", "## Warning Subtypes", ""])
    for set_metrics in metrics["sets"]:
        lines.extend([f"### {set_metrics['label']}", "", "| Warning | Count | Rate |", "| --- | ---: | ---: |"])
        for code, count in set_metrics["warning_counts"].items():
            lines.append(f"| `{code}` | {count} | {pct(set_metrics['warning_rates'][code])} |")
        lines.append("")

    lines.extend(["", "## Hard Finding Subtypes", ""])
    for set_metrics in metrics["sets"]:
        lines.extend([f"### {set_metrics['label']}", "", "| Finding | Count |", "| --- | ---: |"])
        for code, count in set_metrics.get("hard_finding_counts", {}).items():
            lines.append(f"| `{code}` | {count} |")
        lines.append("")

    lines.extend(["", "## Financial Contradiction x Exact Set Match", ""])
    for set_metrics in metrics["sets"]:
        table = set_metrics["esm_2x2"]
        lines.extend(
            [
                f"### {set_metrics['label']}",
                "",
                "| Financial status | ESM pass | ESM fail |",
                "| --- | ---: | ---: |",
                "| Hard financial contradiction | {pass_count} | {fail_count} |".format(
                    pass_count=table["hard_financial_contradiction__esm_pass"],
                    fail_count=table["hard_financial_contradiction__esm_fail"],
                ),
                "| No financial contradiction | {pass_count} | {fail_count} |".format(
                    pass_count=table["no_financial_contradiction__esm_pass"],
                    fail_count=table["no_financial_contradiction__esm_fail"],
                ),
                "",
                f"- Not evaluable excluded: {table['not_evaluable_excluded']}",
                f"- Missing execution_match excluded: {table['missing_execution_match_excluded']}",
                "",
            ]
        )

    if "deltas" in metrics:
        deltas = metrics["deltas"]
        lines.extend(
            [
                "",
                "## Deltas",
                "",
                f"- Execution accuracy delta: {pct(deltas['execution_accuracy_delta'])}",
                f"- Hard FCR delta: {pct(deltas['hard_financial_contradiction_rate_delta'])}",
                f"- Not evaluable rate delta: {pct(deltas['not_evaluable_rate_delta'])}",
                "",
            ]
        )

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def build_new_rules_audit(row_outputs: list[dict[str, Any]]) -> dict[str, Any]:
    examples_by_code: dict[str, list[dict[str, Any]]] = {code: [] for code in NEW_HARD_FINDING_CODES}
    sorted_rows = sorted(
        row_outputs,
        key=lambda row: (
            str(row.get("set") or ""),
            str(row.get("question_id") or ""),
        ),
    )

    for row in sorted_rows:
        for finding in row.get("findings", []):
            code = finding.get("code")
            if code not in examples_by_code or len(examples_by_code[code]) >= 10:
                continue
            evidence = finding.get("evidence", {})
            examples_by_code[code].append(
                {
                    "set": row.get("set"),
                    "question_id": row.get("question_id"),
                    "gold_sql": row.get("gold_sql"),
                    "generated_sql": row.get("generated_sql"),
                    "execution_match": row.get("execution_match"),
                    "finding_code": code,
                    "finding_explanation": finding.get("message"),
                    "gold_evidence": evidence.get("gold"),
                    "generated_evidence": evidence.get("generated"),
                }
            )

    return {
        "rules": NEW_HARD_FINDING_CODES,
        "max_examples_per_rule": 10,
        "examples_by_code": examples_by_code,
    }


def main() -> None:
    args = parse_args()
    schema_annotations = read_json(args.schema_path)

    before_rows = read_jsonl(args.before_jsonl)
    before_by_id, before_duplicates = dedupe_by_question_id(before_rows, args.dedupe, "before")

    after_by_id: dict[str, dict[str, Any]] | None = None
    after_duplicates = 0
    if args.after_jsonl:
        after_rows = read_jsonl(args.after_jsonl)
        after_by_id, after_duplicates = dedupe_by_question_id(after_rows, args.dedupe, "after")
        question_ids = sorted(set(before_by_id) & set(after_by_id))
        join_mode = "inner_join_question_id"
    else:
        question_ids = sorted(before_by_id)
        join_mode = "single_file"

    if args.limit is not None:
        question_ids = question_ids[: args.limit]

    before_metrics, before_outputs = evaluate_rows(
        before_by_id,
        question_ids,
        schema_annotations,
        label="before",
        include_debug=args.include_debug,
    )
    all_outputs = before_outputs
    sets = [before_metrics]

    if after_by_id is not None:
        after_metrics, after_outputs = evaluate_rows(
            after_by_id,
            question_ids,
            schema_annotations,
            label="after",
            include_debug=args.include_debug,
        )
        all_outputs.extend(after_outputs)
        sets.append(after_metrics)

    metrics: dict[str, Any] = {
        "join_mode": join_mode,
        "dedupe_policy": args.dedupe,
        "before_input_rows": len(before_rows),
        "before_unique_question_ids": len(before_by_id),
        "before_duplicate_rows": before_duplicates,
        "after_input_rows": len(after_rows) if args.after_jsonl else None,
        "after_unique_question_ids": len(after_by_id) if after_by_id is not None else None,
        "after_duplicate_rows": after_duplicates if args.after_jsonl else None,
        "joined_question_ids": len(question_ids),
        "sets": sets,
    }

    if len(sets) == 2:
        before, after = sets
        metrics["deltas"] = {
            "execution_accuracy_delta": after["execution_accuracy"] - before["execution_accuracy"],
            "hard_financial_contradiction_rate_delta": (
                after["hard_financial_contradiction_rate"]
                - before["hard_financial_contradiction_rate"]
            ),
            "not_evaluable_rate_delta": after["not_evaluable_rate"] - before["not_evaluable_rate"],
        }

    write_json(args.output_json, metrics)
    if args.output_md:
        write_markdown(args.output_md, metrics)
    if args.row_output_jsonl:
        write_jsonl(args.row_output_jsonl, all_outputs)
    if args.audit_output_json:
        write_json(args.audit_output_json, build_new_rules_audit(all_outputs))

    print(f"Join mode: {join_mode}")
    print(f"Question IDs evaluated: {len(question_ids)}")
    for set_metrics in sets:
        print(
            "{label}: exec_acc={exec_acc:.4f} hard_fcr={hard:.4f} "
            "not_eval={not_eval:.4f}".format(
                label=set_metrics["label"],
                exec_acc=set_metrics["execution_accuracy"],
                hard=set_metrics["hard_financial_contradiction_rate"],
                not_eval=set_metrics["not_evaluable_rate"],
            )
        )
    print(f"Saved metrics to: {args.output_json}")
    if args.output_md:
        print(f"Saved markdown to: {args.output_md}")
    if args.row_output_jsonl:
        print(f"Saved row diagnostics to: {args.row_output_jsonl}")
    if args.audit_output_json:
        print(f"Saved new-rule audit to: {args.audit_output_json}")


if __name__ == "__main__":
    main()
