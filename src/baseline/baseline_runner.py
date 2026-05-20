"""
baseline_runner.py

Run commands should be executed from the project root:

Qwen on the custom KI subset:
python -m src.baseline.baseline_runner \
  --model qwen \
  --custom-subset-csv data/subsets/Labelled_candidate_set.csv \
  --output-path data/outputs/baseline_qwen_ki_subset.jsonl

Arctic on the custom KI subset:
python -m src.baseline.baseline_runner \
  --model arctic \
  --custom-subset-csv data/subsets/Labelled_candidate_set.csv \
  --output-path data/outputs/baseline_arctic_ki_subset.jsonl

Optional: To run FINCH internal partition evaluation instead of the custom subset, omit the 
--custom-subset-csv argument and specify --partition if needed (default is "test"):

Note:
- Qwen uses MLX 4-bit.
- Arctic uses llama.cpp with a GGUF model. If --model-path is not provided, the default GGUF file is downloaded from Hugging Face cache.
"""

import argparse
import json
import sys
from pathlib import Path

from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = Path(__file__).resolve().parents[1]
for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

try:
    from src.utils.data_utils import (
        get_full_schema_cached,
        load_baseline_eval_records,
        load_finch_schemas,
    )
    from src.utils.inference_utils import (
        build_baseline_prompt,
        extract_sql,
        load_completed_question_ids,
    )
except ModuleNotFoundError:
    from utils.data_utils import (
        get_full_schema_cached,
        load_baseline_eval_records,
        load_finch_schemas,
    )
    from utils.inference_utils import (
        build_baseline_prompt,
        extract_sql,
        load_completed_question_ids,
    )


QWEN_MODEL_NAME = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
QWEN_MODEL_KEY = "qwen_coder"

ARCTIC_MODEL_KEY = "arctic_text2sql_r1"
ARCTIC_MODEL_NAME = "Snowflake/Arctic-Text2SQL-R1-7B"
ARCTIC_DEFAULT_REPO_ID = "mradermacher/Arctic-Text2SQL-R1-7B-GGUF"
ARCTIC_DEFAULT_FILENAME = "Arctic-Text2SQL-R1-7B.IQ4_XS.gguf"

DEFAULT_PARTITION = "test"
DEFAULT_MAX_NEW_TOKENS = 192
DEFAULT_N_CTX = 8192
DEFAULT_N_THREADS = 6
DEFAULT_OUTPUT_PATHS = {
    "qwen": "data/outputs/baseline_qwen_test_local.jsonl",
    "arctic": "data/outputs/baseline_arctic_test_local.jsonl",
}


def run_baseline_inference(
    data_subset,
    schemas,
    output_path,
    model_key,
    generate_fn,
    extra_metadata=None,
):
    """
    Shared baseline runner for all models/backends.

    generate_fn should accept one argument:
        generate_fn(prompt: str) -> str
    """

    completed_ids = load_completed_question_ids(output_path)
    print(f"Already completed: {len(completed_ids)}")

    extra_metadata = extra_metadata or {}

    with open(output_path, "a", encoding="utf-8") as f:
        for record in tqdm(data_subset):
            question_id = record["question_id"]

            if question_id in completed_ids:
                continue

            try:
                full_schema = get_full_schema_cached(record, schemas)

                prompt = build_baseline_prompt(
                    question=record["question"],
                    full_schema=full_schema,
                )

                raw_output = generate_fn(prompt)
                pred_sql = extract_sql(raw_output)

                result = {
                    "model_key": model_key,
                    "question_id": question_id,
                    "db_name": record["db_name"],
                    "db_id": record["db_id"],
                    "partition": record["partition"],
                    "difficulty": record["difficulty"],
                    "question": record["question"],
                    "gold_sql": record.get("gold_sql") or record.get("SQL"),
                    "knowledge_intensity": record.get("knowledge_intensity"),
                    "raw_output": raw_output,
                    "pred_sql": pred_sql,
                    "status": "success",
                    "error": None,
                    **extra_metadata,
                }

            except Exception as e:
                result = {
                    "model_key": model_key,
                    "question_id": question_id,
                    "db_name": record.get("db_name"),
                    "db_id": record.get("db_id"),
                    "partition": record.get("partition"),
                    "difficulty": record.get("difficulty"),
                    "question": record.get("question"),
                    "gold_sql": record.get("gold_sql") or record.get("SQL"),
                    "knowledge_intensity": record.get("knowledge_intensity"),
                    "raw_output": None,
                    "pred_sql": None,
                    "status": "failed",
                    "error": str(e),
                    **extra_metadata,
                }

            f.write(json.dumps(result, ensure_ascii=False) + "\n")
            f.flush()
            completed_ids.add(question_id)

    print(f"Saved results to {output_path}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run a frozen FINCH Text-to-SQL baseline.",
    )

    parser.add_argument(
        "--model",
        required=True,
        choices=["qwen", "arctic"],
        help="Baseline model/backend to run.",
    )

    parser.add_argument(
        "--custom-subset-csv",
        default=None,
        help="Local path to custom labelled subset CSV file.",
    )

    parser.add_argument(
        "--partition",
        default=DEFAULT_PARTITION,
        choices=["train", "dev", "val", "test"],
        help="FINCH internal partition to use when no custom CSV is provided.",
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

    return parser.parse_args()


def resolve_output_path(args) -> Path:
    return Path(args.output_path or DEFAULT_OUTPUT_PATHS[args.model])


def resolve_arctic_model_path(args) -> str:
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


def build_qwen_runner(args):
    from mlx_lm import load

    try:
        from src.utils.inference_utils import generate_mlx_output
    except ModuleNotFoundError:
        from utils.inference_utils import generate_mlx_output

    print("Loading Qwen model with MLX 4-bit...")
    model, tokenizer = load(QWEN_MODEL_NAME)

    def generate_fn(prompt):
        return generate_mlx_output(
            model=model,
            tokenizer=tokenizer,
            prompt=prompt,
            max_new_tokens=args.max_new_tokens,
        )

    return QWEN_MODEL_KEY, generate_fn, {
        "model_name": QWEN_MODEL_NAME,
        "inference_backend": "mlx",
        "quantization": "4bit",
        "max_new_tokens": args.max_new_tokens,
    }


def build_arctic_runner(args):
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
        seed=42,
        verbose=args.verbose,
    )

    def generate_fn(prompt):
        return generate_llama_cpp_output(
            llm=llm,
            prompt=prompt,
            max_new_tokens=args.max_new_tokens,
        )

    return ARCTIC_MODEL_KEY, generate_fn, {
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


def main():
    args = parse_args()

    output_path = resolve_output_path(args)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("Loading FINCH schemas...")
    schemas = load_finch_schemas()

    if args.custom_subset_csv:
        print("Loading custom subset from CSV...")
        print(f"Input CSV : {args.custom_subset_csv}")
    else:
        print("Loading FINCH dataset...")
        print(f"Partition : {args.partition}")

    data_list = load_baseline_eval_records(
        custom_subset_csv=args.custom_subset_csv,
        partition=args.partition,
    )

    print(f"Rows      : {len(data_list)}")
    print(f"Output    : {output_path}")

    if args.model == "qwen":
        model_key, generate_fn, extra_metadata = build_qwen_runner(args)
    elif args.model == "arctic":
        model_key, generate_fn, extra_metadata = build_arctic_runner(args)
    else:
        raise ValueError(f"Unsupported model: {args.model}")

    run_baseline_inference(
        data_subset=data_list,
        schemas=schemas,
        output_path=output_path,
        model_key=model_key,
        generate_fn=generate_fn,
        extra_metadata=extra_metadata,
    )


if __name__ == "__main__":
    main()
