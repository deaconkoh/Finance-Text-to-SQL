"""Optional RL refinement for learned SQL repairers."""

from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from src.asa_metrics.asa_metrics import evaluate_asa_row
from src.eval.evaluate_baseline_sql import GROUP_A
from src.repair_learning import DEFAULT_LLAMA31_8B_BASE_MODEL
from src.repair_learning.prompting import parse_repaired_sql_from_text


@dataclass
class RewardConfig:
    db_path: str
    schema_annotations_path: str
    correction_reward: float = 2.0
    asa_bonus: float = 0.5
    corruption_penalty: float = -2.0
    invalid_penalty: float = -1.0
    remaining_wrong_penalty: float = -0.25
    _schema_annotations: dict[str, Any] | None = field(default=None, init=False, repr=False)


@dataclass
class RLConfig:
    train_jsonl: str
    sft_adapter_path: str
    output_dir: str
    db_path: str
    schema_annotations_path: str
    base_model: str = DEFAULT_LLAMA31_8B_BASE_MODEL
    max_new_tokens: int = 768
    learning_rate: float = 1e-6
    batch_size: int = 8
    mini_batch_size: int = 1
    ppo_epochs: int = 1
    load_in_4bit: bool = True
    dataset_num_proc: int = 4
    reward_workers: int = 4
    seed: int = 42


def _execute_sql(db_path: str, sql: str) -> tuple[bool, list[tuple[Any, ...]] | None]:
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute(sql)
            return True, cursor.fetchall()
    except Exception:
        return False, None


def compute_repair_reward(
    example: dict[str, Any],
    model_output: str,
    reward_config: RewardConfig,
) -> float:
    """Reward EX correction, ASA pass, and penalize corruption/invalid output."""

    repaired_sql, _, parse_error = parse_repaired_sql_from_text(model_output)
    if not repaired_sql or parse_error:
        return reward_config.invalid_penalty

    original_group = example.get("evaluation_group")

    repaired_ok, repaired_result = _execute_sql(reward_config.db_path, repaired_sql)
    gold_ok, gold_result = _execute_sql(reward_config.db_path, str(example.get("gold_sql") or ""))
    if not repaired_ok or not gold_ok:
        return reward_config.invalid_penalty

    ex_correct = repaired_result == gold_result
    if original_group != GROUP_A and not ex_correct:
        return reward_config.remaining_wrong_penalty

    reward = 0.0
    if original_group == GROUP_A and not ex_correct:
        reward += reward_config.corruption_penalty
    elif original_group != GROUP_A and ex_correct:
        reward += reward_config.correction_reward

    if ex_correct:
        if reward_config._schema_annotations is None:
            reward_config._schema_annotations = json.loads(
                Path(reward_config.schema_annotations_path).read_text(encoding="utf-8")
            )
        asa_row = evaluate_asa_row(
            {
                "question_id": example.get("question_id"),
                "gold_sql": example.get("gold_sql"),
                "generated_sql": repaired_sql,
                "execution_match": True,
            },
            schema_annotations=reward_config._schema_annotations,
        )
        if asa_row.get("asa_strict") == 1:
            reward += reward_config.asa_bonus

    return reward


def compute_repair_rewards(
    examples: list[dict[str, Any]],
    model_outputs: list[str],
    reward_config: RewardConfig,
    workers: int,
) -> list[float]:
    """Compute independent SQLite/ASA rewards concurrently while preserving order."""

    if len(examples) != len(model_outputs):
        raise ValueError("Reward examples and model outputs must have the same length.")
    if workers < 1:
        raise ValueError("Reward workers must be >= 1.")
    pairs = list(zip(examples, model_outputs, strict=True))
    if workers == 1 or len(pairs) <= 1:
        return [compute_repair_reward(example, output, reward_config) for example, output in pairs]
    with ThreadPoolExecutor(max_workers=min(workers, len(pairs))) as executor:
        return list(
            executor.map(
                lambda pair: compute_repair_reward(pair[0], pair[1], reward_config),
                pairs,
            )
        )


def train_rl_repairer(config: RLConfig) -> None:
    """Run PPO-style RL refinement from the SFT adapter.

    This is intentionally isolated from normal repo imports because it requires
    the optional training stack and a GPU-capable environment.
    """

    try:
        import torch
        from datasets import load_dataset
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from trl import PPOConfig, PPOTrainer
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "RL training requires optional Linux training dependencies. "
            "Install requirements-linux.txt in the training environment."
        ) from exc

    tokenizer = AutoTokenizer.from_pretrained(config.base_model, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    quantization_config = None
    if config.load_in_4bit:
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )

    local_rank = int(os.environ.get("LOCAL_RANK", "0"))
    if torch.cuda.is_available():
        torch.cuda.set_device(local_rank)
    model_kwargs: dict[str, Any] = {
        "torch_dtype": torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        "quantization_config": quantization_config,
    }
    if torch.cuda.is_available():
        model_kwargs["device_map"] = {"": local_rank}
    base = AutoModelForCausalLM.from_pretrained(
        config.base_model,
        **model_kwargs,
    )
    model = PeftModel.from_pretrained(base, config.sft_adapter_path, is_trainable=True)
    raw_dataset = load_dataset("json", data_files=config.train_jsonl, split="train")
    reward_examples = [raw_dataset[index] for index in range(len(raw_dataset))]
    reward_config = RewardConfig(
        db_path=config.db_path,
        schema_annotations_path=config.schema_annotations_path,
    )

    def tokenize(example: dict[str, Any], index: int) -> dict[str, Any]:
        text = tokenizer.apply_chat_template(
            [{"role": "user", "content": example["prompt"]}],
            tokenize=False,
            add_generation_prompt=True,
        )
        encoded = tokenizer(text, truncation=True)
        encoded["query"] = text
        encoded["reward_index"] = index
        return encoded

    dataset = raw_dataset.map(
        tokenize,
        with_indices=True,
        remove_columns=raw_dataset.column_names,
        num_proc=config.dataset_num_proc if config.dataset_num_proc > 1 else None,
    )
    ppo_config = PPOConfig(
        learning_rate=config.learning_rate,
        batch_size=config.batch_size,
        mini_batch_size=config.mini_batch_size,
        ppo_epochs=config.ppo_epochs,
        output_dir=config.output_dir,
        seed=config.seed,
    )
    trainer = PPOTrainer(config=ppo_config, model=model, tokenizer=tokenizer, dataset=dataset)

    for batch in trainer.dataloader:
        query_tensors = batch["input_ids"]
        response_tensors = trainer.generate(
            query_tensors,
            max_new_tokens=config.max_new_tokens,
            return_prompt=False,
        )
        responses = tokenizer.batch_decode(response_tensors, skip_special_tokens=True)
        reward_indices = batch["reward_index"]
        if hasattr(reward_indices, "tolist"):
            reward_indices = reward_indices.tolist()
        batch_examples = [reward_examples[int(index)] for index in reward_indices]
        reward_values = compute_repair_rewards(
            batch_examples,
            responses,
            reward_config=reward_config,
            workers=config.reward_workers,
        )
        rewards = [torch.tensor(value, device=query_tensors.device) for value in reward_values]
        trainer.step(query_tensors, response_tensors, rewards)

    trainer.accelerator.wait_for_everyone()
    if trainer.accelerator.is_main_process:
        trainer.save_pretrained(config.output_dir)
