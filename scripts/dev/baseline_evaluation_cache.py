#!/usr/bin/env python3
"""Validate or record reusable baseline evaluation stage artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl_ids(path: Path) -> list[str]:
    ids: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not row.get("question_id"):
                raise ValueError(f"{path}:{line_number} has no question_id")
            ids.append(str(row["question_id"]))
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path} has duplicate question_id values")
    return ids


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["evaluation", "asa"], required=True)
    parser.add_argument("--input-jsonl", required=True)
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--schema-path", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--metrics-json", default=None)
    parser.add_argument("--metrics-md", default=None)
    parser.add_argument("--row-output-jsonl", default=None)
    parser.add_argument("--workers", type=int, default=4)
    return parser.parse_args()


def expected(args: argparse.Namespace) -> dict[str, Any]:
    input_path = Path(args.input_jsonl)
    expected_data: dict[str, Any] = {
        "stage": args.stage,
        "input_path": str(input_path),
        "input_sha256": sha256(input_path),
        "input_question_ids": read_jsonl_ids(input_path),
        "db_sha256": sha256(Path(args.db_path)),
        "schema_sha256": sha256(Path(args.schema_path)),
        "workers": args.workers,
        "outputs": {
            "output_jsonl": args.output_jsonl,
            "metrics_json": args.metrics_json,
            "metrics_md": args.metrics_md,
            "row_output_jsonl": args.row_output_jsonl,
        },
    }
    return expected_data


def required_outputs(args: argparse.Namespace) -> list[Path]:
    outputs = [Path(args.output_jsonl)]
    if args.metrics_json:
        outputs.append(Path(args.metrics_json))
    if args.metrics_md:
        outputs.append(Path(args.metrics_md))
    if args.row_output_jsonl:
        outputs.append(Path(args.row_output_jsonl))
    return outputs


def validate_outputs(args: argparse.Namespace, data: dict[str, Any]) -> None:
    outputs = required_outputs(args)
    missing = [str(path) for path in outputs if not path.is_file()]
    if missing:
        raise ValueError("Missing cached outputs: " + ", ".join(missing))

    if args.stage == "evaluation":
        output_ids = read_jsonl_ids(Path(args.output_jsonl))
        if output_ids != data["input_question_ids"]:
            raise ValueError("Cached output question IDs do not match the input JSONL")

        if args.metrics_json:
            metrics = json.loads(Path(args.metrics_json).read_text(encoding="utf-8"))
            total = metrics.get("total_examples") if isinstance(metrics, dict) else None
            if total != len(output_ids):
                raise ValueError("Cached metrics total_examples does not match output rows")


def main() -> None:
    args = parse_args()
    data = expected(args)
    manifest_path = Path(args.manifest)
    try:
        validate_outputs(args, data)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"cache-miss: {exc}")
        raise SystemExit(1) from exc

    if manifest_path.is_file():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"cache-miss: invalid manifest: {exc}")
            raise SystemExit(1) from exc
        if existing != data:
            print("cache-miss: input, configuration, or output contract changed")
            raise SystemExit(1)
        print(f"cache-hit: {args.stage}")
        return

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"cache-adopted: {args.stage}")


if __name__ == "__main__":
    main()
