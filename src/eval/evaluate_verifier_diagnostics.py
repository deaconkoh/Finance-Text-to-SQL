#!/usr/bin/env python3
"""Analyze FinVeriSQL verifier diagnostics JSONL files."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_INPUT_DIR = Path("data/outputs/finverisql/dev_diagnostics/new")
DEFAULT_REPORT_NAME = "finverisql_diagnostics_analysis.md"
MODE_ORDER = ("direct", "hybrid", "probe")


@dataclass(frozen=True)
class DiagnosticRow:
    path: Path
    mode: str
    group: str
    record: dict[str, Any]

    @property
    def verification(self) -> dict[str, Any]:
        verification = self.record.get("verification")
        return verification if isinstance(verification, dict) else {}

    @property
    def answers_question(self) -> bool | None:
        return self.verification.get("answers_question")

    @property
    def should_abstain(self) -> bool:
        return bool(self.verification.get("should_abstain"))

    @property
    def ambiguous(self) -> bool:
        return bool(self.verification.get("ambiguous"))

    @property
    def abstained(self) -> bool:
        return self.answers_question is None or self.should_abstain

    @property
    def probes_used(self) -> int | float:
        probes = self.verification.get("probes_used")
        return probes if isinstance(probes, (int, float)) else 0

    @property
    def confidence(self) -> str | None:
        confidence = self.verification.get("confidence")
        return str(confidence).lower() if confidence is not None else None

    @property
    def is_correct_for_true_label(self) -> bool:
        if self.group == "A_correct_executable":
            return self.answers_question is True
        if self.group == "B_wrong_executable":
            return self.answers_question is False
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute Task 1 FinVeriSQL verifier performance metrics."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=(
            "Directory containing diagnostics JSONL files. "
            f"Default: {DEFAULT_INPUT_DIR}"
        ),
    )
    parser.add_argument(
        "--input-path",
        type=Path,
        action="append",
        default=None,
        help=(
            "Specific diagnostics JSONL file to analyze. Can be passed multiple times. "
            "When supplied, --input-dir is ignored."
        ),
    )
    parser.add_argument(
        "--pattern",
        default="verified_group*.jsonl",
        help="Glob used with --input-dir. Default: verified_group*.jsonl",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=None,
        help=(
            "Path for the Markdown report. "
            f"Default: <input-dir>/{DEFAULT_REPORT_NAME}"
        ),
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Print the report only; do not write Markdown output.",
    )
    return parser.parse_args()


def mode_from_record(record: dict[str, Any]) -> str | None:
    probing_mode = record.get("probing_mode")
    if probing_mode == "none":
        return "direct"
    if isinstance(probing_mode, str) and probing_mode:
        return probing_mode
    return None


def infer_group_and_mode(path: Path, record: dict[str, Any]) -> tuple[str, str]:
    record_group = record.get("evaluation_group")
    record_mode = mode_from_record(record)
    stem = path.stem
    parts = stem.split("_")

    group = record_group if isinstance(record_group, str) and record_group else None
    mode = record_mode

    if len(parts) >= 3 and parts[0] == "verified":
        group_token = parts[1]
        if group is None:
            if group_token == "groupA":
                group = "A_correct_executable"
            elif group_token == "groupB":
                group = "B_wrong_executable"

        if mode is None:
            mode = parts[2]

    if group is None:
        raise ValueError(
            f"Could not infer evaluation group for {path.name}; "
            "expected record.evaluation_group or a verified_groupA/groupB filename."
        )

    if mode is None:
        raise ValueError(
            f"Could not infer mode for {path.name}; "
            "expected record.probing_mode or a verified_groupX_<mode> filename."
        )

    return group, mode


def discover_paths(args: argparse.Namespace) -> list[Path]:
    if args.input_path:
        paths = args.input_path
    else:
        paths = sorted(args.input_dir.glob(args.pattern))

    if not paths:
        raise FileNotFoundError(
            f"No diagnostics JSONL files found in {args.input_dir} with pattern {args.pattern!r}"
        )

    missing = [path for path in paths if not path.exists()]
    if missing:
        missing_text = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"Input path(s) not found: {missing_text}")

    return sorted(paths)


def load_rows(paths: list[Path]) -> list[DiagnosticRow]:

    rows: list[DiagnosticRow] = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON in {path}:{line_number}: {exc}") from exc
                group, mode = infer_group_and_mode(path, record)
                rows.append(DiagnosticRow(path=path, mode=mode, group=group, record=record))
    return rows


def default_output_dir(args: argparse.Namespace, paths: list[Path]) -> Path:
    if args.input_path:
        parent_dirs = {path.parent for path in paths}
        if len(parent_dirs) == 1:
            return next(iter(parent_dirs))
        return Path.cwd()
    return args.input_dir


def percent(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "n/a"
    return f"{100 * numerator / denominator:.1f}%"


def percent_from_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{100 * value:.1f}%"


def safe_divide(numerator: int | float, denominator: int | float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def f1_score(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return 2 * precision * recall / (precision + recall)


def mode_order(by_mode: dict[str, list[DiagnosticRow]]) -> list[str]:
    ordered_modes = [mode for mode in MODE_ORDER if mode in by_mode]
    ordered_modes.extend(sorted(set(by_mode) - set(ordered_modes)))
    return ordered_modes


def mode_stats(mode_rows: list[DiagnosticRow]) -> dict[str, float | int | str]:
    group_a = [row for row in mode_rows if row.group == "A_correct_executable"]
    group_b = [row for row in mode_rows if row.group == "B_wrong_executable"]
    predicted_accept = [row for row in mode_rows if row.answers_question is True]
    predicted_reject = [row for row in mode_rows if row.answers_question is False]

    true_accepts = sum(row.answers_question is True for row in group_a)
    true_rejects = sum(row.answers_question is False for row in group_b)
    correct = sum(row.is_correct_for_true_label for row in mode_rows)

    accept_precision_count = sum(row.group == "A_correct_executable" for row in predicted_accept)
    reject_precision_count = sum(row.group == "B_wrong_executable" for row in predicted_reject)

    accept_precision = safe_divide(accept_precision_count, len(predicted_accept))
    accept_recall = safe_divide(true_accepts, len(group_a))
    reject_precision = safe_divide(reject_precision_count, len(predicted_reject))
    reject_recall = safe_divide(true_rejects, len(group_b))

    return {
        "n": len(mode_rows),
        "group_a_n": len(group_a),
        "group_b_n": len(group_b),
        "correct": correct,
        "accuracy": correct / len(mode_rows) if mode_rows else 0.0,
        "true_accepts": true_accepts,
        "true_rejects": true_rejects,
        "predicted_accept_n": len(predicted_accept),
        "predicted_reject_n": len(predicted_reject),
        "accept_precision_count": accept_precision_count,
        "reject_precision_count": reject_precision_count,
        "accept_precision": accept_precision,
        "accept_recall": accept_recall,
        "reject_precision": reject_precision,
        "reject_recall": reject_recall,
        "accept_f1": f1_score(accept_precision, accept_recall),
        "reject_f1": f1_score(reject_precision, reject_recall),
        "macro_f1": None,
        "ambiguous_a": sum(row.ambiguous for row in group_a),
        "ambiguous_b": sum(row.ambiguous for row in group_b),
        "abstentions_a": sum(row.abstained for row in group_a),
        "abstentions_b": sum(row.abstained for row in group_b),
        "avg_probes": sum(row.probes_used for row in mode_rows) / len(mode_rows),
    }


def add_macro_f1(stats: dict[str, float | int | str | None]) -> dict[str, float | int | str | None]:
    accept_f1 = stats["accept_f1"]
    reject_f1 = stats["reject_f1"]
    if isinstance(accept_f1, float) and isinstance(reject_f1, float):
        stats["macro_f1"] = (accept_f1 + reject_f1) / 2
    return stats


def metric_table(rows: list[DiagnosticRow]) -> str:
    by_mode: dict[str, list[DiagnosticRow]] = defaultdict(list)
    for row in rows:
        by_mode[row.mode].append(row)

    ordered_modes = mode_order(by_mode)
    stats_by_mode = {
        mode: add_macro_f1(mode_stats(mode_rows))
        for mode, mode_rows in by_mode.items()
    }
    direct_stats = stats_by_mode.get("direct")
    direct_accuracy = float(direct_stats["accuracy"]) if direct_stats else None
    direct_avg_probes = float(direct_stats["avg_probes"]) if direct_stats else None

    lines = [
        "| Mode | N | Label Mix | Accuracy | Macro F1 | Accept Precision | Accept Recall (Group A) | Accept F1 | Reject Precision | Reject Recall (Group B) | Reject F1 | Avg Probes / Query | Probe Gain / Cost |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for mode in ordered_modes:
        stats = stats_by_mode[mode]
        n = int(stats["n"])
        group_a_n = int(stats["group_a_n"])
        group_b_n = int(stats["group_b_n"])
        true_accepts = int(stats["true_accepts"])
        true_rejects = int(stats["true_rejects"])
        predicted_accept_n = int(stats["predicted_accept_n"])
        predicted_reject_n = int(stats["predicted_reject_n"])
        accept_precision_count = int(stats["accept_precision_count"])
        reject_precision_count = int(stats["reject_precision_count"])
        avg_probes = float(stats["avg_probes"])

        if direct_accuracy is None or direct_avg_probes is None or mode == "direct":
            probe_gain_cost = "baseline"
        else:
            accuracy_gain_points = 100 * (float(stats["accuracy"]) - direct_accuracy)
            extra_probes = avg_probes - direct_avg_probes
            probe_gain_cost = (
                f"{accuracy_gain_points / extra_probes:.1f} pp/probe"
                if extra_probes > 0
                else "n/a"
            )

        lines.append(
            "| {mode} | {n} | A={group_a_n}, B={group_b_n} | {correct}/{n} ({accuracy}) | {macro_f1} | "
            "{accept_precision_count}/{predicted_accept_n} ({accept_precision}) | "
            "{true_accepts}/{group_a_n} ({accept_recall}) | {accept_f1} | "
            "{reject_precision_count}/{predicted_reject_n} ({reject_precision}) | "
            "{true_rejects}/{group_b_n} ({reject_recall}) | {reject_f1} | "
            "{avg_probes:.2f} | {probe_gain_cost} |".format(
                mode=mode,
                n=n,
                group_a_n=group_a_n,
                group_b_n=group_b_n,
                correct=int(stats["correct"]),
                accuracy=percent(int(stats["correct"]), n),
                macro_f1=percent_from_float(stats["macro_f1"]),
                accept_precision_count=accept_precision_count,
                predicted_accept_n=predicted_accept_n,
                accept_precision=percent(accept_precision_count, predicted_accept_n),
                true_accepts=true_accepts,
                accept_recall=percent(true_accepts, group_a_n),
                accept_f1=percent_from_float(stats["accept_f1"]),
                reject_precision_count=reject_precision_count,
                predicted_reject_n=predicted_reject_n,
                reject_precision=percent(reject_precision_count, predicted_reject_n),
                true_rejects=true_rejects,
                reject_recall=percent(true_rejects, group_b_n),
                reject_f1=percent_from_float(stats["reject_f1"]),
                avg_probes=avg_probes,
                probe_gain_cost=probe_gain_cost,
            )
        )

    return "\n".join(lines)


def operational_table(rows: list[DiagnosticRow]) -> str:
    by_mode: dict[str, list[DiagnosticRow]] = defaultdict(list)
    for row in rows:
        by_mode[row.mode].append(row)

    lines = [
        "| Mode | Ambiguous A | Ambiguous B | Abstain A | Abstain B |",
        "|---|---:|---:|---:|---:|",
    ]

    for mode in mode_order(by_mode):
        stats = mode_stats(by_mode[mode])
        group_a_n = int(stats["group_a_n"])
        group_b_n = int(stats["group_b_n"])
        ambiguous_a = int(stats["ambiguous_a"])
        ambiguous_b = int(stats["ambiguous_b"])
        abstentions_a = int(stats["abstentions_a"])
        abstentions_b = int(stats["abstentions_b"])

        lines.append(
            "| {mode} | {ambiguous_a}/{group_a_n} ({amb_a_rate}) | "
            "{ambiguous_b}/{group_b_n} ({amb_b_rate}) | "
            "{abstentions_a}/{group_a_n} ({abs_a_rate}) | "
            "{abstentions_b}/{group_b_n} ({abs_b_rate}) |".format(
                mode=mode,
                ambiguous_a=ambiguous_a,
                group_a_n=group_a_n,
                amb_a_rate=percent(ambiguous_a, group_a_n),
                ambiguous_b=ambiguous_b,
                group_b_n=group_b_n,
                amb_b_rate=percent(ambiguous_b, group_b_n),
                abstentions_a=abstentions_a,
                abs_a_rate=percent(abstentions_a, group_a_n),
                abstentions_b=abstentions_b,
                abs_b_rate=percent(abstentions_b, group_b_n),
            )
        )

    return "\n".join(lines)


def confidence_table(rows: list[DiagnosticRow]) -> str:
    by_mode: dict[str, list[DiagnosticRow]] = defaultdict(list)
    for row in rows:
        by_mode[row.mode].append(row)

    ordered_modes = [mode for mode in MODE_ORDER if mode in by_mode]
    ordered_modes.extend(sorted(set(by_mode) - set(ordered_modes)))

    lines = [
        "| Mode | N | High Confidence | High Confidence Precision | High Confidence Right | High Confidence Wrong | Non-High Confidence |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]

    for mode in ordered_modes:
        mode_rows = by_mode[mode]
        high_confidence = [row for row in mode_rows if row.confidence == "high"]
        high_right = sum(row.is_correct_for_true_label for row in high_confidence)
        high_wrong = len(high_confidence) - high_right
        non_high = len(mode_rows) - len(high_confidence)

        lines.append(
            "| {mode} | {n} | {high}/{n} ({high_rate}) | {precision} | "
            "{high_right}/{high} ({right_rate}) | {high_wrong}/{high} ({wrong_rate}) | "
            "{non_high}/{n} ({non_high_rate}) |".format(
                mode=mode,
                n=len(mode_rows),
                high=len(high_confidence),
                high_rate=percent(len(high_confidence), len(mode_rows)),
                precision=percent(high_right, len(high_confidence)),
                high_right=high_right,
                right_rate=percent(high_right, len(high_confidence)),
                high_wrong=high_wrong,
                wrong_rate=percent(high_wrong, len(high_confidence)),
                non_high=non_high,
                non_high_rate=percent(non_high, len(mode_rows)),
            )
        )

    return "\n".join(lines)


def input_summary(rows: list[DiagnosticRow]) -> str:
    counts = Counter(row.path.name for row in rows)
    lines = [
        f"- Files read: {len(counts)}",
        f"- Rows read: {len(rows)}",
    ]
    for name in sorted(counts):
        lines.append(f"- `{name}`: {counts[name]} rows")
    return "\n".join(lines)


def probe_gain_note() -> str:
    return (
        "Probe Gain / Cost is the accuracy gain over the `direct` baseline, "
        "measured in percentage points, divided by the additional average probes "
        "used per query. Higher is better, but values can be large when probe "
        "usage is very low."
    )


def render_report(rows: list[DiagnosticRow]) -> str:
    sections = [
        "# FinVeriSQL Diagnostics Analysis",
        "## Input Summary",
        input_summary(rows),
        "## Mode Performance Comparison",
        probe_gain_note(),
        metric_table(rows),
        "## Operational Signals",
        operational_table(rows),
        "## High-Confidence Accuracy",
        confidence_table(rows),
    ]
    return "\n\n".join(sections)


def main() -> None:
    args = parse_args()
    paths = discover_paths(args)
    rows = load_rows(paths)
    report = render_report(rows)

    print(report)

    if not args.no_write:
        output_dir = default_output_dir(args, paths)
        report_path = args.output_md or output_dir / DEFAULT_REPORT_NAME
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report + "\n", encoding="utf-8")
        print()
        print("## Written Outputs")
        print()
        print(f"- Markdown report: `{report_path}`")


if __name__ == "__main__":
    main()
