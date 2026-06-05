import json
import re
from pathlib import Path
from typing import Any, Callable
import urllib.request

from finverisql.verifier import MaxTokensReachedError


def build_ollama_generate_fn(
    model_name: str,
    temperature: float = 0.0,
    num_predict: int = 2048,
    timeout: int = 300,
    format_json: bool = True,
    think: bool | None = None,
):
    def generate(prompt: str) -> str:
        payload = {
            "model": model_name,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
            },
        }

        if format_json:
            payload["format"] = "json"
            
        if think is not None:
            payload["think"] = think

        request = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))

        if data.get("done_reason") == "length":
            raise MaxTokensReachedError(
                f"Ollama max tokens reached ({num_predict} limit). "
                f"eval_count={data.get('eval_count')} tokens generated."
            )

        message = data.get("message") or {}
        content = (message.get("content") or "").strip()
        thinking = (message.get("thinking") or "").strip()

        if not content and thinking:
            raise ValueError(
                "Ollama returned empty final content but non-empty thinking. "
                "The model likely exhausted num_predict before producing final JSON. "
                f"done_reason={data.get('done_reason')}, "
                f"eval_count={data.get('eval_count')}, "
                f"thinking_preview={thinking[:500]}"
            )

        if not content:
            raise ValueError(
                "Ollama returned empty final content. "
                f"done_reason={data.get('done_reason')}, "
                f"eval_count={data.get('eval_count')}"
            )

        return content

    return generate

SPECIAL_TOKENS = ("<|im_end|>", "</s>", "<|endoftext|>")

def build_mlx_vlm_generate_fn(
    model_name: str,
    temperature: float = 0.0,
    num_predict: int = 768,
):
    """
    Build a local MLX-VLM generation function.

    Intended for models such as:
        mlx-community/gemma-4-e4b-it-4bit

    Returns:
        prompt: str -> output: str
    """
    from mlx_vlm import load, generate
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config

    print(f"Loading MLX-VLM verifier model: {model_name}")

    model, processor = load(model_name)
    config = load_config(model_name)

    def generate_fn(prompt: str) -> str:
        formatted_prompt = apply_chat_template(
            processor,
            config,
            prompt,
            num_images=0,
        )

        result = generate(
            model,
            processor,
            formatted_prompt,
            max_tokens=num_predict,
            temperature=temperature,
        )

        if hasattr(result, "text"):
            return result.text.strip()

        if hasattr(result, "response"):
            return result.response.strip()

        return str(result).strip()

    return generate_fn

def build_mlx_lm_generate_fn(
    model_name: str,
    num_predict: int = 768,
):
    """
    Build a local MLX-LM generation function for text-only instruct models.

    Intended for models such as:
        mlx-community/Llama-3.1-8B-Instruct-4bit
    """
    from mlx_lm import load, generate

    print(f"Loading MLX-LM verifier model: {model_name}")

    model, tokenizer = load(model_name)

    def generate_fn(prompt: str) -> str:
        messages = [
            {"role": "user", "content": prompt}
        ]

        formatted_prompt = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )

        result = generate(
            model,
            tokenizer,
            prompt=formatted_prompt,
            max_tokens=num_predict,
            verbose=False,
        )

        return result.strip()

    return generate_fn

def build_verifier_generate_fn(
    model_name: str,
    backend: str,
    temperature: float = 0.0,
    num_predict: int = 768,
    timeout: int = 300,
):
    if backend == "mlx-vlm":
        return build_mlx_vlm_generate_fn(
            model_name=model_name,
            temperature=temperature,
            num_predict=num_predict,
        )

    if backend == "mlx-lm":
        return build_mlx_lm_generate_fn(
            model_name=model_name,
            num_predict=num_predict,
        )

    if backend == "ollama":
        return build_ollama_generate_fn(
            model_name=model_name,
            temperature=temperature,
            num_predict=num_predict,
            timeout=timeout,
            format_json=True,
            think=False,
        )

    raise ValueError(
        "Unsupported verifier backend: "
        f"{backend}. Expected one of: auto, ollama, mlx-lm, mlx-vlm."
    )

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