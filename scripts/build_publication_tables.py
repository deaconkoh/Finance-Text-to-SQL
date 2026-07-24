#!/usr/bin/env python3
"""Build publication-ready FinVeriSQL comparison tables from run artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


GROUP_A = "A_correct_executable"
GROUP_B = "B_wrong_executable"
GROUP_C = "C_non_executable"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build main comparison and ablation tables from run artifacts.",
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--manifest", help="Run manifest JSON path.")
    source_group.add_argument(
        "--run-root",
        help=(
            "Completed labeled-run root. Uses the standard 2_run_ablations.sh "
            "artifact layout without requiring debug/run_manifest.json."
        ),
    )
    parser.add_argument(
        "--publication-dir",
        default=None,
        help="Directory for publication Markdown tables only.",
    )
    parser.add_argument(
        "--debug-dir",
        default=None,
        help="Directory for machine-readable table JSON.",
    )
    parser.add_argument(
        "--main-only",
        action="store_true",
        help="Build only the main comparison table; do not require ablation artifacts.",
    )
    return parser.parse_args()


def main_manifest_from_run_root(run_root: str | Path) -> dict[str, Any]:
    root = Path(run_root)
    held_out_metrics = root / "metrics"
    debug = root / "debug"

    def system(
        key: str,
        label: str,
        kind: str,
        metrics: Path,
        asa: Path,
    ) -> dict[str, str]:
        return {
            "key": key,
            "label": label,
            "kind": kind,
            "metrics_json": str(metrics),
            "asa_metrics_json": str(asa),
        }

    def ablation(key: str, label: str) -> dict[str, str]:
        directory = debug / "internal_ablation" / key
        return {
            "key": key,
            "label": label,
            "verify_jsonl": str(directory / f"{key}_verify.jsonl"),
            "metrics_json": str(directory / f"{key}_final_metrics.json"),
            "asa_metrics_json": str(directory / f"{key}_asa_metrics.json"),
        }

    if held_out_metrics.is_dir():
        return {
            "main_systems": [
                system(
                    "generator_only",
                    "Generator only",
                    "generator",
                    held_out_metrics / "baseline" / "metrics.json",
                    held_out_metrics / "baseline" / "asa_metrics.json",
                ),
                system(
                    "generic_self_refine",
                    "Generator + generic self-refine",
                    "repair",
                    held_out_metrics / "main_comparison" / "generic_self_refine" / "final_metrics.json",
                    held_out_metrics / "main_comparison" / "generic_self_refine" / "asa_metrics.json",
                ),
                system(
                    "generic_execution_guided_refine",
                    "Generator + generic execution-guided refine",
                    "repair",
                    held_out_metrics / "main_comparison" / "generic_execution_guided_refine" / "final_metrics.json",
                    held_out_metrics / "main_comparison" / "generic_execution_guided_refine" / "asa_metrics.json",
                ),
                system(
                    "finverisql_full",
                    "Generator + FinVeriSQL full",
                    "repair",
                    held_out_metrics / "finverisql_full" / "final_metrics.json",
                    held_out_metrics / "finverisql_full" / "asa_metrics.json",
                ),
            ],
        }

    return {
        "main_systems": [
            system(
                "generator_only",
                "Generator only",
                "generator",
                debug / "baseline" / "qwen_few_shot_validation_metrics.json",
                debug / "baseline" / "qwen_few_shot_validation_asa_metrics.json",
            ),
            system(
                "generic_self_refine",
                "Generator + generic self-refine",
                "repair",
                debug / "main_comparison" / "generic_self_refine" / "generic_self_refine_final_metrics.json",
                debug / "main_comparison" / "generic_self_refine" / "generic_self_refine_asa_metrics.json",
            ),
            system(
                "generic_execution_guided_refine",
                "Generator + generic execution-guided refine",
                "repair",
                debug / "main_comparison" / "generic_execution_guided_refine" / "generic_execution_guided_refine_final_metrics.json",
                debug / "main_comparison" / "generic_execution_guided_refine" / "generic_execution_guided_refine_asa_metrics.json",
            ),
            system(
                "finverisql_full",
                "Generator + FinVeriSQL full",
                "repair",
                debug / "internal_ablation" / "full" / "full_final_metrics.json",
                debug / "internal_ablation" / "full" / "full_asa_metrics.json",
            ),
        ],
        "ablations": [
            ablation("full", "FinVeriSQL full"),
            ablation("wo_intent_decomposer", "w/o Intent Decomposer"),
            ablation("direct_only", "w/o Probing / direct only"),
            ablation("wo_compact_semantic_profile", "w/o Compact Semantic Profile"),
            ablation("wo_scope_constraints", "w/o Scope Constraints in Repair"),
            ablation("wo_reverification_loop", "w/o re-verification loop"),
        ],
    }


def read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object at {path}")
    return data


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


def safe_rate(numerator: int | float, denominator: int | float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def f1_score(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return 2 * precision * recall / (precision + recall)


def pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def signed_pp(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:+.2f} pp"


def count_rate(count: int | None, total: int | None) -> str:
    if count is None or total is None:
        return "n/a"
    rate = safe_rate(count, total)
    return f"{pct(rate)} ({count}/{total})"


def metric_total(metrics: dict[str, Any]) -> int | None:
    value = metrics.get("metric_total_examples")
    if isinstance(value, int):
        return value

    repair_summary = metrics.get("repair_summary") or {}
    baseline = repair_summary.get("baseline_comparison") or {}
    value = baseline.get("original_metric_total_examples")
    return value if isinstance(value, int) else None


def final_ex_accuracy(metrics: dict[str, Any]) -> float | None:
    value = metrics.get("execution_accuracy")
    if isinstance(value, (int, float)):
        return float(value)

    repair_summary = metrics.get("repair_summary") or {}
    baseline = repair_summary.get("baseline_comparison") or {}
    value = baseline.get("final_execution_accuracy")
    return float(value) if isinstance(value, (int, float)) else None


def asa_set_metrics(asa_metrics: dict[str, Any]) -> dict[str, Any]:
    sets = asa_metrics.get("sets") or []
    if not isinstance(sets, list) or not sets:
        raise ValueError("ASA metrics JSON has no sets.")

    for item in sets:
        if isinstance(item, dict) and item.get("label") == "after":
            return item

    for item in sets:
        if isinstance(item, dict) and item.get("label") == "before":
            return item

    if isinstance(sets[0], dict):
        return sets[0]

    raise ValueError("ASA metrics sets must contain JSON objects.")


def asa_cell(asa_metrics: dict[str, Any]) -> str:
    item = asa_set_metrics(asa_metrics)
    strict = pct(item.get("asa_strict_accuracy"))
    lower = pct(item.get("asa_lower_bound_accuracy"))
    fper = pct(item.get("fper"))
    return f"Strict {strict}; LB {lower}; FPER {fper}"


def repair_rates(metrics: dict[str, Any]) -> dict[str, Any]:
    repair_summary = metrics.get("repair_summary")
    if not isinstance(repair_summary, dict):
        return {
            "correction": None,
            "correction_count": None,
            "correction_total": None,
            "corruption": None,
            "corruption_count": None,
            "corruption_total": None,
            "net_gain_count": None,
            "net_gain_rate": None,
            "net_gain_total": None,
            "conditional_correction": None,
            "conditional_correction_total": None,
            "conditional_corruption": None,
            "conditional_corruption_total": None,
        }

    effectiveness = repair_summary.get("repair_effectiveness") or {}
    safety = repair_summary.get("repair_safety") or {}
    baseline = repair_summary.get("baseline_comparison") or {}
    movement = repair_summary.get("repair_movement") or {}

    fixed = int(effectiveness.get("wrong_to_correct_rows") or 0)
    wrong_total = int(effectiveness.get("originally_wrong_or_nonexec_rows") or 0)
    corrupted = int(safety.get("corrupted_originally_correct_rows") or 0)
    originally_correct = int(safety.get("originally_correct_rows") or 0)
    denominator = int(
        baseline.get("original_metric_total_examples")
        or metric_total(metrics)
        or 0
    )
    net_gain_count = fixed - corrupted

    # New metrics contain a canonical common denominator. Completed artifacts
    # produced before this field existed are reconstructed from their durable
    # movement counts and baseline A/B/C total.
    movement_total_value = movement.get("eligible_evaluation_rows")
    if isinstance(movement_total_value, int):
        movement_total = movement_total_value
    else:
        movement_total = denominator
    corrected_value = movement.get("corrected_rows")
    if isinstance(corrected_value, int):
        fixed = corrected_value
    corrupted_value = movement.get("corrupted_rows")
    if isinstance(corrupted_value, int):
        corrupted = corrupted_value
    net_gain_value = movement.get("net_repair_gain_rows")
    if isinstance(net_gain_value, int):
        net_gain_count = net_gain_value
    else:
        net_gain_count = fixed - corrupted

    return {
        "correction": safe_rate(fixed, movement_total),
        "correction_count": fixed,
        "correction_total": movement_total,
        "corruption": safe_rate(corrupted, movement_total),
        "corruption_count": corrupted,
        "corruption_total": movement_total,
        "net_gain_count": net_gain_count,
        "net_gain_rate": safe_rate(net_gain_count, movement_total),
        "net_gain_total": movement_total,
        "conditional_correction": safe_rate(fixed, wrong_total),
        "conditional_correction_total": wrong_total,
        "conditional_corruption": safe_rate(corrupted, originally_correct),
        "conditional_corruption_total": originally_correct,
    }


def ensure_common_movement_denominator(rows: list[dict[str, Any]], label: str) -> None:
    totals = {
        int(row["correction_total"])
        for row in rows
        if row.get("correction_total") is not None
    }
    if len(totals) > 1:
        formatted = ", ".join(str(total) for total in sorted(totals))
        raise ValueError(
            f"{label} rows do not share one eligible A/B/C denominator: {formatted}."
        )


def detection_metrics(verify_jsonl: str | Path) -> dict[str, Any]:
    rows = read_jsonl(verify_jsonl)
    tp = fp = fn = tn = 0

    for row in rows:
        group = row.get("evaluation_group")
        if group not in {GROUP_A, GROUP_B}:
            continue

        verification = row.get("verification")
        verification = verification if isinstance(verification, dict) else {}
        rejected = verification.get("answers_question") is False

        if group == GROUP_B and rejected:
            tp += 1
        elif group == GROUP_A and rejected:
            fp += 1
        elif group == GROUP_B and not rejected:
            fn += 1
        elif group == GROUP_A and not rejected:
            tn += 1

    precision = safe_rate(tp, tp + fp)
    recall = safe_rate(tp, tp + fn)

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1_score(precision, recall),
    }


def build_main_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for system in manifest["main_systems"]:
        metrics = read_json(system["metrics_json"])
        asa_metrics = read_json(system["asa_metrics_json"])
        rates = repair_rates(metrics)
        asa_after = asa_set_metrics(asa_metrics)

        is_generator = system.get("kind") == "generator"
        rows.append(
            {
                "system": system["label"],
                "ex_accuracy": final_ex_accuracy(metrics),
                "asa_strict_accuracy": asa_after.get("asa_strict_accuracy"),
                "correction_rate": None if is_generator else rates["correction"],
                "correction_count": None if is_generator else rates["correction_count"],
                "correction_total": None if is_generator else rates["correction_total"],
                "corruption_rate": None if is_generator else rates["corruption"],
                "corruption_count": None if is_generator else rates["corruption_count"],
                "corruption_total": None if is_generator else rates["corruption_total"],
                "net_repair_gain_rate": None if is_generator else rates["net_gain_rate"],
                "net_repair_gain_count": None if is_generator else rates["net_gain_count"],
                "net_repair_gain_total": None if is_generator else rates["net_gain_total"],
            }
        )

    ensure_common_movement_denominator(rows, "Main comparison")
    return rows


def build_ablation_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    raw_rows: list[dict[str, Any]] = []

    for variant in manifest["ablations"]:
        metrics = read_json(variant["metrics_json"])
        rates = repair_rates(metrics)
        detection = detection_metrics(variant["verify_jsonl"])
        raw_rows.append(
            {
                "system": variant["label"],
                "key": variant["key"],
                "detection": detection,
                "correction_rate": rates["correction"],
                "correction_count": rates["correction_count"],
                "correction_total": rates["correction_total"],
                "corruption_rate": rates["corruption"],
                "corruption_count": rates["corruption_count"],
                "corruption_total": rates["corruption_total"],
                "net_repair_gain_rate": rates["net_gain_rate"],
                "net_repair_gain_count": rates["net_gain_count"],
                "net_repair_gain_total": rates["net_gain_total"],
                "ex_accuracy": final_ex_accuracy(metrics),
            }
        )

    full_row = next((row for row in raw_rows if row["key"] == "full"), None)
    if full_row is None:
        raise ValueError("Manifest ablations must include key='full'.")

    full_ex = full_row["ex_accuracy"]
    for row in raw_rows:
        row["delta_ex_vs_full"] = (
            None if full_ex is None or row["ex_accuracy"] is None
            else row["ex_accuracy"] - full_ex
        )

    ensure_common_movement_denominator(raw_rows, "Internal ablation")
    return raw_rows


def main_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| System | EX | ASA | Corrected | Corrupted | Net Gain |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]

    for row in rows:
        if row["correction_rate"] is None:
            correction = corruption = net_gain = "-"
        else:
            correction = count_rate(row["correction_count"], row["correction_total"])
            corruption = count_rate(row["corruption_count"], row["corruption_total"])
            net_gain = (
                f"{signed_pp(row['net_repair_gain_rate'])} "
                f"({row['net_repair_gain_count']}/{row['net_repair_gain_total']})"
            )

        lines.append(
            "| {system} | {ex} | {asa} | {correction} | {corruption} | {net_gain} |".format(
                system=row["system"],
                ex=pct(row["ex_accuracy"]),
                asa=pct(row["asa_strict_accuracy"]),
                correction=correction,
                corruption=corruption,
                net_gain=net_gain,
            )
        )

    return "\n".join(lines) + "\n"


def ablation_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| System / Ablation | Detection Precision | Detection Recall | Detection F1 | Correction (% of N) | Corruption (% of N) | Net Repair Gain (% of N) | EX Accuracy | Delta EX vs Full |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for row in rows:
        detection = row["detection"]
        delta = "0.00 pp" if row["key"] == "full" else signed_pp(row["delta_ex_vs_full"])
        lines.append(
            "| {system} | {precision} | {recall} | {f1} | {correction} | {corruption} | {net_gain} | {ex} | {delta} |".format(
                system=row["system"],
                precision=count_rate(detection["tp"], detection["tp"] + detection["fp"]),
                recall=count_rate(detection["tp"], detection["tp"] + detection["fn"]),
                f1=pct(detection["f1"]),
                correction=count_rate(row["correction_count"], row["correction_total"]),
                corruption=count_rate(row["corruption_count"], row["corruption_total"]),
                net_gain=(
                    f"{signed_pp(row['net_repair_gain_rate'])} "
                    f"({row['net_repair_gain_count']}/{row['net_repair_gain_total']})"
                ),
                ex=pct(row["ex_accuracy"]),
                delta=delta,
            )
        )

    return "\n".join(lines) + "\n"


def write_json(path: str | Path, data: Any) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    run_root = Path(args.run_root) if args.run_root else None
    manifest = read_json(args.manifest) if args.manifest else main_manifest_from_run_root(run_root)
    publication_dir = Path(args.publication_dir) if args.publication_dir else (
        run_root / "publication_tables" if run_root else None
    )
    debug_dir = Path(args.debug_dir) if args.debug_dir else (
        (run_root / "tables") if run_root and (run_root / "metrics").is_dir()
        else (run_root / "debug" / "tables" if run_root else None)
    )
    if publication_dir is None or debug_dir is None:
        raise ValueError("--publication-dir and --debug-dir are required with --manifest.")

    publication_dir.mkdir(parents=True, exist_ok=True)
    debug_dir.mkdir(parents=True, exist_ok=True)

    main_rows = build_main_rows(manifest)
    (publication_dir / "main_comparison_table.md").write_text(
        main_markdown(main_rows),
        encoding="utf-8",
    )
    write_json(debug_dir / "main_comparison_table.json", main_rows)

    if not args.main_only:
        if "ablations" not in manifest:
            raise ValueError(
                "This run root contains only main-comparison metrics; use --main-only."
            )
        ablation_rows = build_ablation_rows(manifest)
        (publication_dir / "internal_ablation_table.md").write_text(
            ablation_markdown(ablation_rows),
            encoding="utf-8",
        )
        write_json(debug_dir / "internal_ablation_table.json", ablation_rows)

    print(f"Wrote publication tables to {publication_dir}")
    print(f"Wrote machine-readable tables to {debug_dir}")


if __name__ == "__main__":
    main()
