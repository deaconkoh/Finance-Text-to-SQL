from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path
from typing import Any


RANDOM_SEED = 42
GROUP_B = "B_wrong_executable"


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
        description=(
            "Extract executable-wrong SQL rows from evaluated baseline outputs "
            "for manual error labelling. Writes both JSONL and CSV outputs."
        )
    )

    parser.add_argument(
        "--input-jsonl",
        required=True,
        help="Evaluated row-level JSONL from evaluate_baseline_sql.py.",
    )
    parser.add_argument(
        "--output-path",
        required=True,
        help=(
            "Base output path without extension. "
            "The script will automatically write both .jsonl and .csv files."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of executable-wrong rows to export.",
    )
    parser.add_argument(
        "--sample-random",
        type=str_to_bool,
        default=False,
        help="Whether to randomly sample executable-wrong rows. Use true or false.",
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

            if not isinstance(row, dict):
                raise ValueError(
                    f"Expected JSON object at {path}:{line_number}, "
                    f"got {type(row).__name__}."
                )

            rows.append(row)

    return rows


def is_executable_wrong(row: dict[str, Any]) -> bool:
    return row.get("evaluation_group") == GROUP_B


def to_annotation_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_id": row.get("question_id"),
        "db_id": row.get("db_id"),
        "generator": row.get("generator"),
        "prompt_setting": row.get("prompt_setting"),
        "split": row.get("split"),
        "level": row.get("level"),
        "question": row.get("question"),

        # SQL comparison fields
        "generated_sql": row.get("generated_sql"),
        "gold_sql": row.get("gold_sql"),
        "generated_result": row.get("generated_result"),
        "gold_result": row.get("gold_result"),

        # Evaluation fields
        "generated_execution_status": row.get("generated_execution_status"),
        "gold_execution_status": row.get("gold_execution_status"),
        "execution_match": row.get("execution_match"),
        "evaluation_group": row.get("evaluation_group"),
        "ambiguity_flags": row.get("ambiguity_flags"),
        "excluded_from_primary_metrics": row.get("excluded_from_primary_metrics"),

        # Manual annotation fields
        "primary_error_label": None,
        "secondary_error_label": None,
        "error_sublabel": None,
        "annotation_note": None,
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def flatten_for_csv(row: dict[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}

    for key, value in row.items():
        if isinstance(value, (dict, list)):
            flattened[key] = json.dumps(value, ensure_ascii=False)
        elif value is None:
            flattened[key] = ""
        else:
            flattened[key] = value

    return flattened


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        raise ValueError("No rows to write.")

    csv_rows = [flatten_for_csv(row) for row in rows]
    fieldnames = list(csv_rows[0].keys())

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)


def build_output_paths(output_path: str) -> tuple[Path, Path]:
    base_path = Path(output_path)

    if base_path.suffix in {".jsonl", ".csv"}:
        base_path = base_path.with_suffix("")

    jsonl_path = base_path.with_suffix(".jsonl")
    csv_path = base_path.with_suffix(".csv")

    return jsonl_path, csv_path


def main() -> None:
    args = parse_args()

    input_path = Path(args.input_jsonl)
    output_jsonl_path, output_csv_path = build_output_paths(args.output_path)

    rows = read_jsonl(input_path)

    executable_wrong_rows = [
        row for row in rows
        if is_executable_wrong(row)
    ]

    total_executable_wrong = len(executable_wrong_rows)

    if args.sample_random:
        random.seed(RANDOM_SEED)
        random.shuffle(executable_wrong_rows)

    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError("--limit must be a positive integer.")

        executable_wrong_rows = executable_wrong_rows[: args.limit]

    annotation_rows = [
        to_annotation_row(row)
        for row in executable_wrong_rows
    ]

    write_jsonl(output_jsonl_path, annotation_rows)
    write_csv(output_csv_path, annotation_rows)

    print(f"Input rows: {len(rows)}")
    print(f"Total executable-wrong rows found: {total_executable_wrong}")
    print(f"Executable-wrong rows exported: {len(annotation_rows)}")
    print(f"Random sample: {args.sample_random}")
    print(f"Random seed: {RANDOM_SEED if args.sample_random else None}")
    print(f"Saved JSONL to: {output_jsonl_path}")
    print(f"Saved CSV to: {output_csv_path}")


if __name__ == "__main__":
    main()
