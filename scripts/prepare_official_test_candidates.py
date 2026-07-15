#!/usr/bin/env python3
"""Route official-test predictions for FinVeriSQL without gold SQL labels."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.eval.evaluate_baseline_sql import GROUP_B, GROUP_C, execute_sql


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-jsonl", required=True)
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--max-progress-steps", type=int, default=2_000_000)
    parser.add_argument("--progress-check-interval", type=int, default=1000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = read_jsonl(Path(args.input_jsonl))
    output_rows: list[dict[str, Any]] = []
    with sqlite3.connect(args.db_path) as conn:
        conn.execute("PRAGMA query_only = ON")
        for row in rows:
            _, error = execute_sql(conn, row.get("generated_sql"), args.max_progress_steps, args.progress_check_interval)
            executable = error is None
            output_rows.append(
                {
                    **row,
                    # Existing verifier/repair runners use these values solely for routing.
                    # They do not represent oracle correctness on the hidden test split.
                    "evaluation_group": GROUP_B if executable else GROUP_C,
                    "inference_route": "executable" if executable else "non_executable",
                    "local_execution_error": error,
                    "generated_error": error,
                }
            )
    output = Path(args.output_jsonl)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in output_rows), encoding="utf-8")
    print(f"Prepared {len(output_rows)} official-test routing rows at {output}")


if __name__ == "__main__":
    main()
