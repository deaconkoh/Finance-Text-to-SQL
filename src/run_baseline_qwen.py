import json
from pathlib import Path
 
from datasets import load_dataset
from mlx_lm import load
from tqdm import tqdm
 
from data_utils import (
    load_finch_schemas,
    get_full_schema_cached,
)
from inference_utils import (
    build_baseline_prompt,
    extract_sql,
    load_completed_question_ids,
    generate_model_output,
)
 
MODEL_NAME  = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
PARTITION   = "test"
OUTPUT_PATH = "../outputs/baseline_qwen_test_local.jsonl"
MAX_NEW_TOKENS = 192
MODEL_KEY   = "qwen_coder"
 
 
def run_baseline_inference(
    data_subset,
    schemas,
    model,
    tokenizer,
    output_path,
    model_key=MODEL_KEY,
):
    completed_ids = load_completed_question_ids(output_path)
    print(f"Already completed: {len(completed_ids)}")
 
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
 
                raw_output = generate_model_output(
                    model=model,
                    tokenizer=tokenizer,
                    prompt=prompt,
                    max_new_tokens=MAX_NEW_TOKENS,
                )
 
                pred_sql = extract_sql(raw_output)
 
                result = {
                    "model_key":   model_key,
                    "question_id": question_id,
                    "db_name":     record["db_name"],
                    "db_id":       record["db_id"],
                    "partition":   record["partition"],
                    "difficulty":  record["difficulty"],
                    "question":    record["question"],
                    "gold_sql":    record["SQL"],
                    "raw_output":  raw_output,
                    "pred_sql":    pred_sql,
                    "status":      "success",
                    "error":       None,
                }
 
            except Exception as e:
                result = {
                    "model_key":   model_key,
                    "question_id": question_id,
                    "db_name":     record.get("db_name"),
                    "db_id":       record.get("db_id"),
                    "partition":   record.get("partition"),
                    "difficulty":  record.get("difficulty"),
                    "question":    record.get("question"),
                    "gold_sql":    record.get("SQL"),
                    "raw_output":  None,
                    "pred_sql":    None,
                    "status":      "failed",
                    "error":       str(e),
                }
 
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
            f.flush()
            completed_ids.add(question_id)
 
    print(f"Saved results to {output_path}")
 
 
def main():
    Path("../outputs").mkdir(exist_ok=True)
 
    print("Loading FINCH dataset...")
    dataset = load_dataset("domyn/FINCH")
 
    print("Loading FINCH schemas...")
    schemas = load_finch_schemas()
 
    data = dataset["train"].filter(lambda x: x["partition"] == PARTITION)
    data_list = sorted(
        list(data),
        key=lambda x: (x["db_name"], x["db_id"])
    )
 
    print(f"Partition : {PARTITION}")
    print(f"Rows      : {len(data_list)}")
    print(f"Output    : {OUTPUT_PATH}")
 
    print("Loading model (MLX 4-bit)...")
    model, tokenizer = load(MODEL_NAME)
 
    run_baseline_inference(
        data_subset=data_list,
        schemas=schemas,
        model=model,
        tokenizer=tokenizer,
        output_path=OUTPUT_PATH,
        model_key=MODEL_KEY,
    )
 
 
if __name__ == "__main__":
    main()