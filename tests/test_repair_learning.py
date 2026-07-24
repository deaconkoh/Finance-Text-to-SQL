from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from src.eval.evaluate_baseline_sql import GROUP_A, GROUP_B, GROUP_C
import src.repair_learning.rl as rl
from src.repair_learning.data import build_learning_examples
from src.repair_learning.generate import OllamaRepairGenerator, generate_repair_rows
from src.repair_learning.prompting import parse_repaired_sql_from_text
from src.repair_learning.rl import RewardConfig, compute_repair_reward, compute_repair_rewards
from scripts.dev.train_rl_repairer import parse_args as parse_rl_training_args
from scripts.dev.build_repair_strategy_ablation_table import build_rows, write_outputs
from scripts.build_publication_tables import (
    ensure_common_movement_denominator,
    main_manifest_from_run_root,
    main_markdown,
)


def _semantic_candidate(group: str = GROUP_B) -> dict:
    return {
        "question_id": "q1",
        "db_id": "booksql",
        "split": "train",
        "level": "easy",
        "generator": "qwen",
        "prompt_setting": "few_shot",
        "evaluation_group": group,
        "question": "What is total revenue?",
        "gold_sql": "SELECT SUM(credit) FROM master_txn_table",
        "generated_sql": "SELECT SUM(debit) FROM master_txn_table",
        "intent_mode": "nl_only",
        "intent_representation": {"target": "revenue"},
        "execution_profile": '{"status": "OK"}',
        "verification": {
            "answers_question": False,
            "should_abstain": False,
            "mismatch_type": "financial_measure_error",
            "mismatch_detail": "uses debit instead of credit",
            "stage2_failed_evidence": ["measure uses debit"],
            "repair_hint": "Use credit for revenue.",
            "confidence": "high",
        },
    }


def test_build_learning_examples_uses_gold_sql_target_and_verifier_evidence() -> None:
    examples, manifest = build_learning_examples(
        verifier_rows=[_semantic_candidate()],
        schema_text="CREATE TABLE master_txn_table(debit REAL, credit REAL);",
        split="train",
    )

    assert manifest["examples"] == 1
    assert examples[0]["target_sql"] == "SELECT SUM(credit) FROM master_txn_table"
    assert "Repair hint:" in examples[0]["prompt"]
    assert "Use credit for revenue." in examples[0]["prompt"]
    assert json.loads(examples[0]["completion"])["repaired_sql"] == examples[0]["gold_sql"]


def test_generate_repair_rows_preserves_fixed_verifier_metadata() -> None:
    rows, summary = generate_repair_rows(
        verifier_rows=[_semantic_candidate()],
        schema_text="CREATE TABLE master_txn_table(debit REAL, credit REAL);",
        generator=lambda _prompt: json.dumps(
            {
                "repaired_sql": "SELECT SUM(credit) FROM master_txn_table",
                "edit_summary": "Use credit.",
                "confidence": "high",
            }
        ),
        repair_model="test-model",
        strategy="sft_llama31_8b",
    )

    assert summary["attempted_repairs"] == 1
    assert summary["generated_repairs"] == 1
    assert rows[0]["repair_strategy"] == "sft_llama31_8b"
    assert rows[0]["original_verification"]["mismatch_type"] == "financial_measure_error"
    assert rows[0]["repaired_sql"] == "SELECT SUM(credit) FROM master_txn_table"


def test_generate_repair_rows_uses_batched_generator() -> None:
    class BatchedGenerator:
        def __init__(self) -> None:
            self.batches: list[list[str]] = []

        def __call__(self, prompt: str) -> str:
            raise AssertionError(f"serial generation used for {prompt}")

        def generate_batch(self, prompts: list[str]) -> list[str]:
            self.batches.append(prompts)
            return [json.dumps({"repaired_sql": "SELECT 1"}) for _ in prompts]

    generator = BatchedGenerator()
    rows, summary = generate_repair_rows(
        verifier_rows=[_semantic_candidate(), _semantic_candidate()],
        schema_text="CREATE TABLE master_txn_table(debit REAL, credit REAL);",
        generator=generator,
        repair_model="test-model",
        strategy="sft_llama31_8b",
    )

    assert len(generator.batches) == 1
    assert len(generator.batches[0]) == 2
    assert summary["attempted_repairs"] == 2
    assert len(rows) == 2


def test_ollama_repair_generator_preserves_batch_order() -> None:
    generator = OllamaRepairGenerator(lambda prompt: f"out:{prompt}", workers=2)
    assert generator.generate_batch(["first", "second", "third"]) == [
        "out:first",
        "out:second",
        "out:third",
    ]


def test_parse_repaired_sql_from_text_accepts_json_and_plain_sql() -> None:
    sql, parsed, error = parse_repaired_sql_from_text('{"repaired_sql": "SELECT 1"}')
    assert sql == "SELECT 1"
    assert parsed == {"repaired_sql": "SELECT 1"}
    assert error is None

    sql, parsed, error = parse_repaired_sql_from_text("SELECT 2;")
    assert sql == "SELECT 2;"
    assert parsed is None
    assert error is None


def test_reward_outcome_matrix(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE t(x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")

    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}", encoding="utf-8")
    reward_config = RewardConfig(
        db_path=str(db_path),
        schema_annotations_path=str(schema_path),
    )

    wrong_example = {
        "question_id": "q2",
        "evaluation_group": GROUP_B,
        "original_generated_sql": "SELECT 2",
        "gold_sql": "SELECT 1",
    }
    assert compute_repair_reward(wrong_example, "", reward_config) == reward_config.invalid_penalty
    assert (
        compute_repair_reward(wrong_example, '{"repaired_sql": "SELECT missing FROM t"}', reward_config)
        == reward_config.invalid_penalty
    )
    assert (
        compute_repair_reward(wrong_example, '{"repaired_sql": "SELECT 2"}', reward_config)
        == reward_config.remaining_wrong_penalty
    )
    assert (
        compute_repair_reward(wrong_example, '{"repaired_sql": "SELECT 3"}', reward_config)
        == reward_config.remaining_wrong_penalty
    )

    correct_example = {
        "question_id": "q3",
        "evaluation_group": GROUP_A,
        "original_generated_sql": "SELECT 1",
        "gold_sql": "SELECT 1",
    }
    monkeypatch.setattr(rl, "evaluate_asa_row", lambda *_args, **_kwargs: {"asa_strict": 0})
    assert (
        compute_repair_reward(wrong_example, '{"repaired_sql": "SELECT 1"}', reward_config)
        == reward_config.correction_reward
    )
    assert (
        compute_repair_reward(correct_example, '{"repaired_sql": "SELECT 2"}', reward_config)
        == reward_config.corruption_penalty
    )
    assert (
        compute_repair_reward(
            {**wrong_example, "evaluation_group": GROUP_C},
            '{"repaired_sql": "SELECT 3"}',
            reward_config,
        )
        == reward_config.remaining_wrong_penalty
    )

    monkeypatch.setattr(rl, "evaluate_asa_row", lambda *_args, **_kwargs: {"asa_strict": 1})
    assert (
        compute_repair_reward(wrong_example, '{"repaired_sql": "SELECT 1"}', reward_config)
        == reward_config.correction_reward + reward_config.asa_bonus
    )
    assert (
        compute_repair_reward(correct_example, '{"repaired_sql": "SELECT 1"}', reward_config)
        == reward_config.asa_bonus
    )
    assert compute_repair_rewards(
        [wrong_example, correct_example],
        ['{"repaired_sql": "SELECT 2"}', '{"repaired_sql": "SELECT 2"}'],
        reward_config,
        workers=2,
    ) == [reward_config.remaining_wrong_penalty, reward_config.corruption_penalty]


def test_rl_training_defaults(monkeypatch) -> None:
    config = rl.RLConfig(
        train_jsonl="train.jsonl",
        sft_adapter_path="sft_adapter",
        output_dir="rl_adapter",
        db_path="booksql.sqlite",
        schema_annotations_path="schema.json",
    )
    assert config.learning_rate == 1e-6
    assert config.batch_size == 8
    assert config.mini_batch_size == 1
    assert config.ppo_epochs == 1

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "train_rl_repairer.py",
            "--train-jsonl", "train.jsonl",
            "--sft-adapter-path", "sft_adapter",
            "--output-dir", "rl_adapter",
        ],
    )
    args = parse_rl_training_args()
    assert args.learning_rate == 1e-6
    assert args.batch_size == 8
    assert args.mini_batch_size == 1
    assert args.ppo_epochs == 1


def test_repair_strategy_table_has_requested_columns(tmp_path: Path) -> None:
    metrics = {
        "repair_summary": {
            "repair_effectiveness": {
                "wrong_to_correct_rows": 2,
                "originally_wrong_or_nonexec_rows": 4,
            },
            "repair_safety": {
                "corrupted_originally_correct_rows": 1,
                "originally_correct_rows": 5,
            },
            "baseline_comparison": {
                "original_metric_total_examples": 9,
            },
        }
    }
    asa = {
        "sets": [
            {"label": "before", "asa_strict_accuracy": 0.1},
            {
                "label": "after",
                "asa_strict_accuracy": 0.75,
                "asa_lower_bound_accuracy": 0.7,
                "fper": 0.05,
            },
        ]
    }
    (tmp_path / "prompt_llama31_8b_final_metrics.json").write_text(
        json.dumps(metrics),
        encoding="utf-8",
    )
    (tmp_path / "prompt_llama31_8b_asa_metrics.json").write_text(
        json.dumps(asa),
        encoding="utf-8",
    )

    rows = build_rows(tmp_path, ["prompt_llama31_8b"])
    output_md = tmp_path / "table.md"
    output_json = tmp_path / "table.json"
    write_outputs(rows, output_md, output_json)

    assert output_md.read_text(encoding="utf-8").splitlines()[0] == (
        "| Repair Strategy | Correction (% of N) | Corruption (% of N) | Net Repair Gain (% of N) | ASA |"
    )
    assert "| Prompted Llama-3.1-8B | 22.22% (2/9) | 11.11% (1/9) | +11.11 pp (1/9) | 75.00% |" in output_md.read_text(
        encoding="utf-8"
    )
    row = json.loads(output_json.read_text(encoding="utf-8"))[0]
    assert row["correction_total"] == 9
    assert row["corruption_total"] == 9
    assert row["net_repair_gain_total"] == 9
    assert row["asa_strict_accuracy"] == 0.75
    assert row["asa_lower_bound_accuracy"] == 0.7


def test_common_movement_denominator_rejects_misaligned_rows() -> None:
    with pytest.raises(ValueError, match="do not share one eligible A/B/C denominator"):
        ensure_common_movement_denominator(
            [
                {"correction_total": 100},
                {"correction_total": 99},
            ],
            "test",
        )


def test_main_comparison_table_uses_compact_manuscript_columns() -> None:
    markdown = main_markdown(
        [
            {
                "system": "Generator only",
                "ex_accuracy": 0.7645,
                "asa_strict_accuracy": 0.662,
                "correction_rate": None,
            },
            {
                "system": "FinVeriSQL",
                "ex_accuracy": 0.7708,
                "asa_strict_accuracy": 0.6666,
                "correction_rate": 0.02,
                "correction_count": 2,
                "correction_total": 100,
                "corruption_rate": 0.0,
                "corruption_count": 0,
                "corruption_total": 100,
                "net_repair_gain_rate": 0.02,
                "net_repair_gain_count": 2,
                "net_repair_gain_total": 100,
            },
        ]
    )

    lines = markdown.splitlines()
    assert lines[0] == "| System | EX | ASA | Corrected | Corrupted | Net Gain |"
    assert "Delta" not in markdown
    assert "| Generator only | 76.45% | 66.20% | - | - | - |" in markdown
    assert "| FinVeriSQL | 77.08% | 66.66% | 2.00% (2/100) | 0.00% (0/100) | +2.00 pp (2/100) |" in markdown


def test_main_manifest_from_run_root_uses_standard_artifact_paths(tmp_path: Path) -> None:
    manifest = main_manifest_from_run_root(tmp_path)
    systems = manifest["main_systems"]

    assert [system["key"] for system in systems] == [
        "generator_only",
        "generic_self_refine",
        "generic_execution_guided_refine",
        "finverisql_full",
    ]
    assert systems[-1]["metrics_json"] == str(
        tmp_path / "debug" / "internal_ablation" / "full" / "full_final_metrics.json"
    )


def test_main_manifest_from_held_out_root_uses_metrics_layout(tmp_path: Path) -> None:
    (tmp_path / "metrics").mkdir()
    manifest = main_manifest_from_run_root(tmp_path)
    systems = manifest["main_systems"]

    assert systems[0]["metrics_json"] == str(tmp_path / "metrics" / "baseline" / "metrics.json")
    assert systems[-1]["metrics_json"] == str(
        tmp_path / "metrics" / "finverisql_full" / "final_metrics.json"
    )
