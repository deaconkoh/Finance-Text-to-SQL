#!/usr/bin/env python3
"""Build SFT/RL repair-learning JSONL from fixed verifier outputs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.repair_learning.data import build_examples_from_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixed-verifier-jsonl", required=True)
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--manifest-json", required=True)
    parser.add_argument("--schema-text-path", default="data/booksql/schema.txt")
    parser.add_argument(
        "--split",
        default="train",
        help="Optional split filter. Use an empty string to disable.",
    )
    parser.add_argument("--target-sql-key", default="gold_sql")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_examples_from_file(
        verifier_jsonl=args.fixed_verifier_jsonl,
        output_jsonl=args.output_jsonl,
        manifest_json=args.manifest_json,
        schema_text_path=args.schema_text_path,
        split=args.split or None,
        target_sql_key=args.target_sql_key,
    )
    print(f"Wrote {manifest['examples']} repair-learning examples to {args.output_jsonl}")
    print(f"Wrote manifest to {args.manifest_json}")


if __name__ == "__main__":
    main()

