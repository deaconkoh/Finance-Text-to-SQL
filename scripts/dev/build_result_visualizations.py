#!/usr/bin/env python3
"""Build SVG visualizations from completed FinVeriSQL experiment outputs."""

from __future__ import annotations

import argparse
import json
import math
import sys
from html import escape
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_publication_tables import (  # noqa: E402
    asa_set_metrics,
    ensure_common_movement_denominator,
    final_ex_accuracy,
    main_manifest_from_run_root,
    read_json,
    repair_rates,
)


MAIN_SYSTEM_KEYS = {
    "generator_only",
    "generic_self_refine",
    "generic_execution_guided_refine",
    "finverisql_full",
}

STRATEGY_KEYS = [
    "prompt_llama31_8b",
    "sft_llama31_8b",
    "rl_llama31_8b",
]

STRATEGY_LABELS = {
    "prompt_llama31_8b": "Prompted Llama-3.1-8B",
    "sft_llama31_8b": "SFT Llama-3.1-8B",
    "rl_llama31_8b": "RL Llama-3.1-8B",
}

COLORS = {
    "ex": "#2563eb",
    "asa_strict": "#16a34a",
    "correction": "#16a34a",
    "corruption": "#dc2626",
    "remains_wrong": "#9ca3af",
    "preserved": "#38bdf8",
    "nonexec": "#f59e0b",
    "point": "#7c3aed",
    "grid": "#d4d4d8",
    "axis": "#52525b",
    "text": "#18181b",
    "muted": "#71717a",
    "panel": "#fafafa",
}

DISPLAY_LABELS = {
    "finverisql_full": "ADiR",
}

DIMENSION_LABELS = {
    "financial_object_error": "D1 Financial Object",
    "financial_measure_error": "D2 Financial Measure",
    "computation_logic_error": "D3 Computation Scope",
    "non_executable_error": "Non-executable",
    None: "No rejected label",
}

DIMENSION_ORDER = [
    "financial_object_error",
    "financial_measure_error",
    "computation_logic_error",
    "non_executable_error",
    None,
]

MOVEMENT_FIELDS = [
    ("corrected", "Corrected", COLORS["correction"]),
    ("remains_wrong", "Remains wrong", COLORS["remains_wrong"]),
    ("preserved_correct", "Preserved correct", COLORS["preserved"]),
    ("corrupted", "Corrupted", COLORS["corruption"]),
]

EDIT_CLAUSES = [
    "SELECT",
    "FROM",
    "JOIN",
    "WHERE",
    "GROUP BY",
    "HAVING",
    "ORDER BY",
    "LIMIT",
]

EDIT_SCOPE_DIMENSIONS = [
    "financial_object_error",
    "financial_measure_error",
    "computation_logic_error",
]

EDIT_SCOPE_ALLOWED = {
    "financial_object_error": {"WHERE"},
    "financial_measure_error": {"SELECT"},
    "computation_logic_error": {
        "SELECT",
        "WHERE",
        "GROUP BY",
        "HAVING",
        "ORDER BY",
        "LIMIT",
    },
}

SANKEY_MIDDLE_ORDER = [
    "verifier_accepted",
    "financial_object_error",
    "financial_measure_error",
    "computation_logic_error",
    "non_executable_error",
    "abstained",
]

SANKEY_MIDDLE_LABELS = {
    "verifier_accepted": "Verifier accepted",
    "financial_object_error": "D1 Financial Object",
    "financial_measure_error": "D2 Financial Measure",
    "computation_logic_error": "D3 Computation Scope",
    "non_executable_error": "Non-executable",
    "abstained": "Abstained",
}

SANKEY_MIDDLE_COLORS = {
    "verifier_accepted": "#0d9488",
    "financial_object_error": "#8b5cf6",
    "financial_measure_error": "#2563eb",
    "computation_logic_error": "#f59e0b",
    "non_executable_error": "#a16207",
    "abstained": "#71717a",
}

SANKEY_OUTCOME_ORDER = [
    "preserved_correct",
    "corrected",
    "remains_wrong",
    "corrupted",
]

SANKEY_OUTCOME_LABELS = {
    "preserved_correct": "Preserved correct",
    "corrected": "Corrected",
    "remains_wrong": "Remains wrong",
    "corrupted": "Corrupted",
}

SANKEY_OUTCOME_COLORS = {
    "preserved_correct": COLORS["preserved"],
    "corrected": COLORS["correction"],
    "remains_wrong": COLORS["remains_wrong"],
    "corrupted": COLORS["corruption"],
}

TRADEOFF_SYSTEM_ORDER = [
    "generic_self_refine",
    "generic_execution_guided_refine",
    "finverisql_full",
]

TRADEOFF_LABELS = {
    "generic_self_refine": "Generic self-refine",
    "generic_execution_guided_refine": "Execution-guided",
    "finverisql_full": "ADiR",
}

TRADEOFF_COLORS = {
    "generic_self_refine": "#fdba74",
    "generic_execution_guided_refine": "#fb923c",
    "finverisql_full": "#c2410c",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create dependency-free SVG figures from completed 2_run_ablations.sh "
            "and 4_run_repair_ablation.sh outputs."
        )
    )
    parser.add_argument(
        "--run-root",
        required=True,
        help="Completed 2_run_ablations.sh output root, e.g. data/outputs/finverisql/$RUN_ID.",
    )
    parser.add_argument(
        "--repair-ablation-dir",
        default=None,
        help=(
            "Completed repair-strategy ablation output directory. Defaults to "
            "$RUN_ROOT/debug/repair_strategy_ablation/full_fixed_verifier."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for SVG figures. Defaults to $RUN_ROOT/publication_figures.",
    )
    parser.add_argument(
        "--main-only",
        action="store_true",
        help="Generate main-comparison figures only; skip the repair-strategy figure.",
    )
    parser.add_argument("--width", type=int, default=1200, help="SVG width in pixels.")
    return parser.parse_args()


def resolve_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate


def ensure_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Missing {label}: {path}")


def ensure_dir(path: Path, label: str) -> None:
    if not path.is_dir():
        raise FileNotFoundError(f"Missing {label}: {path}")


def pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def value_or_zero(value: float | None) -> float:
    return 0.0 if value is None else float(value)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def blend_hex(start: str, end: str, ratio: float) -> str:
    ratio = clamp(ratio, 0.0, 1.0)
    start_rgb = tuple(int(start[index : index + 2], 16) for index in (1, 3, 5))
    end_rgb = tuple(int(end[index : index + 2], 16) for index in (1, 3, 5))
    blended = tuple(
        round(start_channel + (end_channel - start_channel) * ratio)
        for start_channel, end_channel in zip(start_rgb, end_rgb)
    )
    return "#" + "".join(f"{channel:02x}" for channel in blended)


def svg_header(width: int, height: int) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">',
        "<style>",
        "text { font-family: Arial, Helvetica, sans-serif; fill: #18181b; }",
        ".title { font-size: 24px; font-weight: 700; }",
        ".subtitle { font-size: 14px; fill: #52525b; }",
        ".label { font-size: 13px; }",
        ".small { font-size: 12px; fill: #52525b; }",
        ".tick { font-size: 11px; fill: #71717a; }",
        "</style>",
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="white"/>',
    ]


def write_svg(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_number}")
            rows.append(row)
    return rows


def rows_by_question_id(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for row in rows:
        question_id = row.get("question_id")
        if not isinstance(question_id, str) or not question_id:
            raise ValueError(f"{label} row is missing question_id")
        if question_id in indexed:
            duplicates.append(question_id)
        indexed[question_id] = row
    if duplicates:
        sample = ", ".join(sorted(set(duplicates))[:5])
        raise ValueError(f"{label} contains duplicate question_id values: {sample}")
    return indexed


def execution_pass(row: dict[str, Any]) -> bool:
    value = row.get("execution_match")
    return value is True or value == 1


def verification_from_row(row: dict[str, Any]) -> dict[str, Any]:
    verification = row.get("original_verification") or row.get("verification")
    return verification if isinstance(verification, dict) else {}


def primary_mismatch_type(row: dict[str, Any]) -> str | None:
    verification = verification_from_row(row)
    value = (
        verification.get("stage2_primary_mismatch_type")
        or verification.get("mismatch_type")
        or verification.get("primary_mismatch_type")
    )
    return str(value) if value else None


def short_sql(sql: Any, max_chars: int = 650) -> str:
    text = str(sql or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def wrap_text(text: Any, max_chars: int) -> list[str]:
    words = str(text or "").replace("\n", " ").split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + len(word) + 1 <= max_chars:
            current += " " + word
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def draw_wrapped_text(
    lines: list[str],
    text: Any,
    x: float,
    y: float,
    max_chars: int,
    css_class: str = "small",
    max_lines: int | None = None,
    fill: str | None = None,
) -> float:
    wrapped = wrap_text(text, max_chars)
    if max_lines is not None and len(wrapped) > max_lines:
        wrapped = wrapped[:max_lines]
        wrapped[-1] = wrapped[-1].rstrip(".") + "..."
    fill_attr = f' style="fill: {fill};"' if fill else ""
    for idx, line in enumerate(wrapped):
        lines.append(
            f'<text class="{css_class}" x="{x:.1f}" y="{y + idx * 17:.1f}"{fill_attr}>'
            f"{escape(line)}</text>"
        )
    return y + len(wrapped) * 17


def draw_box(
    lines: list[str],
    x: float,
    y: float,
    width: float,
    height: float,
    title: str,
    fill: str = "#fafafa",
) -> None:
    lines.append(
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" '
        f'rx="6" fill="{fill}" stroke="#d4d4d8"/>'
    )
    lines.append(f'<text class="label" x="{x + 18:.1f}" y="{y + 28:.1f}" font-weight="700">{escape(title)}</text>')


def read_main_rows(run_root: Path) -> list[dict[str, Any]]:
    manifest_path = run_root / "debug" / "run_manifest.json"
    manifest = (
        read_json(manifest_path)
        if manifest_path.is_file()
        else main_manifest_from_run_root(run_root)
    )

    systems = manifest.get("main_systems")
    if not isinstance(systems, list):
        raise ValueError(f"Expected main_systems list in {manifest_path}")

    rows: list[dict[str, Any]] = []
    missing: list[str] = []

    for system in systems:
        if not isinstance(system, dict) or system.get("key") not in MAIN_SYSTEM_KEYS:
            continue

        metrics_path = resolve_path(system["metrics_json"])
        asa_path = resolve_path(system["asa_metrics_json"])
        if not metrics_path.is_file():
            missing.append(str(metrics_path))
            continue
        if not asa_path.is_file():
            missing.append(str(asa_path))
            continue

        metrics = read_json(metrics_path)
        asa_metrics = read_json(asa_path)
        asa_after = asa_set_metrics(asa_metrics)
        rates = repair_rates(metrics)
        is_generator = system.get("kind") == "generator"
        label = DISPLAY_LABELS.get(system["key"], system["label"])

        rows.append(
            {
                "key": system["key"],
                "label": label,
                "kind": system.get("kind"),
                "ex_accuracy": final_ex_accuracy(metrics),
                "asa_strict_accuracy": asa_after.get("asa_strict_accuracy"),
                "fper": asa_after.get("fper"),
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

    if missing:
        formatted = "\n".join(f"- {path}" for path in missing)
        raise FileNotFoundError(f"Missing main comparison metric files:\n{formatted}")

    found_keys = {row["key"] for row in rows}
    expected_missing = MAIN_SYSTEM_KEYS - found_keys
    if expected_missing:
        raise ValueError(
            "Run manifest does not contain all required main systems: "
            + ", ".join(sorted(expected_missing))
        )

    order = [
        "generator_only",
        "generic_self_refine",
        "generic_execution_guided_refine",
        "finverisql_full",
    ]
    rows = sorted(rows, key=lambda row: order.index(row["key"]))
    ensure_common_movement_denominator(rows, "Main visualization")
    return rows


def build_correction_corruption_tradeoff(
    main_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    by_key = {row.get("key"): row for row in main_rows}
    missing = [key for key in TRADEOFF_SYSTEM_ORDER if key not in by_key]
    if missing:
        raise ValueError(
            "Trade-off visualization is missing required systems: "
            + ", ".join(missing)
        )

    systems: list[dict[str, Any]] = []
    denominators: set[int] = set()
    for key in TRADEOFF_SYSTEM_ORDER:
        row = by_key[key]
        correction_count = row.get("correction_count")
        correction_total = row.get("correction_total")
        corruption_count = row.get("corruption_count")
        corruption_total = row.get("corruption_total")
        net_count = row.get("net_repair_gain_count")
        net_total = row.get("net_repair_gain_total")
        values = {
            "correction_count": correction_count,
            "correction_total": correction_total,
            "corruption_count": corruption_count,
            "corruption_total": corruption_total,
            "net_repair_gain_count": net_count,
            "net_repair_gain_total": net_total,
        }
        if not all(isinstance(value, int) for value in values.values()):
            raise ValueError(f"Trade-off metrics are incomplete for {key}: {values}")
        assert isinstance(correction_count, int)
        assert isinstance(correction_total, int)
        assert isinstance(corruption_count, int)
        assert isinstance(corruption_total, int)
        assert isinstance(net_count, int)
        assert isinstance(net_total, int)
        if len({correction_total, corruption_total, net_total}) != 1:
            raise ValueError(
                f"Trade-off metrics do not share one denominator for {key}."
            )
        if correction_count - corruption_count != net_count:
            raise ValueError(
                f"Trade-off counts violate corrected - corrupted = net gain for {key}."
            )
        denominator = correction_total
        if denominator <= 0:
            raise ValueError(f"Trade-off denominator must be positive for {key}.")
        denominators.add(denominator)
        systems.append(
            {
                "key": key,
                "label": TRADEOFF_LABELS[key],
                "correction_count": correction_count,
                "correction_rate": correction_count / denominator,
                "corruption_count": corruption_count,
                "corruption_rate": corruption_count / denominator,
                "net_repair_gain_count": net_count,
                "net_repair_gain_rate": net_count / denominator,
                "denominator": denominator,
            }
        )

    if len(denominators) != 1:
        raise ValueError(
            "Trade-off systems do not share one eligible A/B/C denominator: "
            + ", ".join(str(value) for value in sorted(denominators))
        )
    return {
        "denominator": next(iter(denominators)),
        "systems": systems,
        "axes": {
            "x": "corruption_rate",
            "y": "correction_rate",
            "rate_denominator": "eligible held-out A/B/C rows",
        },
    }


def read_repair_strategy_rows(repair_dir: Path) -> list[dict[str, Any]]:
    ensure_dir(repair_dir, "repair-strategy ablation directory")
    table_path = repair_dir / "repair_strategy_ablation_table.json"

    if table_path.is_file():
        data = json.loads(table_path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError(f"Expected list in {table_path}")
        rows = [row for row in data if isinstance(row, dict)]
        found = {row.get("strategy") for row in rows}
        if all(key in found for key in STRATEGY_KEYS) and all(
            "net_repair_gain_rate" in row for row in rows
        ):
            rows = sorted(rows, key=lambda row: STRATEGY_KEYS.index(row["strategy"]))
            ensure_common_movement_denominator(rows, "Repair-strategy visualization")
            return rows

    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for strategy in STRATEGY_KEYS:
        metrics_path = repair_dir / f"{strategy}_final_metrics.json"
        asa_path = repair_dir / f"{strategy}_asa_metrics.json"
        if not metrics_path.is_file():
            missing.append(str(metrics_path))
            continue
        if not asa_path.is_file():
            missing.append(str(asa_path))
            continue

        metrics = read_json(metrics_path)
        asa_metrics = read_json(asa_path)
        rates = repair_rates(metrics)
        asa_after = asa_set_metrics(asa_metrics)
        rows.append(
            {
                "strategy": strategy,
                "label": STRATEGY_LABELS[strategy],
                "correction_count": rates["correction_count"],
                "correction_total": rates["correction_total"],
                "correction_rate": rates["correction"],
                "corruption_count": rates["corruption_count"],
                "corruption_total": rates["corruption_total"],
                "corruption_rate": rates["corruption"],
                "net_repair_gain_count": rates["net_gain_count"],
                "net_repair_gain_total": rates["net_gain_total"],
                "net_repair_gain_rate": rates["net_gain_rate"],
                "asa_strict_accuracy": asa_after.get("asa_strict_accuracy"),
                "fper": asa_after.get("fper"),
            }
        )

    if missing:
        formatted = "\n".join(f"- {path}" for path in missing)
        raise FileNotFoundError(f"Missing repair strategy metric files:\n{formatted}")
    if len(rows) != len(STRATEGY_KEYS):
        raise ValueError("Repair strategy ablation outputs are incomplete.")
    ensure_common_movement_denominator(rows, "Repair-strategy visualization")
    return rows


def draw_legend(items: list[tuple[str, str]], x: float, y: float) -> list[str]:
    lines: list[str] = []
    cursor = x
    for label, color in items:
        lines.append(f'<rect x="{cursor:.1f}" y="{y - 10:.1f}" width="14" height="14" fill="{color}"/>')
        lines.append(f'<text class="small" x="{cursor + 20:.1f}" y="{y + 2:.1f}">{escape(label)}</text>')
        cursor += 150
    return lines


def render_main_accuracy_chart(rows: list[dict[str, Any]], output_path: Path, width: int) -> None:
    height = 620
    left = 150
    right = 50
    top = 105
    bottom = 140
    plot_w = width - left - right
    plot_h = height - top - bottom
    baseline_y = top + plot_h
    group_w = plot_w / len(rows)
    bar_w = min(52, group_w / 5)

    lines = svg_header(width, height)
    lines.extend(
        [
            '<text class="title" x="40" y="42">Main System Comparison</text>',
            '<text class="subtitle" x="40" y="66">Execution accuracy and accounting-semantic accuracy after primary metric filtering.</text>',
            *draw_legend(
                [
                    ("EX Accuracy", COLORS["ex"]),
                    ("ASA", COLORS["asa_strict"]),
                ],
                40,
                92,
            ),
        ]
    )

    for tick in range(0, 101, 20):
        y = baseline_y - plot_h * tick / 100
        lines.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" '
            f'stroke="{COLORS["grid"]}" stroke-width="1"/>'
        )
        lines.append(f'<text class="tick" x="{left - 38}" y="{y + 4:.1f}">{tick}%</text>')

    lines.append(
        f'<line x1="{left}" y1="{baseline_y}" x2="{width - right}" y2="{baseline_y}" '
        f'stroke="{COLORS["axis"]}" stroke-width="1.4"/>'
    )
    lines.append(
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{baseline_y}" '
        f'stroke="{COLORS["axis"]}" stroke-width="1.4"/>'
    )

    metrics = [
        ("ex_accuracy", COLORS["ex"]),
        ("asa_strict_accuracy", COLORS["asa_strict"]),
    ]
    for idx, row in enumerate(rows):
        center = left + group_w * idx + group_w / 2
        for metric_idx, (field, color) in enumerate(metrics):
            value = value_or_zero(row.get(field))
            bar_h = plot_h * clamp(value, 0, 1)
            x = center + (metric_idx - 1) * (bar_w + 8) - bar_w / 2
            y = baseline_y - bar_h
            lines.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" '
                f'fill="{color}"/>'
            )
            lines.append(
                f'<text class="tick" text-anchor="middle" x="{x + bar_w / 2:.1f}" '
                f'y="{y - 6:.1f}">{escape(pct(row.get(field)))}</text>'
            )

        label = str(row["label"]).replace("Generator + ", "+ ")
        words = label.split()
        line1 = " ".join(words[:3])
        line2 = " ".join(words[3:])
        lines.append(f'<text class="label" text-anchor="middle" x="{center:.1f}" y="{baseline_y + 28:.1f}">{escape(line1)}</text>')
        if line2:
            lines.append(f'<text class="label" text-anchor="middle" x="{center:.1f}" y="{baseline_y + 46:.1f}">{escape(line2)}</text>')

    lines.append("</svg>")
    write_svg(output_path, lines)


def render_repair_safety_chart(rows: list[dict[str, Any]], output_path: Path, width: int) -> None:
    height = 540
    left = 300
    right = 70
    top = 90
    bottom = 70
    plot_w = width - left - right
    center_x = left + plot_w / 2
    max_rate = max(
        [value_or_zero(row.get("correction_rate")) for row in rows]
        + [value_or_zero(row.get("corruption_rate")) for row in rows]
        + [0.05]
    )
    axis_max = min(1.0, max(0.1, math.ceil(max_rate * 10) / 10))
    scale = (plot_w / 2) / axis_max
    row_gap = (height - top - bottom) / len(rows)
    bar_h = 34

    lines = svg_header(width, height)
    lines.extend(
        [
            '<text class="title" x="40" y="42">End-to-End Repair Movement</text>',
            '<text class="subtitle" x="40" y="66">All rates use the shared eligible A/B/C evaluation population; correction is right of zero and corruption is left.</text>',
            *draw_legend(
                [
                    ("Correction / N", COLORS["correction"]),
                    ("Corruption / N", COLORS["corruption"]),
                ],
                40,
                92,
            ),
        ]
    )

    lines.append(
        f'<line x1="{center_x:.1f}" y1="{top - 10}" x2="{center_x:.1f}" y2="{height - bottom + 10}" '
        f'stroke="{COLORS["axis"]}" stroke-width="1.5"/>'
    )
    for tick in (-axis_max, -axis_max / 2, 0, axis_max / 2, axis_max):
        x = center_x + tick * scale
        lines.append(
            f'<line x1="{x:.1f}" y1="{top - 6}" x2="{x:.1f}" y2="{height - bottom + 6}" '
            f'stroke="{COLORS["grid"]}" stroke-width="1"/>'
        )
        lines.append(
            f'<text class="tick" text-anchor="middle" x="{x:.1f}" y="{height - bottom + 32:.1f}">'
            f'{abs(tick) * 100:.0f}%</text>'
        )

    for idx, row in enumerate(rows):
        y_mid = top + row_gap * idx + row_gap / 2
        correction = value_or_zero(row.get("correction_rate"))
        corruption = value_or_zero(row.get("corruption_rate"))
        corr_w = correction * scale
        corrupt_w = corruption * scale
        lines.append(f'<text class="label" text-anchor="end" x="{left - 18}" y="{y_mid + 5:.1f}">{escape(str(row["label"]))}</text>')
        lines.append(
            f'<rect x="{center_x:.1f}" y="{y_mid - bar_h / 2:.1f}" width="{corr_w:.1f}" '
            f'height="{bar_h}" fill="{COLORS["correction"]}"/>'
        )
        lines.append(
            f'<rect x="{center_x - corrupt_w:.1f}" y="{y_mid - bar_h / 2:.1f}" width="{corrupt_w:.1f}" '
            f'height="{bar_h}" fill="{COLORS["corruption"]}"/>'
        )
        if row.get("kind") == "generator":
            lines.append(f'<text class="small" x="{center_x + 8:.1f}" y="{y_mid + 5:.1f}">n/a</text>')
        else:
            lines.append(f'<text class="small" x="{center_x + corr_w + 8:.1f}" y="{y_mid + 5:.1f}">{escape(pct(row.get("correction_rate")))}</text>')
            lines.append(f'<text class="small" text-anchor="end" x="{center_x - corrupt_w - 8:.1f}" y="{y_mid + 5:.1f}">{escape(pct(row.get("corruption_rate")))}</text>')
            net_gain = value_or_zero(row.get("net_repair_gain_rate"))
            lines.append(f'<text class="small" x="{width - right:.1f}" y="{y_mid + 5:.1f}" text-anchor="end">NRG {net_gain * 100:+.2f} pp</text>')

    lines.append("</svg>")
    write_svg(output_path, lines)


def render_correction_corruption_tradeoff(
    data: dict[str, Any],
    output_path: Path,
    width: int,
) -> None:
    height = 540
    left = 112
    right = 30
    top = 28
    bottom = 68
    plot_w = width - left - right
    plot_h = height - top - bottom
    baseline_y = top + plot_h
    systems = list(data["systems"])

    max_corruption = max(float(row["corruption_rate"]) for row in systems)
    max_correction = max(float(row["correction_rate"]) for row in systems)

    def axis_max(value: float, step: float, minimum: float) -> float:
        return max(minimum, math.ceil(value * 1.12 / step) * step)

    x_max = (
        0.04
        if max_corruption <= 0.04
        else axis_max(max_corruption, 0.01, 0.01)
    )
    y_max = axis_max(max_correction, 0.002, 0.002)

    def point(corruption: float, correction: float) -> tuple[float, float]:
        x = left + clamp(corruption / x_max, 0, 1) * plot_w
        y = baseline_y - clamp(correction / y_max, 0, 1) * plot_h
        return x, y

    lines = svg_header(width, height)

    x_tick_steps = 4 if x_max == 0.04 else 5
    for step_index in range(x_tick_steps + 1):
        x_rate = x_max * step_index / x_tick_steps
        x = left + plot_w * step_index / x_tick_steps
        if 0 < step_index < x_tick_steps:
            lines.append(
                f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{baseline_y}" '
                f'stroke="{COLORS["grid"]}" stroke-width="0.8"/>'
            )
        lines.append(
            f'<text class="tick" text-anchor="middle" x="{x:.1f}" '
            f'y="{baseline_y + 30:.1f}" '
            'style="font-size: 23px; fill: #000000; font-weight: 400;">'
            f'{x_rate * 100:.1f}%</text>'
        )

    y_tick_steps = 5
    for step_index in range(y_tick_steps + 1):
        y_rate = y_max * step_index / y_tick_steps
        y = baseline_y - plot_h * step_index / y_tick_steps
        if 0 < step_index < y_tick_steps:
            lines.append(
                f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" '
                f'stroke="{COLORS["grid"]}" stroke-width="0.8"/>'
            )
        lines.append(
            f'<text class="tick" text-anchor="end" x="{left - 12}" '
            f'y="{y + 8:.1f}" '
            'style="font-size: 23px; fill: #000000; font-weight: 400;">'
            f'{y_rate * 100:.1f}%</text>'
        )

    lines.append(
        f'<line x1="{left}" y1="{baseline_y}" x2="{left + plot_w}" '
        f'y2="{baseline_y}" stroke="{COLORS["axis"]}" stroke-width="1.2"/>'
    )
    lines.append(
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{baseline_y}" '
        f'stroke="{COLORS["axis"]}" stroke-width="1.2"/>'
    )

    zero_gain_endpoint = min(x_max, y_max)
    zero_start = point(0.0, 0.0)
    zero_end = point(zero_gain_endpoint, zero_gain_endpoint)
    lines.append(
        f'<line x1="{zero_start[0]:.1f}" y1="{zero_start[1]:.1f}" '
        f'x2="{zero_end[0]:.1f}" y2="{zero_end[1]:.1f}" '
        f'stroke="#71717a" stroke-width="1.3" stroke-dasharray="7 5"/>'
    )
    lines.append(
        f'<text class="label" x="{zero_end[0] + 12:.1f}" '
        f'y="{zero_end[1] + 22:.1f}" font-weight="700" '
        'style="font-size: 22px;">'
        'Correction = Corruption</text>'
    )

    lines.append(
        f'<text class="label" text-anchor="middle" x="{left + plot_w / 2:.1f}" '
        f'y="{height - 8}" font-weight="700" style="font-size: 27px;">'
        'Corruption rate (%)</text>'
    )
    lines.append(
        f'<text class="label" transform="translate(27 {top + plot_h / 2:.1f}) '
        'rotate(-90)" text-anchor="middle" font-weight="700" '
        'style="font-size: 27px;">Correction rate (%)</text>'
    )

    adir_label_offset = (20.0, -12.0, "start")
    for row in systems:
        key = str(row["key"])
        x, y = point(
            float(row["corruption_rate"]),
            float(row["correction_rate"]),
        )
        color = TRADEOFF_COLORS[key]
        radius = 11 if key == "finverisql_full" else 7
        lines.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius}" fill="{color}" '
            f'fill-opacity="0.90" stroke="{blend_hex(color, "#000000", 0.22)}" '
            'stroke-width="1.2"/>'
        )

        net_gain_pp = float(row["net_repair_gain_rate"]) * 100
        sign = "+" if net_gain_pp >= 0 else "−"
        text_style = (
            "paint-order: stroke; stroke: white; stroke-width: 4px; "
            "stroke-linejoin: round;"
        )
        if key == "generic_self_refine":
            direct_label = f"Generic self-refine ({sign}{abs(net_gain_pp):.2f} pp)"
            lines.append(
                f'<text class="label" text-anchor="end" x="{x - 15:.1f}" '
                f'y="{y + 8:.1f}" style="font-size: 23px; {text_style}">'
                f"{escape(direct_label)}</text>"
            )
            continue
        if key == "generic_execution_guided_refine":
            direct_label = f"Execution-guided ({sign}{abs(net_gain_pp):.2f} pp)"
            lines.append(
                f'<text class="label" text-anchor="start" x="{x + 16:.1f}" '
                f'y="{y - 10:.1f}" style="font-size: 23px; {text_style}">'
                f"{escape(direct_label)}</text>"
            )
            continue

        offset_x, offset_y, anchor = adir_label_offset
        label_x = x + offset_x
        label_y = y + offset_y
        direct_label = f"ADiR ({sign}{abs(net_gain_pp):.2f} pp)"
        lines.append(
            f'<text class="label" text-anchor="{anchor}" x="{label_x:.1f}" '
            f'y="{label_y:.1f}" font-weight="700" '
            f'style="font-size: 25px; {text_style}">'
            f"{escape(direct_label)}</text>"
        )

    lines.append("</svg>")
    write_svg(output_path, lines)


def render_pareto_chart(rows: list[dict[str, Any]], output_path: Path, width: int) -> None:
    height = 620
    left = 110
    right = 80
    top = 105
    bottom = 95
    plot_w = width - left - right
    plot_h = height - top - bottom
    max_x = max([value_or_zero(row.get("corruption_rate")) for row in rows] + [0.05])
    max_y = max([value_or_zero(row.get("correction_rate")) for row in rows] + [0.05])
    axis_x = min(1.0, max(0.1, math.ceil(max_x * 10) / 10))
    axis_y = min(1.0, max(0.1, math.ceil(max_y * 10) / 10))

    lines = svg_header(width, height)
    lines.extend(
        [
            '<text class="title" x="40" y="42">Isolated Repair Strategy Comparison</text>',
            '<text class="subtitle" x="40" y="66">Fixed verifier outputs; only the repair generation strategy changes. Marker size encodes ASA strict accuracy.</text>',
        ]
    )

    for step in range(0, 6):
        x_value = axis_x * step / 5
        x = left + plot_w * step / 5
        y_value = axis_y * step / 5
        y = top + plot_h - plot_h * step / 5
        lines.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + plot_h}" stroke="{COLORS["grid"]}"/>')
        lines.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="{COLORS["grid"]}"/>')
        lines.append(f'<text class="tick" text-anchor="middle" x="{x:.1f}" y="{top + plot_h + 26:.1f}">{x_value * 100:.0f}%</text>')
        lines.append(f'<text class="tick" text-anchor="end" x="{left - 12}" y="{y + 4:.1f}">{y_value * 100:.0f}%</text>')

    lines.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="{COLORS["axis"]}" stroke-width="1.5"/>')
    lines.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="{COLORS["axis"]}" stroke-width="1.5"/>')
    lines.append(f'<text class="label" text-anchor="middle" x="{left + plot_w / 2:.1f}" y="{height - 32}">Corruption (% of eligible A/B/C rows)</text>')
    lines.append(f'<text class="label" transform="translate(34 {top + plot_h / 2:.1f}) rotate(-90)" text-anchor="middle">Correction (% of eligible A/B/C rows)</text>')

    for row in rows:
        x_rate = value_or_zero(row.get("corruption_rate"))
        y_rate = value_or_zero(row.get("correction_rate"))
        asa = value_or_zero(row.get("asa_strict_accuracy"))
        x = left + (x_rate / axis_x) * plot_w if axis_x else left
        y = top + plot_h - (y_rate / axis_y) * plot_h if axis_y else top + plot_h
        radius = 8 + asa * 18
        lines.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{COLORS["point"]}" '
            'fill-opacity="0.72" stroke="#4c1d95" stroke-width="1.5"/>'
        )
        lines.append(f'<text class="label" x="{x + radius + 8:.1f}" y="{y - 4:.1f}">{escape(str(row["label"]))}</text>')
        lines.append(
            f'<text class="small" x="{x + radius + 8:.1f}" y="{y + 14:.1f}">'
            f'corr {escape(pct(row.get("correction_rate")))}, corrupt {escape(pct(row.get("corruption_rate")))}, NRG {value_or_zero(row.get("net_repair_gain_rate")) * 100:+.2f} pp, ASA {escape(pct(row.get("asa_strict_accuracy")))}</text>'
        )

    lines.append("</svg>")
    write_svg(output_path, lines)


def read_diagnostic_artifacts(run_root: Path) -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    filtered_path = run_root / "filtered_inputs" / "finverisql_full.jsonl"
    baseline_path = run_root / "metrics" / "baseline" / "evaluated.jsonl"
    final_path = run_root / "metrics" / "finverisql_full" / "final_evaluated.jsonl"
    ensure_file(filtered_path, "held-out ADiR input rows")
    ensure_file(baseline_path, "held-out baseline evaluated rows")
    ensure_file(final_path, "held-out ADiR final evaluated rows")
    finverisql_rows = read_jsonl(filtered_path)
    baseline_rows = rows_by_question_id(read_jsonl(baseline_path), "baseline evaluated rows")
    final_rows = rows_by_question_id(read_jsonl(final_path), "ADiR final evaluated rows")
    return finverisql_rows, baseline_rows, final_rows


def build_dimension_breakdown(
    finverisql_rows: list[dict[str, Any]],
    baseline_rows: dict[str, dict[str, Any]],
    final_rows: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    counts: dict[str | None, dict[str, int]] = {
        key: {field: 0 for field, _, _ in MOVEMENT_FIELDS} | {"total": 0}
        for key in DIMENSION_ORDER
    }
    for row in finverisql_rows:
        question_id = row.get("question_id")
        if question_id not in baseline_rows or question_id not in final_rows:
            raise ValueError(f"Missing evaluated rows for question_id={question_id}")
        mismatch = primary_mismatch_type(row)
        if mismatch not in counts:
            mismatch = None

        before = execution_pass(baseline_rows[question_id])
        after = execution_pass(final_rows[question_id])
        if before and after:
            movement = "preserved_correct"
        elif before and not after:
            movement = "corrupted"
        elif not before and after:
            movement = "corrected"
        else:
            movement = "remains_wrong"
        counts[mismatch][movement] += 1
        counts[mismatch]["total"] += 1

    return [
        {
            "dimension": key,
            "label": DIMENSION_LABELS[key],
            **counts[key],
        }
        for key in DIMENSION_ORDER
    ]


def build_edit_scope_heatmap(finverisql_rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {
        dimension: {
            "proposed_denominator": 0,
            "accepted_denominator": 0,
            "proposed": {clause: 0 for clause in EDIT_CLAUSES},
            "accepted": {clause: 0 for clause in EDIT_CLAUSES},
        }
        for dimension in EDIT_SCOPE_DIMENSIONS
    }

    for row in finverisql_rows:
        attempts = row.get("repair_attempt_sequence") or []
        if not isinstance(attempts, list):
            raise ValueError(
                f"repair_attempt_sequence must be a list for question_id={row.get('question_id')}"
            )
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            dimension = attempt.get("repair_mode")
            if dimension not in counts:
                continue
            scope_status = attempt.get("scope_check_status")
            if scope_status not in {"accepted", "rejected"}:
                continue
            changed = attempt.get("clause_change_summary") or []
            if not isinstance(changed, list):
                raise ValueError(
                    "clause_change_summary must be a list for "
                    f"question_id={row.get('question_id')}"
                )
            unknown = sorted({str(clause) for clause in changed} - set(EDIT_CLAUSES))
            if unknown:
                raise ValueError(
                    f"Unknown changed clauses for question_id={row.get('question_id')}: "
                    + ", ".join(unknown)
                )

            changed_set = {str(clause) for clause in changed}
            dimension_counts = counts[dimension]
            dimension_counts["proposed_denominator"] += 1
            for clause in changed_set:
                dimension_counts["proposed"][clause] += 1

            if scope_status == "accepted":
                dimension_counts["accepted_denominator"] += 1
                for clause in changed_set:
                    dimension_counts["accepted"][clause] += 1

    dimensions: list[dict[str, Any]] = []
    for dimension in EDIT_SCOPE_DIMENSIONS:
        dimension_counts = counts[dimension]
        proposed_denominator = dimension_counts["proposed_denominator"]
        accepted_denominator = dimension_counts["accepted_denominator"]
        dimensions.append(
            {
                "dimension": dimension,
                "label": DIMENSION_LABELS[dimension],
                "allowed_clauses": sorted(EDIT_SCOPE_ALLOWED[dimension]),
                "disallowed_clauses": [
                    clause
                    for clause in EDIT_CLAUSES
                    if clause not in EDIT_SCOPE_ALLOWED[dimension]
                ],
                "proposed_denominator": proposed_denominator,
                "accepted_denominator": accepted_denominator,
                "rejected_denominator": proposed_denominator - accepted_denominator,
                "proposed": {
                    clause: {
                        "count": dimension_counts["proposed"][clause],
                        "rate": (
                            dimension_counts["proposed"][clause] / proposed_denominator
                            if proposed_denominator
                            else None
                        ),
                    }
                    for clause in EDIT_CLAUSES
                },
                "accepted": {
                    clause: {
                        "count": dimension_counts["accepted"][clause],
                        "rate": (
                            dimension_counts["accepted"][clause] / accepted_denominator
                            if accepted_denominator
                            else None
                        ),
                    }
                    for clause in EDIT_CLAUSES
                },
            }
        )

    return {
        "clauses": EDIT_CLAUSES,
        "dimensions": dimensions,
        "denominator_definition": {
            "proposed": (
                "All D1-D3 repair attempts inspected by the deterministic scope "
                "validator, including accepted and rejected attempts."
            ),
            "accepted": "All D1-D3 repair attempts accepted by the scope validator.",
        },
    }


def verifier_sankey_bucket(row: dict[str, Any]) -> str:
    verification = verification_from_row(row)
    answers_question = verification.get(
        "stage2_answers_question",
        verification.get("answers_question"),
    )
    mismatch = primary_mismatch_type(row)

    if answers_question is True:
        return "verifier_accepted"
    if answers_question is False:
        if mismatch in {
            "financial_object_error",
            "financial_measure_error",
            "computation_logic_error",
            "non_executable_error",
        }:
            return mismatch
        raise ValueError(
            "Rejected verifier row has no supported primary mismatch type for "
            f"question_id={row.get('question_id')}: {mismatch}"
        )
    return "abstained"


def execution_movement(before: dict[str, Any], after: dict[str, Any]) -> str:
    before_correct = execution_pass(before)
    after_correct = execution_pass(after)
    if before_correct and after_correct:
        return "preserved_correct"
    if before_correct and not after_correct:
        return "corrupted"
    if not before_correct and after_correct:
        return "corrected"
    return "remains_wrong"


def build_error_resolution_sankey(
    finverisql_rows: list[dict[str, Any]],
    baseline_rows: dict[str, dict[str, Any]],
    final_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    middle_counts = {key: 0 for key in SANKEY_MIDDLE_ORDER}
    outcome_counts = {key: 0 for key in SANKEY_OUTCOME_ORDER}
    movement_counts = {
        middle: {outcome: 0 for outcome in SANKEY_OUTCOME_ORDER}
        for middle in SANKEY_MIDDLE_ORDER
    }
    seen: set[str] = set()

    for row in finverisql_rows:
        question_id = row.get("question_id")
        if not isinstance(question_id, str) or not question_id:
            raise ValueError("ADiR row is missing question_id")
        if question_id in seen:
            raise ValueError(f"Duplicate ADiR question_id in Sankey input: {question_id}")
        seen.add(question_id)
        if question_id not in baseline_rows or question_id not in final_rows:
            raise ValueError(f"Missing evaluated rows for question_id={question_id}")

        middle = verifier_sankey_bucket(row)
        outcome = execution_movement(
            baseline_rows[question_id],
            final_rows[question_id],
        )
        middle_counts[middle] += 1
        outcome_counts[outcome] += 1
        movement_counts[middle][outcome] += 1

    total = len(finverisql_rows)
    if sum(middle_counts.values()) != total:
        raise ValueError("Sankey verifier counts do not conserve the input population.")
    if sum(outcome_counts.values()) != total:
        raise ValueError("Sankey outcome counts do not conserve the input population.")

    nodes = [
        {
            "id": "all_candidates",
            "stage": "input",
            "label": "All Portable Candidates",
            "count": total,
            "rate": 1.0 if total else None,
        }
    ]
    nodes.extend(
        {
            "id": key,
            "stage": "verifier",
            "label": SANKEY_MIDDLE_LABELS[key],
            "count": middle_counts[key],
            "rate": middle_counts[key] / total if total else None,
        }
        for key in SANKEY_MIDDLE_ORDER
    )
    nodes.extend(
        {
            "id": key,
            "stage": "outcome",
            "label": SANKEY_OUTCOME_LABELS[key],
            "count": outcome_counts[key],
            "rate": outcome_counts[key] / total if total else None,
        }
        for key in SANKEY_OUTCOME_ORDER
    )

    links = [
        {
            "source": "all_candidates",
            "target": middle,
            "count": middle_counts[middle],
            "rate": middle_counts[middle] / total if total else None,
        }
        for middle in SANKEY_MIDDLE_ORDER
    ]
    links.extend(
        {
            "source": middle,
            "target": outcome,
            "count": movement_counts[middle][outcome],
            "rate": movement_counts[middle][outcome] / total if total else None,
        }
        for middle in SANKEY_MIDDLE_ORDER
        for outcome in SANKEY_OUTCOME_ORDER
        if movement_counts[middle][outcome]
    )

    return {
        "total": total,
        "metric": "execution_match",
        "nodes": nodes,
        "links": links,
        "middle_counts": middle_counts,
        "outcome_counts": outcome_counts,
        "movement_counts": movement_counts,
    }


def parse_stage_verdicts(verification: dict[str, Any]) -> dict[int, dict[str, Any]]:
    raw_output = verification.get("raw_output")
    if not isinstance(raw_output, str) or not raw_output.strip():
        return {}
    try:
        raw_steps = json.loads(raw_output)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw_steps, list):
        return {}

    verdicts: dict[int, dict[str, Any]] = {}
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        step = item.get("step")
        raw = item.get("raw")
        if not isinstance(step, str) or not step.startswith("stage2_"):
            continue
        try:
            depth = int(step.rsplit("_", 1)[1])
        except ValueError:
            continue
        if not isinstance(raw, str):
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            verdicts[depth] = parsed
    return verdicts


def expected_rejection(row: dict[str, Any]) -> bool | None:
    group = row.get("evaluation_group")
    if group == "A_correct_executable":
        return False
    if group in {"B_wrong_executable", "C_non_executable"}:
        return True
    return None


def build_probe_convergence(finverisql_rows: list[dict[str, Any]], max_depth: int = 3) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for depth in range(max_depth + 1):
        total = 0
        correct = 0
        tp = fp = fn = 0

        for row in finverisql_rows:
            truth_reject = expected_rejection(row)
            if truth_reject is None:
                continue
            verdicts = parse_stage_verdicts(verification_from_row(row))
            available_depths = [candidate for candidate in verdicts if candidate <= depth]
            if not available_depths:
                continue
            verdict = verdicts[max(available_depths)]
            answers_question = verdict.get("answers_question")
            if answers_question not in {True, False}:
                continue

            predicted_reject = answers_question is False
            total += 1
            if predicted_reject == truth_reject:
                correct += 1
            if predicted_reject and truth_reject:
                tp += 1
            elif predicted_reject and not truth_reject:
                fp += 1
            elif not predicted_reject and truth_reject:
                fn += 1

        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall
            else None
        )
        rows.append(
            {
                "probe_depth": depth,
                "total": total,
                "verification_accuracy": correct / total if total else None,
                "rejection_precision": precision,
                "rejection_recall": recall,
                "rejection_f1": f1,
            }
        )
    return rows


def build_case_study(
    finverisql_rows: list[dict[str, Any]],
    baseline_rows: dict[str, dict[str, Any]],
    final_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    preferred = "booksql_071296"
    candidates = sorted(finverisql_rows, key=lambda row: (row.get("question_id") != preferred, row.get("question_id") or ""))
    for row in candidates:
        question_id = row.get("question_id")
        if question_id not in baseline_rows or question_id not in final_rows:
            continue
        if execution_pass(baseline_rows[question_id]) or not execution_pass(final_rows[question_id]):
            continue
        if row.get("final_sql_source") == "original_generated_sql":
            continue
        mismatch = row.get("initial_mismatch_type") or primary_mismatch_type(row)
        if not mismatch:
            continue
        verification = verification_from_row(row)
        return {
            "question_id": question_id,
            "question": row.get("question"),
            "mismatch_type": mismatch,
            "mismatch_label": DIMENSION_LABELS.get(mismatch, str(mismatch)),
            "failed_evidence": verification.get("stage2_failed_evidence") or verification.get("failed_evidence") or [],
            "repair_hint": verification.get("repair_hint"),
            "original_sql": row.get("original_generated_sql") or row.get("generated_sql"),
            "repaired_sql": row.get("final_repaired_sql") or row.get("repaired_sql"),
            "allowed_clause_changes": row.get("allowed_clause_changes") or [],
            "disallowed_clause_changes": row.get("disallowed_clause_changes") or [],
            "clause_change_summary": row.get("clause_change_summary") or [],
            "final_sql_source": row.get("final_sql_source"),
        }
    raise ValueError("Could not find a corrected ADiR case study row in held-out artifacts.")


def render_dimension_breakdown(rows: list[dict[str, Any]], output_path: Path, width: int) -> None:
    height = 560
    left = 210
    right = 80
    top = 120
    bottom = 70
    plot_w = width - left - right
    row_gap = (height - top - bottom) / len(rows)
    bar_h = 34

    lines = svg_header(width, height)
    lines.extend(
        [
            '<text class="title" x="40" y="42">ADiR Diagnostic Failure-Mode Breakdown</text>',
            '<text class="subtitle" x="40" y="66">Verifier primary mismatch dimension grouped by final EX movement on the held-out complement.</text>',
            *draw_legend([(label, color) for _, label, color in MOVEMENT_FIELDS], 40, 98),
        ]
    )

    total_rows = sum(int(row.get("total") or 0) for row in rows)
    for idx, row in enumerate(rows):
        y = top + idx * row_gap + row_gap / 2
        total = int(row.get("total") or 0)
        x = left
        lines.append(f'<text class="label" text-anchor="end" x="{left - 18}" y="{y + 5:.1f}">{escape(str(row["label"]))}</text>')
        if total == 0:
            lines.append(
                f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" '
                f'stroke="{COLORS["grid"]}" stroke-width="1"/>'
            )
            lines.append(f'<text class="small" x="{left + 8}" y="{y - 8:.1f}">0</text>')
            continue
        for field, label, color in MOVEMENT_FIELDS:
            count = int(row.get(field) or 0)
            segment_w = plot_w * count / total_rows if total_rows else 0
            if segment_w:
                lines.append(
                    f'<rect x="{x:.1f}" y="{y - bar_h / 2:.1f}" width="{segment_w:.1f}" '
                    f'height="{bar_h}" fill="{color}"/>'
                )
                if segment_w > 34:
                    lines.append(
                        f'<text class="tick" text-anchor="middle" x="{x + segment_w / 2:.1f}" '
                        f'y="{y + 4:.1f}" style="fill: white;">{count}</text>'
                    )
            x += segment_w
        lines.append(f'<text class="small" x="{left + plot_w + 12:.1f}" y="{y + 5:.1f}">n={total}</text>')

    lines.append(
        f'<text class="small" x="{left}" y="{height - 28}">Bar length is proportional to count over all held-out ADiR rows (N={total_rows}).</text>'
    )
    lines.append("</svg>")
    write_svg(output_path, lines)


def render_edit_scope_heatmap(
    data: dict[str, Any],
    output_path: Path,
    width: int,
) -> None:
    height = 790
    left = 225
    right = 45
    top = 160
    cell_h = 52
    panel_gap = 118
    clauses = list(data["clauses"])
    dimensions = list(data["dimensions"])
    cell_w = (width - left - right) / len(clauses)
    heat_start = "#f8fafc"
    heat_end = "#1d4ed8"

    lines = svg_header(width, height)
    lines.extend(
        [
            '<text class="title" x="40" y="42">Deterministic Edit-Scope Enforcement</text>',
            '<text class="subtitle" x="40" y="66">Clause edits proposed by the repair model versus edits accepted by the deterministic scope validator.</text>',
            '<rect x="40" y="86" width="16" height="16" fill="white" stroke="#dc2626" stroke-width="2"/>',
            '<text class="small" x="64" y="99">Policy-disallowed clause</text>',
            '<rect x="260" y="86" width="16" height="16" fill="#dbeafe" stroke="#a1a1aa"/>',
            '<text class="small" x="284" y="99">Policy-allowed clause</text>',
            '<text class="small" x="475" y="99">Cell: rate (count / dimension attempts)</text>',
        ]
    )

    def render_panel(panel_key: str, title: str, panel_top: float) -> None:
        lines.append(
            f'<text class="label" x="40" y="{panel_top - 34:.1f}" '
            f'font-weight="700">{escape(title)}</text>'
        )
        for clause_index, clause in enumerate(clauses):
            x = left + clause_index * cell_w + cell_w / 2
            lines.append(
                f'<text class="tick" text-anchor="middle" x="{x:.1f}" '
                f'y="{panel_top - 12:.1f}">{escape(clause)}</text>'
            )

        for dimension_index, dimension in enumerate(dimensions):
            y = panel_top + dimension_index * cell_h
            denominator = int(dimension[f"{panel_key}_denominator"])
            lines.append(
                f'<text class="label" text-anchor="end" x="{left - 16}" '
                f'y="{y + 22:.1f}">{escape(str(dimension["label"]))}</text>'
            )
            lines.append(
                f'<text class="tick" text-anchor="end" x="{left - 16}" '
                f'y="{y + 39:.1f}">n={denominator}</text>'
            )
            allowed = set(dimension["allowed_clauses"])
            for clause_index, clause in enumerate(clauses):
                x = left + clause_index * cell_w
                cell = dimension[panel_key][clause]
                rate = cell["rate"]
                count = int(cell["count"])
                fill = "#f4f4f5" if rate is None else blend_hex(
                    heat_start,
                    heat_end,
                    math.sqrt(float(rate)),
                )
                disallowed = clause not in allowed
                stroke = "#dc2626" if disallowed else "#a1a1aa"
                stroke_width = 2 if disallowed else 1
                lines.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_w:.1f}" '
                    f'height="{cell_h:.1f}" fill="{fill}" stroke="{stroke}" '
                    f'stroke-width="{stroke_width}"/>'
                )
                if rate is None:
                    label = "N/A"
                    detail = "no attempts"
                    text_color = COLORS["muted"]
                else:
                    label = f"{float(rate) * 100:.1f}%"
                    detail = f"{count}/{denominator}"
                    text_color = "white" if float(rate) >= 0.20 else COLORS["text"]
                lines.append(
                    f'<text class="label" text-anchor="middle" '
                    f'x="{x + cell_w / 2:.1f}" y="{y + 21:.1f}" '
                    f'style="fill: {text_color};" font-weight="700">{label}</text>'
                )
                lines.append(
                    f'<text class="tick" text-anchor="middle" '
                    f'x="{x + cell_w / 2:.1f}" y="{y + 39:.1f}" '
                    f'style="fill: {text_color};">{detail}</text>'
                )

    render_panel(
        "proposed",
        "A. Proposed edits inspected by the scope validator",
        top,
    )
    accepted_top = top + len(dimensions) * cell_h + panel_gap
    render_panel(
        "accepted",
        "B. Edits accepted by the scope validator",
        accepted_top,
    )

    legend_y = height - 55
    legend_x = left
    legend_w = 280
    steps = 28
    for step in range(steps):
        ratio = step / (steps - 1)
        lines.append(
            f'<rect x="{legend_x + step * legend_w / steps:.1f}" y="{legend_y:.1f}" '
            f'width="{legend_w / steps + 0.5:.1f}" height="12" '
            f'fill="{blend_hex(heat_start, heat_end, math.sqrt(ratio))}"/>'
        )
    lines.append(f'<text class="tick" x="{legend_x:.1f}" y="{legend_y + 29:.1f}">0%</text>')
    lines.append(
        f'<text class="tick" text-anchor="end" x="{legend_x + legend_w:.1f}" '
        f'y="{legend_y + 29:.1f}">100% of dimension attempts</text>'
    )
    lines.append(
        f'<text class="small" x="{legend_x + legend_w + 45:.1f}" '
        f'y="{legend_y + 11:.1f}">D1 is N/A because this held-out run contains no D1 repair attempts.</text>'
    )
    lines.append("</svg>")
    write_svg(output_path, lines)


def render_error_resolution_sankey(
    data: dict[str, Any],
    output_path: Path,
    width: int,
) -> None:
    width += 200
    height = 720
    plot_top = 72.0
    plot_bottom = 682.0
    plot_h = plot_bottom - plot_top
    node_w = 22.0
    node_gap = 14.0
    zero_h = 10.0
    total = int(data["total"])
    if total <= 0:
        raise ValueError("Cannot render a Sankey with an empty population.")

    nodes_by_id = {node["id"]: node for node in data["nodes"]}
    column_ids = {
        "input": ["all_candidates"],
        "verifier": SANKEY_MIDDLE_ORDER,
        "outcome": SANKEY_OUTCOME_ORDER,
    }
    column_x = {
        "input": 172.0,
        "verifier": width * 0.48,
        "outcome": width - 230.0,
    }
    maximum_overhead = max(
        node_gap * (len(ids) - 1)
        + zero_h * sum(1 for node_id in ids if int(nodes_by_id[node_id]["count"]) == 0)
        for ids in column_ids.values()
    )
    scale = (plot_h - maximum_overhead) / total

    layouts: dict[str, dict[str, float]] = {}
    for stage, ids in column_ids.items():
        content_h = (
            sum(
                int(nodes_by_id[node_id]["count"]) * scale
                if int(nodes_by_id[node_id]["count"])
                else zero_h
                for node_id in ids
            )
            + node_gap * (len(ids) - 1)
        )
        y = plot_top + (plot_h - content_h) / 2
        for node_id in ids:
            count = int(nodes_by_id[node_id]["count"])
            display_h = count * scale if count else zero_h
            layouts[node_id] = {
                "x": column_x[stage],
                "y": y,
                "height": display_h,
                "flow_height": count * scale,
            }
            y += display_h + node_gap

    label_positions: dict[str, float] = {}
    for ids in (SANKEY_MIDDLE_ORDER, SANKEY_OUTCOME_ORDER):
        minimum_gap = 44.0
        positions: list[float] = []
        for node_id in ids:
            layout = layouts[node_id]
            desired = layout["y"] + layout["height"] / 2
            positions.append(max(desired, positions[-1] + minimum_gap) if positions else desired)
        maximum_label_y = height - 34.0
        if positions and positions[-1] > maximum_label_y:
            positions[-1] = maximum_label_y
            for index in range(len(positions) - 2, -1, -1):
                positions[index] = min(positions[index], positions[index + 1] - minimum_gap)
        for node_id, position in zip(ids, positions):
            label_positions[node_id] = position

    lines = svg_header(width, height)
    lines.extend(
        [
            "<style>",
            ".sankey-label { font-size: 21px; font-weight: 700; paint-order: stroke; stroke: white; stroke-width: 5px; stroke-linejoin: round; }",
            ".sankey-value { font-size: 18px; fill: #52525b; paint-order: stroke; stroke: white; stroke-width: 5px; stroke-linejoin: round; }",
            "</style>",
            f'<text class="label" text-anchor="middle" x="{column_x["input"] + node_w / 2:.1f}" y="30" font-weight="700" style="font-size: 22px;">Input</text>',
            f'<text class="label" text-anchor="middle" x="{column_x["verifier"] + node_w / 2:.1f}" y="30" font-weight="700" style="font-size: 22px;">Verifier Decision</text>',
            f'<text class="label" text-anchor="middle" x="{column_x["outcome"] + node_w / 2:.1f}" y="30" font-weight="700" style="font-size: 22px;">Final EX Movement</text>',
        ]
    )

    def band_path(
        source_x: float,
        source_y: float,
        target_x: float,
        target_y: float,
        band_h: float,
    ) -> str:
        control_1 = source_x + (target_x - source_x) * 0.45
        control_2 = source_x + (target_x - source_x) * 0.55
        return (
            f"M {source_x:.2f} {source_y:.2f} "
            f"C {control_1:.2f} {source_y:.2f}, {control_2:.2f} {target_y:.2f}, "
            f"{target_x:.2f} {target_y:.2f} "
            f"L {target_x:.2f} {target_y + band_h:.2f} "
            f"C {control_2:.2f} {target_y + band_h:.2f}, "
            f"{control_1:.2f} {source_y + band_h:.2f}, "
            f"{source_x:.2f} {source_y + band_h:.2f} Z"
        )

    all_layout = layouts["all_candidates"]
    source_y = all_layout["y"]
    for middle in SANKEY_MIDDLE_ORDER:
        count = int(data["middle_counts"][middle])
        if not count:
            continue
        band_h = count * scale
        target = layouts[middle]
        lines.append(
            f'<path d="{band_path(all_layout["x"] + node_w, source_y, target["x"], target["y"], band_h)}" '
            f'fill="{SANKEY_MIDDLE_COLORS[middle]}" fill-opacity="0.22"/>'
        )
        source_y += band_h

    target_offsets = {
        outcome: layouts[outcome]["y"]
        for outcome in SANKEY_OUTCOME_ORDER
    }
    for middle in SANKEY_MIDDLE_ORDER:
        source_offset = layouts[middle]["y"]
        for outcome in SANKEY_OUTCOME_ORDER:
            count = int(data["movement_counts"][middle][outcome])
            if not count:
                continue
            band_h = count * scale
            target_y = target_offsets[outcome]
            lines.append(
                f'<path d="{band_path(layouts[middle]["x"] + node_w, source_offset, layouts[outcome]["x"], target_y, band_h)}" '
                f'fill="{SANKEY_OUTCOME_COLORS[outcome]}" fill-opacity="0.48"/>'
            )
            source_offset += band_h
            target_offsets[outcome] += band_h

    def draw_node(
        node_id: str,
        color: str,
        label_side: str,
    ) -> None:
        node = nodes_by_id[node_id]
        layout = layouts[node_id]
        count = int(node["count"])
        stroke = COLORS["corruption"] if node_id == "corrupted" else "#52525b"
        fill_opacity = "0.10" if count == 0 else "0.95"
        lines.append(
            f'<rect x="{layout["x"]:.1f}" y="{layout["y"]:.1f}" width="{node_w:.1f}" '
            f'height="{layout["height"]:.1f}" fill="{color}" fill-opacity="{fill_opacity}" '
            f'stroke="{stroke}" stroke-width="1.3"/>'
        )
        rate = count / total
        if label_side == "left":
            label_x = layout["x"] - 14
            anchor = "end"
        else:
            label_x = layout["x"] + node_w + 14
            anchor = "start"
        if node_id in label_positions:
            label_y = label_positions[node_id] - 3
            node_center_y = layout["y"] + layout["height"] / 2
            if abs(label_y - node_center_y) > 4:
                line_end_x = label_x - 4 if label_side == "right" else label_x + 4
                lines.append(
                    f'<line x1="{layout["x"] + node_w / 2:.1f}" y1="{node_center_y:.1f}" '
                    f'x2="{line_end_x:.1f}" y2="{label_y:.1f}" stroke="#a1a1aa" '
                    'stroke-width="0.7"/>'
                )
        else:
            label_y = layout["y"] + max(7.0, layout["height"] / 2) - 3
        if node_id == "all_candidates":
            label_y -= 12
            lines.append(
                f'<text class="sankey-label" text-anchor="{anchor}" x="{label_x:.1f}" '
                f'y="{label_y:.1f}">'
                f'<tspan x="{label_x:.1f}" dy="0">All Portable</tspan>'
                f'<tspan x="{label_x:.1f}" dy="25">Candidates</tspan>'
                "</text>"
            )
            value_y = label_y + 50
        else:
            lines.append(
                f'<text class="sankey-label" text-anchor="{anchor}" x="{label_x:.1f}" '
                f'y="{label_y:.1f}">{escape(str(node["label"]))}</text>'
            )
            value_y = label_y + 22
        lines.append(
            f'<text class="sankey-value" text-anchor="{anchor}" x="{label_x:.1f}" '
            f'y="{value_y:.1f}">{rate * 100:.2f}%</text>'
        )

    draw_node("all_candidates", "#334155", "left")
    for middle in SANKEY_MIDDLE_ORDER:
        draw_node(middle, SANKEY_MIDDLE_COLORS[middle], "right")
    for outcome in SANKEY_OUTCOME_ORDER:
        draw_node(outcome, SANKEY_OUTCOME_COLORS[outcome], "right")

    lines.append("</svg>")
    write_svg(output_path, lines)


def render_probe_convergence(rows: list[dict[str, Any]], output_path: Path, width: int) -> None:
    height = 560
    left = 90
    right = 70
    top = 110
    bottom = 85
    plot_w = width - left - right
    plot_h = height - top - bottom
    baseline_y = top + plot_h
    x_step = plot_w / max(1, len(rows) - 1)

    lines = svg_header(width, height)
    lines.extend(
        [
            '<text class="title" x="40" y="42">Self-Probing Convergence</text>',
            '<text class="subtitle" x="40" y="66">Stage-2 verifier verdicts reconstructed from probe traces; K=0 is the initial verification pass.</text>',
            *draw_legend(
                [
                    ("Verification accuracy", COLORS["ex"]),
                    ("Rejection F1", COLORS["point"]),
                ],
                40,
                96,
            ),
        ]
    )

    for tick in range(0, 101, 20):
        y = baseline_y - plot_h * tick / 100
        lines.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="{COLORS["grid"]}"/>')
        lines.append(f'<text class="tick" x="{left - 36}" y="{y + 4:.1f}">{tick}%</text>')
    lines.append(f'<line x1="{left}" y1="{baseline_y}" x2="{width - right}" y2="{baseline_y}" stroke="{COLORS["axis"]}" stroke-width="1.4"/>')
    lines.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{baseline_y}" stroke="{COLORS["axis"]}" stroke-width="1.4"/>')

    def point(row_idx: int, value: float | None) -> tuple[float, float]:
        x = left + row_idx * x_step
        y = baseline_y - plot_h * clamp(value_or_zero(value), 0, 1)
        return x, y

    for field, color in [("verification_accuracy", COLORS["ex"]), ("rejection_f1", COLORS["point"])]:
        coords = [point(idx, row.get(field)) for idx, row in enumerate(rows)]
        path = " ".join(("M" if idx == 0 else "L") + f" {x:.1f} {y:.1f}" for idx, (x, y) in enumerate(coords))
        lines.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="3"/>')
        for idx, (x, y) in enumerate(coords):
            value = rows[idx].get(field)
            lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{color}"/>')
            lines.append(f'<text class="tick" text-anchor="middle" x="{x:.1f}" y="{y - 10:.1f}">{escape(pct(value))}</text>')

    for idx, row in enumerate(rows):
        x = left + idx * x_step
        lines.append(f'<text class="label" text-anchor="middle" x="{x:.1f}" y="{baseline_y + 30}">K={row["probe_depth"]}</text>')
        lines.append(f'<text class="tick" text-anchor="middle" x="{x:.1f}" y="{baseline_y + 48}">n={row["total"]}</text>')
    lines.append(f'<text class="label" text-anchor="middle" x="{left + plot_w / 2:.1f}" y="{height - 20}">Probe depth K</text>')

    lines.append("</svg>")
    write_svg(output_path, lines)


def sql_clause_color(line: str, changed_clauses: set[str]) -> str:
    stripped = line.strip().upper()
    for clause in changed_clauses:
        if stripped.startswith(clause.upper()):
            return COLORS["correction"]
    return COLORS["muted"]


def draw_sql_block(
    lines: list[str],
    sql: Any,
    x: float,
    y: float,
    max_chars: int,
    max_lines: int,
    changed_clauses: set[str],
) -> float:
    sql_lines = []
    for raw_line in short_sql(sql).splitlines():
        sql_lines.extend(wrap_text(raw_line, max_chars))
    if len(sql_lines) > max_lines:
        sql_lines = sql_lines[:max_lines]
        sql_lines[-1] = sql_lines[-1].rstrip(".") + "..."
    for idx, line in enumerate(sql_lines):
        fill = sql_clause_color(line, changed_clauses)
        lines.append(
            f'<text class="small" x="{x:.1f}" y="{y + idx * 16:.1f}" '
            f'font-family="Menlo, Consolas, monospace" style="fill: {fill};">{escape(line)}</text>'
        )
    return y + len(sql_lines) * 16


def render_case_study(case: dict[str, Any], output_path: Path, width: int) -> None:
    height = 760
    margin = 40
    box_w = width - margin * 2
    changed_clauses = {str(item) for item in case.get("clause_change_summary") or []}
    allowed = ", ".join(str(item) for item in case.get("allowed_clause_changes") or []) or "n/a"
    locked = ", ".join(str(item) for item in case.get("disallowed_clause_changes") or []) or "n/a"
    evidence = case.get("failed_evidence")
    evidence_text = "; ".join(str(item) for item in evidence) if isinstance(evidence, list) else str(evidence or "n/a")

    lines = svg_header(width, height)
    lines.extend(
        [
            '<text class="title" x="40" y="42">Qualitative AST Edit Boundary Case</text>',
            f'<text class="subtitle" x="40" y="66">ADiR corrected {escape(str(case["question_id"]))} while constraining edits to verifier-identified clauses.</text>',
        ]
    )

    draw_box(lines, margin, 92, box_w, 140, "Question and flawed candidate SQL")
    cursor = draw_wrapped_text(lines, f'Question: {case.get("question")}', margin + 18, 128, 130, max_lines=2)
    draw_sql_block(lines, case.get("original_sql"), margin + 18, cursor + 12, 132, 5, changed_clauses)

    draw_box(lines, margin, 258, box_w, 160, "Verifier diagnosis")
    cursor = draw_wrapped_text(
        lines,
        f'Mismatch: {case.get("mismatch_label")} ({case.get("mismatch_type")})',
        margin + 18,
        294,
        132,
        css_class="label",
        max_lines=2,
    )
    cursor = draw_wrapped_text(lines, f"Evidence: {evidence_text}", margin + 18, cursor + 8, 132, max_lines=3)
    draw_wrapped_text(lines, f'Repair hint: {case.get("repair_hint") or "n/a"}', margin + 18, cursor + 8, 132, max_lines=3)

    draw_box(lines, margin, 444, box_w, 220, "Repaired SQL and edit boundary")
    draw_sql_block(lines, case.get("repaired_sql"), margin + 18, 480, 132, 7, changed_clauses)
    draw_wrapped_text(lines, f"Changed clauses: {', '.join(sorted(changed_clauses)) or 'n/a'}", margin + 18, 604, 132, max_lines=2, fill=COLORS["correction"])
    draw_wrapped_text(lines, f"Locked clauses: {locked}", margin + 18, 642, 132, max_lines=2, fill=COLORS["muted"])
    draw_wrapped_text(lines, f"Allowed edit scope: {allowed}", margin + 18, 680, 132, max_lines=2, fill=COLORS["muted"])

    lines.append("</svg>")
    write_svg(output_path, lines)


def write_visualization_data(
    output_path: Path,
    run_root: Path,
    repair_ablation_dir: Path | None,
    main_rows: list[dict[str, Any]],
    correction_corruption_tradeoff: dict[str, Any],
    strategy_rows: list[dict[str, Any]],
    dimension_rows: list[dict[str, Any]],
    edit_scope_heatmap: dict[str, Any],
    error_resolution_sankey: dict[str, Any],
    probe_rows: list[dict[str, Any]],
    case_study: dict[str, Any],
) -> None:
    payload = {
        "run_root": str(run_root),
        "repair_ablation_dir": str(repair_ablation_dir) if repair_ablation_dir else None,
        "main_system_comparison": main_rows,
        "correction_corruption_tradeoff": correction_corruption_tradeoff,
        "isolated_repair_strategy_comparison": strategy_rows,
        "dimension_diagnostic_repair_breakdown": dimension_rows,
        "edit_scope_heatmap": edit_scope_heatmap,
        "error_resolution_sankey": error_resolution_sankey,
        "self_probing_convergence": probe_rows,
        "qualitative_case_study": case_study,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    run_root = resolve_path(args.run_root)
    ensure_dir(run_root, "run root")
    repair_ablation_dir = None
    if not args.main_only:
        repair_ablation_dir = (
            resolve_path(args.repair_ablation_dir)
            if args.repair_ablation_dir
            else run_root / "debug" / "repair_strategy_ablation" / "full_fixed_verifier"
        )
    output_dir = resolve_path(args.output_dir) if args.output_dir else run_root / "publication_figures"

    main_rows = read_main_rows(run_root)
    correction_corruption_tradeoff = build_correction_corruption_tradeoff(main_rows)
    strategy_rows = [] if args.main_only else read_repair_strategy_rows(repair_ablation_dir)
    finverisql_rows, baseline_rows, final_rows = read_diagnostic_artifacts(run_root)
    dimension_rows = build_dimension_breakdown(finverisql_rows, baseline_rows, final_rows)
    edit_scope_heatmap = build_edit_scope_heatmap(finverisql_rows)
    error_resolution_sankey = build_error_resolution_sankey(
        finverisql_rows,
        baseline_rows,
        final_rows,
    )
    probe_rows = build_probe_convergence(finverisql_rows)
    case_study = build_case_study(finverisql_rows, baseline_rows, final_rows)

    render_main_accuracy_chart(main_rows, output_dir / "main_system_accuracy.svg", args.width)
    render_repair_safety_chart(main_rows, output_dir / "repair_safety_effectiveness.svg", args.width)
    render_correction_corruption_tradeoff(
        correction_corruption_tradeoff,
        output_dir / "correction_corruption_tradeoff.svg",
        args.width,
    )
    render_dimension_breakdown(
        dimension_rows,
        output_dir / "dimension_diagnostic_repair_breakdown.svg",
        args.width,
    )
    render_edit_scope_heatmap(
        edit_scope_heatmap,
        output_dir / "edit_scope_heatmap.svg",
        args.width,
    )
    render_error_resolution_sankey(
        error_resolution_sankey,
        output_dir / "error_resolution_sankey.svg",
        args.width,
    )
    render_probe_convergence(
        probe_rows,
        output_dir / "self_probing_convergence_curve.svg",
        args.width,
    )
    render_case_study(
        case_study,
        output_dir / "qualitative_ast_edit_boundary_case.svg",
        args.width,
    )
    if not args.main_only:
        render_pareto_chart(strategy_rows, output_dir / "isolated_repair_strategy_pareto.svg", args.width)
    write_visualization_data(
        output_dir / "visualization_data.json",
        run_root,
        repair_ablation_dir,
        main_rows,
        correction_corruption_tradeoff,
        strategy_rows,
        dimension_rows,
        edit_scope_heatmap,
        error_resolution_sankey,
        probe_rows,
        case_study,
    )

    print(f"Wrote SVG figures to: {output_dir}")
    print(f"- {output_dir / 'main_system_accuracy.svg'}")
    print(f"- {output_dir / 'repair_safety_effectiveness.svg'}")
    print(f"- {output_dir / 'correction_corruption_tradeoff.svg'}")
    print(f"- {output_dir / 'dimension_diagnostic_repair_breakdown.svg'}")
    print(f"- {output_dir / 'edit_scope_heatmap.svg'}")
    print(f"- {output_dir / 'error_resolution_sankey.svg'}")
    print(f"- {output_dir / 'self_probing_convergence_curve.svg'}")
    print(f"- {output_dir / 'qualitative_ast_edit_boundary_case.svg'}")
    if not args.main_only:
        print(f"- {output_dir / 'isolated_repair_strategy_pareto.svg'}")
    print(f"- {output_dir / 'visualization_data.json'}")


if __name__ == "__main__":
    main()
