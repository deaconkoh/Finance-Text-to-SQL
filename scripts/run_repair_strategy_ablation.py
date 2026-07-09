#!/usr/bin/env python3
"""Run repair strategies over fixed FinVeriSQL verifier outputs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.repair_learning import DEFAULT_LLAMA31_8B_BASE_MODEL
from src.repair_learning.data import load_schema_text
from src.repair_learning.generate import (
    build_hf_generator,
    build_ollama_generator,
    generate_repairs_from_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixed-verifier-jsonl", required=True)
    parser.add_argument("--baseline-eval-jsonl", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--schema-text-path", default="data/booksql/schema.txt")
    parser.add_argument("--schema-annotations-path", default="data/booksql/schema_annotations.json")
    parser.add_argument("--db-path", default="data/booksql/accounting.sqlite")
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=["prompt_llama31_8b", "sft_llama31_8b", "rl_llama31_8b"],
        choices=["prompt_llama31_8b", "sft_llama31_8b", "rl_llama31_8b"],
    )
    parser.add_argument("--prompt-model-name", default="llama3.1:8b-instruct-fp16")
    parser.add_argument("--base-model", default=DEFAULT_LLAMA31_8B_BASE_MODEL)
    parser.add_argument("--sft-adapter-path", default=None)
    parser.add_argument("--rl-adapter-path", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--num-predict", type=int, default=768)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--no-4bit", action="store_true")
    parser.add_argument("--skip-evaluation", action="store_true")
    return parser.parse_args()


def run_cmd(command: list[str]) -> None:
    print("$ " + " ".join(command), flush=True)
    subprocess.run(command, check=True)


def evaluate_strategy(
    strategy: str,
    repair_jsonl: Path,
    output_dir: Path,
    args: argparse.Namespace,
) -> None:
    final_eval = output_dir / f"{strategy}_final_evaluated.jsonl"
    final_metrics = output_dir / f"{strategy}_final_metrics.json"
    final_metrics_md = output_dir / f"{strategy}_final_metrics.md"
    adapted_jsonl = output_dir / f"{strategy}_adapted_final_input.jsonl"
    asa_json = output_dir / f"{strategy}_asa_metrics.json"
    asa_md = output_dir / f"{strategy}_asa_metrics.md"
    asa_rows = output_dir / f"{strategy}_asa_rows.jsonl"

    run_cmd(
        [
            sys.executable,
            "-m",
            "src.eval.evaluate_final_sql",
            "--input-jsonl",
            str(repair_jsonl),
            "--output-jsonl",
            str(final_eval),
            "--metrics-json",
            str(final_metrics),
            "--metrics-md",
            str(final_metrics_md),
            "--adapted-jsonl",
            str(adapted_jsonl),
            "--db-path",
            args.db_path,
            "--workers",
            str(args.workers),
        ]
    )
    run_cmd(
        [
            sys.executable,
            "-m",
            "src.eval.evaluate_asa",
            "--before-jsonl",
            args.baseline_eval_jsonl,
            "--after-jsonl",
            str(final_eval),
            "--schema-path",
            args.schema_annotations_path,
            "--output-json",
            str(asa_json),
            "--output-md",
            str(asa_md),
            "--row-output-jsonl",
            str(asa_rows),
        ]
    )


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    schema_text = load_schema_text(args.schema_text_path)

    manifest = {
        "fixed_verifier_jsonl": args.fixed_verifier_jsonl,
        "baseline_eval_jsonl": args.baseline_eval_jsonl,
        "schema_text_path": args.schema_text_path,
        "schema_annotations_path": args.schema_annotations_path,
        "db_path": args.db_path,
        "base_model": args.base_model,
        "prompt_model_name": args.prompt_model_name,
        "sft_adapter_path": args.sft_adapter_path,
        "rl_adapter_path": args.rl_adapter_path,
        "strategies": args.strategies,
    }
    (output_dir / "fixed_verifier_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    for strategy in args.strategies:
        if strategy == "prompt_llama31_8b":
            generator = build_ollama_generator(
                model_name=args.prompt_model_name,
                temperature=args.temperature,
                num_predict=args.num_predict,
                timeout=args.timeout,
            )
            repair_model = args.prompt_model_name
        elif strategy == "sft_llama31_8b":
            if not args.sft_adapter_path:
                raise ValueError("--sft-adapter-path is required for sft_llama31_8b")
            generator = build_hf_generator(
                model_name_or_path=args.base_model,
                adapter_path=args.sft_adapter_path,
                max_new_tokens=args.num_predict,
                temperature=args.temperature,
                load_in_4bit=not args.no_4bit,
            )
            repair_model = f"{args.base_model}+{args.sft_adapter_path}"
        else:
            if not args.rl_adapter_path:
                raise ValueError("--rl-adapter-path is required for rl_llama31_8b")
            generator = build_hf_generator(
                model_name_or_path=args.base_model,
                adapter_path=args.rl_adapter_path,
                max_new_tokens=args.num_predict,
                temperature=args.temperature,
                load_in_4bit=not args.no_4bit,
            )
            repair_model = f"{args.base_model}+{args.rl_adapter_path}"

        repair_jsonl = output_dir / f"{strategy}_repairs.jsonl"
        summary_json = output_dir / f"{strategy}_repair_summary.json"
        summary = generate_repairs_from_file(
            fixed_verifier_jsonl=args.fixed_verifier_jsonl,
            output_jsonl=repair_jsonl,
            summary_json=summary_json,
            schema_text=schema_text,
            generator=generator,
            repair_model=repair_model,
            strategy=strategy,
        )
        print(f"{strategy}: generated {summary['generated_repairs']} repairs")

        if not args.skip_evaluation:
            evaluate_strategy(strategy, repair_jsonl, output_dir, args)

    if not args.skip_evaluation:
        run_cmd(
            [
                sys.executable,
                "scripts/dev/build_repair_strategy_ablation_table.py",
                "--ablation-dir",
                str(output_dir),
                "--output-md",
                str(output_dir / "repair_strategy_ablation_table.md"),
                "--output-json",
                str(output_dir / "repair_strategy_ablation_table.json"),
                "--strategies",
                *args.strategies,
            ]
        )

    print(f"Repair strategy ablation outputs: {output_dir}")


if __name__ == "__main__":
    main()
