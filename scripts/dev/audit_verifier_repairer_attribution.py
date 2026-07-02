#!/usr/bin/env python3
"""Attribute verifier and repairer outcomes from existing ASA/FCR artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_ASA_ROWS = PROJECT_ROOT / (
    "data/outputs/metrics_experiments/exp02_asa_fcr_v2/"
    "baseline_vs_repairs_final_sql_asa_rows.jsonl"
)
DEFAULT_VERIFIER_ROWS = PROJECT_ROOT / (
    "data/outputs/finverisql/dev_diagnostics/exp05_sample_2000/"
    "verifier_identified/verified_sample_seed42_nl_only_compact_probe.jsonl"
)
DEFAULT_REPAIR_ROWS = PROJECT_ROOT / (
    "data/outputs/finverisql/dev_diagnostics/exp05_sample_2000/"
    "repairs_identified__seed42_smoke.jsonl"
)
DEFAULT_FINAL_EVAL_ROWS = PROJECT_ROOT / (
    "data/outputs/finverisql/dev_diagnostics/exp05_sample_2000/"
    "repairs_final_sql_evaluated.jsonl"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / (
    "data/outputs/metrics_experiments/exp02_asa_fcr_v2/attribution_audit"
)

ROW_FIELDNAMES = [
    "question_id",
    "evaluation_group",
    "question",
    "gold_sql",
    "baseline_generated_sql",
    "repaired_generated_sql",
    "before_EX",
    "before_Inv",
    "before_asa_strict",
    "before_asa_lower_bound",
    "before_fcr_hard_finding_codes",
    "after_EX",
    "after_Inv",
    "after_asa_strict",
    "after_asa_lower_bound",
    "after_fcr_hard_finding_codes",
    "verifier_caught",
    "verifier_actionable",
    "verifier_answers_question",
    "verifier_mismatch_type",
    "verifier_mismatch_detail",
    "verifier_repair_hint",
    "verifier_confidence",
    "verifier_failed_evidence",
    "repair_attempted",
    "repair_generated",
    "repair_status",
    "repair_mode",
    "repair_error",
    "repair_edit_summary",
    "repair_confidence",
    "final_sql_repaired",
    "missed_by_verifier",
    "caught_but_not_actionable",
    "actionable_but_not_attempted",
    "attempted_but_no_sql",
    "generated_but_inv_not_fixed",
    "inv_fixed",
    "fixed_but_ex_broken",
    "fixed_and_ex_preserved",
    "harmful_gate_accept",
    "over_repair_candidate",
    "primary_bottleneck",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a read-only verifier/repairer attribution audit from existing "
            "baseline-vs-repaired ASA and FinVeriSQL artifacts."
        )
    )
    parser.add_argument("--asa-rows", default=str(DEFAULT_ASA_ROWS))
    parser.add_argument("--verifier-rows", default=str(DEFAULT_VERIFIER_ROWS))
    parser.add_argument("--repair-rows", default=str(DEFAULT_REPAIR_ROWS))
    parser.add_argument("--final-eval-rows", default=str(DEFAULT_FINAL_EVAL_ROWS))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--allow-count-mismatch",
        action="store_true",
        help="Do not fail if the baseline hard-failure cohort is not exactly 95 rows.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow overwriting the four audit output files.",
    )
    return parser.parse_args()


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_number}")
            rows.append(row)
    return rows


def dedupe_by_question_id(
    rows: list[dict[str, Any]],
    label: str,
) -> tuple[dict[str, dict[str, Any]], int]:
    deduped: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    for index, row in enumerate(rows):
        question_id = row.get("question_id")
        if question_id in (None, ""):
            raise ValueError(f"{label} row {index} is missing question_id")
        key = str(question_id)
        if key in deduped:
            duplicate_count += 1
        deduped[key] = row
    return deduped, duplicate_count


def split_asa_rows(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    before: dict[str, dict[str, Any]] = {}
    after: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        question_id = row.get("question_id")
        row_set = row.get("set")
        if question_id in (None, ""):
            raise ValueError(f"ASA row {index} is missing question_id")
        if row_set == "before":
            target = before
        elif row_set == "after":
            target = after
        else:
            raise ValueError(f"ASA row {index} has unsupported set: {row_set!r}")
        target[str(question_id)] = row
    return before, after


def non_empty(value: Any) -> bool:
    return value is not None and value != "" and value != []


def metric_rank(value: Any) -> int:
    if value is None:
        return -1
    if value is False:
        return 0
    if value is True:
        return 1
    if value == 0:
        return 0
    if value == 1:
        return 1
    return -1


def metric_worsened(before: Any, after: Any) -> bool:
    return metric_rank(after) < metric_rank(before)


def sorted_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def fcr_code_key(codes: Any) -> str:
    if not codes:
        return "[]"
    if isinstance(codes, list):
        return sorted_json(codes)
    return sorted_json([codes])


def metric_tuple_key(row: dict[str, Any], prefix: str) -> str:
    return sorted_json(
        {
            "EX": row.get(f"{prefix}_EX"),
            "Inv": row.get(f"{prefix}_Inv"),
            "asa_strict": row.get(f"{prefix}_asa_strict"),
            "asa_lower_bound": row.get(f"{prefix}_asa_lower_bound"),
        }
    )


def classify_primary_bottleneck(flags: dict[str, Any]) -> str:
    if not flags["baseline_hard_failure"]:
        return "not_primary_cohort"
    if flags["missed_by_verifier"]:
        return "verifier_miss"
    if flags["caught_but_not_actionable"]:
        return "weak_verifier_signal"
    if flags["actionable_but_not_attempted"]:
        return "repair_not_attempted"
    if flags["attempted_but_no_sql"]:
        return "repair_generation_failure"
    if flags["fixed_but_ex_broken"]:
        return "repair_fixed_inv_but_broke_ex"
    if flags["harmful_gate_accept"]:
        return "gate_accepted_harmful_repair"
    if flags["generated_but_inv_not_fixed"]:
        return "repair_did_not_fix_inv"
    if flags["fixed_and_ex_preserved"]:
        return "not_bottleneck_fixed"
    return "weak_verifier_signal"


def build_audit_row(
    question_id: str,
    before: dict[str, Any],
    after: dict[str, Any],
    verifier_row: dict[str, Any] | None,
    repair_row: dict[str, Any] | None,
    final_eval_row: dict[str, Any] | None,
) -> dict[str, Any]:
    verification = {}
    if verifier_row:
        verification = verifier_row.get("verification") or {}
    if not verification and repair_row:
        verification = repair_row.get("original_verification") or {}

    repair_result = (repair_row or {}).get("repair_result") or {}
    repair_status = (repair_row or {}).get("repair_status")
    if repair_status is None:
        repair_status = (final_eval_row or {}).get("repair_status")
    repair_mode = (repair_row or {}).get("repair_mode")
    if repair_mode is None:
        repair_mode = (final_eval_row or {}).get("repair_mode")

    final_sql_repaired = bool((final_eval_row or {}).get("final_sql_repaired") is True)
    verifier_caught = verification.get("answers_question") is False
    verifier_actionable = (
        verifier_caught
        and verification.get("should_abstain") is not True
        and non_empty(verification.get("repair_hint"))
    )
    repair_attempted = repair_mode == "semantic" and repair_status != "skipped"
    repaired_sql = (repair_row or {}).get("repaired_sql")
    repair_generated = repair_status == "success" and non_empty(repaired_sql)

    before_ex = before.get("EX")
    before_inv = before.get("Inv")
    before_asa_strict = before.get("asa_strict")
    before_asa_lower_bound = before.get("asa_lower_bound")
    after_ex = after.get("EX")
    after_inv = after.get("Inv")
    after_asa_strict = after.get("asa_strict")
    after_asa_lower_bound = after.get("asa_lower_bound")

    baseline_hard_failure = before_inv == 0
    inv_fixed = baseline_hard_failure and after_inv == 1
    inv_not_fixed = baseline_hard_failure and after_inv == 0
    inv_became_not_evaluable = baseline_hard_failure and after_inv is None
    ex_preserved = before_ex == 1 and after_ex == 1
    ex_broken = before_ex == 1 and after_ex == 0
    any_metric_worsened = any(
        [
            metric_worsened(before_ex, after_ex),
            metric_worsened(before_inv, after_inv),
            metric_worsened(before_asa_strict, after_asa_strict),
            metric_worsened(before_asa_lower_bound, after_asa_lower_bound),
        ]
    )
    harmful_gate_accept = final_sql_repaired and any_metric_worsened
    over_repair_candidate = (
        before_ex == 1
        and before_inv == 1
        and final_sql_repaired
        and any_metric_worsened
    )

    missed_by_verifier = baseline_hard_failure and not verifier_caught
    caught_but_not_actionable = baseline_hard_failure and verifier_caught and not verifier_actionable
    actionable_but_not_attempted = (
        baseline_hard_failure and verifier_actionable and not repair_attempted
    )
    attempted_but_no_sql = baseline_hard_failure and repair_attempted and not repair_generated
    generated_but_inv_not_fixed = baseline_hard_failure and repair_generated and not inv_fixed
    fixed_but_ex_broken = inv_fixed and ex_broken
    fixed_and_ex_preserved = inv_fixed and ex_preserved

    flags = {
        "baseline_hard_failure": baseline_hard_failure,
        "missed_by_verifier": missed_by_verifier,
        "caught_but_not_actionable": caught_but_not_actionable,
        "actionable_but_not_attempted": actionable_but_not_attempted,
        "attempted_but_no_sql": attempted_but_no_sql,
        "generated_but_inv_not_fixed": generated_but_inv_not_fixed,
        "fixed_but_ex_broken": fixed_but_ex_broken,
        "fixed_and_ex_preserved": fixed_and_ex_preserved,
        "harmful_gate_accept": harmful_gate_accept,
    }

    row = {
        "question_id": question_id,
        "evaluation_group": (final_eval_row or repair_row or verifier_row or {}).get("evaluation_group"),
        "question": (final_eval_row or repair_row or verifier_row or {}).get("question"),
        "gold_sql": before.get("gold_sql") or (final_eval_row or repair_row or verifier_row or {}).get("gold_sql"),
        "baseline_generated_sql": before.get("generated_sql") or (repair_row or {}).get("original_generated_sql"),
        "repaired_generated_sql": after.get("generated_sql") or repaired_sql,
        "before_EX": before_ex,
        "before_Inv": before_inv,
        "before_asa_strict": before_asa_strict,
        "before_asa_lower_bound": before_asa_lower_bound,
        "before_fcr_hard_finding_codes": before.get("fcr_hard_finding_codes") or [],
        "after_EX": after_ex,
        "after_Inv": after_inv,
        "after_asa_strict": after_asa_strict,
        "after_asa_lower_bound": after_asa_lower_bound,
        "after_fcr_hard_finding_codes": after.get("fcr_hard_finding_codes") or [],
        "verifier_caught": verifier_caught,
        "verifier_actionable": verifier_actionable,
        "verifier_answers_question": verification.get("answers_question"),
        "verifier_mismatch_type": verification.get("mismatch_type")
        or verification.get("primary_mismatch_type"),
        "verifier_mismatch_detail": verification.get("mismatch_detail"),
        "verifier_repair_hint": verification.get("repair_hint"),
        "verifier_confidence": verification.get("confidence"),
        "verifier_failed_evidence": verification.get("failed_evidence")
        or verification.get("stage2_failed_evidence")
        or [],
        "repair_attempted": repair_attempted,
        "repair_generated": repair_generated,
        "repair_status": repair_status,
        "repair_mode": repair_mode,
        "repair_error": (repair_row or {}).get("repair_error") or repair_result.get("error"),
        "repair_edit_summary": repair_result.get("edit_summary"),
        "repair_confidence": repair_result.get("confidence") or (repair_row or {}).get("confidence"),
        "final_sql_repaired": final_sql_repaired,
        "missed_by_verifier": missed_by_verifier,
        "caught_but_not_actionable": caught_but_not_actionable,
        "actionable_but_not_attempted": actionable_but_not_attempted,
        "attempted_but_no_sql": attempted_but_no_sql,
        "generated_but_inv_not_fixed": generated_but_inv_not_fixed,
        "inv_fixed": inv_fixed,
        "fixed_but_ex_broken": fixed_but_ex_broken,
        "fixed_and_ex_preserved": fixed_and_ex_preserved,
        "harmful_gate_accept": harmful_gate_accept,
        "over_repair_candidate": over_repair_candidate,
        "primary_bottleneck": "",
        "_baseline_hard_failure": baseline_hard_failure,
        "_inv_not_fixed": inv_not_fixed,
        "_inv_became_not_evaluable": inv_became_not_evaluable,
        "_metric_worsened": any_metric_worsened,
    }
    row["primary_bottleneck"] = classify_primary_bottleneck(flags)
    return row


def count_true(rows: list[dict[str, Any]], field: str) -> int:
    return sum(1 for row in rows if row.get(field) is True)


def breakdown_counts(
    rows: list[dict[str, Any]],
    field: str,
    *,
    primary_only: bool = True,
) -> dict[str, int]:
    selected = [row for row in rows if not primary_only or row["_baseline_hard_failure"]]
    counts: Counter[str] = Counter()
    for row in selected:
        value = row.get(field)
        if isinstance(value, list):
            key = sorted_json(value)
        else:
            key = str(value)
        counts[key] += 1
    return dict(sorted(counts.items()))


def before_after_metric_breakdown(rows: list[dict[str, Any]], *, primary_only: bool) -> dict[str, int]:
    selected = [row for row in rows if not primary_only or row["_baseline_hard_failure"]]
    counts: Counter[str] = Counter()
    for row in selected:
        key = sorted_json(
            {
                "before": {
                    "EX": row["before_EX"],
                    "Inv": row["before_Inv"],
                    "asa_strict": row["before_asa_strict"],
                    "asa_lower_bound": row["before_asa_lower_bound"],
                },
                "after": {
                    "EX": row["after_EX"],
                    "Inv": row["after_Inv"],
                    "asa_strict": row["after_asa_strict"],
                    "asa_lower_bound": row["after_asa_lower_bound"],
                },
            }
        )
        counts[key] += 1
    return dict(sorted(counts.items()))


def build_summary(
    audit_rows: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    primary = [row for row in audit_rows if row["_baseline_hard_failure"]]
    verifier_caught = [row for row in primary if row["verifier_caught"]]
    actionable = [row for row in verifier_caught if row["verifier_actionable"]]
    attempted = [row for row in actionable if row["repair_attempted"]]
    generated = [row for row in attempted if row["repair_generated"]]
    fixed = [row for row in generated if row["inv_fixed"]]

    funnel = {
        "baseline_fcr_hard_failures": len(primary),
        "verifier_caught": len(verifier_caught),
        "verifier_actionable": len(actionable),
        "repair_attempted": len(attempted),
        "repair_generated": len(generated),
        "inv_fixed": len(fixed),
        "fixed_inv_and_ex_preserved": sum(1 for row in fixed if row["fixed_and_ex_preserved"]),
        "remained_hard_failure_after_repair": sum(1 for row in primary if row["_inv_not_fixed"]),
        "became_not_evaluable_after_repair": sum(
            1 for row in primary if row["_inv_became_not_evaluable"]
        ),
        "harmful_gate_accepts_any_metric": sum(
            1 for row in audit_rows if row["harmful_gate_accept"]
        ),
        "harmful_gate_accepts_primary_cohort": sum(
            1 for row in primary if row["harmful_gate_accept"]
        ),
        "over_repaired_worse_outside_primary_cohort": sum(
            1
            for row in audit_rows
            if row["over_repair_candidate"] and not row["_baseline_hard_failure"]
        ),
    }

    primary_breakdowns = {
        "fcr_hard_finding_codes": breakdown_counts(
            primary,
            "before_fcr_hard_finding_codes",
            primary_only=False,
        ),
        "verifier_mismatch_type": breakdown_counts(
            primary,
            "verifier_mismatch_type",
            primary_only=False,
        ),
        "verifier_confidence": breakdown_counts(
            primary,
            "verifier_confidence",
            primary_only=False,
        ),
        "repair_status": breakdown_counts(primary, "repair_status", primary_only=False),
        "final_sql_repaired": breakdown_counts(primary, "final_sql_repaired", primary_only=False),
        "before_after_metrics": before_after_metric_breakdown(primary, primary_only=False),
        "primary_bottleneck": breakdown_counts(primary, "primary_bottleneck", primary_only=False),
    }

    full_breakdowns = {
        "fcr_hard_finding_codes": breakdown_counts(
            audit_rows,
            "before_fcr_hard_finding_codes",
            primary_only=False,
        ),
        "verifier_mismatch_type": breakdown_counts(
            audit_rows,
            "verifier_mismatch_type",
            primary_only=False,
        ),
        "verifier_confidence": breakdown_counts(
            audit_rows,
            "verifier_confidence",
            primary_only=False,
        ),
        "repair_status": breakdown_counts(audit_rows, "repair_status", primary_only=False),
        "final_sql_repaired": breakdown_counts(
            audit_rows,
            "final_sql_repaired",
            primary_only=False,
        ),
        "before_after_metrics": before_after_metric_breakdown(audit_rows, primary_only=False),
    }

    label_counts = {
        field: count_true(primary, field)
        for field in [
            "missed_by_verifier",
            "caught_but_not_actionable",
            "actionable_but_not_attempted",
            "attempted_but_no_sql",
            "generated_but_inv_not_fixed",
            "inv_fixed",
            "fixed_but_ex_broken",
            "fixed_and_ex_preserved",
            "harmful_gate_accept",
        ]
    }

    return {
        **metadata,
        "primary_cohort_definition": "before.Inv == 0",
        "diagnostic_metric_worsening_order": "None < 0 < 1",
        "funnel": funnel,
        "primary_label_counts": label_counts,
        "primary_breakdowns": primary_breakdowns,
        "full_audit_breakdowns": full_breakdowns,
    }


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: str | Path, rows: list[dict[str, Any]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as handle:
        for row in rows:
            output = {key: value for key, value in row.items() if not key.startswith("_")}
            handle.write(json.dumps(output, ensure_ascii=False) + "\n")


def csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def write_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ROW_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: csv_value(row.get(field)) for field in ROW_FIELDNAMES})


def write_markdown(path: str | Path, summary: dict[str, Any]) -> None:
    funnel = summary["funnel"]
    lines = [
        "# Verifier/Repairer Attribution Audit",
        "",
        f"- ASA universe: {summary['asa_unique_question_ids']} unique question IDs",
        f"- Primary cohort: `{summary['primary_cohort_definition']}` ({funnel['baseline_fcr_hard_failures']} rows)",
        f"- Dedupe policy for repair/eval artifacts: `{summary['dedupe_policy']}`",
        "",
        "## Funnel",
        "",
        "| Question | Count |",
        "| --- | ---: |",
        f"| Baseline FCR hard failures | {funnel['baseline_fcr_hard_failures']} |",
        f"| Verifier caught | {funnel['verifier_caught']} |",
        f"| Verifier-caught and actionable | {funnel['verifier_actionable']} |",
        f"| Actionable with repair attempts | {funnel['repair_attempted']} |",
        f"| Repair attempts that produced SQL | {funnel['repair_generated']} |",
        f"| Repaired SQL outputs that fixed Inv | {funnel['inv_fixed']} |",
        f"| Fixed Inv cases preserving EX | {funnel['fixed_inv_and_ex_preserved']} |",
        f"| Baseline hard failures still hard after repair | {funnel['remained_hard_failure_after_repair']} |",
        f"| Baseline hard failures became not evaluable after repair | {funnel['became_not_evaluable_after_repair']} |",
        f"| Gate accepted repairs with worse EX, Inv, ASA strict, or ASA lower bound | {funnel['harmful_gate_accepts_any_metric']} |",
        f"| Outside-primary over-repair candidates | {funnel['over_repaired_worse_outside_primary_cohort']} |",
        "",
        "## Primary Bottlenecks",
        "",
        "| Bottleneck | Count |",
        "| --- | ---: |",
    ]
    for label, count in summary["primary_breakdowns"]["primary_bottleneck"].items():
        lines.append(f"| `{label}` | {count} |")

    lines.extend(["", "## Primary Breakdowns", ""])
    for name, counts in summary["primary_breakdowns"].items():
        if name == "primary_bottleneck":
            continue
        lines.extend([f"### {name}", "", "| Value | Count |", "| --- | ---: |"])
        for value, count in counts.items():
            lines.append(f"| `{value}` | {count} |")
        lines.append("")

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def output_paths(output_dir: str | Path) -> dict[str, Path]:
    base = Path(output_dir)
    return {
        "json": base / "verifier_repairer_attribution_summary.json",
        "md": base / "verifier_repairer_attribution_summary.md",
        "jsonl": base / "verifier_repairer_attribution_rows.jsonl",
        "csv": base / "verifier_repairer_attribution_cases.csv",
    }


def ensure_can_write(paths: dict[str, Path], overwrite: bool) -> None:
    existing = [path for path in paths.values() if path.exists()]
    if existing and not overwrite:
        formatted = "\n".join(f"- {path}" for path in existing)
        raise FileExistsError(
            "Refusing to overwrite existing audit outputs. Use --overwrite to replace:\n"
            f"{formatted}"
        )


def build_audit(
    asa_rows_path: str | Path,
    verifier_rows_path: str | Path,
    repair_rows_path: str | Path,
    final_eval_rows_path: str | Path,
    *,
    allow_count_mismatch: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    asa_rows = read_jsonl(asa_rows_path)
    before_by_id, after_by_id = split_asa_rows(asa_rows)
    question_ids = sorted(set(before_by_id) & set(after_by_id))
    if len(question_ids) != 1701:
        raise ValueError(f"Expected 1701 joined ASA question IDs, found {len(question_ids)}")
    if len(before_by_id) != 1701 or len(after_by_id) != 1701:
        raise ValueError(
            "Expected 1701 before and 1701 after ASA rows; found "
            f"{len(before_by_id)} before and {len(after_by_id)} after"
        )

    verifier_by_id, verifier_duplicates = dedupe_by_question_id(
        read_jsonl(verifier_rows_path),
        "verifier",
    )
    repair_by_id, repair_duplicates = dedupe_by_question_id(read_jsonl(repair_rows_path), "repair")
    final_eval_by_id, final_eval_duplicates = dedupe_by_question_id(
        read_jsonl(final_eval_rows_path),
        "final_eval",
    )

    audit_rows = [
        build_audit_row(
            question_id,
            before_by_id[question_id],
            after_by_id[question_id],
            verifier_by_id.get(question_id),
            repair_by_id.get(question_id),
            final_eval_by_id.get(question_id),
        )
        for question_id in question_ids
    ]

    primary_count = sum(1 for row in audit_rows if row["_baseline_hard_failure"])
    if primary_count != 95 and not allow_count_mismatch:
        raise ValueError(
            f"Expected 95 baseline hard failures (before.Inv == 0), found {primary_count}. "
            "Use --allow-count-mismatch to continue."
        )

    primary_codes = {
        tuple(row["before_fcr_hard_finding_codes"])
        for row in audit_rows
        if row["_baseline_hard_failure"]
    }
    metadata = {
        "asa_rows_path": str(asa_rows_path),
        "verifier_rows_path": str(verifier_rows_path),
        "repair_rows_path": str(repair_rows_path),
        "final_eval_rows_path": str(final_eval_rows_path),
        "dedupe_policy": "last",
        "asa_input_rows": len(asa_rows),
        "asa_unique_question_ids": len(question_ids),
        "verifier_unique_question_ids": len(verifier_by_id),
        "verifier_duplicate_rows": verifier_duplicates,
        "repair_unique_question_ids": len(repair_by_id),
        "repair_duplicate_rows": repair_duplicates,
        "final_eval_unique_question_ids": len(final_eval_by_id),
        "final_eval_duplicate_rows": final_eval_duplicates,
        "primary_fcr_hard_finding_code_sets": [list(codes) for codes in sorted(primary_codes)],
    }
    return build_summary(audit_rows, metadata), audit_rows


def main() -> None:
    args = parse_args()
    paths = output_paths(args.output_dir)
    ensure_can_write(paths, args.overwrite)

    summary, audit_rows = build_audit(
        args.asa_rows,
        args.verifier_rows,
        args.repair_rows,
        args.final_eval_rows,
        allow_count_mismatch=args.allow_count_mismatch,
    )

    write_json(paths["json"], summary)
    write_markdown(paths["md"], summary)
    write_jsonl(paths["jsonl"], audit_rows)
    write_csv(paths["csv"], audit_rows)

    print(f"ASA question IDs audited: {summary['asa_unique_question_ids']}")
    print(f"Baseline hard failures: {summary['funnel']['baseline_fcr_hard_failures']}")
    print(f"Verifier caught: {summary['funnel']['verifier_caught']}")
    print(f"Inv fixed: {summary['funnel']['inv_fixed']}")
    print(f"Saved audit outputs to: {Path(args.output_dir)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
