#!/usr/bin/env python3
"""Refine a Llama-3.1-8B SFT repairer with RL rewards."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.repair_learning import DEFAULT_LLAMA31_8B_BASE_MODEL
from src.repair_learning.rl import RLConfig, train_rl_repairer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-jsonl", required=True)
    parser.add_argument("--sft-adapter-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--db-path", default="data/booksql/accounting.sqlite")
    parser.add_argument("--schema-annotations-path", default="data/booksql/schema_annotations.json")
    parser.add_argument("--base-model", default=DEFAULT_LLAMA31_8B_BASE_MODEL)
    parser.add_argument("--max-new-tokens", type=int, default=768)
    parser.add_argument("--learning-rate", type=float, default=1e-6)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--mini-batch-size", type=int, default=1)
    parser.add_argument("--ppo-epochs", type=int, default=1)
    parser.add_argument("--no-4bit", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    train_rl_repairer(
        RLConfig(
            train_jsonl=args.train_jsonl,
            sft_adapter_path=args.sft_adapter_path,
            output_dir=args.output_dir,
            db_path=args.db_path,
            schema_annotations_path=args.schema_annotations_path,
            base_model=args.base_model,
            max_new_tokens=args.max_new_tokens,
            learning_rate=args.learning_rate,
            batch_size=args.batch_size,
            mini_batch_size=args.mini_batch_size,
            ppo_epochs=args.ppo_epochs,
            load_in_4bit=not args.no_4bit,
        )
    )
    print(f"Saved RL repairer adapter to {args.output_dir}")


if __name__ == "__main__":
    main()

