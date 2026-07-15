from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.build_development_excluded_report import filter_source, load_development_ids


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_HELPER = PROJECT_ROOT / "scripts/dev/baseline_evaluation_cache.py"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_development_ids_require_exactly_2000_unique_rows(tmp_path: Path) -> None:
    development = tmp_path / "development_ids.jsonl"
    rows = [{"question_id": f"q{index}", "split": "validation"} for index in range(2000)]
    write_jsonl(development, rows)

    assert load_development_ids(development) == {f"q{index}" for index in range(2000)}

    write_jsonl(development, rows[:-1])
    try:
        load_development_ids(development)
    except ValueError as exc:
        assert "exactly 2,000" in str(exc)
    else:
        raise AssertionError("Expected an invalid development ID count to fail")


def test_filter_source_preserves_order_and_never_changes_source(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    destination = tmp_path / "filtered.jsonl"
    rows = [
        {"question_id": "q1", "split": "validation"},
        {"question_id": "q2", "split": "validation"},
        {"question_id": "q3", "split": "validation"},
    ]
    write_jsonl(source, rows)
    before = source.read_bytes()

    summary = filter_source(source, destination, ["q1", "q2", "q3"], {"q2"})

    assert summary["rows_after"] == 2
    assert [json.loads(line)["question_id"] for line in destination.read_text().splitlines()] == ["q1", "q3"]
    assert source.read_bytes() == before


def test_baseline_evaluation_cache_adopts_then_reuses_valid_outputs(tmp_path: Path) -> None:
    input_jsonl = tmp_path / "baseline.jsonl"
    evaluated_jsonl = tmp_path / "evaluated.jsonl"
    metrics = tmp_path / "metrics.json"
    db_path = tmp_path / "booksql.sqlite"
    schema_path = tmp_path / "schema.json"
    manifest = tmp_path / "evaluation_manifest.json"
    write_jsonl(input_jsonl, [{"question_id": "q1"}, {"question_id": "q2"}])
    write_jsonl(evaluated_jsonl, [{"question_id": "q1"}, {"question_id": "q2"}])
    metrics.write_text(json.dumps({"total_examples": 2}), encoding="utf-8")
    db_path.write_bytes(b"database")
    schema_path.write_text("{}", encoding="utf-8")
    command = [
        sys.executable, str(CACHE_HELPER), "--stage", "evaluation", "--input-jsonl", str(input_jsonl),
        "--db-path", str(db_path), "--schema-path", str(schema_path), "--manifest", str(manifest),
        "--output-jsonl", str(evaluated_jsonl), "--metrics-json", str(metrics), "--workers", "4",
    ]

    adopted = subprocess.run(command, check=True, text=True, capture_output=True)
    reused = subprocess.run(command, check=True, text=True, capture_output=True)

    assert "cache-adopted" in adopted.stdout
    assert "cache-hit" in reused.stdout
