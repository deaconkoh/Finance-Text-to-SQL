"""Generation helpers for fixed-verifier repair strategy ablations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from src.finverisql.repair import SemanticRepairResult
from src.finverisql.repair_runner import (
    build_attempt_output_row,
    classify_candidate_row,
    stable_context_hash,
)
from src.repair_learning.data import read_jsonl, write_jsonl
from src.repair_learning.prompting import build_prompt_for_candidate, parse_repaired_sql_from_text
from src.utils.inference_utils import build_verifier_generate_fn


Generator = Callable[[str], str]


def build_ollama_generator(
    model_name: str,
    temperature: float,
    num_predict: int,
    timeout: int,
) -> Generator:
    return build_verifier_generate_fn(
        model_name=model_name,
        backend="ollama",
        temperature=temperature,
        num_predict=num_predict,
        timeout=timeout,
    )


def build_hf_generator(
    model_name_or_path: str,
    adapter_path: str | None,
    max_new_tokens: int,
    temperature: float,
    load_in_4bit: bool,
) -> Generator:
    """Build a Hugging Face text-generation callable with optional PEFT adapter."""

    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from peft import PeftModel
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Hugging Face learned repair generation requires the optional "
            "training dependencies from requirements-linux.txt."
        ) from exc

    quantization_config = None
    if load_in_4bit:
        from transformers import BitsAndBytesConfig

        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )

    tokenizer = AutoTokenizer.from_pretrained(model_name_or_path, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_name_or_path,
        device_map="auto",
        torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        quantization_config=quantization_config,
    )
    if adapter_path:
        model = PeftModel.from_pretrained(model, adapter_path)
    model.eval()

    def generate(prompt: str) -> str:
        messages = [{"role": "user", "content": prompt}]
        formatted = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        inputs = tokenizer(formatted, return_tensors="pt").to(model.device)
        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=temperature > 0,
                temperature=temperature if temperature > 0 else None,
                pad_token_id=tokenizer.eos_token_id,
            )
        generated = outputs[0][inputs["input_ids"].shape[-1] :]
        return tokenizer.decode(generated, skip_special_tokens=True).strip()

    return generate


def _repair_result_from_raw(raw_output: str) -> SemanticRepairResult:
    repaired_sql, parsed, error = parse_repaired_sql_from_text(raw_output)
    return SemanticRepairResult(
        status="success" if repaired_sql else "failed",
        repaired_sql=repaired_sql,
        edit_summary=(
            str(parsed.get("edit_summary"))
            if isinstance(parsed, dict) and parsed.get("edit_summary") is not None
            else None
        ),
        confidence=(
            str(parsed.get("confidence"))
            if isinstance(parsed, dict) and parsed.get("confidence") is not None
            else None
        ),
        raw_output=raw_output,
        error=error,
    )


def generate_repair_rows(
    verifier_rows: list[dict[str, Any]],
    schema_text: str | None,
    generator: Generator,
    repair_model: str,
    strategy: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output_rows: list[dict[str, Any]] = []
    attempted = generated = skipped = 0
    repair_context_hash = stable_context_hash(
        {
            "strategy": strategy,
            "schema_text": schema_text,
            "fixed_verifier": True,
            "prompt_family": "specialized_first_attempt_or_non_executable",
        }
    )

    for row in verifier_rows:
        is_candidate, repair_kind, skip_reason = classify_candidate_row(row)
        if not is_candidate:
            skipped += 1
            output_rows.append(
                build_attempt_output_row(
                    source_row=row,
                    repair_request=None,
                    repair_result=None,
                    intent_representation_used=(
                        row.get("intent_representation")
                        if isinstance(row.get("intent_representation"), dict)
                        else None
                    ),
                    repair_mode=repair_kind,
                    status="skipped",
                    skip_reason=skip_reason,
                    repair_model=repair_model,
                    intent_mode=str(row.get("intent_mode") or "fixed_verifier"),
                    repair_context_hash=repair_context_hash,
                )
            )
            output_rows[-1]["repair_strategy"] = strategy
            continue

        attempted += 1
        try:
            prompt, repair_mode = build_prompt_for_candidate(row, schema_text=schema_text)
            raw_output = generator(prompt)
            repair_result = _repair_result_from_raw(raw_output)
            if repair_result.repaired_sql:
                generated += 1
            output_row = build_attempt_output_row(
                source_row=row,
                repair_request=None,
                repair_result=repair_result,
                intent_representation_used=(
                    row.get("intent_representation")
                    if isinstance(row.get("intent_representation"), dict)
                    else None
                ),
                repair_mode=repair_mode,
                status="success",
                skip_reason=None,
                repair_model=repair_model,
                intent_mode=str(row.get("intent_mode") or "fixed_verifier"),
                repair_context_hash=repair_context_hash,
            )
            output_row["repair_prompt"] = prompt
        except Exception as exc:
            repair_result = SemanticRepairResult(
                status="failed",
                repaired_sql=None,
                edit_summary=None,
                confidence=None,
                raw_output=None,
                error=str(exc),
            )
            output_row = build_attempt_output_row(
                source_row=row,
                repair_request=None,
                repair_result=repair_result,
                intent_representation_used=(
                    row.get("intent_representation")
                    if isinstance(row.get("intent_representation"), dict)
                    else None
                ),
                repair_mode=repair_kind,
                status="success",
                skip_reason=None,
                repair_model=repair_model,
                intent_mode=str(row.get("intent_mode") or "fixed_verifier"),
                repair_context_hash=repair_context_hash,
            )

        output_row["repair_strategy"] = strategy
        output_rows.append(output_row)

    summary = {
        "input_rows": len(verifier_rows),
        "attempted_repairs": attempted,
        "generated_repairs": generated,
        "skipped_rows": skipped,
        "strategy": strategy,
        "repair_model": repair_model,
    }
    return output_rows, summary


def write_summary(path: str | Path, summary: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def generate_repairs_from_file(
    fixed_verifier_jsonl: str | Path,
    output_jsonl: str | Path,
    summary_json: str | Path,
    schema_text: str | None,
    generator: Generator,
    repair_model: str,
    strategy: str,
) -> dict[str, Any]:
    rows = read_jsonl(fixed_verifier_jsonl)
    output_rows, summary = generate_repair_rows(
        verifier_rows=rows,
        schema_text=schema_text,
        generator=generator,
        repair_model=repair_model,
        strategy=strategy,
    )
    summary["fixed_verifier_jsonl"] = str(fixed_verifier_jsonl)
    summary["output_jsonl"] = str(output_jsonl)
    write_jsonl(output_jsonl, output_rows)
    write_summary(summary_json, summary)
    return summary

