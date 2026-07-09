#!/usr/bin/env python3
"""Train a Llama-3.1-8B SFT repairer adapter."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.repair_learning import DEFAULT_LLAMA31_8B_BASE_MODEL
from src.repair_learning.sft import SFTConfig, train_sft_repairer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-jsonl", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--base-model", default=DEFAULT_LLAMA31_8B_BASE_MODEL)
    parser.add_argument("--max-seq-length", type=int, default=4096)
    parser.add_argument("--num-train-epochs", type=float, default=1.0)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--per-device-train-batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=8)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--no-4bit", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    train_sft_repairer(
        SFTConfig(
            train_jsonl=args.train_jsonl,
            output_dir=args.output_dir,
            base_model=args.base_model,
            max_seq_length=args.max_seq_length,
            num_train_epochs=args.num_train_epochs,
            learning_rate=args.learning_rate,
            per_device_train_batch_size=args.per_device_train_batch_size,
            gradient_accumulation_steps=args.gradient_accumulation_steps,
            lora_r=args.lora_r,
            lora_alpha=args.lora_alpha,
            lora_dropout=args.lora_dropout,
            load_in_4bit=not args.no_4bit,
            seed=args.seed,
        )
    )
    print(f"Saved SFT repairer adapter to {args.output_dir}")


if __name__ == "__main__":
    main()

