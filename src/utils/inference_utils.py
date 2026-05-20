import json
import re
from pathlib import Path


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


def generate_mlx_output(model, tokenizer, prompt, max_new_tokens=192):
    from mlx_lm import generate

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
    ).strip()


def generate_llama_cpp_output(llm, prompt, max_new_tokens=192):
    """
    Backend-specific generation for GGUF models via llama.cpp.

    The baseline prompt itself remains identical across models.
    This wrapper only handles llama.cpp/GGUF generation.
    """

    messages = [{"role": "user", "content": prompt}]

    try:
        response = llm.create_chat_completion(
            messages=messages,
            max_tokens=max_new_tokens,
            temperature=0.0,
            top_p=1.0,
        )
        return response["choices"][0]["message"]["content"].strip()

    except Exception:
        # Fallback if the GGUF file does not expose a usable chat template.
        formatted_prompt = f"""<|im_start|>user
{prompt}<|im_end|>
<|im_start|>assistant
"""

        response = llm(
            formatted_prompt,
            max_tokens=max_new_tokens,
            temperature=0.0,
            top_p=1.0,
            stop=["<|im_end|>", "</s>"],
            echo=False,
        )

        return response["choices"][0]["text"].strip()