#!/usr/bin/env python3
"""Canonicalize and validate hidden BookSQL official-test artifacts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def official_id(row: dict[str, Any], label: str) -> int:
    value = row.get("official_test_id")
    if isinstance(value, bool):
        raise ValueError(f"{label} has invalid official_test_id: {value!r}")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} has invalid official_test_id: {value!r}") from exc
    if str(row.get("question_id")) != str(result):
        raise ValueError(f"{label} question_id does not match official_test_id={result}")
    return result


def source_index(path: Path) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    rows = read_jsonl(path)
    indexed: dict[int, dict[str, Any]] = {}
    for row in rows:
        row_id = official_id(row, str(path))
        if row_id in indexed:
            raise ValueError(f"{path} has duplicate official_test_id={row_id}")
        indexed[row_id] = row
    if list(indexed) != list(range(len(rows))):
        raise ValueError(f"{path} IDs must be ordered, contiguous, and zero-based")
    return rows, indexed


def validate_context(
    row: dict[str, Any],
    source: dict[str, Any],
    row_id: int,
    label: str,
) -> None:
    for key in ("question", "split", "db_id"):
        if key in row and key in source and row.get(key) != source.get(key):
            raise ValueError(f"{label} context mismatch for official_test_id={row_id}: {key}")
    if (
        "generated_sql" in row
        and "generated_sql" in source
        and row.get("generated_sql") != source.get("generated_sql")
    ):
        raise ValueError(
            f"{label} context mismatch for official_test_id={row_id}: generated_sql"
        )
    if (
        "original_generated_sql" in row
        and "generated_sql" in source
        and row.get("original_generated_sql") != source.get("generated_sql")
    ):
        raise ValueError(
            f"{label} context mismatch for official_test_id={row_id}: original_generated_sql"
        )


def canonicalize(args: argparse.Namespace) -> None:
    source_rows, indexed_source = source_index(Path(args.source))
    accepted = set(args.accept_status)
    selected: dict[int, dict[str, Any]] = {}
    foreign: list[int] = []
    for attempt_path_text in args.attempts:
        attempt_path = Path(attempt_path_text)
        if not attempt_path.exists():
            continue
        for row in read_jsonl(attempt_path):
            row_id = official_id(row, str(attempt_path))
            source = indexed_source.get(row_id)
            if source is None:
                foreign.append(row_id)
                continue
            validate_context(row, source, row_id, str(attempt_path))
            if row.get("status") not in accepted:
                continue
            if row_id in selected:
                raise ValueError(f"Duplicate terminal official_test_id={row_id}")
            selected[row_id] = row
    if foreign:
        raise ValueError(f"Foreign official test IDs: {sorted(set(foreign))[:5]}")
    missing = [index for index in range(len(source_rows)) if index not in selected]
    if missing:
        raise ValueError(
            f"Official artifact is incomplete or failed; missing {len(missing)} "
            f"successful IDs. First IDs: {missing[:5]}"
        )
    ordered = [selected[index] for index in range(len(source_rows))]
    write_jsonl(Path(args.output), ordered)
    print(f"Wrote {len(ordered)} canonical official rows to {args.output}")


def validate_artifact(args: argparse.Namespace) -> None:
    source_rows, indexed_source = source_index(Path(args.source))
    rows = read_jsonl(Path(args.path))
    accepted = set(args.accept_status)
    seen: set[int] = set()
    for row in rows:
        row_id = official_id(row, args.path)
        if row_id in seen:
            raise ValueError(f"{args.path} has duplicate official_test_id={row_id}")
        source = indexed_source.get(row_id)
        if source is None:
            raise ValueError(f"{args.path} has foreign official_test_id={row_id}")
        validate_context(row, source, row_id, args.path)
        if row.get("status") not in accepted:
            raise ValueError(f"{args.path} has failed official_test_id={row_id}")
        if args.require_sql:
            sql = None
            for key in ("repaired_sql", "final_repaired_sql", "original_generated_sql", "generated_sql"):
                if isinstance(row.get(key), str) and row[key].strip():
                    sql = row[key]
                    break
            if sql is None:
                raise ValueError(f"{args.path} has empty SQL for official_test_id={row_id}")
        if args.require_intent and not isinstance(row.get("intent_representation"), dict):
            raise ValueError(
                f"{args.path} has no intent representation for official_test_id={row_id}"
            )
        seen.add(row_id)
    if [official_id(row, args.path) for row in rows] != list(range(len(source_rows))):
        raise ValueError(f"{args.path} does not contain the complete ordered official ID set")
    print(f"Validated {len(rows)} official rows: {args.path}")


def read_submission(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["", "id", "pred_sql"]:
            raise ValueError(
                f"{path} header must be exactly ',id,pred_sql'; got {reader.fieldnames}"
            )
        return list(reader)


def validate_submissions(args: argparse.Namespace) -> None:
    source_rows, _ = source_index(Path(args.source))
    expected = list(range(len(source_rows)))
    for item in args.submission:
        path = Path(item)
        rows = read_submission(path)
        indices = [int(row[""]) for row in rows]
        ids = [int(row["id"]) for row in rows]
        if indices != expected or ids != expected:
            raise ValueError(f"{path} does not contain the complete indexed official ID set")
        if any(not row["pred_sql"].strip() for row in rows):
            raise ValueError(f"{path} contains empty pred_sql")
    print(f"Validated {len(args.submission)} complete indexed submissions")


def sql_for_row(row: dict[str, Any]) -> str:
    for key in ("repaired_sql", "final_repaired_sql", "original_generated_sql", "generated_sql"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(args: argparse.Namespace) -> None:
    source_rows, _ = source_index(Path(args.source))
    baseline = read_jsonl(Path(args.baseline))
    routed = read_jsonl(Path(args.routed))
    repaired = read_jsonl(Path(args.repaired))
    if not (len(source_rows) == len(baseline) == len(routed) == len(repaired)):
        raise ValueError("Official diagnostic artifacts have inconsistent row counts")
    expected_ids = list(range(len(source_rows)))
    for label, rows in (
        ("baseline", baseline),
        ("routed", routed),
        ("repair", repaired),
    ):
        ids = [official_id(row, label) for row in rows]
        if ids != expected_ids:
            raise ValueError(f"{label} artifact does not match the official ID set")
    baseline_by_id = {official_id(row, "baseline"): row for row in baseline}
    repaired_by_id = {official_id(row, "repair"): row for row in repaired}
    executable = sum(row.get("local_execution_error") is None for row in routed)
    changed = sum(
        sql_for_row(repaired_by_id[index]) != sql_for_row(baseline_by_id[index])
        for index in range(len(source_rows))
    )
    fallback = sum(
        not (
            isinstance(repaired_by_id[index].get("repaired_sql"), str)
            and repaired_by_id[index]["repaired_sql"].strip()
        )
        and not (
            isinstance(repaired_by_id[index].get("final_repaired_sql"), str)
            and repaired_by_id[index]["final_repaired_sql"].strip()
        )
        for index in range(len(source_rows))
    )
    failures: dict[str, int] = {}
    for label, item in zip(args.failure_label, args.attempt_log):
        path = Path(item)
        failures[label] = (
            sum(row.get("status") not in {"success", "skipped"} for row in read_jsonl(path))
            if path.exists()
            else 0
        )
    summary = {
        "official_test_ex": None,
        "official_test_ex_status": "pending_leaderboard_submission",
        "row_count": len(source_rows),
        "coverage": {
            "qwen_only": len(baseline),
            "adir_full": len(repaired),
            "fraction": 1.0 if source_rows else 0.0,
        },
        "local_executable_rate": executable / len(routed) if routed else None,
        "local_executable_count": executable,
        "changed_row_count": changed,
        "fallback_count": fallback,
        "failures": failures,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    source_manifest = json.loads(Path(args.source_manifest).read_text(encoding="utf-8"))
    manifest = {
        "run_id": args.run_id,
        "status": "complete_pending_manual_leaderboard_submission",
        "official_test_source": source_manifest,
        "configuration": json.loads(args.config),
        "artifacts": {
            str(Path(path)): {"sha256": sha256(Path(path))}
            for path in args.artifact
        },
        "diagnostics": summary,
    }
    Path(args.run_manifest).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote official-test diagnostics to {output}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    canonical = commands.add_parser("canonicalize")
    canonical.add_argument("--source", required=True)
    canonical.add_argument("--attempts", action="append", required=True)
    canonical.add_argument("--output", required=True)
    canonical.add_argument("--accept-status", action="append", default=["success"])
    canonical.set_defaults(func=canonicalize)

    validate = commands.add_parser("validate")
    validate.add_argument("--source", required=True)
    validate.add_argument("--path", required=True)
    validate.add_argument("--accept-status", action="append", default=["success"])
    validate.add_argument("--require-sql", action="store_true")
    validate.add_argument("--require-intent", action="store_true")
    validate.set_defaults(func=validate_artifact)

    submissions = commands.add_parser("validate-submissions")
    submissions.add_argument("--source", required=True)
    submissions.add_argument("--submission", action="append", required=True)
    submissions.set_defaults(func=validate_submissions)

    summary = commands.add_parser("summarize")
    summary.add_argument("--source", required=True)
    summary.add_argument("--source-manifest", required=True)
    summary.add_argument("--baseline", required=True)
    summary.add_argument("--routed", required=True)
    summary.add_argument("--repaired", required=True)
    summary.add_argument("--attempt-log", action="append", default=[])
    summary.add_argument("--failure-label", action="append", default=[])
    summary.add_argument("--artifact", action="append", default=[])
    summary.add_argument("--output", required=True)
    summary.add_argument("--run-manifest", required=True)
    summary.add_argument("--run-id", required=True)
    summary.add_argument("--config", required=True)
    summary.set_defaults(func=summarize)
    return root


def main() -> None:
    args = parser().parse_args()
    if getattr(args, "attempt_log", None) is not None and len(args.attempt_log) != len(args.failure_label):
        raise ValueError("--attempt-log and --failure-label counts must match")
    args.func(args)


if __name__ == "__main__":
    main()
