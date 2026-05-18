from pathlib import Path

from datasets import load_dataset
from mlx_lm import load

from data_utils import load_finch_schemas
from inference_utils import generate_mlx_output
from baseline_runner import run_baseline_inference


MODEL_NAME = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
PARTITION = "test"
OUTPUT_PATH = "../data/outputs/baseline_qwen_test_local.jsonl"
MAX_NEW_TOKENS = 192
MODEL_KEY = "qwen_coder"


def main():
    Path("../data/outputs").mkdir(parents=True, exist_ok=True)

    print("Loading FINCH dataset...")
    dataset = load_dataset("domyn/FINCH")

    print("Loading FINCH schemas...")
    schemas = load_finch_schemas()

    data = dataset["train"].filter(lambda x: x["partition"] == PARTITION)

    data_list = sorted(
        list(data),
        key=lambda x: (x["db_name"], x["db_id"]),
    )

    print(f"Partition : {PARTITION}")
    print(f"Rows      : {len(data_list)}")
    print(f"Output    : {OUTPUT_PATH}")

    print("Loading Qwen model with MLX 4-bit...")
    model, tokenizer = load(MODEL_NAME)

    def qwen_generate_fn(prompt):
        return generate_mlx_output(
            model=model,
            tokenizer=tokenizer,
            prompt=prompt,
            max_new_tokens=MAX_NEW_TOKENS,
        )

    run_baseline_inference(
        data_subset=data_list,
        schemas=schemas,
        output_path=OUTPUT_PATH,
        model_key=MODEL_KEY,
        generate_fn=qwen_generate_fn,
        extra_metadata={
            "model_name": MODEL_NAME,
            "inference_backend": "mlx",
            "quantization": "4bit",
            "max_new_tokens": MAX_NEW_TOKENS,
        },
    )


if __name__ == "__main__":
    main()