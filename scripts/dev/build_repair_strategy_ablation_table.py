#!/usr/bin/env python3
"""Build a compact table for repair-strategy ablation outputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_publication_tables import asa_set_metrics, pct, repair_rates


STRATEGY_LABELS = {
    "prompt_llama31_8b": "Prompted Llama-3.1-8B",
    "sft_llama31_8b": "SFT Llama-3.1-8B",
    "rl_llama31_8b": "RL Llama-3.1-8B",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ablation-dir", required=True)
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=["prompt_llama31_8b", "sft_llama31_8b", "rl_llama31_8b"],
    )
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--output-json", required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object at {path}")
    return data


def count_rate(count: int | None, total: int | None) -> str:
    if count is None or total is None:
        return "n/a"
    rate = count / total if total else 0.0
    return f"{rate * 100:.2f}% ({count}/{total})"


def build_rows(ablation_dir: Path, strategies: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for strategy in strategies:
        metrics_path = ablation_dir / f"{strategy}_final_metrics.json"
        asa_path = ablation_dir / f"{strategy}_asa_metrics.json"
        if not metrics_path.exists():
            continue
        metrics = read_json(metrics_path)
        asa_metrics = read_json(asa_path) if asa_path.exists() else None
        rates = repair_rates(metrics)
        asa_after = asa_set_metrics(asa_metrics) if asa_metrics is not None else {}
        rows.append(
            {
                "strategy": strategy,
                "label": STRATEGY_LABELS.get(strategy, strategy),
                "correction_count": rates["correction_count"],
                "correction_total": rates["correction_total"],
                "correction_rate": rates["correction"],
                "corruption_count": rates["corruption_count"],
                "corruption_total": rates["corruption_total"],
                "corruption_rate": rates["corruption"],
                "asa_strict_accuracy": asa_after.get("asa_strict_accuracy"),
                "asa_lower_bound_accuracy": asa_after.get("asa_lower_bound_accuracy"),
                "fper": asa_after.get("fper"),
            }
        )
    return rows


def write_outputs(rows: list[dict[str, Any]], output_md: Path, output_json: Path) -> None:
    lines = [
        "| Repair Strategy | Correction Rate | Corruption Rate | ASA |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {strategy} | {correction} | {corruption} | {asa} |".format(
                strategy=row["label"],
                correction=count_rate(row["correction_count"], row["correction_total"]),
                corruption=count_rate(row["corruption_count"], row["corruption_total"]),
                asa=pct(row["asa_strict_accuracy"]),
            )
        )

    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    output_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    rows = build_rows(Path(args.ablation_dir), args.strategies)
    write_outputs(rows, Path(args.output_md), Path(args.output_json))
    print(f"Wrote {len(rows)} repair-strategy rows to {args.output_md}")


if __name__ == "__main__":
    main()
