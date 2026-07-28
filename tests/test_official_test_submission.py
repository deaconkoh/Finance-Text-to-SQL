from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import official_test_artifacts


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_submission_export_preserves_order_and_uses_original_sql_fallback(tmp_path: Path) -> None:
    source = tmp_path / "repairs.jsonl"
    submission = tmp_path / "submission.csv"
    predictions = tmp_path / "predictions.jsonl"
    table = tmp_path / "table.md"
    summary = tmp_path / "summary.json"
    write_jsonl(
        source,
        [
            {"question_id": "1", "official_test_id": 1, "original_generated_sql": "SELECT 'a,b'"},
            {"question_id": "0", "official_test_id": 0, "repaired_sql": "SELECT 1"},
        ],
    )

    subprocess.run(
        [
            sys.executable,
            "scripts/export_official_test_submission.py",
            "--input-jsonl", str(source),
            "--submission-csv", str(submission),
            "--predictions-jsonl", str(predictions),
            "--table-md", str(table),
            "--summary-json", str(summary),
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )

    with submission.open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle)) == [
            {"": "0", "id": "0", "pred_sql": "SELECT 1"},
            {"": "1", "id": "1", "pred_sql": "SELECT 'a,b'"},
        ]
    assert submission.read_text(encoding="utf-8").splitlines()[0] == ",id,pred_sql"
    assert "\"SELECT 'a,b'\"" in submission.read_text(encoding="utf-8")
    assert "Pending official BookSQL leaderboard submission" in table.read_text(encoding="utf-8")
    assert json.loads(summary.read_text(encoding="utf-8"))["test_ex"] is None


def canonical_args(source: Path, attempts: Path, output: Path):
    return type(
        "Args",
        (),
        {
            "source": str(source),
            "attempts": [str(attempts)],
            "output": str(output),
            "accept_status": ["success"],
        },
    )()


def test_official_canonicalization_rejects_bad_id_and_context_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    output = tmp_path / "canonical.jsonl"
    source_rows = [
        {"question_id": str(index), "official_test_id": index, "question": f"q{index}", "split": "test"}
        for index in range(2)
    ]
    write_jsonl(source, source_rows)

    cases = [
        (
            [
                {**source_rows[0], "status": "success"},
                {**source_rows[0], "status": "success"},
                {**source_rows[1], "status": "success"},
            ],
            "Duplicate terminal",
        ),
        ([{**source_rows[0], "status": "success"}], "incomplete or failed"),
        (
            [
                {**source_rows[0], "status": "success"},
                {"question_id": "2", "official_test_id": 2, "question": "foreign", "status": "success"},
            ],
            "Foreign",
        ),
        (
            [
                {**source_rows[0], "status": "success"},
                {**source_rows[1], "status": "failed"},
            ],
            "incomplete or failed",
        ),
        (
            [
                {**source_rows[0], "status": "success"},
                {**source_rows[1], "question": "changed", "status": "success"},
            ],
            "context mismatch",
        ),
    ]
    attempts = tmp_path / "attempts.jsonl"
    for rows, message in cases:
        write_jsonl(attempts, rows)
        with pytest.raises(ValueError, match=message):
            official_test_artifacts.canonicalize(canonical_args(source, attempts, output))
