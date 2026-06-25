"""Evaluate final SQL after applying FinVeriSQL repairs.

This script adapts repair-generation rows into the standard BookSQL evaluation
shape, then reuses ``src.eval.evaluate_baseline_sql`` so reportable metrics
match the baseline evaluator.

For each input row:
    final generated_sql = repaired_sql if present, else original_generated_sql

Example:
    python -m src.eval.evaluate_final_sql \
      --input-jsonl data/outputs/finverisql/dev_diagnostics/exp05_sample_2000/repairs_identified__seed42_smoke.jsonl \
      --output-jsonl data/outputs/finverisql/dev_diagnostics/exp05_sample_2000/repairs_final_sql_evaluated.jsonl \
      --metrics-json data/outputs/finverisql/dev_diagnostics/exp05_sample_2000/repairs_final_sql_metrics.json \
      --metrics-md data/outputs/finverisql/dev_diagnostics/exp05_sample_2000/repairs_final_sql_metrics.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = Path(__file__).resolve().parents[1]

for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

try:
    from src.eval.evaluate_baseline_sql import (
        GROUP_A,
        GROUP_B,
        GROUP_C,
        GROUP_D,
        apply_subset,
        build_metrics,
        evaluate_row_worker,
        read_jsonl,
        remove_internal_fields,
        str_to_bool,
        write_json,
        write_jsonl,
    )
    from src.utils.data_utils import get_booksql_db_path
except ModuleNotFoundError:
    from eval.evaluate_baseline_sql import (
        GROUP_A,
        GROUP_B,
        GROUP_C,
        GROUP_D,
        apply_subset,
        build_metrics,
        evaluate_row_worker,
        read_jsonl,
        remove_internal_fields,
        str_to_bool,
        write_json,
        write_jsonl,
    )
    from utils.data_utils import get_booksql_db_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate final reportable BookSQL metrics after applying "
            "FinVeriSQL repaired SQL where available."
        ),
    )
    parser.add_argument("--input-jsonl", required=True, help="Repair output JSONL.")
    parser.add_argument(
        "--output-jsonl",
        required=True,
        help="Output row-level evaluated final-SQL JSONL.",
    )
    parser.add_argument("--metrics-json", required=True, help="Output metrics JSON.")
    parser.add_argument(
        "--metrics-md",
        default=None,
        help="Optional Markdown metrics report.",
    )
    parser.add_argument(
        "--adapted-jsonl",
        default=None,
        help=(
            "Optional path to write the adapted final-SQL input rows before "
            "evaluation."
        ),
    )
    parser.add_argument(
        "--original-sql-key",
        default="original_generated_sql",
        help="Input key containing the original generated SQL.",
    )
    parser.add_argument(
        "--repaired-sql-key",
        default="repaired_sql",
        help="Input key containing the repaired SQL.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional explicit BookSQL SQLite path.",
    )
    parser.add_argument(
        "--max-result-preview-rows",
        type=int,
        default=100,
        help="Maximum result rows to store per query in evaluated JSONL.",
    )
    parser.add_argument(
        "--treat-empty-results-as-ambiguous",
        action="store_true",
        help="Route empty/null result patterns to Group D, matching evaluate_baseline_sql.py.",
    )
    parser.add_argument(
        "--evaluate-subset",
        type=str_to_bool,
        default=False,
        help="Whether to evaluate only a subset of rows. Use true or false.",
    )
    parser.add_argument(
        "--subset-size",
        type=int,
        default=50,
        help="Number of rows to evaluate when --evaluate-subset true.",
    )
    parser.add_argument(
        "--subset-start",
        type=int,
        default=0,
        help="Starting row index for subset evaluation.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of worker threads.",
    )
    parser.add_argument(
        "--max-progress-steps",
        type=int,
        default=2_000_000,
        help="Maximum SQLite progress-handler steps before aborting a query.",
    )
    parser.add_argument(
        "--progress-check-interval",
        type=int,
        default=1000,
        help="SQLite VM instruction interval for progress-handler checks.",
    )
    parser.add_argument(
        "--log-every",
        type=int,
        default=50,
        help="Print progress every N completed rows.",
    )
    parser.add_argument(
        "--slow-query-threshold",
        type=float,
        default=5.0,
        help="Print progress immediately when one row takes at least this many seconds.",
    )
    return parser.parse_args()


def text_or_none(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value)
    return text if text.strip() else None


def safe_rate(numerator: int | float, denominator: int | float) -> float:
    return numerator / denominator if denominator else 0.0


def format_count_rate(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator} ({safe_rate(numerator, denominator):.4f})"


def format_rate(value: int | float) -> str:
    return f"{value:.4f}"


def get_original_verification(row: dict[str, Any]) -> dict[str, Any]:
    verification = row.get("original_verification") or row.get("verification")
    return verification if isinstance(verification, dict) else {}


def get_probes_used(row: dict[str, Any]) -> int | float:
    probes_used = get_original_verification(row).get("probes_used")
    return probes_used if isinstance(probes_used, (int, float)) else 0


def was_verifier_rejected(row: dict[str, Any]) -> bool:
    verification = get_original_verification(row)
    return verification.get("answers_question") is False


def was_verifier_abstained(row: dict[str, Any]) -> bool:
    verification = get_original_verification(row)
    return (
        verification.get("answers_question") is None
        or verification.get("should_abstain") is True
    )


def was_verifier_ambiguous(row: dict[str, Any]) -> bool:
    return get_original_verification(row).get("ambiguous") is True


def was_high_confidence(row: dict[str, Any]) -> bool:
    confidence = get_original_verification(row).get("confidence")
    return str(confidence).lower() == "high" if confidence is not None else False


def adapt_repair_rows(
    rows: list[dict[str, Any]],
    original_sql_key: str,
    repaired_sql_key: str,
) -> list[dict[str, Any]]:
    adapted_rows: list[dict[str, Any]] = []

    for row in rows:
        repaired_sql = text_or_none(row.get(repaired_sql_key))
        original_sql = text_or_none(
            row.get(original_sql_key)
            or row.get("generated_sql")
            or row.get("pred_sql")
        )
        final_sql = repaired_sql or original_sql
        final_sql_source = "repaired_sql" if repaired_sql else "original_generated_sql"

        adapted = {
            **row,
            "generated_sql": final_sql,
            "final_sql_source": final_sql_source if final_sql else "missing",
            "final_sql_repaired": repaired_sql is not None,
            "original_generated_sql_for_repair_eval": original_sql,
        }

        if not final_sql:
            adapted["status"] = "failed"
            adapted["error"] = "missing final SQL"
        elif not adapted.get("status"):
            adapted["status"] = "success"
            adapted["error"] = None

        adapted_rows.append(adapted)

    return adapted_rows


def build_repair_summary(
    source_rows: list[dict[str, Any]],
    adapted_rows: list[dict[str, Any]],
    evaluated_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    total_rows = len(adapted_rows)
    source_counts = Counter(row.get("final_sql_source") for row in adapted_rows)
    original_group_counts = Counter(row.get("evaluation_group") for row in source_rows)
    final_group_counts = Counter(row.get("evaluation_group") for row in evaluated_rows)
    repair_status_counts = Counter(row.get("status") for row in source_rows)
    repair_result_counts = Counter(row.get("repair_status") for row in source_rows)
    repair_mode_counts = Counter(row.get("repair_mode") for row in source_rows)

    originally_correct = 0
    originally_wrong_or_nonexec = 0
    preserved_correct = 0
    original_correct_repaired = 0
    corrupted_correct = 0
    repaired_final_correct = 0
    attempted_repairs = 0
    repair_error_rows = 0
    generated_repairs = 0
    executable_repairs = 0
    applied_wrong_rows = 0
    applied_wrong_to_correct = 0
    wrong_to_correct = 0
    wrong_to_wrong_or_nonexec = 0
    nonexec_to_correct = 0
    verifier_rejected_rows = 0
    probed_rows = 0
    probed_rejected_rows = 0
    non_probed_rows = 0
    non_probed_rejected_rows = 0
    ambiguous_rows = 0
    abstention_rows = 0
    high_confidence_rows = 0
    high_confidence_rejected_rows = 0
    total_probes = 0
    rejected_probes = 0

    for source_row, adapted_row, evaluated_row in zip(
        source_rows,
        adapted_rows,
        evaluated_rows,
        strict=True,
    ):
        was_originally_correct = source_row.get("evaluation_group") == GROUP_A
        final_correct = evaluated_row.get("evaluation_group") == GROUP_A
        original_group = source_row.get("evaluation_group")
        final_group = evaluated_row.get("evaluation_group")
        used_repair = adapted_row.get("final_sql_repaired") is True
        attempted_repair = source_row.get("status") != "skipped"
        probes_used = get_probes_used(source_row)
        verifier_rejected = was_verifier_rejected(source_row)

        total_probes += probes_used

        if verifier_rejected:
            verifier_rejected_rows += 1
            rejected_probes += probes_used

        if probes_used > 0:
            probed_rows += 1
            if verifier_rejected:
                probed_rejected_rows += 1
        else:
            non_probed_rows += 1
            if verifier_rejected:
                non_probed_rejected_rows += 1

        if was_verifier_ambiguous(source_row):
            ambiguous_rows += 1

        if was_verifier_abstained(source_row):
            abstention_rows += 1

        if was_high_confidence(source_row):
            high_confidence_rows += 1
            if verifier_rejected:
                high_confidence_rejected_rows += 1

        if attempted_repair:
            attempted_repairs += 1

        if source_row.get("repair_status") not in {None, "skipped", "success"}:
            repair_error_rows += 1

        if was_originally_correct:
            originally_correct += 1
        elif original_group in {GROUP_B, GROUP_C}:
            originally_wrong_or_nonexec += 1

        if was_originally_correct and final_correct:
            preserved_correct += 1

        if was_originally_correct and used_repair:
            original_correct_repaired += 1

        if was_originally_correct and used_repair and not final_correct:
            corrupted_correct += 1

        if used_repair and final_correct:
            repaired_final_correct += 1

        if used_repair:
            generated_repairs += 1
            if final_group in {GROUP_A, GROUP_B}:
                executable_repairs += 1

        if original_group in {GROUP_B, GROUP_C} and final_group == GROUP_A:
            wrong_to_correct += 1
            if original_group == GROUP_C:
                nonexec_to_correct += 1

        if original_group in {GROUP_B, GROUP_C} and final_group in {GROUP_B, GROUP_C}:
            wrong_to_wrong_or_nonexec += 1

        if used_repair and original_group in {GROUP_B, GROUP_C}:
            applied_wrong_rows += 1
            if final_group == GROUP_A:
                applied_wrong_to_correct += 1

    repaired_rows = source_counts.get("repaired_sql", 0)
    original_metric_total = (
        original_group_counts.get(GROUP_A, 0)
        + original_group_counts.get(GROUP_B, 0)
        + original_group_counts.get(GROUP_C, 0)
    )
    final_metric_total = (
        final_group_counts.get(GROUP_A, 0)
        + final_group_counts.get(GROUP_B, 0)
        + final_group_counts.get(GROUP_C, 0)
    )
    original_execution_accuracy = safe_rate(
        original_group_counts.get(GROUP_A, 0),
        original_metric_total,
    )
    final_execution_accuracy = safe_rate(
        final_group_counts.get(GROUP_A, 0),
        final_metric_total,
    )

    return {
        "final_sql_source_counts": dict(source_counts),
        "original_group_counts": dict(original_group_counts),
        "final_group_counts": dict(final_group_counts),
        "repair_status_counts": dict(repair_status_counts),
        "repair_result_counts": dict(repair_result_counts),
        "repair_mode_counts": dict(repair_mode_counts),
        "baseline_comparison": {
            "original_metric_total_examples": original_metric_total,
            "final_metric_total_examples": final_metric_total,
            "original_execution_accuracy": original_execution_accuracy,
            "final_execution_accuracy": final_execution_accuracy,
            "delta_execution_accuracy": (
                final_execution_accuracy - original_execution_accuracy
            ),
            "original_execution_correct": original_group_counts.get(GROUP_A, 0),
            "final_execution_correct": final_group_counts.get(GROUP_A, 0),
            "net_correct_gain": (
                final_group_counts.get(GROUP_A, 0)
                - original_group_counts.get(GROUP_A, 0)
            ),
        },
        "repair_coverage": {
            "verifier_rejected_rows": verifier_rejected_rows,
            "rejection_rate": safe_rate(verifier_rejected_rows, total_rows),
            "attempted_repairs": attempted_repairs,
            "repair_attempt_rate": safe_rate(attempted_repairs, total_rows),
            "generated_repairs": generated_repairs,
            "repair_generation_rate": safe_rate(generated_repairs, attempted_repairs),
            "applied_repairs": repaired_rows,
            "repair_application_rate": safe_rate(repaired_rows, total_rows),
            "skipped_repairs": repair_status_counts.get("skipped", 0),
            "repair_skip_rate": safe_rate(repair_status_counts.get("skipped", 0), total_rows),
            "repair_error_rows": repair_error_rows,
            "repair_failure_rate": safe_rate(repair_error_rows, attempted_repairs),
            "fallback_original_sql_rows": source_counts.get("original_generated_sql", 0),
            "missing_final_sql_rows": source_counts.get("missing", 0),
        },
        "repair_effectiveness": {
            "originally_wrong_or_nonexec_rows": originally_wrong_or_nonexec,
            "wrong_to_correct_rows": wrong_to_correct,
            "wrong_to_correct_rate": safe_rate(
                wrong_to_correct,
                originally_wrong_or_nonexec,
            ),
            "applied_wrong_rows": applied_wrong_rows,
            "applied_wrong_to_correct_rows": applied_wrong_to_correct,
            "applied_wrong_to_correct_rate": safe_rate(
                applied_wrong_to_correct,
                applied_wrong_rows,
            ),
            "repair_success_rate": safe_rate(wrong_to_correct, attempted_repairs),
            "repair_precision": safe_rate(applied_wrong_to_correct, repaired_rows),
            "wrong_to_wrong_or_nonexec_rows": wrong_to_wrong_or_nonexec,
            "wrong_to_wrong_or_nonexec_rate": safe_rate(
                wrong_to_wrong_or_nonexec,
                originally_wrong_or_nonexec,
            ),
            "nonexec_to_correct_rows": nonexec_to_correct,
            "nonexec_to_correct_rate": safe_rate(
                nonexec_to_correct,
                original_group_counts.get(GROUP_C, 0),
            ),
            "executable_repair_rate": safe_rate(executable_repairs, generated_repairs),
            "executable_repairs": executable_repairs,
            "repaired_rows_final_correct": repaired_final_correct,
            "repaired_rows_final_correct_rate": safe_rate(
                repaired_final_correct,
                repaired_rows,
            ),
        },
        "repair_safety": {
            "originally_correct_rows": originally_correct,
            "original_correct_repaired_rows": original_correct_repaired,
            "preserved_originally_correct_rows": preserved_correct,
            "corrupted_originally_correct_rows": corrupted_correct,
            "corruption_rate": safe_rate(corrupted_correct, original_correct_repaired),
            "overall_corruption_rate": safe_rate(corrupted_correct, originally_correct),
            "preservation_rate": safe_rate(preserved_correct, originally_correct),
            "net_gain_after_corruption": wrong_to_correct - corrupted_correct,
        },
        "probe_summary": {
            "probed_rows": probed_rows,
            "probe_rate": safe_rate(probed_rows, total_rows),
            "total_probes": total_probes,
            "avg_probes_per_query": safe_rate(total_probes, total_rows),
            "avg_probes_per_rejected_query": safe_rate(
                rejected_probes,
                verifier_rejected_rows,
            ),
            "probed_rejected_rows": probed_rejected_rows,
            "probe_rejection_rate": safe_rate(probed_rejected_rows, probed_rows),
            "non_probed_rows": non_probed_rows,
            "non_probe_rejection_rate": safe_rate(
                non_probed_rejected_rows,
                non_probed_rows,
            ),
            "ambiguous_rows": ambiguous_rows,
            "ambiguous_rate": safe_rate(ambiguous_rows, total_rows),
            "abstention_rows": abstention_rows,
            "abstention_rate": safe_rate(abstention_rows, total_rows),
            "high_confidence_rows": high_confidence_rows,
            "high_confidence_rejected_rows": high_confidence_rejected_rows,
            "high_confidence_rejection_rate": safe_rate(
                high_confidence_rejected_rows,
                high_confidence_rows,
            ),
        },
        "headline_metrics": {
            "original_execution_accuracy": original_execution_accuracy,
            "final_execution_accuracy": final_execution_accuracy,
            "delta_execution_accuracy": (
                final_execution_accuracy - original_execution_accuracy
            ),
            "net_correct_gain": (
                final_group_counts.get(GROUP_A, 0)
                - original_group_counts.get(GROUP_A, 0)
            ),
            "repair_attempt_rate": safe_rate(attempted_repairs, total_rows),
            "repair_generation_rate": safe_rate(generated_repairs, attempted_repairs),
            "repair_application_rate": safe_rate(repaired_rows, total_rows),
            "targeted_repair_success_rate": safe_rate(
                applied_wrong_to_correct,
                applied_wrong_rows,
            ),
            "end_to_end_repair_precision": safe_rate(
                applied_wrong_to_correct,
                repaired_rows,
            ),
            "corruption_rate": safe_rate(corrupted_correct, original_correct_repaired),
            "overall_corruption_rate": safe_rate(corrupted_correct, originally_correct),
        },
        "repaired_sql_rows": repaired_rows,
        "fallback_original_sql_rows": source_counts.get("original_generated_sql", 0),
        "missing_final_sql_rows": source_counts.get("missing", 0),
        "originally_correct_rows": originally_correct,
        "preserved_originally_correct_rows": preserved_correct,
        "corrupted_originally_correct_rows": corrupted_correct,
        "repaired_rows_final_correct": repaired_final_correct,
        "repair_application_rate": safe_rate(repaired_rows, total_rows),
        "corruption_rate": safe_rate(corrupted_correct, original_correct_repaired),
        "repaired_rows_final_correct_rate": safe_rate(
            repaired_final_correct,
            repaired_rows,
        ),
    }


def write_metrics_markdown(path: Path, metrics: dict[str, Any]) -> None:
    repair_summary = metrics["repair_summary"]
    headline = repair_summary["headline_metrics"]
    baseline = repair_summary["baseline_comparison"]
    coverage = repair_summary["repair_coverage"]
    effectiveness = repair_summary["repair_effectiveness"]
    safety = repair_summary["repair_safety"]
    original_groups = repair_summary["original_group_counts"]
    final_groups = repair_summary["final_group_counts"]
    original_total = baseline["original_metric_total_examples"]
    final_total = baseline["final_metric_total_examples"]

    group_labels = [
        (GROUP_A, "A: correct executable"),
        (GROUP_B, "B: wrong executable"),
        (GROUP_C, "C: non-executable"),
        (GROUP_D, "D: ambiguous/excluded"),
    ]

    lines = [
        "# Final Repair Evaluation",
        "",
        "## Headline Results",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Total examples | {metrics['total_examples']} |",
        f"| Original execution accuracy | {format_rate(headline['original_execution_accuracy'])} |",
        f"| Final execution accuracy | {format_rate(headline['final_execution_accuracy'])} |",
        f"| Execution accuracy delta | {format_rate(headline['delta_execution_accuracy'])} |",
        f"| Net correct gain | {headline['net_correct_gain']} |",
        f"| Corruption rate | {format_count_rate(safety['corrupted_originally_correct_rows'], safety['original_correct_repaired_rows'])} |",
        f"| Targeted repair success | {format_count_rate(effectiveness['applied_wrong_to_correct_rows'], effectiveness['applied_wrong_rows'])} |",
        f"| End-to-end repair precision | {format_count_rate(effectiveness['applied_wrong_to_correct_rows'], coverage['applied_repairs'])} |",
        "",
        "## Original vs Final Groups",
        "",
        "| Group | Original | Final After Repair | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]

    for group_key, label in group_labels:
        original_count = original_groups.get(group_key, 0)
        final_count = final_groups.get(group_key, 0)
        original_denominator = (
            metrics["total_examples"] if group_key == GROUP_D else original_total
        )
        final_denominator = (
            metrics["total_examples"] if group_key == GROUP_D else final_total
        )
        lines.append(
            "| {label} | {original} | {final} | {delta:+d} |".format(
                label=label,
                original=format_count_rate(original_count, original_denominator),
                final=format_count_rate(final_count, final_denominator),
                delta=final_count - original_count,
            )
        )

    lines.extend(
        [
        "",
        "## Repair Funnel",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Verifier rejected rows | {format_count_rate(coverage['verifier_rejected_rows'], metrics['total_examples'])} |",
        f"| Attempted repairs | {format_count_rate(coverage['attempted_repairs'], metrics['total_examples'])} |",
        f"| Generated repairs | {format_count_rate(coverage['generated_repairs'], coverage['attempted_repairs'])} |",
        f"| Applied repairs | {format_count_rate(coverage['applied_repairs'], metrics['total_examples'])} |",
        f"| Fallback to original SQL | {format_count_rate(coverage['fallback_original_sql_rows'], metrics['total_examples'])} |",
        "",
        "## Repair Effectiveness",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| True repair targets touched | {format_count_rate(effectiveness['applied_wrong_rows'], coverage['applied_repairs'])} |",
        f"| True targets fixed | {format_count_rate(effectiveness['applied_wrong_to_correct_rows'], effectiveness['applied_wrong_rows'])} |",
        f"| All original wrong/non-exec fixed | {format_count_rate(effectiveness['wrong_to_correct_rows'], effectiveness['originally_wrong_or_nonexec_rows'])} |",
        f"| End-to-end precision across all applied repairs | {format_count_rate(effectiveness['applied_wrong_to_correct_rows'], coverage['applied_repairs'])} |",
        f"| Repaired SQL executable | {format_count_rate(effectiveness['executable_repairs'], coverage['generated_repairs'])} |",
        "",
        "## Repair Safety",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Originally correct rows touched by repair | {format_count_rate(safety['original_correct_repaired_rows'], safety['originally_correct_rows'])} |",
        f"| Preserved originally correct rows | {format_count_rate(safety['preserved_originally_correct_rows'], safety['originally_correct_rows'])} |",
        f"| Corrupted originally correct touched rows | {format_count_rate(safety['corrupted_originally_correct_rows'], safety['original_correct_repaired_rows'])} |",
        f"| Overall corruption among originally correct | {format_count_rate(safety['corrupted_originally_correct_rows'], safety['originally_correct_rows'])} |",
        f"| Net gain after corruption | {safety['net_gain_after_corruption']} |",
        "",
        "## Readout",
        "",
        f"- Repairs changed correctness by {format_rate(headline['delta_execution_accuracy'])} execution-accuracy points.",
        f"- The repairer fixed {effectiveness['applied_wrong_to_correct_rows']} true wrong/non-executable targets.",
        f"- The pipeline corrupted {safety['corrupted_originally_correct_rows']} originally correct rows that were touched by repair.",
        f"- Low end-to-end repair precision can come from either weak repair quality or verifier over-routing correct SQL into repair.",
        "",
        ]
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()

    input_jsonl = Path(args.input_jsonl)
    output_jsonl = Path(args.output_jsonl)
    metrics_json = Path(args.metrics_json)
    metrics_md = Path(args.metrics_md) if args.metrics_md else None

    db_path = str(get_booksql_db_path(args.db_path))
    source_rows = read_jsonl(input_jsonl)
    adapted_rows = adapt_repair_rows(
        rows=source_rows,
        original_sql_key=args.original_sql_key,
        repaired_sql_key=args.repaired_sql_key,
    )

    if args.adapted_jsonl:
        write_jsonl(Path(args.adapted_jsonl), adapted_rows)

    rows, original_num_rows = apply_subset(
        rows=adapted_rows,
        evaluate_subset=args.evaluate_subset,
        subset_start=args.subset_start,
        subset_size=args.subset_size,
    )

    print(f"Using workers: {args.workers}")
    print(f"DB path: {db_path}")

    start_time = time.perf_counter()
    evaluated_rows: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(
                evaluate_row_worker,
                row_index,
                row,
                db_path,
                args.max_result_preview_rows,
                args.treat_empty_results_as_ambiguous,
                args.max_progress_steps,
                args.progress_check_interval,
            )
            for row_index, row in enumerate(rows)
        ]

        for completed_count, future in enumerate(as_completed(futures), start=1):
            evaluated_row = future.result()
            source_row = rows[evaluated_row["_row_index"]]
            evaluated_row["final_sql_source"] = source_row.get("final_sql_source")
            evaluated_row["final_sql_repaired"] = source_row.get("final_sql_repaired")
            evaluated_row["repair_status"] = source_row.get("repair_status")
            evaluated_row["repair_mode"] = source_row.get("repair_mode")
            evaluated_rows.append(evaluated_row)

            row_time = evaluated_row.get("evaluation_time_seconds", 0)
            if (
                completed_count % args.log_every == 0
                or row_time >= args.slow_query_threshold
            ):
                print(
                    f"[{completed_count}/{len(rows)}] "
                    f"qid={evaluated_row.get('question_id')} "
                    f"time={row_time:.2f}s "
                    f"group={evaluated_row.get('evaluation_group')} "
                    f"gen_status={evaluated_row.get('generated_execution_status')}",
                    flush=True,
                )

    evaluated_rows.sort(key=lambda row: row["_row_index"])
    total_time = time.perf_counter() - start_time

    metrics = build_metrics(evaluated_rows)
    metrics["repair_summary"] = build_repair_summary(
        source_rows=rows,
        adapted_rows=rows,
        evaluated_rows=evaluated_rows,
    )
    metrics["total_evaluation_time_seconds"] = round(total_time, 4)
    metrics["workers"] = args.workers
    metrics["max_progress_steps"] = args.max_progress_steps
    metrics["progress_check_interval"] = args.progress_check_interval
    metrics["evaluate_subset"] = args.evaluate_subset
    metrics["subset_size"] = args.subset_size if args.evaluate_subset else None
    metrics["subset_start"] = args.subset_start if args.evaluate_subset else None
    metrics["original_num_rows"] = original_num_rows

    output_rows = [remove_internal_fields(row) for row in evaluated_rows]
    write_jsonl(output_jsonl, output_rows)
    write_json(metrics_json, metrics)

    if metrics_md:
        write_metrics_markdown(metrics_md, metrics)

    repair_summary = metrics["repair_summary"]
    headline = repair_summary["headline_metrics"]
    coverage = repair_summary["repair_coverage"]
    effectiveness = repair_summary["repair_effectiveness"]
    safety = repair_summary["repair_safety"]
    original_groups = repair_summary["original_group_counts"]
    final_groups = repair_summary["final_group_counts"]

    print(f"Evaluated rows: {metrics['total_examples']}")
    print("Original groups:")
    print(f"  A correct executable: {original_groups.get(GROUP_A, 0)}")
    print(f"  B wrong executable: {original_groups.get(GROUP_B, 0)}")
    print(f"  C non-executable: {original_groups.get(GROUP_C, 0)}")
    print(f"  D ambiguous/excluded: {original_groups.get(GROUP_D, 0)}")
    print("Final groups after repair:")
    print(f"  A correct executable: {final_groups.get(GROUP_A, 0)}")
    print(f"  B wrong executable: {final_groups.get(GROUP_B, 0)}")
    print(f"  C non-executable: {final_groups.get(GROUP_C, 0)}")
    print(f"  D ambiguous/excluded: {final_groups.get(GROUP_D, 0)}")
    print(f"Original execution accuracy: {headline['original_execution_accuracy']:.4f}")
    print(f"Final execution accuracy: {headline['final_execution_accuracy']:.4f}")
    print(f"Execution accuracy delta: {headline['delta_execution_accuracy']:.4f}")
    print(f"Net correct gain: {headline['net_correct_gain']}")
    print(
        "Repair effectiveness on true targets: "
        f"{format_count_rate(effectiveness['applied_wrong_to_correct_rows'], effectiveness['applied_wrong_rows'])}"
    )
    print(
        "End-to-end repair precision: "
        f"{format_count_rate(effectiveness['applied_wrong_to_correct_rows'], coverage['applied_repairs'])}"
    )
    print(
        "Corruption rate: "
        f"{format_count_rate(safety['corrupted_originally_correct_rows'], safety['original_correct_repaired_rows'])}"
    )
    print(f"Total evaluation time: {total_time:.2f}s")
    print(f"Saved row-level results to {output_jsonl}")
    print(f"Saved metrics to {metrics_json}")
    if metrics_md:
        print(f"Saved markdown metrics to {metrics_md}")


if __name__ == "__main__":
    main()
