#!/usr/bin/env python3
"""Build only the internal-ablation table from a hardware-run directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_publication_tables import ablation_markdown, build_ablation_rows


LABELS = {
    "full": "FinVeriSQL full",
    "wo_intent_decomposer": "w/o Intent Decomposer",
    "direct_only": "w/o Probing / direct only",
    "wo_compact_semantic_profile": "w/o Compact Semantic Profile",
    "wo_scope_constraints": "w/o Scope Constraints in Repair",
    "wo_reverification_loop": "w/o re-verification loop",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ablation-dir", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--variants", nargs="+", default=list(LABELS))
    args = parser.parse_args()
    root = Path(args.ablation_dir)
    manifest = {"ablations": []}
    for key in args.variants:
        directory = root / key
        metrics = directory / f"{key}_final_metrics.json"
        asa = directory / f"{key}_asa_metrics.json"
        verify = directory / f"{key}_verify.jsonl"
        if metrics.is_file() and asa.is_file() and verify.is_file():
            manifest["ablations"].append(
                {
                    "key": key,
                    "label": LABELS.get(key, key),
                    "verify_jsonl": str(verify),
                    "metrics_json": str(metrics),
                    "asa_metrics_json": str(asa),
                }
            )
    rows = build_ablation_rows(manifest)
    output_md = Path(args.output_md)
    output_json = Path(args.output_json)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(ablation_markdown(rows), encoding="utf-8")
    output_json.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} internal-ablation rows to {output_md}")


if __name__ == "__main__":
    main()
