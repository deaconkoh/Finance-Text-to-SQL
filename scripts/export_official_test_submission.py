#!/usr/bin/env python3
"""Export final FinVeriSQL SQL as a BookSQL leaderboard submission."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def final_sql(row: dict[str, Any]) -> str:
    for key in ("repaired_sql", "final_repaired_sql", "original_generated_sql", "generated_sql"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"Official test row {row.get('question_id')!r} has no final SQL.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-jsonl", required=True)
    parser.add_argument("--submission-csv", required=True)
    parser.add_argument("--predictions-jsonl", required=True)
    parser.add_argument("--table-md", required=True)
    parser.add_argument("--summary-json", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = read_jsonl(Path(args.input_jsonl))
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        try:
            official_id = int(row.get("official_test_id", row["question_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid official test id in row: {row.get('question_id')!r}") from exc
        if official_id in seen:
            raise ValueError(f"Duplicate official test id: {official_id}")
        seen.add(official_id)
        selected.append({**row, "official_test_id": official_id, "final_sql": final_sql(row)})

    selected.sort(key=lambda row: row["official_test_id"])
    if [row["official_test_id"] for row in selected] != list(range(len(selected))):
        raise ValueError("Official test IDs must be a complete contiguous zero-based sequence.")

    predictions = Path(args.predictions_jsonl)
    predictions.parent.mkdir(parents=True, exist_ok=True)
    predictions.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected), encoding="utf-8")
    submission = Path(args.submission_csv)
    with submission.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "pred_sql"])
        writer.writeheader()
        writer.writerows({"id": row["official_test_id"], "pred_sql": row["final_sql"]} for row in selected)

    table = "| System | Test EX |\n| --- | ---: |\n| FinVeriSQL | Pending official BookSQL leaderboard submission |\n"
    Path(args.table_md).write_text(table, encoding="utf-8")
    Path(args.summary_json).write_text(json.dumps({"system": "FinVeriSQL", "test_ex": None, "status": "pending_official_leaderboard_submission", "submission_csv": str(submission), "rows": len(selected)}, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote BookSQL submission with {len(selected)} rows: {submission}")


if __name__ == "__main__":
    main()
