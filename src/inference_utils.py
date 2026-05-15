import json
import re
from pathlib import Path

from mlx_lm import generate


def build_baseline_prompt(question, full_schema):
    return f"""Database schema:
{full_schema}

Instruction:
Generate the SQL query. Return only the SQL query.

Question:
{question}
"""


def extract_sql(text):
    sql_block = re.search(r"```sql\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if sql_block:
        return sql_block.group(1).strip()

    generic_block = re.search(r"```\s*(.*?)```", text, re.DOTALL)
    if generic_block:
        return generic_block.group(1).strip()

    return text.strip()


def load_completed_question_ids(output_path):
    output_path = Path(output_path)

    if not output_path.exists():
        return set()

    completed = set()

    with open(output_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                completed.add(row["question_id"])
            except Exception:
                continue

    return completed


def generate_model_output(model, tokenizer, prompt, max_new_tokens=192):
    messages = [{"role": "user", "content": prompt}]

    formatted = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )

    return generate(
        model,
        tokenizer,
        prompt=formatted,
        max_tokens=max_new_tokens,
        verbose=False,
    )