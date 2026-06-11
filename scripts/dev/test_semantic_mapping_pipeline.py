#!/usr/bin/env python3
"""
Run the real schema-loading + SQL parsing + semantic-mapping pipeline on real baseline output.

Usage from project root:

    python scripts/dev/test_semantic_mapping_pipeline.py

Optional:

    python scripts/dev/test_semantic_mapping_pipeline.py --limit 50

    python scripts/dev/test_semantic_mapping_pipeline.py \
      --input data/outputs/baseline/baseline_qwen_train_sample_50_few_shot.jsonl \
      --schema data/booksql/schema_annotations.json

This script is non-destructive:
- It does not delete, move, or overwrite project files by default.
- It writes a new timestamped JSONL debug output under data/outputs/debug/.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_INPUT_PATH = PROJECT_ROOT / "data/outputs/baseline/baseline_qwen_train_sample_50_few_shot.jsonl"
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "data/booksql/schema_annotations.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/outputs/debug"


def import_real_pipeline():
    """
    Import the actual project pipeline functions.

    Adjust only if your function names differ.
    """
    from src.finverisql.schema_loader import SchemaAnnotationStore
    from src.finverisql.sql_parser import parse_sql
    from src.finverisql.sql_semantic_mapping import build_sql_financial_semantics

    from src.finverisql.compact_semantic_profile import build_verifier_payload

    return (
        SchemaAnnotationStore,
        parse_sql,
        build_sql_financial_semantics,
        build_verifier_payload,
    )


def load_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_no}: {path}") from exc

            if limit is not None and len(rows) >= limit:
                break

    return rows


def extract_sql_from_text(text: str) -> str | None:
    """
    Extract SQL from a model response string.

    Handles:
    - raw SQL
    - ```sql ... ```
    - text that contains a SELECT query
    """
    if not isinstance(text, str) or not text.strip():
        return None

    text = text.strip()

    fenced = re.search(
        r"```(?:sql)?\s*(.*?)```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if fenced:
        candidate = fenced.group(1).strip()
        if candidate.lower().startswith("select"):
            return candidate.rstrip(";") + ";"

    if text.lower().startswith("select"):
        return text.rstrip(";") + ";"

    embedded = re.search(
        r"(select\s+.+)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if embedded:
        return embedded.group(1).strip().rstrip(";") + ";"

    return None


def find_sql(row: dict[str, Any], preferred_sql_field: str | None = None) -> tuple[str | None, str | None]:
    """
    Find SQL from a baseline JSONL row.

    If you know the exact field name, pass --sql-field.
    Otherwise, this searches common output fields.
    """
    if preferred_sql_field:
        value = row.get(preferred_sql_field)
        sql = extract_sql_from_text(value) if isinstance(value, str) else None
        return sql, preferred_sql_field if sql else None

    candidate_fields = [
        "pred_sql",
        "predicted_sql",
        "prediction_sql",
        "generated_sql",
        "model_sql",
        "response_sql",
        "output_sql",
        "sql",
        "query",
        "prediction",
        "output",
        "response",
        "model_output",
        "completion",
        "text",
        "gold_sql",
    ]

    for field in candidate_fields:
        value = row.get(field)

        if isinstance(value, str):
            sql = extract_sql_from_text(value)
            if sql:
                return sql, field

        if isinstance(value, dict):
            nested_sql, nested_field = find_sql(value)
            if nested_sql:
                return nested_sql, f"{field}.{nested_field}"

    # Last resort: scan every string field.
    for field, value in row.items():
        if isinstance(value, str):
            sql = extract_sql_from_text(value)
            if sql:
                return sql, field

    return None, None


def to_jsonable(obj: Any) -> Any:
    """
    Convert dataclass-like outputs to JSON-serialisable objects.
    """
    if obj is None:
        return None

    if hasattr(obj, "to_dict"):
        return to_jsonable(obj.to_dict())

    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}

    if isinstance(obj, list):
        return [to_jsonable(v) for v in obj]

    if isinstance(obj, tuple):
        return [to_jsonable(v) for v in obj]

    if isinstance(obj, (str, int, float, bool)):
        return obj

    return str(obj)


def collect_text_flags(obj: Any) -> list[str]:
    """
    Extract useful warning/status signals from semantic output.
    This does not judge correctness. It just surfaces things to inspect.
    """
    flags: list[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for key, value in x.items():
                key_lower = str(key).lower()

                if key_lower in {
                    "warning",
                    "warnings",
                    "value_status",
                    "invalid_value_policy",
                    "missing_value_policy",
                    "parse_error",
                    "unsupported_lineage",
                    "resolution_status",
                }:
                    flags.append(f"{key}: {value}")

                walk(value)

        elif isinstance(x, list):
            for item in x:
                walk(item)

    walk(obj)

    return flags


def run_one_row(
    *,
    row: dict[str, Any],
    index: int,
    schema_store: Any,
    parse_sql: Any,
    build_sql_financial_semantics: Any,
    build_verifier_payload: Any,
    sql_field: str | None,
) -> dict[str, Any]:
    sql, detected_sql_field = find_sql(row, preferred_sql_field=sql_field)

    result: dict[str, Any] = {
        "index": index,
        "id": row.get("id") or row.get("question_id") or row.get("qid"),
        "question": row.get("question") or row.get("nl_question") or row.get("utterance"),
        "gold_sql": row.get("gold_sql"),
        "detected_sql_field": detected_sql_field,
        "sql": sql,
        "status": None,
    }

    if not sql:
        result["status"] = "sql_missing"
        return result

    try:
        parsed_sql = parse_sql(sql)
        parsed_dict = to_jsonable(parsed_sql)

        semantic_profile = build_sql_financial_semantics(parsed_sql, schema_store)
        semantic_dict = to_jsonable(semantic_profile)

        compact_profile = build_verifier_payload(semantic_profile)
        compact_dict = to_jsonable(compact_profile)

        result["status"] = "ok"
        result["parsed_sql"] = parsed_dict
        result["semantic_profile"] = semantic_dict
        result["compact_semantic_profile"] = compact_dict
        result["semantic_flags"] = collect_text_flags(semantic_dict)
        result["compact_flags"] = collect_text_flags(compact_dict)

        result["profile_sizes"] = {
            "semantic_profile_chars": len(json.dumps(semantic_dict, ensure_ascii=False)),
            "compact_semantic_profile_chars": len(json.dumps(compact_dict, ensure_ascii=False)),
        }

    except Exception as exc:
        result["status"] = "error"
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)

    return result


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(r.get("status") for r in results)
    fsir_counts = Counter(r.get("fsir_status") for r in results if "fsir_status" in r)

    flag_counts: Counter[str] = Counter()

    for result in results:
        for flag in result.get("semantic_flags", []):
            flag_text = str(flag).lower()

            if "unobserved_literal" in flag_text:
                flag_counts["unobserved_literal"] += 1
            if "missing_marker" in flag_text or "missing_literal" in flag_text:
                flag_counts["missing_literal"] += 1
            if "ambiguous" in flag_text:
                flag_counts["ambiguous_resolution"] += 1
            if "transaction_line" in flag_text or "transaction lines" in flag_text:
                flag_counts["transaction_line_grain"] += 1
            if "requires_account_context" in flag_text or "requires account" in flag_text:
                flag_counts["requires_account_context"] += 1

    return {
        "rows_processed": len(results),
        "status_counts": dict(status_counts),
        "fsir_status_counts": dict(fsir_counts),
        "semantic_flag_counts": dict(flag_counts),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA_PATH)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sql-field", type=str, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Only applies when --output is explicitly provided.",
    )

    args = parser.parse_args()

    input_path = args.input.resolve()
    schema_path = args.schema.resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"Input JSONL not found: {input_path}")

    if not schema_path.exists():
        raise FileNotFoundError(f"Schema annotation JSON not found: {schema_path}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.output is None:
        output_path = DEFAULT_OUTPUT_DIR / f"real_semantic_mapping_pipeline_{timestamp}.jsonl"
    else:
        output_path = args.output.resolve()
        if output_path.exists() and not args.overwrite:
            raise FileExistsError(
                f"Output already exists: {output_path}\n"
                "Pass --overwrite if you intentionally want to replace it."
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    (
        SchemaAnnotationStore,
        parse_sql,
        build_sql_financial_semantics,
        build_verifier_payload,
    ) = import_real_pipeline()

    schema_store = SchemaAnnotationStore.from_json(schema_path)
    rows = load_jsonl(input_path, limit=args.limit)
    
    print("=" * 88)
    print("REAL SEMANTIC MAPPING PIPELINE TEST")
    print("=" * 88)
    print(f"Input:  {input_path}")
    print(f"Schema: {schema_path}")
    print(f"Output: {output_path}")
    print(f"Rows:   {len(rows)}")

    results: list[dict[str, Any]] = []

    with output_path.open("w", encoding="utf-8") as out:
        for index, row in enumerate(rows):
            result = run_one_row(
                row=row,
                index=index,
                schema_store=schema_store,
                parse_sql=parse_sql,
                build_sql_financial_semantics=build_sql_financial_semantics,
                build_verifier_payload=build_verifier_payload,
                sql_field=args.sql_field,
            )

            results.append(result)
            out.write(json.dumps(result, ensure_ascii=False) + "\n")

            status = result.get("status")
            sql_field = result.get("detected_sql_field")
            print(f"[{index + 1}/{len(rows)}] {status} sql_field={sql_field}")

    summary = summarise(results)

    print("\n" + "=" * 88)
    print("SUMMARY")
    print("=" * 88)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nWrote real pipeline output to:\n{output_path}")


if __name__ == "__main__":
    main()