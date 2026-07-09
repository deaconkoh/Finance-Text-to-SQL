"""Data preparation for fixed-verifier learned repair ablations."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from src.finverisql.repair_runner import classify_candidate_row
from src.repair_learning.prompting import build_prompt_for_candidate, repair_completion_json


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            row = json.loads(text)
            if not isinstance(row, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_number}")
            rows.append(row)
    return rows


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_schema_text(schema_path: str | Path | None) -> str:
    if schema_path is None:
        return ""
    path = Path(schema_path)
    if not path.exists():
        raise FileNotFoundError(f"Schema text path not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def build_learning_examples(
    verifier_rows: list[dict[str, Any]],
    schema_text: str | None,
    split: str | None = None,
    target_sql_key: str = "gold_sql",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Create SFT/RL examples from fixed verifier outputs."""

    examples: list[dict[str, Any]] = []
    skip_counts: Counter[str] = Counter()
    candidate_counts: Counter[str] = Counter()
    group_counts: Counter[str] = Counter()

    for row in verifier_rows:
        if split is not None and row.get("split") != split:
            skip_counts["split_filtered"] += 1
            continue

        is_candidate, repair_kind, skip_reason = classify_candidate_row(row)
        if not is_candidate:
            skip_counts[skip_reason or "not_candidate"] += 1
            continue

        target_sql = str(row.get(target_sql_key) or "").strip()
        if not target_sql:
            skip_counts[f"missing_{target_sql_key}"] += 1
            continue

        try:
            prompt, repair_mode = build_prompt_for_candidate(row, schema_text=schema_text)
        except Exception as exc:
            skip_counts[f"prompt_error:{type(exc).__name__}"] += 1
            continue

        group = str(row.get("evaluation_group") or "")
        candidate_counts[repair_kind or "unknown"] += 1
        group_counts[group] += 1
        examples.append(
            {
                "question_id": row.get("question_id") or row.get("id"),
                "split": row.get("split"),
                "evaluation_group": row.get("evaluation_group"),
                "repair_kind": repair_kind,
                "repair_mode": repair_mode,
                "prompt": prompt,
                "completion": repair_completion_json(target_sql),
                "target_sql": target_sql,
                "original_generated_sql": row.get("generated_sql"),
                "gold_sql": row.get("gold_sql"),
                "verification": row.get("verification"),
                "is_corruption_probe": group == "A_correct_executable",
            }
        )

    manifest = {
        "input_rows": len(verifier_rows),
        "examples": len(examples),
        "split_filter": split,
        "target_sql_key": target_sql_key,
        "candidate_counts": dict(candidate_counts),
        "group_counts": dict(group_counts),
        "skip_counts": dict(skip_counts),
    }
    return examples, manifest


def build_examples_from_file(
    verifier_jsonl: str | Path,
    output_jsonl: str | Path,
    manifest_json: str | Path,
    schema_text_path: str | Path | None = None,
    split: str | None = None,
    target_sql_key: str = "gold_sql",
) -> dict[str, Any]:
    rows = read_jsonl(verifier_jsonl)
    schema_text = load_schema_text(schema_text_path)
    examples, manifest = build_learning_examples(
        verifier_rows=rows,
        schema_text=schema_text,
        split=split,
        target_sql_key=target_sql_key,
    )
    manifest["fixed_verifier_jsonl"] = str(verifier_jsonl)
    manifest["schema_text_path"] = str(schema_text_path) if schema_text_path else None
    write_jsonl(output_jsonl, examples)
    write_json(manifest_json, manifest)
    return manifest

