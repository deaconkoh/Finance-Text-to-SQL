#!/usr/bin/env python3
"""Evaluate invariant-only ASA metrics for SQL output JSONL files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.asa_metrics.asa_metrics import evaluate_asa_rows


GROUP_D = "D_ambiguous"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compute invariant-only ASA metrics for one JSONL file, or before/after "
            "JSONL files joined by question_id."
        )
    )
    parser.add_argument(
        "--before-jsonl",
        required=True,
        help="Baseline/evaluated JSONL with question_id, gold_sql, generated_sql, execution_match.",
    )
    parser.add_argument(
        "--after-jsonl",
        default=None,
        help="Optional after-repair evaluated JSONL. Uses question_id intersection.",
    )
    parser.add_argument(
        "--schema-path",
        default="data/booksql/schema_annotations.json",
        help="Schema annotations JSON path.",
    )
    parser.add_argument("--output-json", required=True, help="Aggregate metrics JSON output path.")
    parser.add_argument("--output-md", default=None, help="Optional aggregate Markdown output path.")
    parser.add_argument(
        "--row-output-jsonl",
        default=None,
        help="Optional row-level ASA diagnostics JSONL output path.",
    )
    parser.add_argument(
        "--dedupe",
        choices=["first", "last", "error"],
        default="last",
        help="How to handle duplicate question_id rows in each input.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Optional limit after joining.")
    parser.add_argument(
        "--include-fcr-details",
        action="store_true",
        help="Include full FCR findings and warnings in row diagnostics.",
    )
    parser.add_argument(
        "--include-group-d",
        action="store_true",
        help=(
            "Include Group D ambiguous/excluded rows. By default they are "
            "filtered out to match primary EX metric denominators."
        ),
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


def is_group_d_or_excluded(row: dict[str, Any]) -> bool:
    return (
        row.get("excluded_from_primary_metrics") is True
        or row.get("evaluation_group") == GROUP_D
    )


def filter_primary_metric_rows(
    by_id: dict[str, dict[str, Any]],
    question_ids: list[str],
) -> tuple[list[str], int]:
    kept_ids = [
        question_id
        for question_id in question_ids
        if not is_group_d_or_excluded(by_id[question_id])
    ]
    return kept_ids, len(question_ids) - len(kept_ids)


def filter_joined_primary_metric_rows(
    before_by_id: dict[str, dict[str, Any]],
    after_by_id: dict[str, dict[str, Any]] | None,
    question_ids: list[str],
) -> tuple[list[str], int]:
    kept_ids = []
    filtered = 0

    for question_id in question_ids:
        if is_group_d_or_excluded(before_by_id[question_id]):
            filtered += 1
            continue

        if after_by_id is not None and is_group_d_or_excluded(after_by_id[question_id]):
            filtered += 1
            continue

        kept_ids.append(question_id)

    return kept_ids, filtered


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
        "# ASA Invariant Metrics",
        "",
        f"- Join mode: `{metrics['join_mode']}`",
        f"- Question IDs evaluated: {metrics['joined_question_ids']}",
        f"- Dedupe policy: `{metrics['dedupe_policy']}`",
        f"- Group D filtered: {metrics['group_d_filtered_question_ids']}",
        "",
        "| Set | Rows | EX Acc | ASA Strict Acc | ASA Lower Bound | FPER | FPER Lower Bound | Inv Evaluability | Inv Failures |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in metrics["sets"]:
        lines.append(
            "| {label} | {rows} | {ex} | {asa} | {lower} | {fper} | {fper_lb} | {inv_eval} | {inv_failures} |".format(
                label=item["label"],
                rows=item["total_rows"],
                ex=pct(item["ex_accuracy"]),
                asa=pct(item["asa_strict_accuracy"]),
                lower=pct(item["asa_lower_bound_accuracy"]),
                fper=pct(item["fper"]),
                fper_lb=pct(item["fper_lower_bound"]),
                inv_eval=pct(item["inv_evaluability_rate_among_ex_pass"]),
                inv_failures=item["inv_failure_count"],
            )
        )

    lines.extend(["", "## FCR Hard Findings", ""])
    for item in metrics["sets"]:
        lines.extend([f"### {item['label']}", "", "| Code | Count |", "| --- | ---: |"])
        for code, count in item["fcr_hard_finding_counts"].items():
            lines.append(f"| `{code}` | {count} |")
        lines.append("")

    lines.extend(["", "## Inv Not Evaluable Reasons", ""])
    for item in metrics["sets"]:
        lines.extend([f"### {item['label']}", "", "| Code | Count |", "| --- | ---: |"])
        for code, count in item["inv_not_evaluable_reason_counts"].items():
            lines.append(f"| `{code}` | {count} |")
        lines.append("")

    if "deltas" in metrics:
        deltas = metrics["deltas"]
        lines.extend(
            [
                "## Deltas",
                "",
                f"- EX accuracy delta: {pct(deltas['ex_accuracy_delta'])}",
                f"- ASA strict accuracy delta: {pct(deltas['asa_strict_accuracy_delta'])}",
                f"- ASA lower-bound accuracy delta: {pct(deltas['asa_lower_bound_accuracy_delta'])}",
                f"- FPER delta: {pct(deltas['fper_delta'])}",
                f"- FPER lower-bound delta: {pct(deltas['fper_lower_bound_delta'])}",
                "",
            ]
        )

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    schema_annotations = read_json(args.schema_path)

    before_rows = read_jsonl(args.before_jsonl)
    before_by_id, before_duplicates = dedupe_by_question_id(before_rows, args.dedupe, "before")

    after_by_id: dict[str, dict[str, Any]] | None = None
    after_rows: list[dict[str, Any]] = []
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

    joined_before_group_d_filter = len(question_ids)
    group_d_filtered = 0
    if not args.include_group_d:
        if after_by_id is None:
            question_ids, group_d_filtered = filter_primary_metric_rows(
                before_by_id,
                question_ids,
            )
        else:
            question_ids, group_d_filtered = filter_joined_primary_metric_rows(
                before_by_id,
                after_by_id,
                question_ids,
            )

    before_selected = [before_by_id[question_id] for question_id in question_ids]
    before_metrics, before_outputs = evaluate_asa_rows(
        before_selected,
        schema_annotations,
        label="before",
        include_fcr_details=args.include_fcr_details,
    )
    sets = [before_metrics]
    outputs = before_outputs

    if after_by_id is not None:
        after_selected = [after_by_id[question_id] for question_id in question_ids]
        after_metrics, after_outputs = evaluate_asa_rows(
            after_selected,
            schema_annotations,
            label="after",
            include_fcr_details=args.include_fcr_details,
        )
        sets.append(after_metrics)
        outputs.extend(after_outputs)

    metrics: dict[str, Any] = {
        "join_mode": join_mode,
        "dedupe_policy": args.dedupe,
        "before_input_rows": len(before_rows),
        "before_unique_question_ids": len(before_by_id),
        "before_duplicate_rows": before_duplicates,
        "after_input_rows": len(after_rows) if args.after_jsonl else None,
        "after_unique_question_ids": len(after_by_id) if after_by_id is not None else None,
        "after_duplicate_rows": after_duplicates if args.after_jsonl else None,
        "joined_question_ids_before_group_d_filter": joined_before_group_d_filter,
        "group_d_filter_enabled": not args.include_group_d,
        "group_d_filtered_question_ids": group_d_filtered,
        "joined_question_ids": len(question_ids),
        "sets": sets,
    }

    if len(sets) == 2:
        before, after = sets
        metrics["deltas"] = {
            "ex_accuracy_delta": after["ex_accuracy"] - before["ex_accuracy"],
            "asa_strict_accuracy_delta": after["asa_strict_accuracy"]
            - before["asa_strict_accuracy"],
            "asa_lower_bound_accuracy_delta": after["asa_lower_bound_accuracy"]
            - before["asa_lower_bound_accuracy"],
            "fper_delta": after["fper"] - before["fper"],
            "fper_lower_bound_delta": after["fper_lower_bound"] - before["fper_lower_bound"],
        }

    write_json(args.output_json, metrics)
    if args.output_md:
        write_markdown(args.output_md, metrics)
    if args.row_output_jsonl:
        write_jsonl(args.row_output_jsonl, outputs)

    print(f"Join mode: {join_mode}")
    print(f"Question IDs evaluated: {len(question_ids)}")
    if not args.include_group_d:
        print(f"Group D filtered: {group_d_filtered}")
    for item in sets:
        print(
            "{label}: ex_acc={ex:.4f} asa_strict={asa:.4f} "
            "asa_lower_bound={lower:.4f} fper={fper:.4f}".format(
                label=item["label"],
                ex=item["ex_accuracy"],
                asa=item["asa_strict_accuracy"],
                lower=item["asa_lower_bound_accuracy"],
                fper=item["fper"],
            )
        )
    print(f"Saved metrics to: {args.output_json}")
    if args.output_md:
        print(f"Saved markdown to: {args.output_md}")
    if args.row_output_jsonl:
        print(f"Saved row diagnostics to: {args.row_output_jsonl}")


if __name__ == "__main__":
    main()
