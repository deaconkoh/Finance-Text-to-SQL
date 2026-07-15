#!/usr/bin/env python3
"""Build reportable validation metrics excluding the frozen development sample.

This script deliberately performs no generation, verification, repair, or training.
It filters completed labeled-run artifacts by question ID and reruns only the local
SQL and accounting-semantic evaluators on the disjoint validation complement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEVELOPMENT_IDS = (
    PROJECT_ROOT / "data/protocol/booksql_validation_development_2000_ids.jsonl"
)
VARIANTS = [
    ("full", "FinVeriSQL full"),
    ("wo_intent_decomposer", "w/o Intent Decomposer"),
    ("direct_only", "w/o Probing / direct only"),
    ("wo_compact_semantic_profile", "w/o Compact Semantic Profile"),
    ("wo_scope_constraints", "w/o Scope Constraints in Repair"),
    ("wo_reverification_loop", "w/o re-verification loop"),
]
MAIN_SYSTEMS = [
    ("generator_only", "Generator only", "generator", "baseline/qwen_few_shot_validation.jsonl"),
    (
        "generic_self_refine",
        "Generator + generic self-refine",
        "repair",
        "main_comparison/generic_self_refine/generic_self_refine.jsonl",
    ),
    (
        "generic_execution_guided_refine",
        "Generator + generic execution-guided refine",
        "repair",
        "main_comparison/generic_execution_guided_refine/generic_execution_guided_refine.jsonl",
    ),
    ("finverisql_full", "Generator + FinVeriSQL full", "repair", "internal_ablation/full/full_repairs.jsonl"),
]
REPAIR_STRATEGIES = ["prompt_llama31_8b", "sft_llama31_8b", "rl_llama31_8b"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, help="Completed labeled-run root.")
    parser.add_argument(
        "--official-test-run-root",
        required=True,
        help="Run root containing official_test/submission.csv.",
    )
    parser.add_argument("--development-ids", default=DEFAULT_DEVELOPMENT_IDS)
    parser.add_argument("--db-path", default=PROJECT_ROOT / "data/booksql/accounting.sqlite")
    parser.add_argument("--schema-path", default=PROJECT_ROOT / "data/booksql/schema_annotations.json")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Defaults to <run-root>/development_excluded_publication_report.",
    )
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not row.get("question_id"):
                raise ValueError(f"{path}:{line_number} must be an object with question_id")
            rows.append(row)
    ids = [str(row["question_id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path} contains duplicate question_id values")
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def ids(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row["question_id"]) for row in rows]


def load_development_ids(path: Path) -> set[str]:
    rows = read_jsonl(path)
    development_ids = set(ids(rows))
    if len(development_ids) != 2000:
        raise ValueError(f"Expected exactly 2,000 frozen development IDs in {path}")
    return development_ids


def require_validation_population(rows: list[dict[str, Any]], source: Path) -> list[str]:
    population = ids(rows)
    if not population:
        raise ValueError(f"{source} contains no rows")
    splits = {row.get("split") for row in rows}
    if splits != {"validation"}:
        raise ValueError(f"{source} must contain only validation rows; found {sorted(splits)!r}")
    return population


def filter_source(
    source: Path,
    destination: Path,
    population_ids: list[str],
    development_ids: set[str],
) -> dict[str, Any]:
    rows = read_jsonl(source)
    source_ids = ids(rows)
    if source_ids != population_ids:
        raise ValueError(
            f"{source} does not have the same ordered question IDs as the baseline population"
        )
    filtered = [row for row in rows if str(row["question_id"]) not in development_ids]
    if not filtered:
        raise ValueError(f"Filtering {source} removed every row")
    write_jsonl(destination, filtered)
    return {
        "source": str(source),
        "source_sha256": sha256(source),
        "filtered": str(destination),
        "filtered_sha256": sha256(destination),
        "rows_before": len(rows),
        "rows_after": len(filtered),
    }


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def evaluate_baseline(source: Path, destination: Path, db_path: Path, workers: int) -> Path:
    evaluated = destination / "evaluated.jsonl"
    metrics = destination / "metrics.json"
    run([
        sys.executable, "-m", "src.eval.evaluate_baseline_sql", "--input-jsonl", str(source),
        "--output-jsonl", str(evaluated), "--metrics-json", str(metrics), "--db-path", str(db_path),
        "--workers", str(workers),
    ])
    return evaluated


def evaluate_asa(before: Path, after: Path | None, destination: Path, schema_path: Path) -> Path:
    metrics = destination / "asa_metrics.json"
    command = [
        sys.executable, "-m", "src.eval.evaluate_asa", "--before-jsonl", str(before),
        "--schema-path", str(schema_path), "--output-json", str(metrics),
        "--output-md", str(destination / "asa_metrics.md"),
        "--row-output-jsonl", str(destination / "asa_rows.jsonl"),
    ]
    if after is not None:
        command[5:5] = ["--after-jsonl", str(after)]
    run(command)
    return metrics


def evaluate_repair(source: Path, destination: Path, before: Path, db_path: Path, schema_path: Path, workers: int) -> tuple[Path, Path]:
    evaluated = destination / "final_evaluated.jsonl"
    metrics = destination / "final_metrics.json"
    run([
        sys.executable, "-m", "src.eval.evaluate_final_sql", "--input-jsonl", str(source),
        "--output-jsonl", str(evaluated), "--metrics-json", str(metrics),
        "--metrics-md", str(destination / "final_metrics.md"),
        "--adapted-jsonl", str(destination / "adapted_final_input.jsonl"),
        "--db-path", str(db_path), "--workers", str(workers),
    ])
    asa = evaluate_asa(before, evaluated, destination, schema_path)
    return metrics, asa


def write_manifest(path: Path, main_systems: list[dict[str, Any]], ablations: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps({"main_systems": main_systems, "ablations": ablations}, indent=2) + "\n",
        encoding="utf-8",
    )


def add_protocol_note(path: Path, title: str, retained: int, excluded: int) -> None:
    original = path.read_text(encoding="utf-8")
    note = (
        f"# {title}\n\n"
        f"Local validation metrics are computed on the frozen development-excluded complement "
        f"({retained} retained validation examples; {excluded} development examples excluded).\n\n"
    )
    path.write_text(note + original, encoding="utf-8")


def write_official_table(official_root: Path, output: Path) -> None:
    submission = official_root / "official_test/submission.csv"
    upstream_table = official_root / "official_test/official_test_table.md"
    if not submission.is_file():
        raise FileNotFoundError(f"Missing official BookSQL submission: {submission}")
    if not upstream_table.is_file():
        raise FileNotFoundError(f"Missing official-test placeholder table: {upstream_table}")
    output.write_text(
        "# Official BookSQL Test Set\n\n"
        "Gold SQL and local execution results are unavailable for the official test split. "
        "Submit the validated CSV to the official BookSQL leaderboard to obtain Test EX.\n\n"
        "| System | Test EX |\n"
        "| --- | ---: |\n"
        "| FinVeriSQL | Pending official BookSQL leaderboard submission |\n",
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    run_root = Path(args.run_root).resolve()
    debug = run_root / "debug"
    output = Path(args.output_dir).resolve() if args.output_dir else run_root / "development_excluded_publication_report"
    filtered_dir = output / "filtered_inputs"
    metrics_dir = output / "metrics"
    publication_dir = output / "publication_tables"
    table_debug = output / "table_debug"
    for directory in (filtered_dir, metrics_dir, publication_dir, table_debug):
        directory.mkdir(parents=True, exist_ok=True)

    development_path = Path(args.development_ids).resolve()
    development_ids = load_development_ids(development_path)
    baseline_source = debug / "baseline/qwen_few_shot_validation.jsonl"
    baseline_rows = read_jsonl(baseline_source)
    population_ids = require_validation_population(baseline_rows, baseline_source)
    population_set = set(population_ids)
    if not development_ids <= population_set:
        raise ValueError("Frozen development IDs are not all present in the labeled validation population")
    retained = len(population_ids) - len(development_ids)
    if retained <= 0:
        raise ValueError("The development exclusion leaves no validation examples")

    provenance: dict[str, Any] = {
        "protocol": "validation complement excluding frozen 2,000-example development sample",
        "development_ids_path": str(development_path),
        "development_ids_sha256": sha256(development_path),
        "development_ids_count": len(development_ids),
        "validation_population_count": len(population_ids),
        "reportable_validation_count": retained,
        "source_artifacts": {},
    }

    baseline_filtered = filtered_dir / "baseline.jsonl"
    provenance["source_artifacts"]["baseline"] = filter_source(
        baseline_source, baseline_filtered, population_ids, development_ids
    )
    baseline_dir = metrics_dir / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    baseline_evaluated = evaluate_baseline(baseline_filtered, baseline_dir, Path(args.db_path), args.workers)
    baseline_asa = evaluate_asa(baseline_evaluated, None, baseline_dir, Path(args.schema_path))

    main_manifest: list[dict[str, Any]] = [{
        "key": "generator_only", "label": "Generator only", "kind": "generator",
        "metrics_json": str(baseline_dir / "metrics.json"), "asa_metrics_json": str(baseline_asa),
    }]
    for key, label, kind, relative_source in MAIN_SYSTEMS[1:]:
        source = debug / relative_source
        filtered = filtered_dir / f"{key}.jsonl"
        provenance["source_artifacts"][key] = filter_source(source, filtered, population_ids, development_ids)
        destination = metrics_dir / "main_comparison" / key
        destination.mkdir(parents=True, exist_ok=True)
        metrics, asa = evaluate_repair(filtered, destination, baseline_evaluated, Path(args.db_path), Path(args.schema_path), args.workers)
        main_manifest.append({"key": key, "label": label, "kind": kind, "metrics_json": str(metrics), "asa_metrics_json": str(asa)})

    ablation_manifest: list[dict[str, Any]] = []
    for key, label in VARIANTS:
        source_dir = debug / "internal_ablation" / key
        repair_source = source_dir / f"{key}_repairs.jsonl"
        verify_source = source_dir / f"{key}_verify.jsonl"
        filtered_repair = filtered_dir / "internal_ablation" / f"{key}_repairs.jsonl"
        filtered_verify = filtered_dir / "internal_ablation" / f"{key}_verify.jsonl"
        provenance["source_artifacts"][f"ablation_{key}_repair"] = filter_source(repair_source, filtered_repair, population_ids, development_ids)
        provenance["source_artifacts"][f"ablation_{key}_verify"] = filter_source(verify_source, filtered_verify, population_ids, development_ids)
        destination = metrics_dir / "internal_ablation" / key
        destination.mkdir(parents=True, exist_ok=True)
        metrics, asa = evaluate_repair(filtered_repair, destination, baseline_evaluated, Path(args.db_path), Path(args.schema_path), args.workers)
        ablation_manifest.append({"key": key, "label": label, "verify_jsonl": str(filtered_verify), "metrics_json": str(metrics), "asa_metrics_json": str(asa)})

    strategy_source_dir = debug / "repair_strategy_ablation/full_fixed_verifier"
    strategy_output_dir = metrics_dir / "repair_strategy_ablation"
    strategy_output_dir.mkdir(parents=True, exist_ok=True)
    for strategy in REPAIR_STRATEGIES:
        source = strategy_source_dir / f"{strategy}_repairs.jsonl"
        filtered = filtered_dir / "repair_strategy_ablation" / f"{strategy}_repairs.jsonl"
        provenance["source_artifacts"][f"repair_strategy_{strategy}"] = filter_source(source, filtered, population_ids, development_ids)
        metrics, asa = evaluate_repair(filtered, strategy_output_dir / strategy, baseline_evaluated, Path(args.db_path), Path(args.schema_path), args.workers)
        # The table builder consumes a flat strategy directory; retain its expected names.
        for produced, target in ((metrics, strategy_output_dir / f"{strategy}_final_metrics.json"), (asa, strategy_output_dir / f"{strategy}_asa_metrics.json")):
            target.write_bytes(produced.read_bytes())

    manifest = output / "run_manifest.json"
    write_manifest(manifest, main_manifest, ablation_manifest)
    run([
        sys.executable, "scripts/build_publication_tables.py", "--manifest", str(manifest),
        "--publication-dir", str(publication_dir), "--debug-dir", str(table_debug),
    ])
    run([
        sys.executable, "scripts/dev/build_repair_strategy_ablation_table.py",
        "--ablation-dir", str(strategy_output_dir), "--output-md", str(publication_dir / "repair_strategy_ablation_table.md"),
        "--output-json", str(table_debug / "repair_strategy_ablation_table.json"),
    ])
    for filename, title in (
        ("main_comparison_table.md", "Main Comparison"),
        ("internal_ablation_table.md", "Internal Ablation"),
        ("repair_strategy_ablation_table.md", "Repair Strategy Ablation"),
    ):
        add_protocol_note(publication_dir / filename, title, retained, len(development_ids))
    write_official_table(Path(args.official_test_run_root).resolve(), publication_dir / "official_booksql_test_table.md")
    provenance["official_test_run_root"] = str(Path(args.official_test_run_root).resolve())
    provenance["official_submission_csv"] = str(Path(args.official_test_run_root).resolve() / "official_test/submission.csv")
    provenance["official_submission_sha256"] = sha256(Path(provenance["official_submission_csv"]))
    try:
        provenance["git_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
    except subprocess.CalledProcessError:
        provenance["git_commit"] = None
    (output / "report_provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote development-excluded report to {output}")


if __name__ == "__main__":
    main()
