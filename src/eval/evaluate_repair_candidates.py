#!/usr/bin/env python3
"""Evaluate generated FinVeriSQL repair candidates by execution accuracy."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from tqdm import tqdm


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


try:
    from src.eval.evaluate_baseline_sql import compare_results, execute_sql, has_order_by, result_preview
    from src.finverisql.repair_runner import read_jsonl
    from src.utils.data_utils import get_booksql_db_path
except ModuleNotFoundError:
    from eval.evaluate_baseline_sql import compare_results, execute_sql, has_order_by, result_preview
    from finverisql.repair_runner import read_jsonl
    from utils.data_utils import get_booksql_db_path


GROUP_A = "A_correct_executable"
GROUP_B = "B_wrong_executable"
GROUP_C = "C_non_executable"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate FinVeriSQL repair-generation JSONL by executing repaired SQL.",
    )
    parser.add_argument("--input-path", required=True, help="Repair-generation JSONL from run_finverisql_repair.py.")
    parser.add_argument("--output-path", required=True, help="Row-level evaluated repair JSONL.")
    parser.add_argument("--metrics-json", required=True, help="Aggregate repair metrics JSON.")
    parser.add_argument("--metrics-md", required=True, help="Aggregate repair metrics Markdown.")
    parser.add_argument("--db-path", default=None, help="Optional explicit BookSQL SQLite path.")
    parser.add_argument("--max-result-preview-rows", type=int, default=100, help="Maximum SQL result preview rows to store.")
    parser.add_argument("--max-progress-steps", type=int, default=2_000_000, help="SQLite progress-handler step limit.")
    parser.add_argument("--progress-check-interval", type=int, default=1000, help="SQLite VM instruction interval for progress checks.")
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on processed repair rows.")
    return parser.parse_args()


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def before_exec_match_from_group(evaluation_group: Any) -> bool | None:
    if evaluation_group == GROUP_A:
        return True

    if evaluation_group in {GROUP_B, GROUP_C}:
        return False

    return None


def evaluate_repair_row(
    row: dict[str, Any],
    conn: sqlite3.Connection,
    max_result_preview_rows: int,
    max_progress_steps: int,
    progress_check_interval: int,
) -> dict[str, Any]:
    repaired_sql = row.get("repaired_sql")
    gold_sql = row.get("gold_sql")
    before_exec_match = before_exec_match_from_group(row.get("evaluation_group"))

    evaluated = {
        **row,
        "before_exec_match": before_exec_match,
        "after_exec_match": None,
        "repair_success": False,
        "gold_execution_status": None,
        "repaired_execution_status": None,
        "gold_result": None,
        "repaired_result": None,
        "gold_error": None,
        "repaired_error": None,
    }

    if not repaired_sql:
        evaluated["repair_evaluation_status"] = "skipped_no_repaired_sql"
        return evaluated

    gold_rows, gold_error = execute_sql(
        conn=conn,
        sql=gold_sql,
        max_progress_steps=max_progress_steps,
        progress_check_interval=progress_check_interval,
    )
    repaired_rows, repaired_error = execute_sql(
        conn=conn,
        sql=repaired_sql,
        max_progress_steps=max_progress_steps,
        progress_check_interval=progress_check_interval,
    )

    after_exec_match = False
    if gold_error is None and repaired_error is None:
        after_exec_match = compare_results(
            gold_rows=gold_rows or [],
            generated_rows=repaired_rows or [],
            order_sensitive=has_order_by(gold_sql),
        )

    evaluated.update(
        {
            "repair_evaluation_status": "success",
            "after_exec_match": after_exec_match,
            "repair_success": before_exec_match is False and after_exec_match,
            "gold_execution_status": "success" if gold_error is None else "error",
            "repaired_execution_status": "success" if repaired_error is None else "error",
            "gold_result": (
                result_preview(gold_rows or [], max_result_preview_rows)
                if gold_error is None
                else None
            ),
            "repaired_result": (
                result_preview(repaired_rows or [], max_result_preview_rows)
                if repaired_error is None
                else None
            ),
            "gold_error": gold_error,
            "repaired_error": repaired_error,
        }
    )

    return evaluated


def safe_rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def build_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_rows = len(rows)
    attempted_rows = [row for row in rows if row.get("status") != "skipped"]
    generated_rows = [row for row in attempted_rows if row.get("repaired_sql")]
    executable_rows = [
        row
        for row in generated_rows
        if row.get("repaired_execution_status") == "success"
    ]
    successful_rows = [row for row in rows if row.get("repair_success") is True]

    group_b_attempted = [
        row for row in attempted_rows if row.get("evaluation_group") == GROUP_B
    ]
    group_c_attempted = [
        row for row in attempted_rows if row.get("evaluation_group") == GROUP_C
    ]

    return {
        "total_repair_rows": total_rows,
        "attempted_repairs": len(attempted_rows),
        "generated_repairs": len(generated_rows),
        "executable_repairs": len(executable_rows),
        "successful_repairs": len(successful_rows),
        "group_b_attempted_repairs": len(group_b_attempted),
        "group_c_attempted_repairs": len(group_c_attempted),
        "generation_rate": safe_rate(len(generated_rows), len(attempted_rows)),
        "repair_executable_rate": safe_rate(len(executable_rows), len(generated_rows)),
        "correction_rate": safe_rate(len(successful_rows), len(attempted_rows)),
        "final_execution_accuracy_contribution": safe_rate(len(successful_rows), total_rows),
    }


def write_metrics_markdown(path: str | Path, metrics: dict[str, Any]) -> None:
    lines = [
        "# FinVeriSQL Repair Evaluation",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Total repair rows | {metrics['total_repair_rows']} |",
        f"| Attempted repairs | {metrics['attempted_repairs']} |",
        f"| Generated repairs | {metrics['generated_repairs']} |",
        f"| Executable repairs | {metrics['executable_repairs']} |",
        f"| Successful repairs | {metrics['successful_repairs']} |",
        f"| Correction rate | {metrics['correction_rate']:.4f} |",
        f"| Final execution-accuracy contribution | {metrics['final_execution_accuracy_contribution']:.4f} |",
        "",
    ]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()

    rows = read_jsonl(args.input_path)
    if args.limit is not None:
        rows = rows[: args.limit]

    db_path = get_booksql_db_path(args.db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA query_only = ON")

    try:
        evaluated_rows = [
            evaluate_repair_row(
                row=row,
                conn=conn,
                max_result_preview_rows=args.max_result_preview_rows,
                max_progress_steps=args.max_progress_steps,
                progress_check_interval=args.progress_check_interval,
            )
            for row in tqdm(rows)
        ]
    finally:
        conn.close()

    metrics = build_metrics(evaluated_rows)

    write_jsonl(args.output_path, evaluated_rows)
    write_json(args.metrics_json, metrics)
    write_metrics_markdown(args.metrics_md, metrics)

    print(f"Evaluated repair rows: {metrics['total_repair_rows']}")
    print(f"Attempted repairs: {metrics['attempted_repairs']}")
    print(f"Generated repairs: {metrics['generated_repairs']}")
    print(f"Executable repairs: {metrics['executable_repairs']}")
    print(f"Successful repairs: {metrics['successful_repairs']}")
    print(f"Correction rate: {metrics['correction_rate']:.4f}")
    print(f"Saved row-level results to: {args.output_path}")
    print(f"Saved metrics to: {args.metrics_json}")
    print(f"Saved markdown metrics to: {args.metrics_md}")


if __name__ == "__main__":
    main()
