import json

from tqdm import tqdm

from data_utils import get_full_schema_cached
from inference_utils import (
    build_baseline_prompt,
    extract_sql,
    load_completed_question_ids,
)


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
                    "gold_sql": record["SQL"],
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
                    "gold_sql": record.get("SQL"),
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