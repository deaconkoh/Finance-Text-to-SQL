import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = Path(__file__).resolve().parents[1]
for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

try:
    from src.utils.data_utils import get_finch_db_path
except ModuleNotFoundError:
    from utils.data_utils import get_finch_db_path


MAX_RESULT_PREVIEW_ROWS = 100
ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate FINCH Text-to-SQL JSONL outputs.",
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
        "--max-result-preview-rows",
        type=int,
        default=MAX_RESULT_PREVIEW_ROWS,
        help="Maximum result rows to store per query in evaluated JSONL.",
    )
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number}: {exc}") from exc
    return rows


def normalize_sql(sql: Any) -> str:
    if sql is None:
        return ""
    normalized = str(sql).strip().rstrip(";").strip().lower()
    return re.sub(r"\s+", " ", normalized)


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


def result_preview(rows: list[tuple[Any, ...]], max_rows: int) -> dict[str, Any]:
    preview_rows = rows[:max_rows]
    return {
        "row_count": len(rows),
        "truncated": len(rows) > max_rows,
        "rows": make_json_safe(preview_rows),
    }


def canonical_row(row: tuple[Any, ...]) -> str:
    return json.dumps(make_json_safe(row), ensure_ascii=False, sort_keys=True)


def compare_results(
    gold_rows: list[tuple[Any, ...]],
    pred_rows: list[tuple[Any, ...]],
    order_sensitive: bool,
) -> bool:
    if order_sensitive:
        return [canonical_row(row) for row in pred_rows] == [
            canonical_row(row) for row in gold_rows
        ]
    return Counter(canonical_row(row) for row in pred_rows) == Counter(
        canonical_row(row) for row in gold_rows
    )


def execute_sql(db_path: str, sql: Any) -> tuple[list[tuple[Any, ...]] | None, str | None, str | None]:
    if sql is None or not str(sql).strip():
        return None, "empty SQL", "empty_sql"

    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute(str(sql))
            rows = cursor.fetchall()
        return rows, None, None

    except sqlite3.OperationalError as exc:
        error_str = str(exc).lower()

        if "syntax error" in error_str:
            error_type = "syntax_error"
        elif any(phrase in error_str for phrase in [
            "no such table",
            "no such column", 
            "ambiguous column name",
            "table already exists",
        ]):
            error_type = "schema_error"
        else:
            error_type = "runtime_error"

        return None, str(exc), error_type

    except Exception as exc:
        return None, str(exc), "runtime_error"


def evaluate_row(row: dict[str, Any], max_result_preview_rows: int) -> dict[str, Any]:
    evaluated = dict(row)

    gold_sql = row.get("gold_sql")
    pred_sql = row.get("pred_sql")

    exact_string_match = normalize_sql(pred_sql) == normalize_sql(gold_sql)

    gold_rows = None
    pred_rows = None
    gold_error = None
    pred_error = None
    gold_error_type = None  
    pred_error_type = None  

    try:
        db_path = get_finch_db_path(row)
    except Exception as exc:
        db_path = None
        db_error = f"database lookup failed: {exc}"
        gold_error = db_error
        pred_error = db_error

    if db_path is not None:
        gold_rows, gold_error, gold_error_type = execute_sql(db_path, gold_sql)
        pred_rows, pred_error, pred_error_type = execute_sql(db_path, pred_sql)

    valid_sql = pred_error is None
    order_sensitive = has_order_by(gold_sql)
    execution_correct = (
        gold_error is None
        and pred_error is None
        and gold_rows is not None
        and pred_rows is not None
        and compare_results(gold_rows, pred_rows, order_sensitive)
    )

    evaluated.update(
        {
            "valid_sql": valid_sql,
            "execution_correct": execution_correct,
            "exact_string_match": exact_string_match,
            "gold_result": result_preview(
                gold_rows or [],
                max_result_preview_rows,
            )
            if gold_error is None
            else None,
            "pred_result": result_preview(
                pred_rows or [],
                max_result_preview_rows,
            )
            if pred_error is None
            else None,
            "gold_error": gold_error,
            "gold_error_type": gold_error_type,
            "pred_error": pred_error,
            "pred_error_type": pred_error_type
        }
    )
    return evaluated


def empty_metric_counts() -> dict[str, int]:
    return {
        "total": 0,
        "valid_sql_count": 0,
        "execution_correct_count": 0,
        "exact_string_match_count": 0,
    }


def add_metric_row(counts: dict[str, int], row: dict[str, Any]) -> None:
    counts["total"] += 1
    counts["valid_sql_count"] += int(bool(row["valid_sql"]))
    counts["execution_correct_count"] += int(bool(row["execution_correct"]))
    counts["exact_string_match_count"] += int(bool(row["exact_string_match"]))


def finalize_metric_counts(counts: dict[str, int]) -> dict[str, Any]:
    total = counts["total"]
    return {
        **counts,
        "valid_sql_rate": counts["valid_sql_count"] / total if total else 0.0,
        "execution_accuracy": counts["execution_correct_count"] / total
        if total
        else 0.0,
        "exact_string_match_rate": counts["exact_string_match_count"] / total if total else 0.0,
    }


def build_metrics(evaluated_rows: list[dict[str, Any]]) -> dict[str, Any]:
    overall = empty_metric_counts()
    by_knowledge_intensity = defaultdict(lambda: empty_metric_counts())
    by_db_name = defaultdict(lambda: empty_metric_counts())
    by_difficulty = defaultdict(lambda: empty_metric_counts())

    for row in evaluated_rows:
        add_metric_row(overall, row)
        add_metric_row(by_knowledge_intensity[str(row.get("knowledge_intensity"))], row)
        add_metric_row(by_db_name[str(row.get("db_name"))], row)
        add_metric_row(by_difficulty[str(row.get("difficulty"))], row)

    return {
        **finalize_metric_counts(overall),
        "breakdown_by_knowledge_intensity": {
            key: finalize_metric_counts(value)
            for key, value in sorted(by_knowledge_intensity.items())
        },
        "breakdown_by_db_name": {
            key: finalize_metric_counts(value) for key, value in sorted(by_db_name.items())
        },
        "breakdown_by_difficulty": {
            key: finalize_metric_counts(value)
            for key, value in sorted(by_difficulty.items())
        },
    }


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


def main() -> None:
    args = parse_args()
    input_jsonl = Path(args.input_jsonl)
    output_jsonl = Path(args.output_jsonl)
    metrics_json = Path(args.metrics_json)

    rows = read_jsonl(input_jsonl)
    evaluated_rows = [
        evaluate_row(row, args.max_result_preview_rows)
        for row in rows
    ]
    metrics = build_metrics(evaluated_rows)

    write_jsonl(output_jsonl, evaluated_rows)
    write_json(metrics_json, metrics)

    print(f"Evaluated rows: {metrics['total']}")
    print(f"Valid SQL rate: {metrics['valid_sql_rate']:.4f}")
    print(f"Execution accuracy: {metrics['execution_accuracy']:.4f}")
    print(f"Exact string match rate: {metrics['exact_string_match_rate']:.4f}")
    print(f"Saved row-level results to {output_jsonl}")
    print(f"Saved metrics to {metrics_json}")


if __name__ == "__main__":
    main()
