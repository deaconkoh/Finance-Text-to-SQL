#!/usr/bin/env python3
"""
Precompute FinVeriSQL Stage 1 intent decompositions.

This script runs IntentDecomposer once over an evaluation JSONL and writes a
static JSONL cache that can be reused by scripts/run_finverisql_verify.py.
Use this when intent decomposition and semantic verification use different
models, so the verifier ablations do not repeatedly load or call the intent
model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

from tqdm import tqdm


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


try:
    from src.finverisql.intent_decomposer import IntentDecomposer
    from src.finverisql.schema_loader import SchemaAnnotationStore
    from src.utils.inference_utils import build_verifier_generate_fn
except ModuleNotFoundError:
    from finverisql.intent_decomposer import IntentDecomposer
    from finverisql.schema_loader import SchemaAnnotationStore
    from utils.inference_utils import build_verifier_generate_fn


DEFAULT_SCHEMA_PATH = "data/booksql/schema_annotations.json"
DEFAULT_MODEL_NAME = "mlx-community/gemma-4-e4b-it-4bit"


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    rows: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSONL at {path}, line {line_number}: {exc}"
                ) from exc

    return rows


def append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


def stable_question_hash(question: Any) -> str:
    text = str(question or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def get_evaluation_group(row: dict[str, Any]) -> str | None:
    return (
        row.get("evaluation_group")
        or row.get("eval_group")
        or row.get("group")
    )


def get_question(row: dict[str, Any], question_key: str) -> str:
    value = row.get(question_key)

    if value is None:
        raise KeyError(f"Question key '{question_key}' not found in row.")

    return str(value)


def get_question_id(row: dict[str, Any], question_key: str) -> str:
    return str(
        row.get("question_id")
        or row.get("id")
        or row.get(question_key)
    )


def get_cache_key(
    row: dict[str, Any],
    question_key: str,
    intent_model: str,
    intent_mode: str,
) -> tuple[str, str, str, str]:
    question = row.get(question_key)

    return (
        get_question_id(row, question_key),
        stable_question_hash(question),
        intent_model,
        intent_mode,
    )


def load_completed_keys(
    output_path: str | Path,
) -> set[tuple[str, str, str, str]]:
    path = Path(output_path)

    if not path.exists():
        return set()

    completed: set[tuple[str, str, str, str]] = set()

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)

                if row.get("status") and row.get("status") != "success":
                    continue

                completed.add(
                    (
                        str(row.get("question_id") or row.get("id")),
                        str(row.get("question_hash")),
                        str(row.get("intent_model")),
                        str(row.get("intent_mode")),
                    )
                )
            except Exception:
                continue

    return completed


def maybe_shuffle_rows(
    rows: list[dict[str, Any]],
    sample_seed: int | None,
) -> list[dict[str, Any]]:
    if sample_seed is None:
        return rows

    shuffled = list(rows)
    rng = random.Random(sample_seed)
    rng.shuffle(shuffled)
    return shuffled


def run_precompute(args: argparse.Namespace) -> None:
    rows = read_jsonl(args.input_path)

    if args.evaluation_group:
        rows = [
            row for row in rows
            if get_evaluation_group(row) == args.evaluation_group
        ]

    rows = maybe_shuffle_rows(rows, args.sample_seed)

    if args.limit is not None:
        rows = rows[: args.limit]

    completed_keys = load_completed_keys(args.output_path)
    seen_input_keys: set[tuple[str, str, str, str]] = set()
    pending_rows: list[dict[str, Any]] = []

    for row in rows:
        cache_key = get_cache_key(
            row=row,
            question_key=args.question_key,
            intent_model=args.model_name,
            intent_mode=args.intent_mode,
        )

        if cache_key in completed_keys or cache_key in seen_input_keys:
            continue

        seen_input_keys.add(cache_key)
        pending_rows.append(row)

    print(f"Input rows selected: {len(rows)}")
    print(f"Unique pending intents: {len(pending_rows)}")
    print(f"Intent backend: {args.backend}")
    print(f"Intent model: {args.model_name}")
    print(f"Intent mode: {args.intent_mode}")
    print(f"Sample seed: {args.sample_seed if args.sample_seed is not None else 'disabled'}")

    if not pending_rows:
        print("Nothing left to decompose.")
        return

    schema_store = (
        SchemaAnnotationStore.from_json(args.schema_path)
        if args.intent_mode == "metadata_guided"
        else None
    )

    if args.intent_mode == "none":
        def llm_generate_fn(prompt: str) -> str:
            raise RuntimeError("intent_mode='none' should not call the LLM.")
    else:
        llm_generate_fn = build_verifier_generate_fn(
            model_name=args.model_name,
            backend=args.backend,
            temperature=args.temperature,
            num_predict=args.num_predict,
            timeout=args.timeout,
        )

    decomposer = IntentDecomposer(
        llm_call=llm_generate_fn,
        intent_mode=args.intent_mode,
        schema_store=schema_store,
    )

    for row in tqdm(pending_rows):
        question = row.get(args.question_key)
        question_id = get_question_id(row, args.question_key)

        try:
            question_text = get_question(row, args.question_key)
            intent_representation = decomposer.decompose(question_text)

            output_row = {
                "question_id": question_id,
                "db_id": row.get("db_id"),
                "split": row.get("split"),
                "level": row.get("level"),
                "evaluation_group": get_evaluation_group(row),
                "question": question_text,
                "question_hash": stable_question_hash(question),
                "intent_representation": intent_representation,
                "intent_model": args.model_name,
                "intent_backend": args.backend,
                "intent_mode": args.intent_mode,
                "status": "success",
                "error": None,
            }

        except Exception as exc:
            output_row = {
                "question_id": question_id,
                "db_id": row.get("db_id"),
                "split": row.get("split"),
                "level": row.get("level"),
                "evaluation_group": get_evaluation_group(row),
                "question": row.get(args.question_key),
                "question_hash": stable_question_hash(question),
                "intent_representation": None,
                "intent_model": args.model_name,
                "intent_backend": args.backend,
                "intent_mode": args.intent_mode,
                "status": "failed",
                "error": str(exc),
            }

        append_jsonl(args.output_path, output_row)

    print(f"Saved intent cache to: {args.output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Precompute FinVeriSQL intent decomposition JSONL cache.",
    )

    parser.add_argument(
        "--input-path",
        required=True,
        help="Input JSONL containing Text-to-SQL evaluation rows.",
    )
    parser.add_argument(
        "--output-path",
        required=True,
        help="Output JSONL path for precomputed intent representations.",
    )
    parser.add_argument(
        "--schema-path",
        default=DEFAULT_SCHEMA_PATH,
        help="Path to schema annotation JSON.",
    )
    parser.add_argument(
        "--intent-mode",
        choices=["none", "nl_only", "metadata_guided"],
        default="metadata_guided",
        help=(
            "Intent decomposition mode. 'none' skips decomposition and stores "
            "the raw question as the intent representation."
        ),
    )
    parser.add_argument(
        "--model-name",
        default=DEFAULT_MODEL_NAME,
        help="Intent decomposition model name.",
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "ollama", "mlx-lm", "mlx-vlm"],
        default="auto",
        help="Intent model backend.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Intent model temperature.",
    )
    parser.add_argument(
        "--num-predict",
        type=int,
        default=1024,
        help="Maximum tokens to generate per intent slot.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Ollama request timeout in seconds. Ignored for MLX-VLM backends.",
    )
    parser.add_argument(
        "--evaluation-group",
        default=None,
        help="Optional filter, e.g. A_correct_executable or B_wrong_executable.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Optional number of rows to decompose after filtering and optional "
            "seeded shuffling."
        ),
    )
    parser.add_argument(
        "--sample-seed",
        type=int,
        default=None,
        help=(
            "Optional random seed for reproducible row shuffling before "
            "applying --limit."
        ),
    )
    parser.add_argument(
        "--question-key",
        default="question",
        help="JSONL key containing the natural language question.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_precompute(args)


if __name__ == "__main__":
    main()
