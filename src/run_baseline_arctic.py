import argparse
from pathlib import Path

from datasets import load_dataset
from huggingface_hub import hf_hub_download
from llama_cpp import Llama

from data_utils import load_finch_schemas
from inference_utils import generate_llama_cpp_output
from baseline_runner import run_baseline_inference


MODEL_KEY = "arctic_text2sql_r1"
DEFAULT_PARTITION = "test"
DEFAULT_OUTPUT_PATH = "../data/outputs/baseline_arctic_test_local.jsonl"
DEFAULT_MAX_NEW_TOKENS = 192
DEFAULT_N_CTX = 8192
DEFAULT_N_THREADS = 6

DEFAULT_REPO_ID = "mradermacher/Arctic-Text2SQL-R1-7B-GGUF"
DEFAULT_FILENAME = "Arctic-Text2SQL-R1-7B.IQ4_XS.gguf"


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--model-path",
        default=None,
        help="Optional local path to Arctic GGUF model file.",
    )

    parser.add_argument(
        "--repo-id",
        default=DEFAULT_REPO_ID,
        help="Hugging Face repo ID for Arctic GGUF model.",
    )

    parser.add_argument(
        "--filename",
        default=DEFAULT_FILENAME,
        help="GGUF filename inside the Hugging Face repo.",
    )

    parser.add_argument(
        "--partition",
        default=DEFAULT_PARTITION,
        choices=["train", "dev", "val", "test"],
    )

    parser.add_argument(
        "--output-path",
        default=DEFAULT_OUTPUT_PATH,
    )

    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=DEFAULT_MAX_NEW_TOKENS,
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


def resolve_model_path(args):
    if args.model_path is not None:
        model_path = Path(args.model_path)

        if not model_path.exists():
            raise FileNotFoundError(f"Local model path does not exist: {model_path}")

        return str(model_path)

    print("No local --model-path provided.")
    print("Downloading Arctic GGUF from Hugging Face cache...")
    print(f"Repo     : {args.repo_id}")
    print(f"Filename : {args.filename}")

    return hf_hub_download(
        repo_id=args.repo_id,
        filename=args.filename,
    )


def main():
    args = parse_args()

    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("Loading FINCH dataset...")
    dataset = load_dataset("domyn/FINCH")

    print("Loading FINCH schemas...")
    schemas = load_finch_schemas()

    data = dataset["train"].filter(lambda x: x["partition"] == args.partition)

    data_list = sorted(
        list(data),
        key=lambda x: (x["db_name"], x["db_id"]),
    )

    print(f"Partition : {args.partition}")
    print(f"Rows      : {len(data_list)}")
    print(f"Output    : {output_path}")

    model_path = resolve_model_path(args)

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

    def arctic_generate_fn(prompt):
        return generate_llama_cpp_output(
            llm=llm,
            prompt=prompt,
            max_new_tokens=args.max_new_tokens,
        )

    run_baseline_inference(
        data_subset=data_list,
        schemas=schemas,
        output_path=output_path,
        model_key=MODEL_KEY,
        generate_fn=arctic_generate_fn,
        extra_metadata={
            "model_name": "Snowflake/Arctic-Text2SQL-R1-7B",
            "hf_repo_id": args.repo_id,
            "hf_filename": args.filename,
            "model_path": str(model_path),
            "inference_backend": "llama.cpp",
            "quantization": "GGUF 4-bit",
            "max_new_tokens": args.max_new_tokens,
            "n_ctx": args.n_ctx,
            "n_gpu_layers": args.n_gpu_layers,
            "n_threads": args.n_threads,
        },
    )


if __name__ == "__main__":
    main()