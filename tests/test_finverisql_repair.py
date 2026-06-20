from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.finverisql.repair import (
    NonExecutableRepairRequest,
    SemanticRepairRequest,
    SemanticRepairResult,
    build_non_executable_repair_prompt,
    build_semantic_repair_prompt,
    repair_non_executable_sql,
    repair_semantic_sql,
)
from src.finverisql.repair_runner import (
    build_attempt_output_row,
    build_non_executable_repair_request,
    build_semantic_repair_request,
    classify_candidate_row,
    stable_context_hash,
)
from scripts.evaluate_finverisql_repairs import (
    build_metrics as build_repair_evaluation_metrics,
    evaluate_repair_row,
)


def make_request() -> SemanticRepairRequest:
    return SemanticRepairRequest(
        question_id="q1",
        question="What was revenue last month?",
        generated_sql="SELECT COUNT(*) FROM invoices;",
        intent_representation={"measure_kind": "monetary"},
        execution_profile={"status": "OK", "measurement": [{"function": "COUNT"}]},
        primary_mismatch_type="financial_measure_error",
        mismatch_detail="Counts rows instead of summing monetary amount.",
        failed_evidence=["Profile uses COUNT(*) not SUM(amount)."],
        repair_hint="Replace row count with the correct monetary aggregation.",
        diagnostic_dimensions={"financial_measure": "contradicted"},
        confidence="high",
    )


def test_request_and_result_serialize_cleanly() -> None:
    request = make_request()
    result = SemanticRepairResult(
        status="success",
        repaired_sql="SELECT SUM(amount) FROM invoices;",
        edit_summary="Changed COUNT(*) to SUM(amount).",
        confidence="high",
        raw_output="{}",
        error=None,
    )

    json.dumps(request.to_dict())
    json.dumps(result.to_dict())


def test_prompt_builder_uses_only_targeted_fields() -> None:
    prompt = build_semantic_repair_prompt(make_request())

    assert "Original SQL" in prompt
    assert "Structured intent" in prompt
    assert "Execution profile" in prompt
    assert "Primary mismatch type" in prompt
    assert "Failed evidence" in prompt
    assert "Repair hint" in prompt
    assert "raw_output" not in prompt
    assert "gold_sql" not in prompt


def test_non_executable_prompt_uses_error_and_schema() -> None:
    request = NonExecutableRepairRequest(
        question_id="q2",
        question="How much revenue last month?",
        generated_sql="SELECT SUM(amount FROM invoices",
        execution_error="near \"FROM\": syntax error",
        schema_text="Table invoices(id, amount, invoice_date)",
        intent_representation={"measure_kind": "monetary"},
    )

    prompt = build_non_executable_repair_prompt(request)
    assert "Execution error" in prompt
    assert "Schema" in prompt
    assert "near \"FROM\": syntax error" in prompt


def test_repair_normalization_handles_valid_and_malformed_json() -> None:
    success = repair_semantic_sql(
        make_request(),
        lambda _prompt: """```json
{"repaired_sql":"SELECT SUM(amount) FROM invoices;","edit_summary":"Use SUM(amount).","confidence":"high"}
```""",
    )
    assert success.status == "success"
    assert success.repaired_sql == "SELECT SUM(amount) FROM invoices;"

    failed = repair_semantic_sql(make_request(), lambda _prompt: "not-json")
    assert failed.status == "failed"
    assert failed.error is not None

    syntax_success = repair_non_executable_sql(
        NonExecutableRepairRequest(
            question_id="q2",
            question="Q",
            generated_sql="SELECT SUM(amount FROM invoices",
            execution_error="syntax error",
            schema_text="schema",
        ),
        lambda _prompt: '{"repaired_sql":"SELECT SUM(amount) FROM invoices;","edit_summary":"Closed SUM().","confidence":"medium"}',
    )
    assert syntax_success.status == "success"
    assert syntax_success.repaired_sql == "SELECT SUM(amount) FROM invoices;"


def test_classify_candidate_row_filters_expected_group_b_cases() -> None:
    base_row = {
        "question_id": "q1",
        "question": "Q",
        "generated_sql": "SELECT 1;",
        "evaluation_group": "B_wrong_executable",
        "status": "success",
        "intent_representation": {"foo": "bar"},
        "execution_profile": '{"status":"OK"}',
        "verification": {
            "answers_question": False,
            "should_abstain": False,
            "mismatch_type": "financial_measure_error",
            "stage2_failed_evidence": ["Used COUNT(*)"],
            "repair_hint": "Use SUM(amount).",
        },
    }

    assert classify_candidate_row(base_row) == (True, "semantic", None)

    rejected_group_a = dict(base_row, evaluation_group="A_correct_executable")
    assert classify_candidate_row(rejected_group_a) == (True, "semantic", None)

    accepted_group_a = dict(
        base_row,
        evaluation_group="A_correct_executable",
        verification={
            "answers_question": True,
            "should_abstain": False,
            "mismatch_type": None,
            "stage2_failed_evidence": [],
            "repair_hint": None,
        },
    )
    assert classify_candidate_row(accepted_group_a) == (False, None, "verification_not_rejected")

    bad_group = dict(base_row, evaluation_group="D_ambiguous")
    assert classify_candidate_row(bad_group) == (False, None, "unsupported_evaluation_group")

    abstained = dict(
        base_row,
        verification={
            "answers_question": False,
            "should_abstain": True,
            "mismatch_type": "financial_measure_error",
            "stage2_failed_evidence": ["Used COUNT(*)"],
            "repair_hint": "Use SUM(amount).",
        },
    )
    assert classify_candidate_row(abstained) == (False, None, "verification_abstained")

    missing_repair_hint = dict(
        base_row,
        verification={
            "answers_question": False,
            "should_abstain": False,
            "mismatch_type": "financial_measure_error",
        },
    )
    assert classify_candidate_row(missing_repair_hint) == (False, None, "missing_failed_evidence")

    missing_repair_hint["verification"]["stage2_failed_evidence"] = ["Used COUNT(*)"]
    assert classify_candidate_row(missing_repair_hint) == (False, None, "missing_repair_hint")

    group_c = {
        "question_id": "q3",
        "question": "Q",
        "generated_sql": "SELECT SUM(amount FROM invoices",
        "evaluation_group": "C_non_executable",
        "generated_error": "syntax error",
    }
    assert classify_candidate_row(group_c) == (True, "non_executable", None)


def test_build_request_maps_verifier_evidence() -> None:
    row = {
        "question_id": "q1",
        "question": "Q",
        "generated_sql": "SELECT COUNT(*) FROM invoices;",
        "intent_representation": {"measure_kind": "monetary"},
        "execution_profile": '{"status":"OK"}',
        "verification": {
            "mismatch_type": "financial_measure_error",
            "mismatch_detail": "Wrong measure.",
            "stage2_failed_evidence": ["Used COUNT(*)"],
            "repair_hint": "Use SUM(amount).",
            "stage2_diagnostic_dimensions": {"financial_measure": "contradicted"},
            "confidence": "high",
        },
    }

    request = build_semantic_repair_request(row)
    assert request.primary_mismatch_type == "financial_measure_error"
    assert request.failed_evidence == ["Used COUNT(*)"]
    assert request.repair_hint == "Use SUM(amount)."

    group_c_request = build_non_executable_repair_request(
        row={
            "question_id": "q2",
            "question": "Q",
            "generated_sql": "SELECT SUM(amount FROM invoices",
            "generated_error": "syntax error",
        },
        schema_text="schema",
        intent_representation={"foo": "bar"},
    )
    assert group_c_request.execution_error == "syntax error"
    assert group_c_request.intent_representation == {"foo": "bar"}


def test_attempt_output_row_is_generation_only() -> None:
    repair_result = SemanticRepairResult(
        status="success",
        repaired_sql="SELECT SUM(amount) FROM invoices;",
        edit_summary="Use SUM(amount).",
        confidence="high",
        raw_output="{}",
        error=None,
    )
    context_hash = stable_context_hash({"schema_text": "schema", "intent_mode": "nl_only"})

    output_row = build_attempt_output_row(
        source_row={
            "question_id": "q1",
            "question": "Q",
            "generated_sql": "SELECT COUNT(*) FROM invoices;",
            "gold_sql": "SELECT SUM(amount) FROM invoices;",
            "evaluation_group": "B_wrong_executable",
            "verification": {"answers_question": False},
        },
        repair_request=make_request(),
        repair_result=repair_result,
        intent_representation_used={"measure_kind": "monetary"},
        repair_mode="semantic",
        status="success",
        skip_reason=None,
        repair_model="repair-model",
        intent_mode="nl_only",
        repair_context_hash=context_hash,
    )

    assert output_row["repair_status"] == "success"
    assert output_row["repaired_sql"] == "SELECT SUM(amount) FROM invoices;"
    assert output_row["repair_context_hash"] == context_hash

    removed_fields = {
        "accepted_repair",
        "rejection_reason",
        "repaired_verification",
        "repaired_execution_status",
        "repaired_execution_error",
        "repaired_execution_result",
        "repaired_execution_profile",
        "repaired_profile_status",
    }
    assert removed_fields.isdisjoint(output_row)


def test_repair_evaluator_computes_row_and_metrics(tmp_path: Path) -> None:
    db_path = tmp_path / "fixture.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE invoices (amount INTEGER)")
    conn.executemany("INSERT INTO invoices(amount) VALUES (?)", [(10,), (20,)])
    conn.execute("PRAGMA query_only = ON")

    rows = [
        {
            "question_id": "q1",
            "status": "success",
            "evaluation_group": "B_wrong_executable",
            "gold_sql": "SELECT SUM(amount) FROM invoices;",
            "repaired_sql": "SELECT SUM(amount) FROM invoices;",
        },
        {
            "question_id": "q2",
            "status": "success",
            "evaluation_group": "B_wrong_executable",
            "gold_sql": "SELECT SUM(amount) FROM invoices;",
            "repaired_sql": "SELECT COUNT(*) FROM invoices;",
        },
        {
            "question_id": "q3",
            "status": "skipped",
            "evaluation_group": "B_wrong_executable",
            "gold_sql": "SELECT SUM(amount) FROM invoices;",
            "repaired_sql": None,
        },
    ]

    evaluated = [
        evaluate_repair_row(
            row=row,
            conn=conn,
            max_result_preview_rows=10,
            max_progress_steps=1000,
            progress_check_interval=100,
        )
        for row in rows
    ]
    conn.close()

    assert evaluated[0]["after_exec_match"] is True
    assert evaluated[0]["repair_success"] is True
    assert evaluated[1]["after_exec_match"] is False
    assert evaluated[1]["repair_success"] is False
    assert evaluated[2]["repair_evaluation_status"] == "skipped_no_repaired_sql"

    metrics = build_repair_evaluation_metrics(evaluated)
    assert metrics["attempted_repairs"] == 2
    assert metrics["generated_repairs"] == 2
    assert metrics["executable_repairs"] == 2
    assert metrics["successful_repairs"] == 1
    assert metrics["correction_rate"] == 0.5
