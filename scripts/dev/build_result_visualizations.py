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
    "asa_lower": "#f59e0b",
    "correction": "#16a34a",
    "corruption": "#dc2626",
    "point": "#7c3aed",
    "grid": "#d4d4d8",
    "axis": "#52525b",
    "text": "#18181b",
    "muted": "#71717a",
    "panel": "#fafafa",
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


def read_main_rows(run_root: Path) -> list[dict[str, Any]]:
    manifest_path = run_root / "debug" / "run_manifest.json"
    ensure_file(manifest_path, "2_run_ablations.sh run manifest")
    manifest = read_json(manifest_path)

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

        rows.append(
            {
                "key": system["key"],
                "label": system["label"],
                "kind": system.get("kind"),
                "ex_accuracy": final_ex_accuracy(metrics),
                "asa_strict_accuracy": asa_after.get("asa_strict_accuracy"),
                "asa_lower_bound_accuracy": asa_after.get("asa_lower_bound_accuracy"),
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
                "asa_lower_bound_accuracy": asa_after.get("asa_lower_bound_accuracy"),
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
                    ("ASA Strict", COLORS["asa_strict"]),
                    ("ASA Lower Bound", COLORS["asa_lower"]),
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
        ("asa_lower_bound_accuracy", COLORS["asa_lower"]),
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


def write_visualization_data(
    output_path: Path,
    run_root: Path,
    repair_ablation_dir: Path,
    main_rows: list[dict[str, Any]],
    strategy_rows: list[dict[str, Any]],
) -> None:
    payload = {
        "run_root": str(run_root),
        "repair_ablation_dir": str(repair_ablation_dir),
        "main_system_comparison": main_rows,
        "isolated_repair_strategy_comparison": strategy_rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    run_root = resolve_path(args.run_root)
    ensure_dir(run_root, "run root")
    repair_ablation_dir = (
        resolve_path(args.repair_ablation_dir)
        if args.repair_ablation_dir
        else run_root / "debug" / "repair_strategy_ablation" / "full_fixed_verifier"
    )
    output_dir = resolve_path(args.output_dir) if args.output_dir else run_root / "publication_figures"

    main_rows = read_main_rows(run_root)
    strategy_rows = read_repair_strategy_rows(repair_ablation_dir)

    render_main_accuracy_chart(main_rows, output_dir / "main_system_accuracy.svg", args.width)
    render_repair_safety_chart(main_rows, output_dir / "repair_safety_effectiveness.svg", args.width)
    render_pareto_chart(strategy_rows, output_dir / "isolated_repair_strategy_pareto.svg", args.width)
    write_visualization_data(
        output_dir / "visualization_data.json",
        run_root,
        repair_ablation_dir,
        main_rows,
        strategy_rows,
    )

    print(f"Wrote SVG figures to: {output_dir}")
    print(f"- {output_dir / 'main_system_accuracy.svg'}")
    print(f"- {output_dir / 'repair_safety_effectiveness.svg'}")
    print(f"- {output_dir / 'isolated_repair_strategy_pareto.svg'}")
    print(f"- {output_dir / 'visualization_data.json'}")


if __name__ == "__main__":
    main()
