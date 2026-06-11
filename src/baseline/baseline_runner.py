"""
Run frozen BookSQL baseline generation.

Expected setup workflow:
    python scripts/setup_booksql.py

That setup script prepares:
    data/booksql/booksql_normalized.jsonl
    data/booksql/accounting.sqlite
    data/booksql/schema.txt

This runner supports:
    - zero-shot baseline generation
    - few-shot baseline generation with exactly 3 train examples:
      one easy, one medium, one hard

Examples:

Qwen zero-shot validation:
    python -m src.baseline.baseline_runner \
      --model qwen \
      --split validation \
      --prompt-setting zero_shot

Qwen few-shot validation:
    python -m src.baseline.baseline_runner \
      --model qwen \
      --split validation \
      --prompt-setting few_shot

Qwen zero-shot on a small validation sample:
    python -m src.baseline.baseline_runner \
      --model qwen \
      --split validation \
      --prompt-setting zero_shot \
      --data-path data/booksql/booksql_validation_sample_5.jsonl \
      --output-path data/outputs/baseline_qwen_validation_sample_5_zero_shot.jsonl

Qwen few-shot on a small validation sample:
    python -m src.baseline.baseline_runner \
      --model qwen \
      --split validation \
      --prompt-setting few_shot \
      --data-path data/booksql/booksql_validation_sample_5.jsonl \
      --output-path data/outputs/baseline_qwen_validation_sample_5_few_shot.jsonl

For few-shot sample runs:
    --data-path controls the inference records.
    Few-shot examples are still loaded from the full train split by default.
    Use --few-shot-data-path only if you want to override the train source.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from tqdm import tqdm


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = Path(__file__).resolve().parents[1]

for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

try:
    from src.utils.data_utils import BOOKSQL_DATASET_NAME, load_booksql_records
    from src.utils.inference_utils import (
        build_few_shot_prompt,
        build_zero_shot_prompt,
        extract_sql,
        load_completed_run_keys,
    )
except ModuleNotFoundError:
    from utils.data_utils import BOOKSQL_DATASET_NAME, load_booksql_records
    from utils.inference_utils import (
        build_few_shot_prompt,
        build_zero_shot_prompt,
        extract_sql,
        load_completed_run_keys,
    )


QWEN_MODEL_NAME = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
QWEN_GENERATOR = "qwen"

ARCTIC_GENERATOR = "arctic"
ARCTIC_MODEL_NAME = "Snowflake/Arctic-Text2SQL-R1-7B"
ARCTIC_DEFAULT_REPO_ID = "mradermacher/Arctic-Text2SQL-R1-7B-GGUF"
ARCTIC_DEFAULT_FILENAME = "Arctic-Text2SQL-R1-7B.IQ4_XS.gguf"

DEFAULT_SPLIT = "validation"
DEFAULT_PROMPT_SETTING = "zero_shot"
DEFAULT_MAX_NEW_TOKENS = 128
DEFAULT_N_CTX = 8192
DEFAULT_N_THREADS = 4


def normalize_level(value: Any) -> str:
    """Normalize BookSQL difficulty levels.

    Args:
        value: Raw level value from a BookSQL record.

    Returns:
        Lowercase stripped level string.
    """
    return str(value).strip().lower()


def select_few_shot_examples(
    train_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Select exactly three deterministic few-shot examples.

    Args:
        train_records: Prepared BookSQL records, usually from the train split.

    Returns:
        Three example dictionaries in easy, medium, hard order.

    Raises:
        ValueError: If at least one required level is missing.

    Assumption:
        The first available example for each level is used for reproducibility.
    """
    required_levels = ("easy", "medium", "hard")
    selected_by_level: dict[str, dict[str, Any]] = {}

    for record in train_records:
        if str(record.get("split", "")).strip().lower() != "train":
            continue

        level = normalize_level(record.get("level"))

        if level in required_levels and level not in selected_by_level:
            selected_by_level[level] = record

        if len(selected_by_level) == len(required_levels):
            break

    missing_levels = [
        level for level in required_levels if level not in selected_by_level
    ]

    if missing_levels:
        raise ValueError(
            "Could not select few-shot examples. Missing train example level(s): "
            f"{', '.join(missing_levels)}"
        )

    return [
        {
            "question_id": selected_by_level[level]["question_id"],
            "level": level,
            "question": selected_by_level[level]["question"],
            "gold_sql": selected_by_level[level]["gold_sql"],
        }
        for level in required_levels
    ]


def run_baseline_inference(
    records: list[dict[str, Any]],
    output_path: Path,
    generator: str,
    generate_fn: Callable[[str], str],
    model_metadata: dict[str, Any],
    prompt_setting: str = DEFAULT_PROMPT_SETTING,
    few_shot_examples: list[dict[str, Any]] | None = None,
) -> None:
    """Run append-only baseline inference over prepared BookSQL records.

    Args:
        records: Inference records with question, schema, gold SQL, split, and
            level fields.
        output_path: JSONL output path to append/resume.
        generator: Generator key such as `qwen` or `arctic`.
        generate_fn: Callable that maps a prompt string to raw model output.
        model_metadata: Metadata stored in each output row.
        prompt_setting: `zero_shot` or `few_shot`.
        few_shot_examples: Required three examples for few-shot mode.

    Returns:
        None. Rows are appended to `output_path`.

    Raises:
        ValueError: If prompt configuration is invalid.

    Edge cases:
        Completed `(question_id, generator, prompt_setting)` keys are skipped so
        long local runs can resume safely.
    """
    if prompt_setting not in {"zero_shot", "few_shot"}:
        raise ValueError(f"Unsupported prompt setting: {prompt_setting}")

    if prompt_setting == "few_shot" and not few_shot_examples:
        raise ValueError("few_shot prompting requires selected train examples.")

    completed_keys = load_completed_run_keys(output_path)
    print(f"Already completed for this output file: {len(completed_keys)}")

    with output_path.open("a", encoding="utf-8") as f:
        for record in tqdm(records):
            run_key = (record["question_id"], generator, prompt_setting)

            if run_key in completed_keys:
                continue

            try:
                if prompt_setting == "few_shot":
                    prompt = build_few_shot_prompt(
                        question=record["question"],
                        schema=record["schema"],
                        examples=few_shot_examples,
                    )
                else:
                    prompt = build_zero_shot_prompt(
                        question=record["question"],
                        schema=record["schema"],
                    )

                raw_output = generate_fn(prompt)
                # Keep raw_output untouched for auditability; generated_sql is
                # the cleaned field used by evaluation and parsing.
                generated_sql = extract_sql(raw_output)

                result = {
                    "question_id": record["question_id"],
                    "db_id": record["db_id"],
                    "split": record["split"],
                    "level": record["level"],
                    "generator": generator,
                    "prompt_setting": prompt_setting,
                    "question": record["question"],
                    "gold_sql": record["gold_sql"],
                    "generated_sql": generated_sql,
                    "raw_output": raw_output,
                    "model_metadata": model_metadata,
                    "status": "success",
                    "error": None,
                }

                if prompt_setting == "few_shot":
                    result["few_shot_examples"] = few_shot_examples

            except Exception as exc:
                result = {
                    "question_id": record.get("question_id"),
                    "db_id": record.get("db_id"),
                    "split": record.get("split"),
                    "level": record.get("level"),
                    "generator": generator,
                    "prompt_setting": prompt_setting,
                    "question": record.get("question"),
                    "gold_sql": record.get("gold_sql"),
                    "generated_sql": None,
                    "raw_output": None,
                    "model_metadata": model_metadata,
                    "status": "failed",
                    "error": str(exc),
                }

                if prompt_setting == "few_shot":
                    result["few_shot_examples"] = few_shot_examples

            f.write(json.dumps(result, ensure_ascii=False) + "\n")
            f.flush()
            completed_keys.add(run_key)

    print(f"Saved results to {output_path}")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for baseline generation.

    Returns:
        Parsed argparse namespace.
    """
    parser = argparse.ArgumentParser(
        description="Run a frozen BookSQL Text-to-SQL baseline.",
    )

    parser.add_argument(
        "--model",
        required=True,
        choices=["qwen", "arctic"],
        help="Baseline model/backend to run.",
    )
    parser.add_argument(
        "--prompt-setting",
        default=DEFAULT_PROMPT_SETTING,
        choices=["zero_shot", "few_shot"],
        help="Prompt setting.",
    )
    parser.add_argument(
        "--split",
        default=DEFAULT_SPLIT,
        help="Prepared BookSQL split to run. Use 'all' to run every split.",
    )
    parser.add_argument(
        "--data-path",
        default=None,
        help=(
            "Optional local JSONL input for inference records. "
            "Useful for running a small validation sample."
        ),
    )
    
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Optional maximum number of inference records to run. "
            "Useful for smoke tests. Does not affect few-shot example selection."
        ),
    )
    
    parser.add_argument(
        "--few-shot-data-path",
        default=None,
        help=(
            "Optional local JSONL source for selecting few-shot train examples. "
            "If omitted, examples are loaded from the prepared full BookSQL dataset."
        ),
    )
    parser.add_argument(
        "--schema-path",
        default=None,
        help="Optional explicit BookSQL schema path.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Optional explicit BookSQL SQLite path.",
    )
    parser.add_argument(
        "--dataset-name",
        default=BOOKSQL_DATASET_NAME,
        help=(
            "Kept for compatibility with load_booksql_records(). "
            "BookSQL setup should already be prepared locally."
        ),
    )
    parser.add_argument(
        "--output-path",
        default=None,
        help="Append/resume JSONL output path.",
    )
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=DEFAULT_MAX_NEW_TOKENS,
    )
    parser.add_argument(
        "--model-path",
        default=None,
        help="Optional local path to Arctic GGUF model file.",
    )
    parser.add_argument(
        "--repo-id",
        default=ARCTIC_DEFAULT_REPO_ID,
        help="Hugging Face repo ID for Arctic GGUF model.",
    )
    parser.add_argument(
        "--filename",
        default=ARCTIC_DEFAULT_FILENAME,
        help="GGUF filename inside the Hugging Face repo.",
    )
    parser.add_argument(
        "--n-ctx",
        type=int,
        default=DEFAULT_N_CTX,
    )
    parser.add_argument(
        "--n-gpu-layers",
        type=int,
        default=-1,
        help="Use -1 to offload all supported layers to Metal GPU.",
    )
    parser.add_argument(
        "--n-threads",
        type=int,
        default=DEFAULT_N_THREADS,
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
    )
    
    parser.add_argument(
        "--n-batch",
        type=int,
        default=512,
        help="llama.cpp prompt processing batch size.",
    )
    
    parser.add_argument(
        "--question-ids",
        nargs="+",
        default=None,
        help="Optional list of specific question_ids to run.",
    )

    return parser.parse_args()


def resolve_output_path(args: argparse.Namespace) -> Path:
    """Resolve the append/resume JSONL output path.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Explicit `--output-path` when provided, otherwise the default
        `data/outputs/baseline_{model}_{split}_{prompt_setting}.jsonl`.
    """
    if args.output_path:
        return Path(args.output_path)

    filename = (
        f"baseline_{args.model}_{args.split}_{args.prompt_setting}.jsonl"
    )

    return Path("data") / "outputs" / filename


def resolve_arctic_model_path(args: argparse.Namespace) -> str:
    """Resolve the Arctic GGUF model path.

    Args:
        args: Parsed CLI arguments containing optional local path or Hugging
            Face repo/filename.

    Returns:
        Local model file path, downloading through Hugging Face cache when no
        explicit path is provided.

    Raises:
        FileNotFoundError: If `--model-path` is provided but does not exist.
    """
    if args.model_path is not None:
        model_path = Path(args.model_path)

        if not model_path.exists():
            raise FileNotFoundError(f"Local model path does not exist: {model_path}")

        return str(model_path)

    from huggingface_hub import hf_hub_download

    print("No local --model-path provided.")
    print("Downloading Arctic GGUF from Hugging Face cache...")
    print(f"Repo     : {args.repo_id}")
    print(f"Filename : {args.filename}")

    return hf_hub_download(
        repo_id=args.repo_id,
        filename=args.filename,
    )


def build_qwen_runner(
    args: argparse.Namespace,
) -> tuple[str, Callable[[str], str], dict[str, Any]]:
    """Load Qwen through MLX and return a baseline generation runner.

    Args:
        args: Parsed CLI arguments, including `max_new_tokens`.

    Returns:
        Tuple of generator key, generation callable, and model metadata.

    Assumption:
        The local environment has `mlx_lm` installed and can load the configured
        4-bit Qwen model.
    """
    from mlx_lm import load

    try:
        from src.utils.inference_utils import generate_mlx_output
    except ModuleNotFoundError:
        from utils.inference_utils import generate_mlx_output

    print("Loading Qwen model with MLX 4-bit...")
    model, tokenizer = load(QWEN_MODEL_NAME)

    def generate_fn(prompt: str) -> str:
        """Generate one Qwen baseline response with the loaded MLX model."""
        return generate_mlx_output(
            model=model,
            tokenizer=tokenizer,
            prompt=prompt,
            max_new_tokens=args.max_new_tokens,
        )

    return QWEN_GENERATOR, generate_fn, {
        "model_name": QWEN_MODEL_NAME,
        "inference_backend": "mlx",
        "quantization": "4bit",
        "max_new_tokens": args.max_new_tokens,
    }


def build_arctic_runner(
    args: argparse.Namespace,
) -> tuple[str, Callable[[str], str], dict[str, Any]]:
    """Load Arctic Text2SQL through llama.cpp and return a runner.

    Args:
        args: Parsed CLI arguments containing GGUF path/download settings and
            llama.cpp runtime configuration.

    Returns:
        Tuple of generator key, generation callable, and model metadata.

    Assumption:
        The environment has `llama_cpp` installed and the GGUF file is available
        locally or through Hugging Face cache.
    """
    from llama_cpp import Llama

    try:
        from src.utils.inference_utils import generate_llama_cpp_output
    except ModuleNotFoundError:
        from utils.inference_utils import generate_llama_cpp_output

    model_path = resolve_arctic_model_path(args)

    print("Loading Arctic GGUF with llama.cpp...")
    print(f"Model path: {model_path}")

    llm = Llama(
        model_path=model_path,
        n_ctx=args.n_ctx,
        n_gpu_layers=args.n_gpu_layers,
        n_threads=args.n_threads,
        n_batch=args.n_batch,
        flash_attn=True,
        seed=42,
        verbose=args.verbose,
    )

    def generate_fn(prompt: str) -> str:
        """Generate one Arctic baseline response with the loaded GGUF model."""
        return generate_llama_cpp_output(
            llm=llm,
            prompt=prompt,
            max_new_tokens=args.max_new_tokens,
        )

    return ARCTIC_GENERATOR, generate_fn, {
        "model_name": ARCTIC_MODEL_NAME,
        "hf_repo_id": args.repo_id,
        "hf_filename": args.filename,
        "model_path": str(model_path),
        "inference_backend": "llama.cpp",
        "quantization": "GGUF 4-bit",
        "max_new_tokens": args.max_new_tokens,
        "n_ctx": args.n_ctx,
        "n_gpu_layers": args.n_gpu_layers,
        "n_threads": args.n_threads,
    }


def load_inference_records(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Load and optionally filter BookSQL inference records.

    Args:
        args: Parsed CLI arguments controlling split, data path, question IDs,
            and optional limit.

    Returns:
        Prepared records for baseline generation.

    Raises:
        ValueError: If no records are loaded or `--limit` is invalid.
    """
    requested_split = None if args.split == "all" else args.split

    print("Loading BookSQL inference records...")
    print(f"Split     : {args.split}")

    records = load_booksql_records(
        split=requested_split,
        data_path=args.data_path,
        schema_path=args.schema_path,
        db_path=args.db_path,
        dataset_name=args.dataset_name,
    )

    if not records:
        raise ValueError(
            f"No BookSQL records loaded for split '{args.split}'. "
            "Check the split name and data source."
        )
        
    if args.question_ids:
        target_ids = set(args.question_ids)
        records = [r for r in records if r.get("question_id") in target_ids]
        print(f"Filtered to {len(records)} specific question IDs.")

    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError("--limit must be a positive integer.")

        original_count = len(records)
        records = records[: args.limit]
        print(f"Limit     : {args.limit} / {original_count} records")

    return records


def load_few_shot_examples(
    args: argparse.Namespace,
) -> list[dict[str, Any]] | None:
    """Load deterministic few-shot examples when requested.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Three few-shot examples for `few_shot` mode, otherwise `None`.
    """
    if args.prompt_setting != "few_shot":
        return None

    print("Loading BookSQL train records for few-shot examples...")

    train_records = load_booksql_records(
        split="train",
        data_path=args.few_shot_data_path,
        schema_path=args.schema_path,
        db_path=args.db_path,
        dataset_name=args.dataset_name,
    )

    few_shot_examples = select_few_shot_examples(train_records)

    print(
        "Few-shot examples: "
        + ", ".join(
            f"{example['level']}={example['question_id']}"
            for example in few_shot_examples
        )
    )

    return few_shot_examples


def main() -> None:
    """CLI entrypoint for frozen baseline generation.

    Returns:
        None. Writes append-only JSONL output through `run_baseline_inference`.
    """
    args = parse_args()

    output_path = resolve_output_path(args)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    records = load_inference_records(args)
    few_shot_examples = load_few_shot_examples(args)

    print(f"Rows      : {len(records)}")
    print(f"Output    : {output_path}")

    if args.model == "qwen":
        generator, generate_fn, model_metadata = build_qwen_runner(args)
    elif args.model == "arctic":
        generator, generate_fn, model_metadata = build_arctic_runner(args)
    else:
        raise ValueError(f"Unsupported model: {args.model}")

    run_baseline_inference(
        records=records,
        output_path=output_path,
        generator=generator,
        generate_fn=generate_fn,
        model_metadata=model_metadata,
        prompt_setting=args.prompt_setting,
        few_shot_examples=few_shot_examples,
    )


if __name__ == "__main__":
    main()
