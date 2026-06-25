#!/usr/bin/env python3
"""Compute Accounting-Adversarial Test Suite Accuracy for SQL outputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.asa_metrics.old.accounting_adversarial import evaluate_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate before/after SQL output JSONL files with the "
            "Accounting-Adversarial Test Suite."
        )
    )
    parser.add_argument("--before-jsonl", required=True)
    parser.add_argument("--after-jsonl", default=None)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", default=None)
    parser.add_argument("--row-output-jsonl", default=None)
    parser.add_argument("--fixture-date", default="2026-06-23")
    parser.add_argument("--dedupe", choices=["first", "last", "error"], default="last")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-progress-steps", type=int, default=2_000_000)
    parser.add_argument("--progress-check-interval", type=int, default=1000)
    return parser.parse_args()


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
        qid = row.get("question_id")
        if qid in (None, ""):
            raise ValueError(f"{label} row {index} is missing question_id")
        key = str(qid)
        if key in deduped:
            duplicate_count += 1
            if policy == "error":
                raise ValueError(f"{label} has duplicate question_id: {key}")
            if policy == "first":
                continue
        deduped[key] = row
    return deduped, duplicate_count


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def pct(value: float) -> str:
    return f"{value:.4f}"


def write_markdown(path: str | Path, metrics: dict[str, Any]) -> None:
    lines = [
        "# Accounting-Adversarial Test Suite Metrics",
        "",
        f"- Join mode: `{metrics['join_mode']}`",
        f"- Question IDs evaluated: {metrics['joined_question_ids']}",
        f"- Dedupe policy: `{metrics['dedupe_policy']}`",
        f"- Fixture date: `{metrics['fixture_date']}`",
        "",
        "| Set | Rows | Original EX | Adversarial Acc | Testability | Original EX Pass, Adv Fail |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in metrics["sets"]:
        lines.append(
            "| {label} | {rows} | {orig} | {adv} | {testability} | {fail_rate} |".format(
                label=item["label"],
                rows=item["total_rows"],
                orig=pct(item["original_execution_accuracy"]),
                adv=pct(item["accounting_adversarial_test_suite_accuracy"]),
                testability=pct(item["adversarial_testability_rate"]),
                fail_rate=pct(item["original_ex_pass_adversarial_fail_rate"]),
            )
        )
    lines.extend(["", "## Template Counts", ""])
    for item in metrics["sets"]:
        lines.extend([f"### {item['label']}", "", "| Template | Applicable | Tested | Failed |", "| --- | ---: | ---: | ---: |"])
        for name, counts in item["template_counts"].items():
            lines.append(f"| `{name}` | {counts['applicable']} | {counts['tested']} | {counts['failure']} |")
        lines.extend(
            [
                "",
                f"- Gold-error excluded templates: {item['gold_error_excluded_templates']}",
                f"- Gold-empty excluded templates: {item['gold_empty_excluded_templates']}",
                f"- Non-discriminative preflight templates: {item['preflight_non_discriminative_templates']}",
                "",
            ]
        )
    if "deltas" in metrics:
        deltas = metrics["deltas"]
        lines.extend(
            [
                "## Deltas",
                "",
                f"- Original execution accuracy delta: {pct(deltas['original_execution_accuracy_delta'])}",
                f"- Accounting-adversarial accuracy delta: {pct(deltas['accounting_adversarial_test_suite_accuracy_delta'])}",
                f"- Testability delta: {pct(deltas['adversarial_testability_rate_delta'])}",
                "",
            ]
        )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
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

    before_selected = [before_by_id[qid] for qid in question_ids]
    before_metrics, before_outputs = evaluate_rows(
        before_selected,
        set_label="before",
        fixture_date=args.fixture_date,
        max_progress_steps=args.max_progress_steps,
        progress_check_interval=args.progress_check_interval,
    )
    sets = [before_metrics]
    outputs = before_outputs

    if after_by_id is not None:
        after_selected = [after_by_id[qid] for qid in question_ids]
        after_metrics, after_outputs = evaluate_rows(
            after_selected,
            set_label="after",
            fixture_date=args.fixture_date,
            max_progress_steps=args.max_progress_steps,
            progress_check_interval=args.progress_check_interval,
        )
        sets.append(after_metrics)
        outputs.extend(after_outputs)

    metrics: dict[str, Any] = {
        "join_mode": join_mode,
        "dedupe_policy": args.dedupe,
        "fixture_date": args.fixture_date,
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
            "original_execution_accuracy_delta": after["original_execution_accuracy"] - before["original_execution_accuracy"],
            "accounting_adversarial_test_suite_accuracy_delta": (
                after["accounting_adversarial_test_suite_accuracy"]
                - before["accounting_adversarial_test_suite_accuracy"]
            ),
            "adversarial_testability_rate_delta": after["adversarial_testability_rate"] - before["adversarial_testability_rate"],
        }

    write_json(args.output_json, metrics)
    if args.output_md:
        write_markdown(args.output_md, metrics)
    if args.row_output_jsonl:
        write_jsonl(args.row_output_jsonl, outputs)

    print(f"Join mode: {join_mode}")
    print(f"Question IDs evaluated: {len(question_ids)}")
    for item in sets:
        print(
            "{label}: original_ex={orig:.4f} adversarial_acc={adv:.4f} testability={testability:.4f}".format(
                label=item["label"],
                orig=item["original_execution_accuracy"],
                adv=item["accounting_adversarial_test_suite_accuracy"],
                testability=item["adversarial_testability_rate"],
            )
        )
    print(f"Saved metrics to: {args.output_json}")


if __name__ == "__main__":
    main()
