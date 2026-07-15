from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


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
            {"question_id": "1", "official_test_id": 1, "original_generated_sql": "SELECT 2"},
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
            {"id": "0", "pred_sql": "SELECT 1"},
            {"id": "1", "pred_sql": "SELECT 2"},
        ]
    assert "Pending official BookSQL leaderboard submission" in table.read_text(encoding="utf-8")
    assert json.loads(summary.read_text(encoding="utf-8"))["test_ex"] is None
