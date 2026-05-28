import json
import re
from pathlib import Path
from typing import Any

SPECIAL_TOKENS = ("<|im_end|>", "</s>", "<|endoftext|>")


def build_zero_shot_prompt(question: str, schema: str) -> str:
    return f"""Instruction:
You are given a database schema and a natural language question.
Generate a valid SQL query that answers the question.
Return only the SQL query. No explanation.

Schema:
{schema}

Question:
{question}
"""


def build_few_shot_prompt(
    question: str,
    schema: str,
    examples: list[dict[str, Any]],
) -> str:
    if len(examples) != 3:
        raise ValueError(
            f"Few-shot prompt requires exactly 3 examples, got {len(examples)}"
        )

    return f"""Instruction:
You are given a database schema and a natural language question.
Generate a valid SQL query that answers the question.
Return only the SQL query. No explanation.

Here are some examples:
Schema:
{schema}

Question: {examples[0]["question"]}
SQL: {examples[0]["gold_sql"]}

Question: {examples[1]["question"]}
SQL: {examples[1]["gold_sql"]}

Question: {examples[2]["question"]}
SQL: {examples[2]["gold_sql"]}

Now answer:
Schema:
{schema}
Question: {question}
SQL:
"""


def build_baseline_prompt(
    question: str,
    schema: str,
    prompt_setting: str = "zero_shot",
    examples: list[dict[str, Any]] | None = None,
) -> str:
    """
    Backwards-compatible prompt builder.

    Prefer calling build_zero_shot_prompt() or build_few_shot_prompt() directly
    inside baseline_runner.py.
    """
    if prompt_setting == "zero_shot":
        return build_zero_shot_prompt(question=question, schema=schema)

    if prompt_setting == "few_shot":
        if examples is None:
            raise ValueError("few_shot prompt_setting requires examples.")
        return build_few_shot_prompt(
            question=question,
            schema=schema,
            examples=examples,
        )

    raise ValueError(f"Unsupported prompt_setting: {prompt_setting}")


def strip_special_tokens(text: str) -> str:
    cleaned = text

    for token in SPECIAL_TOKENS:
        cleaned = cleaned.replace(token, "")

    return cleaned.strip()


def trim_after_sql_statement(text: str) -> str:
    """
    Conservative cleanup for non-fenced model output.

    If a semicolon exists, keep content up to the first semicolon.
    This prevents trailing explanation from entering generated_sql.
    """
    text = text.strip()

    semicolon_index = text.find(";")
    if semicolon_index != -1:
        return text[: semicolon_index + 1].strip()

    return text


def extract_sql(text: str | None) -> str:
    """
    Extract SQL from model output.

    raw_output should remain unchanged in baseline_runner.py.
    This function only cleans the generated_sql field.
    """
    if text is None:
        return ""

    cleaned_text = strip_special_tokens(text)

    sql_block = re.search(
        r"```sql\s*(.*?)```",
        cleaned_text,
        re.DOTALL | re.IGNORECASE,
    )
    if sql_block:
        return strip_special_tokens(sql_block.group(1))

    generic_block = re.search(r"```\s*(.*?)```", cleaned_text, re.DOTALL)
    if generic_block:
        return strip_special_tokens(generic_block.group(1))

    sql_start = re.search(r"\b(select|with)\b", cleaned_text, re.IGNORECASE)
    if sql_start:
        candidate = cleaned_text[sql_start.start() :]
        return strip_special_tokens(trim_after_sql_statement(candidate))

    return cleaned_text


def load_completed_run_keys(output_path: str | Path) -> set[tuple[str, str | None, str]]:
    output_path = Path(output_path)

    if not output_path.exists():
        return set()

    completed = set()

    with output_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                completed.add(
                    (
                        row["question_id"],
                        row.get("generator") or row.get("model_key"),
                        row.get("prompt_setting", "zero_shot"),
                    )
                )
            except Exception:
                continue

    return completed


def load_completed_question_ids(output_path: str | Path) -> set[str]:
    """
    Legacy helper.

    Prefer load_completed_run_keys() because the same question may be run with
    different models or prompt settings.
    """
    output_path = Path(output_path)

    if not output_path.exists():
        return set()

    completed = set()

    with output_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                completed.add(row["question_id"])
            except Exception:
                continue

    return completed


def generate_mlx_output(model, tokenizer, prompt: str, max_new_tokens: int = 192) -> str:
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


def generate_llama_cpp_output(llm, prompt: str, max_new_tokens: int = 192) -> str:
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
            stop=["<|im_end|>", "</s>", "<|endoftext|>"],
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
            stop=["<|im_end|>", "</s>", "<|endoftext|>"],
            echo=False,
        )

        return response["choices"][0]["text"].strip()