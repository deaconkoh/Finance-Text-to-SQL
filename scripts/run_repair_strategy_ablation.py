#!/usr/bin/env python3
"""Run repair strategies over fixed FinVeriSQL verifier outputs."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.repair_learning import DEFAULT_LLAMA31_8B_BASE_MODEL
from src.repair_learning.data import load_schema_text
from src.repair_learning.generate import (
    OllamaRepairGenerator,
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
    parser.add_argument("--adapter-inference-batch-size", type=int, default=4)
    parser.add_argument("--ollama-workers", type=int, default=4)
    parser.add_argument(
        "--parallel-adapter-strategies",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run SFT and RL adapter generation concurrently on separate GPUs.",
    )
    parser.add_argument("--no-4bit", action="store_true")
    parser.add_argument("--skip-evaluation", action="store_true")
    return parser.parse_args()


def run_cmd(command: list[str], env: dict[str, str] | None = None) -> None:
    print("$ " + " ".join(command), flush=True)
    subprocess.run(command, check=True, env=env)


def generate_strategy(
    strategy: str,
    output_dir: Path,
    schema_text: str,
    args: argparse.Namespace,
) -> None:
    if strategy == "prompt_llama31_8b":
        generator = OllamaRepairGenerator(
            build_ollama_generator(
                model_name=args.prompt_model_name,
                temperature=args.temperature,
                num_predict=args.num_predict,
                timeout=args.timeout,
            ),
            workers=args.ollama_workers,
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
            batch_size=args.adapter_inference_batch_size,
            device="cuda:0" if os.environ.get("CUDA_VISIBLE_DEVICES") else None,
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
            batch_size=args.adapter_inference_batch_size,
            device="cuda:0" if os.environ.get("CUDA_VISIBLE_DEVICES") else None,
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


def strategy_command(args: argparse.Namespace, strategy: str) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--fixed-verifier-jsonl", args.fixed_verifier_jsonl,
        "--baseline-eval-jsonl", args.baseline_eval_jsonl,
        "--output-dir", args.output_dir,
        "--schema-text-path", args.schema_text_path,
        "--schema-annotations-path", args.schema_annotations_path,
        "--db-path", args.db_path,
        "--strategies", strategy,
        "--prompt-model-name", args.prompt_model_name,
        "--base-model", args.base_model,
        "--temperature", str(args.temperature),
        "--num-predict", str(args.num_predict),
        "--timeout", str(args.timeout),
        "--workers", str(args.workers),
        "--adapter-inference-batch-size", str(args.adapter_inference_batch_size),
        "--ollama-workers", str(args.ollama_workers),
        "--skip-evaluation",
        "--no-parallel-adapter-strategies",
    ]
    adapter_path = args.sft_adapter_path if strategy == "sft_llama31_8b" else args.rl_adapter_path
    if strategy == "sft_llama31_8b":
        command.extend(["--sft-adapter-path", adapter_path])
    if strategy == "rl_llama31_8b":
        command.extend(["--rl-adapter-path", adapter_path])
    if args.no_4bit:
        command.append("--no-4bit")
    return command


def run_parallel_adapter_generation(args: argparse.Namespace) -> None:
    adapter_strategies = [
        strategy for strategy in args.strategies if strategy in {"sft_llama31_8b", "rl_llama31_8b"}
    ]
    if len(adapter_strategies) < 2 or not args.parallel_adapter_strategies:
        return
    if not args.sft_adapter_path or not args.rl_adapter_path:
        raise ValueError(
            "--sft-adapter-path and --rl-adapter-path are required when "
            "parallel adapter generation is enabled."
        )
    processes: list[tuple[subprocess.Popen[bytes], object]] = []
    for gpu_index, strategy in enumerate(adapter_strategies):
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_index)
        command = strategy_command(args, strategy)
        log_path = Path(args.output_dir) / f"{strategy}_generation.log"
        log_handle = log_path.open("a", encoding="utf-8")
        log_handle.write(
            f"\nCUDA_VISIBLE_DEVICES={gpu_index} $ {' '.join(command)}\n"
        )
        log_handle.flush()
        print(
            f"CUDA_VISIBLE_DEVICES={gpu_index}; live log: {log_path}",
            flush=True,
        )
        processes.append(
            (subprocess.Popen(command, env=env, stdout=log_handle, stderr=subprocess.STDOUT), log_handle)
        )
    try:
        failures = [process.wait() for process, _ in processes]
    finally:
        for _, log_handle in processes:
            log_handle.close()
    if any(return_code != 0 for return_code in failures):
        raise subprocess.CalledProcessError(next(code for code in failures if code != 0), "adapter generation")


def dependency_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in ("torch", "transformers", "accelerate", "peft", "trl"):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    return versions


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
        "adapter_inference_batch_size": args.adapter_inference_batch_size,
        "ollama_workers": args.ollama_workers,
        "parallel_adapter_strategies": args.parallel_adapter_strategies,
        "adapter_devices": {"sft_llama31_8b": "0", "rl_llama31_8b": "1"},
        "dependency_versions": dependency_versions(),
    }
    manifest_path = output_dir / "fixed_verifier_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    run_parallel_adapter_generation(args)
    parallel_adapters = (
        args.parallel_adapter_strategies
        and {"sft_llama31_8b", "rl_llama31_8b"}.issubset(args.strategies)
    )
    for strategy in args.strategies:
        if not (parallel_adapters and strategy in {"sft_llama31_8b", "rl_llama31_8b"}):
            generate_strategy(strategy, output_dir, schema_text, args)

    # Child generation processes write their own narrow manifests. Restore the
    # parent manifest so the final artifact records the complete comparison.
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if not args.skip_evaluation:
        for strategy in args.strategies:
            evaluate_strategy(
                strategy,
                output_dir / f"{strategy}_repairs.jsonl",
                output_dir,
                args,
            )

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
