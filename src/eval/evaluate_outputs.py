from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = Path(__file__).resolve().parents[1]

for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

try:
    from src.utils.data_utils import get_booksql_db_path
except ModuleNotFoundError:
    from utils.data_utils import get_booksql_db_path


MAX_RESULT_PREVIEW_ROWS = 100
ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)

GROUP_A = "A_correct_executable"
GROUP_B = "B_wrong_executable"
GROUP_C = "C_non_executable"
GROUP_D = "D_ambiguous"

thread_local = threading.local()


def str_to_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value

    value = value.lower()

    if value in {"true", "1", "yes", "y"}:
        return True

    if value in {"false", "0", "no", "n"}:
        return False

    raise argparse.ArgumentTypeError("Expected true or false.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate BookSQL baseline JSONL outputs by SQL execution in parallel.",
    )

    parser.add_argument(
        "--input-jsonl",
        required=True,
        help="Input baseline output JSONL.",
    )
    parser.add_argument(
        "--output-jsonl",
        required=True,
        help="Output row-level evaluated JSONL.",
    )
    parser.add_argument(
        "--metrics-json",
        required=True,
        help="Output summary metrics JSON.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help=(
            "Optional explicit BookSQL SQLite path. "
            "Defaults to data/booksql/accounting.sqlite after setup."
        ),
    )
    parser.add_argument(
        "--max-result-preview-rows",
        type=int,
        default=MAX_RESULT_PREVIEW_ROWS,
        help="Maximum result rows to store per query in evaluated JSONL.",
    )
    parser.add_argument(
        "--treat-empty-results-as-ambiguous",
        action="store_true",
        help=(
            "If set, rows with empty/null result patterns are placed into "
            "Group D and excluded from primary metrics."
        ),
    )
    parser.add_argument(
        "--evaluate-subset",
        type=str_to_bool,
        default=False,
        help="Whether to evaluate only a subset of rows. Use true or false.",
    )
    parser.add_argument(
        "--subset-size",
        type=int,
        default=50,
        help="Number of rows to evaluate when --evaluate-subset true.",
    )
    parser.add_argument(
        "--subset-start",
        type=int,
        default=0,
        help="Starting row index for subset evaluation.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of worker threads. Start with 4 for SQLite read-only evaluation.",
    )
    parser.add_argument(
        "--max-progress-steps",
        type=int,
        default=2_000_000,
        help=(
            "Maximum SQLite progress-handler steps before aborting a query. "
            "Lower this if generated SQL hangs too long."
        ),
    )
    parser.add_argument(
        "--progress-check-interval",
        type=int,
        default=1000,
        help="SQLite VM instruction interval for progress-handler checks.",
    )
    parser.add_argument(
        "--log-every",
        type=int,
        default=50,
        help="Print progress every N completed rows.",
    )
    parser.add_argument(
        "--slow-query-threshold",
        type=float,
        default=5.0,
        help="Print progress immediately when one row takes at least this many seconds.",
    )

    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc

            if not isinstance(row, dict):
                raise ValueError(
                    f"Expected JSON object on line {line_number}, "
                    f"got {type(row).__name__}."
                )

            rows.append(row)

    return rows


def apply_subset(
    rows: list[dict[str, Any]],
    evaluate_subset: bool,
    subset_start: int,
    subset_size: int,
) -> tuple[list[dict[str, Any]], int]:
    original_num_rows = len(rows)

    if not evaluate_subset:
        print(f"Evaluating full dataset: {original_num_rows} rows")
        return rows, original_num_rows

    if subset_start < 0:
        raise ValueError("--subset-start must be >= 0")

    if subset_size <= 0:
        raise ValueError("--subset-size must be > 0")

    subset_end = subset_start + subset_size
    subset_rows = rows[subset_start:subset_end]

    print(
        f"Evaluating subset: rows {subset_start} to {subset_end - 1} "
        f"({len(subset_rows)} rows out of {original_num_rows})"
    )

    return subset_rows, original_num_rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def get_thread_local_connection(db_path: str) -> sqlite3.Connection:
    """
    Each worker thread gets its own SQLite connection.

    Do not share one SQLite connection across threads.
    """
    if not hasattr(thread_local, "conn"):
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA query_only = ON")
        thread_local.conn = conn

    return thread_local.conn


def has_order_by(sql: Any) -> bool:
    if sql is None:
        return False

    return ORDER_BY_RE.search(str(sql)) is not None


def make_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, bytes):
        return {"type": "bytes", "hex": value.hex()}

    if isinstance(value, tuple):
        return [make_json_safe(item) for item in value]

    if isinstance(value, list):
        return [make_json_safe(item) for item in value]

    if isinstance(value, dict):
        return {str(key): make_json_safe(item) for key, item in value.items()}

    return str(value)


def result_preview(
    rows: list[tuple[Any, ...]],
    max_rows: int,
) -> dict[str, Any]:
    preview_rows = rows[:max_rows]

    return {
        "row_count": len(rows),
        "truncated": len(rows) > max_rows,
        "rows": make_json_safe(preview_rows),
    }


def normalise_value_for_compare(value: Any) -> Any:
    """
    Normalise values for fast hash-based comparison.

    Floats are rounded to 6 decimal places to avoid tiny numerical drift.
    """
    if isinstance(value, float):
        return round(value, 6)

    return value


def normalise_row_for_compare(row: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(normalise_value_for_compare(value) for value in row)


def compare_results(
    gold_rows: list[tuple[Any, ...]],
    generated_rows: list[tuple[Any, ...]],
    order_sensitive: bool,
) -> bool:
    """
    Compare generated and gold SQL execution results.

    If gold SQL contains ORDER BY, preserve row order.
    Otherwise, compare order-insensitively using Counter for speed.
    """
    if len(gold_rows) != len(generated_rows):
        return False

    if order_sensitive:
        return all(
            normalise_row_for_compare(gold_row)
            == normalise_row_for_compare(generated_row)
            for gold_row, generated_row in zip(gold_rows, generated_rows)
        )

    return Counter(
        normalise_row_for_compare(row) for row in gold_rows
    ) == Counter(
        normalise_row_for_compare(row) for row in generated_rows
    )


def execute_sql(
    conn: sqlite3.Connection,
    sql: Any,
    max_progress_steps: int,
    progress_check_interval: int,
) -> tuple[list[tuple[Any, ...]] | None, str | None]:
    """
    Execute SQL with a SQLite progress-handler guard.

    This prevents one bad generated SQL query from hanging the whole evaluation.
    """
    if sql is None or not str(sql).strip():
        return None, "empty SQL"

    progress_steps = 0

    def progress_handler() -> int:
        nonlocal progress_steps
        progress_steps += 1

        if progress_steps > max_progress_steps:
            return 1

        return 0

    try:
        conn.set_progress_handler(progress_handler, progress_check_interval)
        cursor = conn.execute(str(sql))
        rows = cursor.fetchall()
        return rows, None

    except Exception as exc:
        error_message = str(exc)

        if "interrupted" in error_message.lower():
            return None, (
                "query_timeout: SQLite progress limit exceeded "
                f"(max_progress_steps={max_progress_steps})"
            )

        return None, error_message

    finally:
        conn.set_progress_handler(None, 0)


def rows_are_all_null(rows: list[tuple[Any, ...]] | None) -> bool:
    if not rows:
        return False

    return all(all(value is None for value in row) for row in rows)


def get_ambiguity_flags(
    gold_rows: list[tuple[Any, ...]] | None,
    generated_rows: list[tuple[Any, ...]] | None,
    gold_error: str | None,
    generated_error: str | None,
) -> list[str]:
    flags: list[str] = []

    if gold_error:
        flags.append("gold_sql_error")

    if generated_error:
        flags.append("generated_sql_error")

    if gold_error is not None or generated_error is not None:
        return flags

    gold_rows = gold_rows or []
    generated_rows = generated_rows or []

    if len(gold_rows) == 0 and len(generated_rows) == 0:
        flags.append("both_results_empty")
    elif len(gold_rows) == 0:
        flags.append("gold_result_empty")
    elif len(generated_rows) == 0:
        flags.append("generated_result_empty")

    if rows_are_all_null(gold_rows):
        flags.append("gold_result_all_null")

    if rows_are_all_null(generated_rows):
        flags.append("generated_result_all_null")

    return flags


def should_place_in_group_d(
    ambiguity_flags: list[str],
    treat_empty_results_as_ambiguous: bool,
) -> bool:
    if "gold_sql_error" in ambiguity_flags:
        return True

    if not treat_empty_results_as_ambiguous:
        return False

    empty_or_null_flags = {
        "both_results_empty",
        "gold_result_empty",
        "generated_result_empty",
        "gold_result_all_null",
        "generated_result_all_null",
    }

    return any(flag in empty_or_null_flags for flag in ambiguity_flags)


def assign_evaluation_group(
    generated_error: str | None,
    execution_match: bool,
    ambiguity_flags: list[str],
    treat_empty_results_as_ambiguous: bool,
) -> str:
    if should_place_in_group_d(
        ambiguity_flags=ambiguity_flags,
        treat_empty_results_as_ambiguous=treat_empty_results_as_ambiguous,
    ):
        return GROUP_D

    if generated_error is not None:
        return GROUP_C

    if execution_match:
        return GROUP_A

    return GROUP_B


def build_error_message(
    baseline_status: Any,
    baseline_error: Any,
    gold_error: str | None,
    generated_error: str | None,
) -> str | None:
    error_parts: list[str] = []

    if baseline_status and baseline_status != "success":
        error_parts.append(f"baseline status: {baseline_status}")

    if baseline_error:
        error_parts.append(f"baseline error: {baseline_error}")

    if generated_error:
        error_parts.append(f"generated SQL error: {generated_error}")

    if gold_error:
        error_parts.append(f"gold SQL error: {gold_error}")

    return "; ".join(error_parts) if error_parts else None


def evaluate_row_worker(
    row_index: int,
    row: dict[str, Any],
    db_path: str,
    max_result_preview_rows: int,
    treat_empty_results_as_ambiguous: bool,
    max_progress_steps: int,
    progress_check_interval: int,
) -> dict[str, Any]:
    start_time = time.perf_counter()

    conn = get_thread_local_connection(db_path)

    generated_sql = row.get("generated_sql") or row.get("pred_sql")
    gold_sql = row.get("gold_sql")

    baseline_status = row.get("status")
    baseline_error = row.get("error")

    gold_rows, gold_error = execute_sql(
        conn=conn,
        sql=gold_sql,
        max_progress_steps=max_progress_steps,
        progress_check_interval=progress_check_interval,
    )
    generated_rows, generated_error = execute_sql(
        conn=conn,
        sql=generated_sql,
        max_progress_steps=max_progress_steps,
        progress_check_interval=progress_check_interval,
    )

    execution_match = False
    if gold_error is None and generated_error is None:
        execution_match = compare_results(
            gold_rows=gold_rows if gold_rows is not None else [],
            generated_rows=generated_rows if generated_rows is not None else [],
            order_sensitive=has_order_by(gold_sql),
        )

    ambiguity_flags = get_ambiguity_flags(
        gold_rows=gold_rows,
        generated_rows=generated_rows,
        gold_error=gold_error,
        generated_error=generated_error,
    )

    evaluation_group = assign_evaluation_group(
        generated_error=generated_error,
        execution_match=execution_match,
        ambiguity_flags=ambiguity_flags,
        treat_empty_results_as_ambiguous=treat_empty_results_as_ambiguous,
    )

    elapsed = time.perf_counter() - start_time

    return {
        "_row_index": row_index,

        "question_id": row.get("question_id"),
        "db_id": row.get("db_id", "booksql"),
        "generator": row.get("generator") or row.get("model_key"),
        "prompt_setting": row.get("prompt_setting", "zero_shot"),
        "split": row.get("split"),
        "level": row.get("level"),
        "question": row.get("question"),

        "baseline_status": baseline_status,
        "baseline_error": baseline_error,
        "model_metadata": row.get("model_metadata"),
        "few_shot_examples": row.get("few_shot_examples"),
        "raw_output": row.get("raw_output"),

        "generated_sql": generated_sql,
        "gold_sql": gold_sql,

        "generated_execution_status": "success" if generated_error is None else "error",
        "gold_execution_status": "success" if gold_error is None else "error",

        "generated_result": (
            result_preview(generated_rows or [], max_result_preview_rows)
            if generated_error is None
            else None
        ),
        "gold_result": (
            result_preview(gold_rows or [], max_result_preview_rows)
            if gold_error is None
            else None
        ),

        "execution_match": execution_match,
        "evaluation_group": evaluation_group,
        "excluded_from_primary_metrics": evaluation_group == GROUP_D,
        "ambiguity_flags": ambiguity_flags,

        "generated_error": generated_error,
        "gold_error": gold_error,
        "error_message": build_error_message(
            baseline_status=baseline_status,
            baseline_error=baseline_error,
            gold_error=gold_error,
            generated_error=generated_error,
        ),

        "evaluation_time_seconds": round(elapsed, 4),
    }


def safe_rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def build_metrics(evaluated_rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(evaluated_rows)

    group_counts = Counter(row["evaluation_group"] for row in evaluated_rows)

    metric_rows = [
        row
        for row in evaluated_rows
        if not row["excluded_from_primary_metrics"]
    ]
    metric_total = len(metric_rows)

    num_group_a = group_counts[GROUP_A]
    num_group_b = group_counts[GROUP_B]
    num_group_c = group_counts[GROUP_C]
    num_group_d = group_counts[GROUP_D]

    num_execution_correct = num_group_a
    num_valid_sql = num_group_a + num_group_b
    num_executable_wrong = num_group_b
    num_execution_error = num_group_c

    slowest_rows = sorted(
        evaluated_rows,
        key=lambda row: row.get("evaluation_time_seconds", 0),
        reverse=True,
    )[:10]

    return {
        "total_examples": total,
        "metric_total_examples": metric_total,

        "group_counts": {
            GROUP_A: num_group_a,
            GROUP_B: num_group_b,
            GROUP_C: num_group_c,
            GROUP_D: num_group_d,
        },

        "execution_accuracy": safe_rate(num_execution_correct, metric_total),
        "valid_sql_rate": safe_rate(num_valid_sql, metric_total),
        "executable_wrong_rate": safe_rate(num_executable_wrong, metric_total),

        "num_execution_correct": num_execution_correct,
        "num_valid_sql": num_valid_sql,
        "num_executable_wrong": num_executable_wrong,
        "num_execution_error": num_execution_error,
        "num_excluded_ambiguous": num_group_d,

        "slowest_rows": [
            {
                "question_id": row.get("question_id"),
                "evaluation_time_seconds": row.get("evaluation_time_seconds"),
                "evaluation_group": row.get("evaluation_group"),
                "generated_error": row.get("generated_error"),
                "gold_error": row.get("gold_error"),
            }
            for row in slowest_rows
        ],
    }


def remove_internal_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if not key.startswith("_")
    }


def main() -> None:
    args = parse_args()

    input_jsonl = Path(args.input_jsonl)
    output_jsonl = Path(args.output_jsonl)
    metrics_json = Path(args.metrics_json)

    db_path = str(get_booksql_db_path(args.db_path))
    rows = read_jsonl(input_jsonl)
    rows, original_num_rows = apply_subset(
        rows=rows,
        evaluate_subset=args.evaluate_subset,
        subset_start=args.subset_start,
        subset_size=args.subset_size,
    )

    print(f"Using workers: {args.workers}")
    print(f"DB path: {db_path}")

    start_time = time.perf_counter()

    evaluated_rows: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(
                evaluate_row_worker,
                row_index,
                row,
                db_path,
                args.max_result_preview_rows,
                args.treat_empty_results_as_ambiguous,
                args.max_progress_steps,
                args.progress_check_interval,
            )
            for row_index, row in enumerate(rows)
        ]

        for completed_count, future in enumerate(as_completed(futures), start=1):
            evaluated_row = future.result()
            evaluated_rows.append(evaluated_row)

            row_time = evaluated_row.get("evaluation_time_seconds", 0)

            if (
                completed_count % args.log_every == 0
                or row_time >= args.slow_query_threshold
            ):
                print(
                    f"[{completed_count}/{len(rows)}] "
                    f"qid={evaluated_row.get('question_id')} "
                    f"time={row_time:.2f}s "
                    f"group={evaluated_row.get('evaluation_group')} "
                    f"gen_status={evaluated_row.get('generated_execution_status')}",
                    flush=True,
                )

    evaluated_rows.sort(key=lambda row: row["_row_index"])

    total_time = time.perf_counter() - start_time

    metrics = build_metrics(evaluated_rows)
    metrics["total_evaluation_time_seconds"] = round(total_time, 4)
    metrics["workers"] = args.workers
    metrics["max_progress_steps"] = args.max_progress_steps
    metrics["progress_check_interval"] = args.progress_check_interval
    metrics["evaluate_subset"] = args.evaluate_subset
    metrics["subset_size"] = args.subset_size if args.evaluate_subset else None
    metrics["subset_start"] = args.subset_start if args.evaluate_subset else None
    metrics["original_num_rows"] = original_num_rows

    output_rows = [remove_internal_fields(row) for row in evaluated_rows]

    write_jsonl(output_jsonl, output_rows)
    write_json(metrics_json, metrics)

    print(f"Evaluated rows: {metrics['total_examples']}")
    print(f"Metric denominator: {metrics['metric_total_examples']}")
    print(f"Group A correct executable: {metrics['group_counts'][GROUP_A]}")
    print(f"Group B wrong executable: {metrics['group_counts'][GROUP_B]}")
    print(f"Group C non-executable: {metrics['group_counts'][GROUP_C]}")
    print(f"Group D ambiguous: {metrics['group_counts'][GROUP_D]}")
    print(f"Execution accuracy: {metrics['execution_accuracy']:.4f}")
    print(f"Valid SQL rate: {metrics['valid_sql_rate']:.4f}")
    print(f"Executable-wrong rate: {metrics['executable_wrong_rate']:.4f}")
    print(f"Total evaluation time: {total_time:.2f}s")
    print(f"Saved row-level results to {output_jsonl}")
    print(f"Saved metrics to {metrics_json}")


if __name__ == "__main__":
    main()