from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from src.eval.evaluate_baseline_sql import GROUP_A, GROUP_B
from src.repair_learning.data import build_learning_examples
from src.repair_learning.generate import generate_repair_rows
from src.repair_learning.prompting import parse_repaired_sql_from_text
from src.repair_learning.rl import RewardConfig, compute_repair_reward
from scripts.dev.build_repair_strategy_ablation_table import build_rows, write_outputs


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


def test_parse_repaired_sql_from_text_accepts_json_and_plain_sql() -> None:
    sql, parsed, error = parse_repaired_sql_from_text('{"repaired_sql": "SELECT 1"}')
    assert sql == "SELECT 1"
    assert parsed == {"repaired_sql": "SELECT 1"}
    assert error is None

    sql, parsed, error = parse_repaired_sql_from_text("SELECT 2;")
    assert sql == "SELECT 2;"
    assert parsed is None
    assert error is None


def test_reward_penalizes_invalid_unchanged_wrong_and_corruption(tmp_path: Path) -> None:
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
        compute_repair_reward(wrong_example, '{"repaired_sql": "SELECT 2"}', reward_config)
        == reward_config.unchanged_wrong_penalty
    )

    correct_example = {
        "question_id": "q3",
        "evaluation_group": GROUP_A,
        "original_generated_sql": "SELECT 1",
        "gold_sql": "SELECT 1",
    }
    assert (
        compute_repair_reward(correct_example, '{"repaired_sql": "SELECT 2"}', reward_config)
        == reward_config.corruption_penalty
    )


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
        "| Repair Strategy | Correction Rate | Corruption Rate | ASA |"
    )
    assert "| Prompted Llama-3.1-8B | 50.00% (2/4) | 20.00% (1/5) | 75.00% |" in output_md.read_text(
        encoding="utf-8"
    )
    row = json.loads(output_json.read_text(encoding="utf-8"))[0]
    assert row["asa_strict_accuracy"] == 0.75
    assert row["asa_lower_bound_accuracy"] == 0.7
