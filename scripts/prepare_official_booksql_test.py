#!/usr/bin/env python3
"""Download and freeze the question-only official BookSQL test split."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-path", required=True)
    parser.add_argument("--manifest-path", required=True)
    parser.add_argument("--dataset-name", default="Exploration-Lab/BookSQL")
    parser.add_argument("--filename", default="BookSQL/test.json")
    parser.add_argument("--revision", default=None)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_source(path: Path, filename: str) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected a JSON list in {filename}, got {type(payload).__name__}.")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            raise ValueError(f"Official test row {index} is not a JSON object.")
        rows.append(dict(row))
    return rows


def resolved_snapshot_revision(path: Path) -> str | None:
    parts = path.resolve().parts
    try:
        index = parts.index("snapshots")
    except ValueError:
        return None
    return parts[index + 1] if index + 1 < len(parts) else None


def download_source(
    dataset_name: str,
    filename: str,
    revision: str | None,
) -> tuple[Path, str]:
    from huggingface_hub import HfApi, hf_hub_download

    source = Path(
        hf_hub_download(
            repo_id=dataset_name,
            filename=filename,
            repo_type="dataset",
            revision=revision,
            token=True,
        )
    )
    resolved = resolved_snapshot_revision(source)
    if not resolved:
        resolved = str(
            HfApi().dataset_info(dataset_name, revision=revision, token=True).sha
        )
    if not resolved:
        raise ValueError("Hugging Face did not provide a resolved dataset revision.")
    return source, resolved


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
                "question": question.strip(),
                "level": str(row.get("Levels") or "unknown"),
                "split": "test",
            }
        )
    return normalized


def validate_normalized(path: Path, expected_count: int | None = None) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [row.get("official_test_id") for row in rows]
    if ids != list(range(len(rows))):
        raise ValueError("Official test IDs must be a contiguous zero-based sequence in source order.")
    if any(str(row.get("question_id")) != str(row["official_test_id"]) for row in rows):
        raise ValueError("Official test question_id must equal official_test_id.")
    if expected_count is not None and len(rows) != expected_count:
        raise ValueError(
            f"Official test row count changed unexpectedly: {len(rows)} != {expected_count}"
        )
    return rows


def main() -> None:
    args = parse_args()
    output = Path(args.output_path)
    manifest_path = Path(args.manifest_path)

    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key, actual in (
            ("dataset_name", args.dataset_name),
            ("filename", args.filename),
        ):
            if manifest.get(key) != actual:
                raise ValueError(f"Recorded official source {key} changed unexpectedly.")
        if args.revision != manifest.get("requested_revision"):
            raise ValueError("Requested revision differs from the recorded official source request.")
        source = Path(str(manifest.get("cache_path") or ""))
        if not source.is_file():
            raise FileNotFoundError(f"Recorded Hugging Face cache source is unavailable: {source}")
        if sha256(source) != manifest.get("source_sha256"):
            raise ValueError("Recorded official BookSQL source changed unexpectedly.")
        if not output.exists():
            rows = normalize(read_source(source, args.filename))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
        rows = validate_normalized(output, int(manifest["row_count"]))
        if sha256(output) != manifest.get("normalized_sha256"):
            raise ValueError("Normalized official BookSQL source changed unexpectedly.")
        print(
            f"Reused {len(rows)} official BookSQL test questions at recorded revision "
            f"{manifest['resolved_revision']}"
        )
        return

    source, resolved_revision = download_source(
        args.dataset_name,
        args.filename,
        args.revision,
    )
    rows = normalize(read_source(source, args.filename))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    validate_normalized(output, len(rows))
    manifest = {
        "dataset_name": args.dataset_name,
        "filename": args.filename,
        "requested_revision": args.revision,
        "resolved_revision": resolved_revision,
        "cache_path": str(source.resolve()),
        "source_sha256": sha256(source),
        "normalized_path": str(output),
        "normalized_sha256": sha256(output),
        "row_count": len(rows),
        "official_id_min": 0 if rows else None,
        "official_id_max": len(rows) - 1 if rows else None,
        "official_ids_contiguous_zero_based": True,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {len(rows)} official BookSQL test questions from revision "
        f"{resolved_revision} to {output}"
    )


if __name__ == "__main__":
    main()
