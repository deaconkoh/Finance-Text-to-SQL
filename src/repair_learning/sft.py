"""Optional SFT training for Llama-3.1-8B repairers."""

from __future__ import annotations

import os
from dataclasses import dataclass

from src.repair_learning import DEFAULT_LLAMA31_8B_BASE_MODEL


@dataclass
class SFTConfig:
    train_jsonl: str
    output_dir: str
    base_model: str = DEFAULT_LLAMA31_8B_BASE_MODEL
    max_seq_length: int = 4096
    num_train_epochs: float = 1.0
    learning_rate: float = 2e-4
    per_device_train_batch_size: int = 4
    gradient_accumulation_steps: int = 1
    lora_r: int = 16
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    load_in_4bit: bool = True
    seed: int = 42
    dataset_num_proc: int = 4
    dataloader_num_workers: int = 4
    resume_from_checkpoint: str | None = None


def train_sft_repairer(config: SFTConfig) -> None:
    """Train a LoRA SFT adapter from repair-learning JSONL examples."""

    try:
        from datasets import load_dataset
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        from transformers import DataCollatorForSeq2Seq, Trainer, TrainingArguments
        import torch
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "SFT training requires optional Linux training dependencies. "
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
    world_size = int(os.environ.get("WORLD_SIZE", "1"))
    if torch.cuda.is_available():
        torch.cuda.set_device(local_rank)

    model_kwargs = {
        "torch_dtype": torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        "quantization_config": quantization_config,
    }
    if torch.cuda.is_available():
        # Each Accelerate process must own exactly one GPU; device_map="auto"
        # would otherwise place one model across both GPUs in every process.
        model_kwargs["device_map"] = {"": local_rank}
    model = AutoModelForCausalLM.from_pretrained(
        config.base_model,
        **model_kwargs,
    )

    peft_config = LoraConfig(
        r=config.lora_r,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=[
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
    )
    if config.load_in_4bit:
        model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, peft_config)
    model.config.use_cache = False

    dataset = load_dataset("json", data_files=config.train_jsonl, split="train")

    def tokenize_example(example: dict) -> dict:
        prompt_messages = [{"role": "user", "content": example["prompt"]}]
        full_messages = [
            {"role": "user", "content": example["prompt"]},
            {"role": "assistant", "content": example["completion"]},
        ]
        prompt_text = tokenizer.apply_chat_template(
            prompt_messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        full_text = tokenizer.apply_chat_template(full_messages, tokenize=False)
        tokenized = tokenizer(
            full_text,
            truncation=True,
            max_length=config.max_seq_length,
        )
        prompt_ids = tokenizer(
            prompt_text,
            truncation=True,
            max_length=config.max_seq_length,
            add_special_tokens=False,
        )["input_ids"]
        labels = list(tokenized["input_ids"])
        prompt_len = min(len(prompt_ids), len(labels))
        labels[:prompt_len] = [-100] * prompt_len
        labels = [
            label if mask else -100
            for label, mask in zip(labels, tokenized["attention_mask"], strict=True)
        ]
        tokenized["labels"] = labels
        return tokenized

    tokenized_dataset = dataset.map(
        tokenize_example,
        remove_columns=dataset.column_names,
        num_proc=config.dataset_num_proc if config.dataset_num_proc > 1 else None,
    )
    training_args = TrainingArguments(
        output_dir=config.output_dir,
        num_train_epochs=config.num_train_epochs,
        learning_rate=config.learning_rate,
        per_device_train_batch_size=config.per_device_train_batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        logging_steps=10,
        save_strategy="epoch",
        seed=config.seed,
        bf16=torch.cuda.is_available(),
        remove_unused_columns=False,
        dataloader_num_workers=config.dataloader_num_workers,
        ddp_find_unused_parameters=False if world_size > 1 else None,
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized_dataset,
        tokenizer=tokenizer,
        data_collator=DataCollatorForSeq2Seq(
            tokenizer=tokenizer,
            label_pad_token_id=-100,
            pad_to_multiple_of=8,
        ),
    )
    trainer.train(resume_from_checkpoint=config.resume_from_checkpoint)
    if trainer.is_world_process_zero():
        trainer.save_model(config.output_dir)
        tokenizer.save_pretrained(config.output_dir)
