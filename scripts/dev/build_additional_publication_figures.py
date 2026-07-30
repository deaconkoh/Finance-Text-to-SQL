#!/usr/bin/env python3
"""Build non-overlapping publication figures for ADiR efficiency and robustness."""

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

from scripts.dev.build_result_visualizations import (  # noqa: E402
    COLORS,
    clamp,
    read_jsonl,
    rows_by_question_id,
    svg_header,
    write_svg,
)


SYSTEM_ORDER = [
    "generic_self_refine",
    "generic_execution_guided_refine",
    "adir_full",
]

SYSTEM_LABELS = {
    "generic_self_refine": "Generic self-refine",
    "generic_execution_guided_refine": "Execution-guided",
    "adir_full": "ADiR",
}

YIELD_LABELS = {
    "generic_self_refine": "Self-refine",
    "generic_execution_guided_refine": "Execution-guided",
    "adir_full": "ADiR",
}

SYSTEM_COLORS = {
    "generic_self_refine": "#fdba74",
    "generic_execution_guided_refine": "#fb923c",
    "adir_full": "#c2410c",
}

DIFFICULTY_ORDER = ["easy", "medium", "hard"]
NEGATIVE_COLOR = "#d55e00"
POSITIVE_COLOR = "#0072b2"
NEUTRAL_COLOR = "#f4f4f5"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build correction-yield, difficulty-robustness, and EX-to-ASA "
            "publication figures from a development-excluded main comparison."
        )
    )
    parser.add_argument(
        "--cohort-root",
        required=True,
        help=(
            "Development-excluded comparison root containing metrics/baseline, "
            "metrics/finverisql_full, and metrics/main_comparison."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory. Defaults to $COHORT_ROOT/publication_figures.",
    )
    parser.add_argument("--width", type=int, default=1200, help="SVG width in pixels.")
    parser.add_argument(
        "--yield-width",
        type=int,
        default=900,
        help="Repair-yield SVG width in pixels. Its height uses a 1.5:1 aspect ratio.",
    )
    return parser.parse_args()


def resolve_path(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing JSON artifact: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def execution_pass(row: dict[str, Any]) -> bool:
    value = row.get("execution_match")
    return value is True or value == 1


def applied_repair(row: dict[str, Any]) -> bool:
    return row.get("final_sql_repaired") is True


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        raise ValueError("Wilson interval requires a positive denominator.")
    if successes < 0 or successes > total:
        raise ValueError("Wilson successes must be between zero and total.")
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total
            + z * z / (4 * total * total)
        )
        / denominator
    )
    return center - margin, center + margin


def eligible_question_ids(
    baseline_rows: dict[str, dict[str, Any]],
    system_rows: dict[str, dict[str, dict[str, Any]]],
) -> set[str]:
    adir_ids = set(system_rows["adir_full"])
    if not adir_ids:
        raise ValueError("ADiR evaluated output is empty.")
    missing_by_source: dict[str, list[str]] = {}
    if missing := sorted(adir_ids - set(baseline_rows)):
        missing_by_source["baseline"] = missing
    for key, rows in system_rows.items():
        if missing := sorted(adir_ids - set(rows)):
            missing_by_source[key] = missing
    if missing_by_source:
        detail = "; ".join(
            f"{key}: {', '.join(values[:5])}"
            for key, values in missing_by_source.items()
        )
        raise ValueError(f"ADiR cohort is missing from comparison artifacts: {detail}")
    common_ids = set(baseline_rows)
    for rows in system_rows.values():
        common_ids &= set(rows)
    if common_ids != adir_ids:
        raise ValueError(
            "The shared comparison cohort does not exactly match the ADiR cohort: "
            f"shared={len(common_ids)}, ADiR={len(adir_ids)}."
        )
    return adir_ids


def movement_sets(
    eligible_ids: set[str],
    baseline_rows: dict[str, dict[str, Any]],
    final_rows: dict[str, dict[str, Any]],
) -> tuple[set[str], set[str], set[str]]:
    corrected = {
        question_id
        for question_id in eligible_ids
        if not execution_pass(baseline_rows[question_id])
        and execution_pass(final_rows[question_id])
    }
    corrupted = {
        question_id
        for question_id in eligible_ids
        if execution_pass(baseline_rows[question_id])
        and not execution_pass(final_rows[question_id])
    }
    applied = {
        question_id
        for question_id in eligible_ids
        if applied_repair(final_rows[question_id])
    }
    unexplained = (corrected | corrupted) - applied
    if unexplained:
        sample = ", ".join(sorted(unexplained)[:5])
        raise ValueError(
            "EX movement occurred without an applied repair flag: " + sample
        )
    return corrected, corrupted, applied


def build_repair_yield(
    eligible_ids: set[str],
    baseline_rows: dict[str, dict[str, Any]],
    system_rows: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    systems: list[dict[str, Any]] = []
    for key in SYSTEM_ORDER:
        corrected, corrupted, applied = movement_sets(
            eligible_ids,
            baseline_rows,
            system_rows[key],
        )
        if not applied:
            raise ValueError(f"{key} has no applied repairs.")
        correction_count = len(corrected)
        applied_count = len(applied)
        lower, upper = wilson_interval(correction_count, applied_count)
        systems.append(
            {
                "key": key,
                "label": SYSTEM_LABELS[key],
                "eligible_count": len(eligible_ids),
                "applied_repair_count": applied_count,
                "correction_count": correction_count,
                "corruption_count": len(corrupted),
                "correction_yield": correction_count / applied_count,
                "wilson_95_lower": lower,
                "wilson_95_upper": upper,
                "repairs_per_correction": (
                    applied_count / correction_count if correction_count else None
                ),
            }
        )
    by_key = {row["key"]: row for row in systems}
    caption = (
        "Repair yield among applied revisions. Repair yield is the proportion of "
        "applied repairs that convert an EX-incorrect prediction into an EX-correct "
        "prediction. Points show observed yield, and whiskers denote 95% Wilson "
        "confidence intervals. ADiR requires approximately "
        f'{by_key["adir_full"]["repairs_per_correction"]:.1f} applied repairs per '
        "correction, compared with "
        f'{by_key["generic_execution_guided_refine"]["repairs_per_correction"]:.1f} '
        "for execution-guided refinement and "
        f'{by_key["generic_self_refine"]["repairs_per_correction"]:.1f} for '
        "self-refine."
    )
    return {
        "eligible_count": len(eligible_ids),
        "definition": "EX wrong-to-correct rows / rows with an applied repair",
        "suggested_caption": caption,
        "systems": systems,
    }


def build_difficulty_net_gain(
    eligible_ids: set[str],
    baseline_rows: dict[str, dict[str, Any]],
    system_rows: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    levels = [
        level
        for level in DIFFICULTY_ORDER
        if any(
            str(baseline_rows[question_id].get("level", "")).lower() == level
            for question_id in eligible_ids
        )
    ]
    unknown_levels = sorted(
        {
            str(baseline_rows[question_id].get("level", "")).lower()
            for question_id in eligible_ids
        }
        - set(DIFFICULTY_ORDER)
    )
    if unknown_levels:
        raise ValueError(
            "Unsupported or missing difficulty values: " + ", ".join(unknown_levels)
        )
    if not levels:
        raise ValueError("No supported difficulty levels in eligible cohort.")

    cohorts = {
        level: {
            question_id
            for question_id in eligible_ids
            if str(baseline_rows[question_id].get("level", "")).lower() == level
        }
        for level in levels
    }
    systems: list[dict[str, Any]] = []
    for key in SYSTEM_ORDER:
        corrected, corrupted, _ = movement_sets(
            eligible_ids,
            baseline_rows,
            system_rows[key],
        )
        cells: list[dict[str, Any]] = []
        for level in levels:
            cohort = cohorts[level]
            correction_count = len(corrected & cohort)
            corruption_count = len(corrupted & cohort)
            net_count = correction_count - corruption_count
            cells.append(
                {
                    "difficulty": level,
                    "cohort_count": len(cohort),
                    "correction_count": correction_count,
                    "corruption_count": corruption_count,
                    "net_gain_count": net_count,
                    "net_gain_rate": net_count / len(cohort),
                }
            )
        systems.append(
            {
                "key": key,
                "label": SYSTEM_LABELS[key],
                "cells": cells,
            }
        )
    return {
        "eligible_count": len(eligible_ids),
        "difficulty_levels": [
            {"difficulty": level, "cohort_count": len(cohorts[level])}
            for level in levels
        ],
        "definition": "(EX corrections - EX corruptions) / difficulty cohort",
        "systems": systems,
    }


def asa_set(metrics: dict[str, Any], preferred_label: str) -> dict[str, Any]:
    sets = metrics.get("sets")
    if not isinstance(sets, list):
        raise ValueError("ASA metrics do not contain a sets list.")
    matches = [
        row
        for row in sets
        if isinstance(row, dict) and row.get("label") == preferred_label
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one ASA set labelled {preferred_label!r}; "
            f"found {len(matches)}."
        )
    return matches[0]


def build_ex_asa_transfer(
    eligible_ids: set[str],
    baseline_rows: dict[str, dict[str, Any]],
    system_rows: dict[str, dict[str, dict[str, Any]]],
    baseline_asa: dict[str, Any],
    system_asa: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    baseline_asa_set = asa_set(baseline_asa, "before")
    baseline_total = int(baseline_asa_set["total_rows"])
    if baseline_total != len(eligible_ids):
        raise ValueError(
            "Baseline ASA denominator does not match eligible cohort: "
            f"{baseline_total} != {len(eligible_ids)}."
        )
    baseline_ex_count = sum(
        execution_pass(baseline_rows[question_id]) for question_id in eligible_ids
    )
    baseline_ex_rate = baseline_ex_count / len(eligible_ids)
    baseline_asa_rate = float(baseline_asa_set["asa_lower_bound_accuracy"])

    systems: list[dict[str, Any]] = []
    for key in SYSTEM_ORDER:
        after = asa_set(system_asa[key], "after")
        total = int(after["total_rows"])
        if total != len(eligible_ids):
            raise ValueError(
                f"{key} ASA denominator does not match eligible cohort: "
                f"{total} != {len(eligible_ids)}."
            )
        final_ex_count = sum(
            execution_pass(system_rows[key][question_id])
            for question_id in eligible_ids
        )
        final_ex_rate = final_ex_count / len(eligible_ids)
        final_asa_rate = float(after["asa_lower_bound_accuracy"])
        systems.append(
            {
                "key": key,
                "label": SYSTEM_LABELS[key],
                "eligible_count": len(eligible_ids),
                "baseline_ex_count": baseline_ex_count,
                "final_ex_count": final_ex_count,
                "ex_delta": final_ex_rate - baseline_ex_rate,
                "baseline_asa_pass_count": int(
                    baseline_asa_set["asa_lower_bound_pass_rows"]
                ),
                "final_asa_pass_count": int(after["asa_lower_bound_pass_rows"]),
                "asa_lower_bound_delta": final_asa_rate - baseline_asa_rate,
                "asa_decision_available_count": int(
                    after["asa_decision_available_rows"]
                ),
            }
        )
    return {
        "eligible_count": len(eligible_ids),
        "baseline_ex_accuracy": baseline_ex_rate,
        "baseline_asa_lower_bound_accuracy": baseline_asa_rate,
        "asa_definition": "ASA pass rows / complete eligible cohort",
        "suggested_caption": (
            "Transfer from execution correctness to accounting-semantic correctness. "
            "Bars report changes from the generator-only baseline in execution "
            "accuracy and fixed-denominator ASA lower-bound accuracy. Positive values "
            "indicate net improvement. Execution-guided refinement improves EX while "
            "reducing ASA, whereas ADiR improves both metrics."
        ),
        "systems": systems,
    }


def render_repair_yield(
    data: dict[str, Any],
    output_path: Path,
    width: int = 900,
) -> None:
    height = round(width / 1.5)
    left = width * 0.24
    right = width * 0.055
    top = height * 0.09
    bottom = height * 0.15
    plot_width = width - left - right
    systems = list(data["systems"])
    upper_bound = max(float(row["wilson_95_upper"]) for row in systems)
    axis_max = max(0.25, math.ceil(upper_bound / 0.05) * 0.05)
    row_gap = (height - top - bottom) / len(systems)

    def scale(value: float) -> float:
        return left + clamp(value / axis_max, 0.0, 1.0) * plot_width

    lines = svg_header(width, height)
    for tick_index in range(round(axis_max / 0.05) + 1):
        value = tick_index * 0.05
        x = scale(value)
        lines.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" '
            f'y2="{height - bottom}" stroke="{COLORS["grid"]}" stroke-width="1"/>'
        )
        lines.append(
            f'<text class="tick" text-anchor="middle" x="{x:.1f}" '
            f'y="{height - bottom + 31}" style="font-size: 20px;">'
            f"{value * 100:.0f}%</text>"
        )

    for index, row in enumerate(systems):
        y = top + row_gap * (index + 0.5)
        key = str(row["key"])
        color = SYSTEM_COLORS[key]
        lower_x = scale(float(row["wilson_95_lower"]))
        upper_x = scale(float(row["wilson_95_upper"]))
        point_x = scale(float(row["correction_yield"]))
        label_weight = ' font-weight="700"' if key == "adir_full" else ""
        if key == "generic_self_refine":
            label_x = left - 22
            lines.append(
                f'<text class="label" text-anchor="end" x="{label_x:.1f}" '
                f'y="{y - 8:.1f}" style="font-size: 23px;">'
                f'<tspan x="{label_x:.1f}">Generic</tspan>'
                f'<tspan x="{label_x:.1f}" dy="25">self-refine</tspan>'
                "</text>"
            )
        else:
            lines.append(
                f'<text class="label" text-anchor="end" x="{left - 22}" '
                f'y="{y + 7:.1f}"{label_weight} style="font-size: 23px;">'
                f'{escape(YIELD_LABELS[key])}</text>'
            )
        lines.append(
            f'<line x1="{lower_x:.1f}" y1="{y:.1f}" x2="{upper_x:.1f}" '
            f'y2="{y:.1f}" stroke="{color}" stroke-width="4"/>'
        )
        for cap_x in (lower_x, upper_x):
            lines.append(
                f'<line x1="{cap_x:.1f}" y1="{y - 10:.1f}" '
                f'x2="{cap_x:.1f}" y2="{y + 10:.1f}" '
                f'stroke="{color}" stroke-width="3"/>'
            )
        radius = 12 if key == "adir_full" else 9
        lines.append(
            f'<circle cx="{point_x:.1f}" cy="{y:.1f}" r="{radius}" '
            f'fill="{color}" stroke="#7c2d12" stroke-width="1.2"/>'
        )
        yield_label = f'{float(row["correction_yield"]) * 100:.2f}%'
        if key == "adir_full":
            lines.append(
                f'<text class="label" text-anchor="middle" x="{point_x:.1f}" '
                f'y="{y - 22:.1f}" font-weight="700" style="font-size: 22px;">'
                f"{escape(yield_label)}</text>"
            )
        else:
            lines.append(
                f'<text class="label" text-anchor="middle" x="{point_x:.1f}" '
                f'y="{y + 30:.1f}" style="font-size: 21px;">'
                f"{escape(yield_label)}</text>"
            )

    lines.append(
        f'<text class="label" text-anchor="middle" x="{left + plot_width / 2:.1f}" '
        f'y="{height - 22}" font-weight="700" style="font-size: 24px;">'
        "Repair yield (%)</text>"
    )
    lines.append("</svg>")
    write_svg(output_path, lines)


def blend(start: str, end: str, ratio: float) -> str:
    ratio = clamp(ratio, 0.0, 1.0)
    start_rgb = tuple(int(start[index : index + 2], 16) for index in (1, 3, 5))
    end_rgb = tuple(int(end[index : index + 2], 16) for index in (1, 3, 5))
    rgb = tuple(
        round(start_value + (end_value - start_value) * ratio)
        for start_value, end_value in zip(start_rgb, end_rgb)
    )
    return "#" + "".join(f"{value:02x}" for value in rgb)


def render_difficulty_net_gain(
    data: dict[str, Any],
    output_path: Path,
    width: int = 1200,
) -> None:
    height = 535
    left = 315
    right = 55
    top = 88
    bottom = 105
    systems = list(data["systems"])
    levels = list(data["difficulty_levels"])
    cell_width = (width - left - right) / len(levels)
    cell_height = (height - top - bottom) / len(systems)
    max_abs = max(
        abs(float(cell["net_gain_rate"]))
        for system in systems
        for cell in system["cells"]
    )
    if max_abs == 0:
        max_abs = 1.0

    lines = svg_header(width, height)
    for column, level in enumerate(levels):
        center = left + cell_width * (column + 0.5)
        label = str(level["difficulty"]).capitalize()
        lines.append(
            f'<text class="label" text-anchor="middle" x="{center:.1f}" y="38" '
            f'font-weight="700" style="font-size: 25px;">{escape(label)}</text>'
        )

    for row_index, system in enumerate(systems):
        key = str(system["key"])
        center_y = top + cell_height * (row_index + 0.5)
        label_weight = ' font-weight="700"' if key == "adir_full" else ""
        lines.append(
            f'<text class="label" text-anchor="end" x="{left - 22}" '
            f'y="{center_y + 7:.1f}"{label_weight} style="font-size: 23px;">'
            f'{escape(str(system["label"]))}</text>'
        )
        cells_by_level = {
            str(cell["difficulty"]): cell for cell in system["cells"]
        }
        for column, level in enumerate(levels):
            cell = cells_by_level[str(level["difficulty"])]
            rate = float(cell["net_gain_rate"])
            intensity = abs(rate) / max_abs
            fill = blend(
                NEUTRAL_COLOR,
                POSITIVE_COLOR if rate >= 0 else NEGATIVE_COLOR,
                0.18 + 0.82 * intensity,
            )
            text_color = "#ffffff" if intensity > 0.58 else "#18181b"
            x = left + cell_width * column
            y = top + cell_height * row_index
            sign = "+" if rate >= 0 else "−"
            lines.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_width:.1f}" '
                f'height="{cell_height:.1f}" fill="{fill}" stroke="#ffffff" '
                'stroke-width="4"/>'
            )
            lines.append(
                f'<text class="label" text-anchor="middle" '
                f'x="{x + cell_width / 2:.1f}" y="{y + cell_height / 2 + 9:.1f}" '
                f'font-weight="700" style="font-size: 27px; fill: {text_color};">'
                f'{sign}{abs(rate) * 100:.2f} pp</text>'
            )

    legend_y = height - 52
    legend_x = left + 45
    swatch_width = 82
    legend_values = [
        ("Negative net gain", NEGATIVE_COLOR),
        ("Near zero", NEUTRAL_COLOR),
        ("Positive net gain", POSITIVE_COLOR),
    ]
    cursor = legend_x
    for label, color in legend_values:
        lines.append(
            f'<rect x="{cursor:.1f}" y="{legend_y - 17}" width="28" height="28" '
            f'fill="{color}" stroke="#d4d4d8"/>'
        )
        lines.append(
            f'<text class="small" x="{cursor + 40:.1f}" y="{legend_y + 5}" '
            f'style="font-size: 18px;">{escape(label)}</text>'
        )
        cursor += swatch_width + 165
    lines.append("</svg>")
    write_svg(output_path, lines)


def render_ex_asa_transfer(
    data: dict[str, Any],
    output_path: Path,
    width: int = 1200,
) -> None:
    height = 520
    left = 285
    right = 65
    top = 72
    bottom = 90
    systems = list(data["systems"])
    plot_width = width - left - right
    plot_height = height - top - bottom
    axis_min = -0.036
    axis_max = 0.010
    tick_values = [-0.035, -0.030, -0.020, -0.010, 0.0, 0.005, 0.010]
    row_gap = plot_height / len(systems)
    bar_height = 24

    def scale(value: float) -> float:
        return left + ((value - axis_min) / (axis_max - axis_min)) * plot_width

    for row in systems:
        for field in ("ex_delta", "asa_lower_bound_delta"):
            value = float(row[field])
            if not axis_min <= value <= axis_max:
                raise ValueError(
                    f"{row['key']} {field}={value} falls outside "
                    f"[{axis_min}, {axis_max}]."
                )

    zero_x = scale(0.0)
    lines = svg_header(width, height)
    lines.extend(
        [
            "<defs>",
            '<pattern id="exBarPattern" patternUnits="userSpaceOnUse" width="9" height="9">',
            f'<rect width="9" height="9" fill="{COLORS["ex"]}"/>',
            '<path d="M-2 2 L2 -2 M0 9 L9 0 M7 11 L11 7" '
            'stroke="#dbeafe" stroke-width="1.4" opacity="0.80"/>',
            "</pattern>",
            '<pattern id="asaBarPattern" patternUnits="userSpaceOnUse" width="8" height="8">',
            f'<rect width="8" height="8" fill="{COLORS["asa_strict"]}"/>',
            '<circle cx="2" cy="2" r="1.2" fill="#dcfce7" opacity="0.90"/>',
            '<circle cx="6" cy="6" r="1.2" fill="#dcfce7" opacity="0.90"/>',
            "</pattern>",
            "</defs>",
        ]
    )
    legend = [
        ("EX", "url(#exBarPattern)", "#1e40af"),
        ("ASA lower bound", "url(#asaBarPattern)", "#166534"),
    ]
    cursor = left
    for label, fill, stroke in legend:
        lines.append(
            f'<rect x="{cursor:.1f}" y="22" width="24" height="18" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>'
        )
        lines.append(
            f'<text class="small" x="{cursor + 34:.1f}" y="38" '
            f'style="font-size: 19px;">{escape(label)}</text>'
        )
        cursor += 210

    for value in tick_values:
        x = scale(value)
        lines.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" '
            f'y2="{height - bottom}" stroke="{COLORS["grid"]}" stroke-width="1"/>'
        )
        tick_label = (
            "0.0"
            if value == 0
            else f"{value * 100:+.1f}".replace("-", "−")
        )
        lines.append(
            f'<text class="tick" text-anchor="middle" x="{x:.1f}" '
            f'y="{height - bottom + 29}" style="font-size: 18px;">'
            f"{tick_label}</text>"
        )

    for index, row in enumerate(systems):
        key = str(row["key"])
        y_center = top + row_gap * (index + 0.5)
        label_weight = ' font-weight="700"' if key == "adir_full" else ""
        lines.append(
            f'<text class="label" text-anchor="end" x="{left - 22}" '
            f'y="{y_center + 7:.1f}"{label_weight} style="font-size: 23px;">'
            f'{escape(str(row["label"]))}</text>'
        )
        for offset, field, fill, stroke in [
            (
                -bar_height / 2 - 3,
                "ex_delta",
                "url(#exBarPattern)",
                "#1e40af",
            ),
            (
                bar_height / 2 + 3,
                "asa_lower_bound_delta",
                "url(#asaBarPattern)",
                "#166534",
            ),
        ]:
            value = float(row[field])
            value_x = scale(value)
            x = min(zero_x, value_x)
            width_value = abs(value_x - zero_x)
            y = y_center + offset - bar_height / 2
            lines.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{width_value:.1f}" '
                f'height="{bar_height}" fill="{fill}" stroke="{stroke}" '
                'stroke-width="1.2"/>'
            )
            label_x = (
                zero_x + width_value + 10
                if value >= 0
                else zero_x - width_value - 10
            )
            anchor = "start" if value >= 0 else "end"
            sign = "+" if value >= 0 else "−"
            lines.append(
                f'<text class="small" text-anchor="{anchor}" x="{label_x:.1f}" '
                f'y="{y + 19:.1f}" style="font-size: 18px;">'
                f'{sign}{abs(value) * 100:.2f} pp</text>'
            )

    lines.append(
        f'<line data-role="zero-line" x1="{zero_x:.1f}" y1="{top}" '
        f'x2="{zero_x:.1f}" y2="{height - bottom}" stroke="#27272a" '
        'stroke-width="2.5"/>'
    )
    lines.append(
        f'<text class="label" text-anchor="middle" x="{left + plot_width / 2:.1f}" '
        f'y="{height - 18}" font-weight="700" style="font-size: 23px;">'
        "Net repair gain (percentage points)</text>"
    )
    lines.append("</svg>")
    write_svg(output_path, lines)


def load_artifacts(
    cohort_root: Path,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, dict[str, Any]]],
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, str],
]:
    metrics_root = cohort_root / "metrics"
    paths = {
        "baseline_evaluated": metrics_root / "baseline" / "evaluated.jsonl",
        "baseline_asa": metrics_root / "baseline" / "asa_metrics.json",
        "generic_self_refine_evaluated": (
            metrics_root
            / "main_comparison"
            / "generic_self_refine"
            / "final_evaluated.jsonl"
        ),
        "generic_self_refine_asa": (
            metrics_root
            / "main_comparison"
            / "generic_self_refine"
            / "asa_metrics.json"
        ),
        "generic_execution_guided_refine_evaluated": (
            metrics_root
            / "main_comparison"
            / "generic_execution_guided_refine"
            / "final_evaluated.jsonl"
        ),
        "generic_execution_guided_refine_asa": (
            metrics_root
            / "main_comparison"
            / "generic_execution_guided_refine"
            / "asa_metrics.json"
        ),
        "adir_full_evaluated": (
            metrics_root / "finverisql_full" / "final_evaluated.jsonl"
        ),
        "adir_full_asa": metrics_root / "finverisql_full" / "asa_metrics.json",
    }
    for label, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"Missing {label}: {path}")

    baseline_rows = rows_by_question_id(
        read_jsonl(paths["baseline_evaluated"]),
        "baseline evaluated rows",
    )
    system_rows = {
        key: rows_by_question_id(
            read_jsonl(paths[f"{key}_evaluated"]),
            f"{key} evaluated rows",
        )
        for key in SYSTEM_ORDER
    }
    baseline_asa = read_json(paths["baseline_asa"])
    system_asa = {
        key: read_json(paths[f"{key}_asa"])
        for key in SYSTEM_ORDER
    }
    source_paths = {
        label: str(path.relative_to(PROJECT_ROOT))
        if path.is_relative_to(PROJECT_ROOT)
        else str(path)
        for label, path in paths.items()
    }
    return baseline_rows, system_rows, baseline_asa, system_asa, source_paths


def main() -> None:
    args = parse_args()
    cohort_root = resolve_path(args.cohort_root)
    if not cohort_root.is_dir():
        raise FileNotFoundError(f"Missing cohort root: {cohort_root}")
    output_dir = (
        resolve_path(args.output_dir)
        if args.output_dir
        else cohort_root / "publication_figures"
    )

    baseline_rows, system_rows, baseline_asa, system_asa, source_paths = load_artifacts(
        cohort_root
    )
    eligible_ids = eligible_question_ids(baseline_rows, system_rows)
    repair_yield = build_repair_yield(eligible_ids, baseline_rows, system_rows)
    difficulty_net_gain = build_difficulty_net_gain(
        eligible_ids,
        baseline_rows,
        system_rows,
    )
    ex_asa_transfer = build_ex_asa_transfer(
        eligible_ids,
        baseline_rows,
        system_rows,
        baseline_asa,
        system_asa,
    )

    yield_path = output_dir / "repair_correction_yield.svg"
    difficulty_path = output_dir / "difficulty_conditioned_net_gain.svg"
    transfer_path = output_dir / "ex_asa_transfer.svg"
    data_path = output_dir / "additional_visualization_data.json"
    render_repair_yield(repair_yield, yield_path, args.yield_width)
    render_difficulty_net_gain(difficulty_net_gain, difficulty_path, args.width)
    render_ex_asa_transfer(ex_asa_transfer, transfer_path, args.width)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(
        json.dumps(
            {
                "cohort_root": str(cohort_root),
                "source_artifacts": source_paths,
                "repair_correction_yield": repair_yield,
                "difficulty_conditioned_net_gain": difficulty_net_gain,
                "ex_asa_transfer": ex_asa_transfer,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Wrote additional publication figures to: {output_dir}")
    for path in (yield_path, difficulty_path, transfer_path, data_path):
        print(f"- {path}")


if __name__ == "__main__":
    main()
