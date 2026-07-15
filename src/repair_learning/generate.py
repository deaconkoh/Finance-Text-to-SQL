"""Generation helpers for fixed-verifier repair strategy ablations."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Protocol

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


class SupportsBatchGeneration(Protocol):
    def __call__(self, prompt: str) -> str: ...

    def generate_batch(self, prompts: list[str]) -> list[str]: ...


class OllamaRepairGenerator:
    """Bounded concurrent wrapper around the existing Ollama generator."""

    def __init__(self, generator: Generator, workers: int) -> None:
        if workers < 1:
            raise ValueError("Ollama generation workers must be >= 1.")
        self._generator = generator
        self._workers = workers

    def __call__(self, prompt: str) -> str:
        return self._generator(prompt)

    def generate_batch(self, prompts: list[str]) -> list[str]:
        if len(prompts) <= 1 or self._workers == 1:
            return [self._generator(prompt) for prompt in prompts]
        with ThreadPoolExecutor(max_workers=min(self._workers, len(prompts))) as executor:
            return list(executor.map(self._generator, prompts))


class HuggingFaceRepairGenerator:
    """Batched local generation for one adapter assigned to one CUDA device."""

    def __init__(
        self,
        model_name_or_path: str,
        adapter_path: str | None,
        max_new_tokens: int,
        temperature: float,
        load_in_4bit: bool,
        batch_size: int,
        device: str | None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("Adapter inference batch size must be >= 1.")
        try:
            import torch
            from peft import PeftModel
            from transformers import AutoModelForCausalLM, AutoTokenizer
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

        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(model_name_or_path, use_fast=True)
        if self._tokenizer.pad_token is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token
        self._tokenizer.padding_side = "left"

        if device is None:
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
        model_kwargs: dict[str, Any] = {
            "torch_dtype": torch.bfloat16 if torch.cuda.is_available() else torch.float32,
            "quantization_config": quantization_config,
        }
        if device.startswith("cuda"):
            model_kwargs["device_map"] = {"": device}
        self._model = AutoModelForCausalLM.from_pretrained(model_name_or_path, **model_kwargs)
        if adapter_path:
            self._model = PeftModel.from_pretrained(self._model, adapter_path)
        self._model.eval()
        self._device = torch.device(device)
        self._max_new_tokens = max_new_tokens
        self._temperature = temperature
        self._batch_size = batch_size

    def __call__(self, prompt: str) -> str:
        return self.generate_batch([prompt])[0]

    def generate_batch(self, prompts: list[str]) -> list[str]:
        outputs: list[str] = []
        for start in range(0, len(prompts), self._batch_size):
            prompt_batch = prompts[start : start + self._batch_size]
            formatted = [
                self._tokenizer.apply_chat_template(
                    [{"role": "user", "content": prompt}],
                    tokenize=False,
                    add_generation_prompt=True,
                )
                for prompt in prompt_batch
            ]
            inputs = self._tokenizer(
                formatted,
                return_tensors="pt",
                padding=True,
                pad_to_multiple_of=8,
            ).to(self._device)
            with self._torch.no_grad():
                generated = self._model.generate(
                    **inputs,
                    max_new_tokens=self._max_new_tokens,
                    do_sample=self._temperature > 0,
                    temperature=self._temperature if self._temperature > 0 else None,
                    pad_token_id=self._tokenizer.eos_token_id,
                )
            prompt_length = inputs["input_ids"].shape[-1]
            outputs.extend(
                self._tokenizer.decode(row[prompt_length:], skip_special_tokens=True).strip()
                for row in generated
            )
        return outputs


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
    batch_size: int = 4,
    device: str | None = None,
) -> SupportsBatchGeneration:
    """Build a batched Hugging Face generator with optional PEFT adapter."""

    return HuggingFaceRepairGenerator(
        model_name_or_path=model_name_or_path,
        adapter_path=adapter_path,
        max_new_tokens=max_new_tokens,
        temperature=temperature,
        load_in_4bit=load_in_4bit,
        batch_size=batch_size,
        device=device,
    )


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
    generator: Generator | SupportsBatchGeneration,
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

    pending: list[tuple[dict[str, Any], str, str]] = []

    def flush_pending() -> None:
        nonlocal generated
        if not pending:
            return
        prompts = [prompt for _, prompt, _ in pending]
        try:
            batch_generate = getattr(generator, "generate_batch", None)
            raw_outputs = batch_generate(prompts) if callable(batch_generate) else [generator(prompt) for prompt in prompts]
            if len(raw_outputs) != len(pending):
                raise ValueError("Generator returned a different number of outputs than prompts.")
        except Exception as exc:
            raw_outputs = [None] * len(pending)
            batch_error = str(exc)
        else:
            batch_error = None

        for (row, prompt, repair_mode), raw_output in zip(pending, raw_outputs, strict=True):
            if batch_error is None and raw_output is not None:
                repair_result = _repair_result_from_raw(raw_output)
                if repair_result.repaired_sql:
                    generated += 1
            else:
                repair_result = SemanticRepairResult(
                    status="failed", repaired_sql=None, edit_summary=None, confidence=None,
                    raw_output=None, error=batch_error,
                )
            output_row = build_attempt_output_row(
                source_row=row, repair_request=None, repair_result=repair_result,
                intent_representation_used=row.get("intent_representation") if isinstance(row.get("intent_representation"), dict) else None,
                repair_mode=repair_mode, status="success", skip_reason=None,
                repair_model=repair_model, intent_mode=str(row.get("intent_mode") or "fixed_verifier"),
                repair_context_hash=repair_context_hash,
            )
            output_row["repair_prompt"] = prompt
            output_row["repair_strategy"] = strategy
            output_rows.append(output_row)
        pending.clear()

    for row in verifier_rows:
        is_candidate, repair_kind, skip_reason = classify_candidate_row(row)
        if not is_candidate:
            flush_pending()
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
            pending.append((row, prompt, repair_mode))
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

    flush_pending()

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
