from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_SCRIPT = PROJECT_ROOT / "scripts/dev/baseline_evaluation_cache.py"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def cache_command(tmp_path: Path, manifest: Path) -> list[str]:
    input_jsonl = tmp_path / "repairs.jsonl"
    output_jsonl = tmp_path / "final_evaluated.jsonl"
    metrics_json = tmp_path / "metrics.json"
    adapted_jsonl = tmp_path / "adapted.jsonl"
    db_path = tmp_path / "booksql.sqlite"
    schema_path = tmp_path / "schema.json"
    return [
        sys.executable,
        str(CACHE_SCRIPT),
        "--stage", "evaluation",
        "--evaluation-kind", "final",
        "--input-jsonl", str(input_jsonl),
        "--db-path", str(db_path),
        "--schema-path", str(schema_path),
        "--manifest", str(manifest),
        "--output-jsonl", str(output_jsonl),
        "--metrics-json", str(metrics_json),
        "--required-output", str(adapted_jsonl),
    ]


def test_final_evaluation_cache_reuses_valid_artifacts(tmp_path: Path) -> None:
    rows = [{"question_id": "q1"}, {"question_id": "q2"}]
    write_jsonl(tmp_path / "repairs.jsonl", rows)
    write_jsonl(tmp_path / "final_evaluated.jsonl", rows)
    write_jsonl(tmp_path / "adapted.jsonl", rows)
    (tmp_path / "metrics.json").write_text('{"total_examples": 2}\n', encoding="utf-8")
    (tmp_path / "booksql.sqlite").write_bytes(b"database")
    (tmp_path / "schema.json").write_text("{}\n", encoding="utf-8")

    manifest = tmp_path / "final_evaluation_manifest.json"
    first = subprocess.run(cache_command(tmp_path, manifest), check=True, capture_output=True, text=True)
    second = subprocess.run(cache_command(tmp_path, manifest), check=True, capture_output=True, text=True)

    assert "cache-adopted: evaluation" in first.stdout
    assert "cache-hit: evaluation" in second.stdout


def test_final_evaluation_cache_misses_when_refinement_output_changes(tmp_path: Path) -> None:
    rows = [{"question_id": "q1"}]
    write_jsonl(tmp_path / "repairs.jsonl", rows)
    write_jsonl(tmp_path / "final_evaluated.jsonl", rows)
    write_jsonl(tmp_path / "adapted.jsonl", rows)
    (tmp_path / "metrics.json").write_text('{"total_examples": 1}\n', encoding="utf-8")
    (tmp_path / "booksql.sqlite").write_bytes(b"database")
    (tmp_path / "schema.json").write_text("{}\n", encoding="utf-8")

    manifest = tmp_path / "final_evaluation_manifest.json"
    subprocess.run(cache_command(tmp_path, manifest), check=True, capture_output=True, text=True)
    write_jsonl(tmp_path / "repairs.jsonl", [{"question_id": "q1", "changed": True}])
    rerun = subprocess.run(cache_command(tmp_path, manifest), capture_output=True, text=True)

    assert rerun.returncode == 1
    assert "configuration" in rerun.stdout


def test_baseline_evaluation_cache_refreshes_stale_manifest_for_valid_artifacts(tmp_path: Path) -> None:
    input_jsonl = tmp_path / "baseline.jsonl"
    output_jsonl = tmp_path / "baseline_evaluated.jsonl"
    metrics_json = tmp_path / "metrics.json"
    db_path = tmp_path / "booksql.sqlite"
    schema_path = tmp_path / "schema.json"
    manifest = tmp_path / "evaluation_manifest.json"

    rows = [
        {"question_id": "q1", "generated_sql": "SELECT 1;"},
        {"question_id": "q2", "generated_sql": "SELECT 2;"},
    ]
    write_jsonl(input_jsonl, rows)
    write_jsonl(output_jsonl, rows)
    metrics_json.write_text('{"total_examples": 2}\n', encoding="utf-8")
    db_path.write_bytes(b"database")
    schema_path.write_text("{}\n", encoding="utf-8")
    manifest.write_text('{"old": "shape"}\n', encoding="utf-8")

    command = [
        sys.executable,
        str(CACHE_SCRIPT),
        "--stage", "evaluation",
        "--evaluation-kind", "baseline",
        "--input-jsonl", str(input_jsonl),
        "--db-path", str(db_path),
        "--schema-path", str(schema_path),
        "--manifest", str(manifest),
        "--output-jsonl", str(output_jsonl),
        "--metrics-json", str(metrics_json),
    ]

    first = subprocess.run(command, check=True, capture_output=True, text=True)
    second = subprocess.run(command, check=True, capture_output=True, text=True)

    assert "cache-adopted: refreshed stale manifest" in first.stdout
    assert "cache-hit: evaluation" in second.stdout


def test_baseline_evaluation_cache_misses_when_cached_sql_differs(tmp_path: Path) -> None:
    input_jsonl = tmp_path / "baseline.jsonl"
    output_jsonl = tmp_path / "baseline_evaluated.jsonl"
    metrics_json = tmp_path / "metrics.json"
    db_path = tmp_path / "booksql.sqlite"
    schema_path = tmp_path / "schema.json"
    manifest = tmp_path / "evaluation_manifest.json"

    write_jsonl(input_jsonl, [{"question_id": "q1", "generated_sql": "SELECT 1;"}])
    write_jsonl(output_jsonl, [{"question_id": "q1", "generated_sql": "SELECT 2;"}])
    metrics_json.write_text('{"total_examples": 1}\n', encoding="utf-8")
    db_path.write_bytes(b"database")
    schema_path.write_text("{}\n", encoding="utf-8")
    manifest.write_text('{"old": "shape"}\n', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(CACHE_SCRIPT),
            "--stage", "evaluation",
            "--evaluation-kind", "baseline",
            "--input-jsonl", str(input_jsonl),
            "--db-path", str(db_path),
            "--schema-path", str(schema_path),
            "--manifest", str(manifest),
            "--output-jsonl", str(output_jsonl),
            "--metrics-json", str(metrics_json),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "SQL does not match" in result.stdout


def test_asa_cache_allows_group_d_filtered_row_output(tmp_path: Path) -> None:
    input_jsonl = tmp_path / "baseline_evaluated.jsonl"
    asa_rows = tmp_path / "asa_rows.jsonl"
    asa_json = tmp_path / "asa_metrics.json"
    asa_md = tmp_path / "asa_metrics.md"
    db_path = tmp_path / "booksql.sqlite"
    schema_path = tmp_path / "schema.json"
    manifest = tmp_path / "asa_manifest.json"

    write_jsonl(
        input_jsonl,
        [
            {"question_id": "q1", "evaluation_group": "A_correct_executable"},
            {"question_id": "q2", "evaluation_group": "B_wrong_executable"},
            {"question_id": "q3", "evaluation_group": "D_ambiguous"},
        ],
    )
    write_jsonl(
        asa_rows,
        [
            {"question_id": "q1"},
            {"question_id": "q2"},
        ],
    )
    asa_json.write_text(
        json.dumps(
            {
                "joined_question_ids": 2,
                "group_d_filtered_question_ids": 1,
                "sets": [{"label": "before", "total_rows": 2}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    asa_md.write_text("# ASA\n", encoding="utf-8")
    db_path.write_bytes(b"database")
    schema_path.write_text("{}\n", encoding="utf-8")
    manifest.write_text('{"old": "shape"}\n', encoding="utf-8")

    command = [
        sys.executable,
        str(CACHE_SCRIPT),
        "--stage", "asa",
        "--evaluation-kind", "baseline",
        "--input-jsonl", str(input_jsonl),
        "--db-path", str(db_path),
        "--schema-path", str(schema_path),
        "--manifest", str(manifest),
        "--output-jsonl", str(asa_rows),
        "--metrics-json", str(asa_json),
        "--metrics-md", str(asa_md),
        "--row-output-jsonl", str(asa_rows),
    ]

    first = subprocess.run(command, check=True, capture_output=True, text=True)
    second = subprocess.run(command, check=True, capture_output=True, text=True)

    assert "cache-adopted: refreshed stale manifest" in first.stdout
    assert "cache-hit: asa" in second.stdout


def test_ablation_launcher_caches_generic_final_evaluations() -> None:
    launcher = (PROJECT_ROOT / "2_run_ablations.sh").read_text(encoding="utf-8")

    assert "run_cached_final_evaluation" in launcher
    assert "run_cached_asa_evaluation" in launcher
    assert 'run_generic_refine "generic_self_refine"' in launcher
    assert 'run_generic_refine "generic_execution_guided_refine"' in launcher
