"""
Smoke-test runner, not final experiment pipeline
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.finverisql.intent import heuristic_intent_for_smoke_test
from src.finverisql.schema_loader import SchemaAnnotationStore
from src.finverisql.verifier import verify_sql


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run FinVeriSQL D1/D2 verification over generated SQL outputs."
    )

    parser.add_argument(
        "--input-jsonl",
        required=True,
        help="Input JSONL containing question and generated_sql fields.",
    )
    parser.add_argument(
        "--output-jsonl",
        required=True,
        help="Output JSONL with FinVeriSQL verification reports.",
    )
    parser.add_argument(
        "--schema-annotations",
        default="data/booksql/schema_annotations.json",
        help="Path to frozen schema annotation JSON.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of rows to process.",
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
                raise ValueError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc

            rows.append(row)

    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    args = parse_args()

    input_path = Path(args.input_jsonl)
    output_path = Path(args.output_jsonl)

    rows = read_jsonl(input_path)

    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError("--limit must be a positive integer.")
        rows = rows[: args.limit]

    schema_store = SchemaAnnotationStore.from_json(args.schema_annotations)

    output_rows: list[dict[str, Any]] = []

    for row in rows:
        question = row.get("question")
        generated_sql = row.get("generated_sql")

        if not question or not generated_sql:
            output_rows.append(
                {
                    **row,
                    "finverisql_error": "Missing question or generated_sql.",
                }
            )
            continue

        intent = heuristic_intent_for_smoke_test(question)

        report = verify_sql(
            question=question,
            generated_sql=generated_sql,
            intent=intent,
            schema_store=schema_store,
        )

        output_rows.append(
            {
                "question_id": row.get("question_id"),
                "generator": row.get("generator"),
                "prompt_setting": row.get("prompt_setting"),
                "evaluation_group": row.get("evaluation_group"),
                "question": question,
                "generated_sql": generated_sql,
                "gold_sql": row.get("gold_sql"),
                "finverisql_report": report.to_dict(),
            }
        )

    write_jsonl(output_path, output_rows)

    print(f"Processed rows: {len(output_rows)}")
    print(f"Saved to: {output_path}")


if __name__ == "__main__":
    main()