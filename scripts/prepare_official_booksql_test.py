#!/usr/bin/env python3
"""Download and normalize the question-only official BookSQL test split."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--dataset-name", default="Exploration-Lab/BookSQL")
    parser.add_argument("--filename", default="BookSQL/test.json")
    return parser.parse_args()


def load_rows(dataset_name: str, filename: str) -> list[dict[str, Any]]:
    from huggingface_hub import hf_hub_download

    source = Path(hf_hub_download(repo_id=dataset_name, filename=filename, repo_type="dataset"))
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected a JSON list in {filename}, got {type(payload).__name__}.")
    return [dict(row) for row in payload]


def normalize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        question = row.get("Query") or row.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"Official test row {index} has no non-empty Query field.")
        normalized.append(
            {
                "question_id": str(index),
                "official_test_id": index,
                "db_id": "booksql",
                "question": question,
                "level": str(row.get("Levels") or "unknown"),
                "split": "test",
            }
        )
    return normalized


def main() -> None:
    args = parse_args()
    rows = normalize(load_rows(args.dataset_name, args.filename))
    output = Path(args.output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(f"Wrote {len(rows)} official BookSQL test questions to {output}")


if __name__ == "__main__":
    main()
