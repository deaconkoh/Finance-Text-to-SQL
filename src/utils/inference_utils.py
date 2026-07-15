"""Inference and prompt utilities for FinVeriSQL experiments.

This module contains lightweight wrappers around local LLM backends used by
baseline generation and verifier calls. It supports Ollama chat models,
MLX-VLM verifier models, MLX-LM text models, and legacy llama.cpp/GGUF calls.

Main inputs:
- Model/backend configuration.
- Natural-language question, serialized schema, and optional few-shot examples.
- Raw model output text from baseline SQL generators.

Main outputs:
- Callable generation functions.
- Baseline prompt strings.
- Cleaned SQL strings and resumability key sets for JSONL output files.

The prompt helpers preserve the project's fixed baseline prompt behavior unless
callers explicitly choose a few-shot setting.
"""

import json
import re
from pathlib import Path
from typing import Any
import urllib.request

from src.finverisql.verifier import MaxTokensReachedError


def build_ollama_generate_fn(
    model_name: str,
    temperature: float = 0.0,
    num_predict: int = 2048,
    timeout: int = 300,
    format_json: bool = True,
    think: bool | None = None,
    seed: int | None = None,
):
    """Create an Ollama chat generation callable.

    Args:
        model_name: Ollama model name.
        temperature: Sampling temperature passed to Ollama.
        num_predict: Maximum generated tokens.
        timeout: HTTP timeout in seconds.
        format_json: Whether to request Ollama JSON-format output.
        think: Optional Ollama reasoning/thinking toggle for models that
            support it.

    Returns:
        Callable that accepts a prompt string and returns final assistant text.

    Raises:
        MaxTokensReachedError: When Ollama reports generation stopped at the
            token limit.
        ValueError: When Ollama returns no final content.

    Assumption:
        Ollama is available at `http://localhost:11434/api/chat`.
    """
    def generate(prompt: str) -> str:
        """Send one prompt to Ollama and return final assistant content."""
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

        if seed is not None:
            payload["options"]["seed"] = seed

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

        # Some reasoning models can spend the full token budget in hidden or
        # exposed thinking and never emit the final JSON payload.
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
    """Build a local MLX-VLM generation function.

    Args:
        model_name: MLX-VLM model identifier.
        temperature: Sampling temperature.
        num_predict: Maximum generated tokens.

    Returns:
        Callable mapping `prompt: str` to `output: str`.

    Assumption:
        Intended for verifier-style local models such as
        `mlx-community/gemma-4-e4b-it-4bit`.
    """
    from mlx_vlm import load, generate
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config

    print(f"Loading MLX-VLM verifier model: {model_name}")

    model, processor = load(model_name)
    config = load_config(model_name)

    def generate_fn(prompt: str) -> str:
        """Generate one verifier response with the loaded MLX-VLM model."""
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
    """Build a local MLX-LM generation function for text-only instruct models.

    Args:
        model_name: MLX-LM model identifier.
        num_predict: Maximum generated tokens.

    Returns:
        Callable mapping `prompt: str` to `output: str`.

    Assumption:
        Intended for chat-template-compatible instruct models such as
        `mlx-community/Llama-3.1-8B-Instruct-4bit`.
    """
    from mlx_lm import load, generate

    print(f"Loading MLX-LM verifier model: {model_name}")

    model, tokenizer = load(model_name)

    def generate_fn(prompt: str) -> str:
        """Generate one verifier response with the loaded MLX-LM model."""
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
    seed: int | None = None,
):
    """Dispatch verifier generation to the configured backend.

    Args:
        model_name: Local or Ollama model identifier.
        backend: One of `ollama`, `mlx-lm`, or `mlx-vlm`.
        temperature: Sampling temperature for backends that expose it.
        num_predict: Maximum generated tokens.
        timeout: Ollama HTTP timeout in seconds.

    Returns:
        Backend-specific generation callable.

    Raises:
        ValueError: If `backend` is unsupported.
    """
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
            seed=seed,
        )

    raise ValueError(
        "Unsupported verifier backend: "
        f"{backend}. Expected one of: auto, ollama, mlx-lm, mlx-vlm."
    )

def build_zero_shot_prompt(question: str, schema: str) -> str:
    """Build the zero-shot baseline Text-to-SQL prompt.

    Args:
        question: Natural-language question.
        schema: Serialized database schema.

    Returns:
        Prompt string instructing the model to return only SQL.
    """
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
    """Build the fixed three-example few-shot baseline prompt.

    Args:
        question: Natural-language question to answer.
        schema: Serialized database schema shared by the prompt.
        examples: Exactly three dictionaries containing `question` and
            `gold_sql` keys.

    Returns:
        Prompt string with three demonstrations followed by the target question.

    Raises:
        ValueError: If the caller does not provide exactly three examples.
    """
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
    """Build a baseline prompt for zero-shot or few-shot generation.

    Args:
        question: Natural-language question.
        schema: Serialized schema.
        prompt_setting: `zero_shot` or `few_shot`.
        examples: Required few-shot examples when `prompt_setting` is
            `few_shot`.

    Returns:
        Prompt string for the baseline SQL generator.

    Raises:
        ValueError: If `prompt_setting` is unsupported or examples are missing
            for few-shot mode.

    Note:
        This helper is kept for backward compatibility. New baseline code can
        call `build_zero_shot_prompt` or `build_few_shot_prompt` directly.
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
    """Remove known model stop tokens from generated text.

    Args:
        text: Raw model output.

    Returns:
        Text with known special tokens removed and surrounding whitespace
        stripped.
    """
    cleaned = text

    for token in SPECIAL_TOKENS:
        cleaned = cleaned.replace(token, "")

    return cleaned.strip()


def trim_after_sql_statement(text: str) -> str:
    """Trim trailing explanation after the first SQL statement.

    Args:
        text: Non-fenced model output beginning with SQL.

    Returns:
        Content through the first semicolon if present, otherwise stripped text.

    Assumption:
        This is conservative cleanup for `generated_sql`; callers should keep
        `raw_output` unchanged for auditability.
    """
    text = text.strip()

    semicolon_index = text.find(";")
    if semicolon_index != -1:
        return text[: semicolon_index + 1].strip()

    return text


def extract_sql(text: str | None) -> str:
    """Extract candidate SQL from raw model output.

    Args:
        text: Raw model output, possibly fenced or containing explanation.

    Returns:
        Best-effort SQL string. Returns an empty string for `None`.

    Edge cases:
        SQL fenced with ```sql is preferred, then any fenced block, then text
        starting at the first `SELECT` or `WITH`. Raw output should remain
        unchanged in the caller.
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
        # Keep only the first statement so trailing explanations do not get
        # passed into SQLite execution or semantic parsing.
        return strip_special_tokens(trim_after_sql_statement(candidate))

    return cleaned_text


def load_completed_run_keys(output_path: str | Path) -> set[tuple[str, str | None, str]]:
    """Load completed `(question_id, generator, prompt_setting)` run keys.

    Args:
        output_path: JSONL output file path.

    Returns:
        Set of completed run keys for resumable baseline generation.

    Edge cases:
        Malformed lines are skipped so partially written JSONL files do not
        block resumption.
    """
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
    """Load completed question IDs from a JSONL output file.

    Args:
        output_path: JSONL output file path.

    Returns:
        Set of `question_id` values already present in the file.

    Note:
        This is a legacy helper. Prefer `load_completed_run_keys` because the
        same question may be run with different models or prompt settings.
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
    """Generate SQL text with an MLX-LM model/tokenizer pair.

    Args:
        model: Loaded MLX-LM model.
        tokenizer: Loaded MLX-LM tokenizer with chat-template support.
        prompt: Baseline prompt string.
        max_new_tokens: Maximum generated tokens.

    Returns:
        Stripped model output text.
    """
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
    """Generate SQL text with a llama.cpp-compatible GGUF model.

    Args:
        llm: Loaded llama.cpp model object.
        prompt: Baseline prompt string.
        max_new_tokens: Maximum generated tokens.

    Returns:
        Stripped model output text.

    Assumption:
        The baseline prompt itself remains identical across models; this wrapper
        only handles llama.cpp/GGUF generation and a fallback manual chat format.
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
        # NOTE: This preserves behavior for older local GGUF files that cannot
        # create chat completions directly.
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
