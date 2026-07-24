from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
import importlib

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.finverisql.repair import (
    NonExecutableRepairRequest,
    SemanticRepairRequest,
    SemanticRepairResult,
    build_computation_logic_repair_prompt,
    build_financial_measure_repair_prompt,
    build_financial_object_repair_prompt,
    build_non_executable_repair_prompt,
    build_semantic_repair_prompt,
    repair_non_executable_sql,
    repair_semantic_sql,
)
from src.finverisql.generic_repair import (
    GenericSemanticRepairRequest,
    build_generic_semantic_repair_prompt,
)
import src.finverisql.repair_runner as repair_runner
from src.finverisql.repair_runner import (
    build_attempt_output_row,
    build_non_executable_repair_request,
    build_semantic_repair_request,
    classify_candidate_row,
    detect_grouped_output_requirement,
    get_repair_run_key,
    load_completed_keys,
    route_mismatch_to_repair_mode,
    run_generic_semantic_repair_chain,
    run_non_executable_then_semantic_repair_chain,
    run_specialized_first_repair_no_reverification,
    run_specialized_semantic_repair_chain,
    stable_context_hash,
    validate_repair_scope,
)
from src.finverisql.schema_loader import SchemaAnnotationStore
from src.eval.evaluate_repair_candidates import (
    build_metrics as build_repair_evaluation_metrics,
    evaluate_repair_row,
)
from src.eval.evaluate_final_sql import adapt_repair_rows, build_repair_summary


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
        schema_text="Table invoices(id, amount, invoice_date)",
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


def test_completed_repair_keys_normalize_legacy_skipped_rows(tmp_path: Path) -> None:
    source_row = {
        "question_id": "q-skipped",
        "generated_sql": "SELECT 1;",
    }
    repair_context_hash = "ctx"
    output_row = build_attempt_output_row(
        source_row=source_row,
        repair_request=None,
        repair_result=None,
        intent_representation_used=None,
        repair_mode=None,
        status="skipped",
        skip_reason="verification_not_rejected",
        repair_model="llama3.1:8b",
        intent_mode="nl_only",
        repair_context_hash=repair_context_hash,
    )
    assert output_row["repair_mode"] is None

    output_path = tmp_path / "repairs.jsonl"
    output_path.write_text(json.dumps(output_row) + "\n", encoding="utf-8")

    completed = load_completed_keys(output_path)
    expected_key = get_repair_run_key(
        row=source_row,
        repair_mode="unknown",
        repair_model="llama3.1:8b",
        intent_mode="nl_only",
        repair_context_hash=repair_context_hash,
    )

    assert expected_key in completed


def test_prompt_builder_uses_only_targeted_fields() -> None:
    prompt = build_semantic_repair_prompt(make_request())

    assert "SQLite" in prompt
    assert "Original SQL" in prompt
    assert "Structured intent" in prompt
    assert "Execution profile" in prompt
    assert "Schema metadata" in prompt
    assert "Table invoices(id, amount, invoice_date)" in prompt
    assert "EXTRACT" in prompt
    assert "Primary mismatch type" in prompt
    assert "Failed evidence" in prompt
    assert "Repair hint" in prompt
    assert "raw_output" not in prompt
    assert "gold_sql" not in prompt


def test_specialized_prompts_include_scope_and_current_sql_only() -> None:
    request = make_request()
    request.repair_mode = "financial_measure_error"
    request.current_sql = "SELECT COUNT(*) FROM invoices WHERE status = 'paid';"
    request.original_sql = "SELECT COUNT(*) FROM invoices;"
    request.allowed_clause_changes = ["SELECT"]
    request.disallowed_clause_changes = [
        "FROM",
        "JOIN",
        "WHERE",
        "GROUP BY",
        "HAVING",
        "ORDER BY",
        "LIMIT",
    ]

    prompt = build_financial_measure_repair_prompt(request)

    assert "Current SQL" in prompt
    assert "SELECT COUNT(*) FROM invoices WHERE status = 'paid';" in prompt
    assert "Allowed clause changes: SELECT" in prompt
    assert "Disallowed clause changes: FROM, JOIN, WHERE, GROUP BY, HAVING, ORDER BY, LIMIT" in prompt
    assert "gold_sql" not in prompt
    assert "previous repair" not in prompt.lower()

    object_request = make_request()
    object_request.repair_mode = "financial_object_error"
    object_request.current_sql = object_request.generated_sql
    object_request.allowed_clause_changes = ["WHERE"]
    object_request.disallowed_clause_changes = [
        "SELECT",
        "FROM",
        "JOIN",
        "GROUP BY",
        "HAVING",
        "ORDER BY",
        "LIMIT",
    ]
    object_prompt = build_financial_object_repair_prompt(object_request)
    assert "Allowed clause changes: WHERE" in object_prompt
    assert "Disallowed clause changes: SELECT, FROM, JOIN, GROUP BY, HAVING, ORDER BY, LIMIT" in object_prompt

    logic_request = make_request()
    logic_request.repair_mode = "computation_logic_error"
    logic_request.current_sql = logic_request.generated_sql
    logic_request.allowed_clause_changes = ["SELECT", "GROUP BY", "HAVING", "ORDER BY", "LIMIT", "WHERE"]
    logic_request.disallowed_clause_changes = ["FROM", "JOIN"]
    logic_prompt = build_computation_logic_repair_prompt(logic_request)
    assert "Allowed clause changes: SELECT, GROUP BY, HAVING, ORDER BY, LIMIT, WHERE" in logic_prompt
    assert "Disallowed clause changes: FROM, JOIN" in logic_prompt
    assert "Do not add GROUP BY unless the question explicitly asks" in logic_prompt


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
    assert "Structured intent" not in prompt
    assert "measure_kind" not in prompt


def test_generic_repair_prompt_has_no_scope_constraints() -> None:
    request = GenericSemanticRepairRequest(
        question_id="q-generic",
        question="What paid invoice revenue was booked in 2024?",
        original_sql="SELECT COUNT(*) FROM invoices WHERE status = 'paid';",
        current_sql="SELECT COUNT(*) FROM invoices WHERE status = 'paid';",
        intent_representation={"measure_kind": "monetary"},
        execution_profile={"status": "OK", "measurement": [{"function": "COUNT"}]},
        mismatch_type="financial_measure_error",
        mismatch_detail="Counts rows instead of summing monetary amount.",
        failed_evidence=["Profile uses COUNT(*) not SUM(amount)."],
        repair_hint="Replace row count with the correct monetary aggregation.",
        diagnostic_dimensions={"financial_measure": "contradicted"},
        confidence="high",
        schema_text="Table invoices(id, amount, invoice_date)",
    )

    prompt = build_generic_semantic_repair_prompt(request)

    assert "generic semantic repair ablation" in prompt
    assert "Current SQL" in prompt
    assert "Verifier mismatch type" in prompt
    assert "financial_measure_error" in prompt
    assert "Repair hint" in prompt
    assert "Scope constraints" not in prompt
    assert "Allowed clause changes" not in prompt
    assert "Disallowed clause changes" not in prompt
    assert "Machine validation will reject" not in prompt


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


def test_semantic_repair_recovers_raw_multiline_sql_json_string() -> None:
    raw_output = """{
  "repaired_sql": "SELECT
  SUM(amount)
FROM invoices;",
  "edit_summary": "Use SUM(amount).",
  "confidence": "high"
}"""

    result = repair_semantic_sql(make_request(), lambda _prompt: raw_output)

    assert result.status == "success"
    assert result.repaired_sql == "SELECT\n  SUM(amount)\nFROM invoices;"
    assert result.raw_output == raw_output


def test_non_executable_repair_recovers_raw_multiline_sql_json_string() -> None:
    raw_output = """{
  "repaired_sql": "SELECT
  SUM(amount)
FROM invoices;",
  "edit_summary": "Closed SUM().",
  "confidence": "medium"
}"""

    result = repair_non_executable_sql(
        NonExecutableRepairRequest(
            question_id="q2",
            question="Q",
            generated_sql="SELECT SUM(amount FROM invoices",
            execution_error="syntax error",
            schema_text="schema",
        ),
        lambda _prompt: raw_output,
    )

    assert result.status == "success"
    assert result.repaired_sql == "SELECT\n  SUM(amount)\nFROM invoices;"
    assert result.raw_output == raw_output


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

    verifier_group_c = {
        "question_id": "q4",
        "question": "Q",
        "generated_sql": "SELECT bad_column FROM invoices;",
        "evaluation_group": "C_non_executable",
        "execution_profile": json.dumps(
            {
                "status": "EXECUTION_ERROR",
                "profile_type": "execution_error",
                "execution_error": "no such column: bad_column",
            }
        ),
        "verification": {
            "answers_question": False,
            "mismatch_type": "non_executable_error",
            "stage2_failed_evidence": ["no such column: bad_column"],
            "repair_hint": "Fix execution error.",
        },
    }
    assert classify_candidate_row(verifier_group_c) == (True, "non_executable", None)


def test_specialized_routing_rules() -> None:
    for mismatch_type in (
        "financial_measure_error",
        "financial_object_error",
        "computation_logic_error",
    ):
        assert route_mismatch_to_repair_mode(mismatch_type, set()) == (mismatch_type, None)

    assert route_mismatch_to_repair_mode("value_entity_error", set()) == (
        None,
        "unsupported_mismatch_type",
    )
    assert route_mismatch_to_repair_mode(
        "financial_measure_error",
        {"financial_measure_error"},
    ) == (None, "same_error_persisted_after_repair")


def test_scope_validation_financial_measure_select_only() -> None:
    original = "SELECT COUNT(*) FROM invoices WHERE status = 'paid';"
    select_only = "SELECT SUM(amount) FROM invoices WHERE status = 'paid';"
    where_change = "SELECT SUM(amount) FROM invoices WHERE status = 'open';"
    group_change = "SELECT SUM(amount) FROM invoices WHERE status = 'paid' GROUP BY customer_id;"
    join_change = "SELECT SUM(amount) FROM invoices JOIN customers ON invoices.customer_id = customers.id WHERE status = 'paid';"

    accepted = validate_repair_scope(original, select_only, "financial_measure_error")
    assert accepted.status == "accepted"
    assert accepted.changed_clauses == ("SELECT",)

    assert validate_repair_scope(original, where_change, "financial_measure_error").violated_clauses == ("WHERE",)
    assert validate_repair_scope(original, group_change, "financial_measure_error").violated_clauses == ("GROUP BY",)
    assert validate_repair_scope(original, join_change, "financial_measure_error").violated_clauses == ("JOIN",)


def test_scope_validation_financial_object_where_only() -> None:
    original = "SELECT SUM(amount) FROM invoices WHERE status = 'paid';"
    where_change = "SELECT SUM(amount) FROM invoices WHERE status = 'open';"
    select_change = "SELECT COUNT(*) FROM invoices WHERE status = 'open';"
    join_change = "SELECT SUM(amount) FROM invoices JOIN customers ON invoices.customer_id = customers.id WHERE status = 'open';"
    group_change = "SELECT SUM(amount) FROM invoices WHERE status = 'open' GROUP BY customer_id;"

    accepted = validate_repair_scope(original, where_change, "financial_object_error")
    assert accepted.status == "accepted"
    assert accepted.changed_clauses == ("WHERE",)

    assert validate_repair_scope(original, select_change, "financial_object_error").violated_clauses == ("SELECT",)
    assert validate_repair_scope(original, join_change, "financial_object_error").violated_clauses == ("JOIN",)
    assert validate_repair_scope(original, group_change, "financial_object_error").violated_clauses == ("GROUP BY",)


def test_scope_validation_computation_logic_rules() -> None:
    original = "SELECT customer_id, SUM(amount) FROM invoices WHERE invoice_date >= '2024-01-01' GROUP BY customer_id;"
    group_order_limit = "SELECT customer_id, SUM(amount) FROM invoices WHERE invoice_date >= '2024-01-01' GROUP BY customer_id ORDER BY SUM(amount) DESC LIMIT 5;"
    temporal_where = "SELECT customer_id, SUM(amount) FROM invoices WHERE invoice_date >= '2024-02-01' GROUP BY customer_id;"
    non_temporal_where = "SELECT customer_id, SUM(amount) FROM invoices WHERE status = 'paid' GROUP BY customer_id;"
    join_change = "SELECT customer_id, SUM(amount) FROM invoices JOIN customers ON invoices.customer_id = customers.id WHERE invoice_date >= '2024-01-01' GROUP BY customer_id;"

    accepted = validate_repair_scope(original, group_order_limit, "computation_logic_error")
    assert accepted.status == "accepted"
    assert accepted.changed_clauses == ("ORDER BY", "LIMIT")

    temporal = validate_repair_scope(original, temporal_where, "computation_logic_error")
    assert temporal.status == "accepted"
    assert temporal.changed_clauses == ("WHERE",)

    non_temporal = validate_repair_scope(original, non_temporal_where, "computation_logic_error")
    assert non_temporal.status == "rejected"
    assert non_temporal.violated_clauses == ("WHERE",)

    assert validate_repair_scope(original, join_change, "computation_logic_error").violated_clauses == ("JOIN",)


def test_scope_validation_rejects_removed_select_alias_used_by_order_by() -> None:
    original = (
        "SELECT Account, SUM(Amount) AS Total_Expense "
        "FROM master_txn_table "
        "WHERE Transaction_TYPE = 'expense' "
        "GROUP BY Account ORDER BY Total_Expense DESC LIMIT 1;"
    )
    repaired = (
        "SELECT DISTINCT Account AS Account_name "
        "FROM master_txn_table "
        "WHERE Transaction_TYPE = 'expense' "
        "GROUP BY Account ORDER BY Total_Expense DESC LIMIT 1;"
    )

    result = validate_repair_scope(original, repaired, "financial_measure_error")

    assert result.status == "rejected"
    assert result.violated_clauses == ("SELECT",)
    assert "Total_Expense".lower() in str(result.error).lower()


def test_grouped_output_detection_uses_intent_and_question_signals() -> None:
    assert detect_grouped_output_requirement(
        "What was the mean invoice value in Last 12 months?",
        {"slots": {"time": {"requires_group_by_period": False}}},
    ) == (False, ())

    assert detect_grouped_output_requirement(
        "How many invoices remained unpaid?",
        {"slots": {"operation": {"comparison": {"required": False}, "group_by": []}}},
    ) == (False, ())

    grouped, evidence = detect_grouped_output_requirement(
        "Compare sales by customer MTD",
        {"slots": {"operation": {"comparison": {"required": True}, "group_by": ["customer"]}}},
    )
    assert grouped is True
    assert "intent.slots.operation.comparison.required" in evidence
    assert "intent.slots.operation.group_by" in evidence
    assert "question token: by" in evidence

    grouped, evidence = detect_grouped_output_requirement(
        "What account had our biggest expense in q3?",
        {"slots": {"operation": {"order_by": [{"target": "expense", "direction": "desc"}], "limit": 1}}},
    )
    assert grouped is True
    assert "intent.slots.operation.order_by" in evidence
    assert "intent.slots.operation.limit" in evidence
    assert "question token: biggest" in evidence

    grouped, evidence = detect_grouped_output_requirement(
        "monthly sales by customer",
        {"slots": {"time": {"requires_group_by_period": True}}},
    )
    assert grouped is True
    assert "intent.slots.time.requires_group_by_period" in evidence


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

    request = build_semantic_repair_request(row, schema_text="schema")
    assert request.primary_mismatch_type == "financial_measure_error"
    assert request.failed_evidence == ["Used COUNT(*)"]
    assert request.repair_hint == "Use SUM(amount)."
    assert request.schema_text == "schema"

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


def test_no_reverification_cli_mode_and_context_hash(monkeypatch) -> None:
    run_script = importlib.import_module("scripts.run_finverisql_repair")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_finverisql_repair.py",
            "--input-path",
            "input.jsonl",
            "--output-path",
            "output.jsonl",
            "--semantic-repair-framework",
            "no_reverification",
        ],
    )

    args = run_script.parse_args()
    no_reverification_hash = stable_context_hash(
        {
            "schema_text": "schema",
            "intent_mode": "nl_only",
            "repair_context_version": run_script.REPAIR_CONTEXT_VERSION,
            "semantic_repair_framework": "no_reverification",
            "verifier_model_name": None,
            "verifier_backend": None,
            "profile_mode": None,
            "probing_mode": None,
            "max_probes": None,
        }
    )
    specialized_hash = stable_context_hash(
        {
            "schema_text": "schema",
            "intent_mode": "nl_only",
            "repair_context_version": run_script.REPAIR_CONTEXT_VERSION,
            "semantic_repair_framework": "specialized_chain",
            "verifier_model_name": "model",
            "verifier_backend": "mlx-lm",
            "profile_mode": "compact",
            "probing_mode": "probe",
            "max_probes": 7,
        }
    )

    output_row = build_attempt_output_row(
        source_row=make_chain_row(),
        repair_request=None,
        repair_result=None,
        intent_representation_used={"measure_kind": "monetary"},
        repair_mode=args.semantic_repair_framework,
        status="success",
        skip_reason=None,
        repair_model="repair-model",
        intent_mode="nl_only",
        repair_context_hash=no_reverification_hash,
    )
    output_row["semantic_repair_framework"] = args.semantic_repair_framework

    assert args.semantic_repair_framework == "no_reverification"
    assert "no_reverification" in run_script.SCHEMA_ANNOTATION_FRAMEWORKS
    assert "no_reverification" not in run_script.CHAIN_REPAIR_FRAMEWORKS
    assert no_reverification_hash != specialized_hash
    assert output_row["semantic_repair_framework"] == "no_reverification"


def make_chain_row(mismatch_type: str = "financial_measure_error") -> dict[str, object]:
    return {
        "question_id": "q-chain",
        "question": "What paid invoice revenue was booked in 2024?",
        "generated_sql": "SELECT COUNT(*) FROM invoices WHERE status = 'paid';",
        "intent_representation": {"measure_kind": "monetary"},
        "execution_profile": '{"status":"OK"}',
        "verification": {
            "answers_question": False,
            "should_abstain": False,
            "mismatch_type": mismatch_type,
            "mismatch_detail": "Wrong starting mismatch.",
            "stage2_failed_evidence": ["Evidence"],
            "repair_hint": "Fix it.",
            "confidence": "high",
        },
    }


def make_schema_store() -> SchemaAnnotationStore:
    return SchemaAnnotationStore(
        {
            "invoices": {
                "amount": {"semantic_role": "financial_measure", "measure_type": "flow", "unit": "monetary"},
                "status": {"semantic_role": "status_classifier"},
                "invoice_date": {"semantic_role": "transaction_date", "measure_type": "date"},
            }
        }
    )


def verifier_dict(
    answers_question: bool | None,
    mismatch_type: str | None = None,
    repair_hint: str | None = "Fix it.",
) -> dict[str, object]:
    return {
        "answers_question": answers_question,
        "mismatch_type": mismatch_type,
        "mismatch_detail": "Verifier detail.",
        "repair_hint": repair_hint,
        "ambiguous": answers_question is None,
        "should_abstain": answers_question is None,
        "abstain_reason": "test" if answers_question is None else None,
        "confidence": "high",
        "stage2_failed_evidence": ["Reverification evidence"],
        "stage2_diagnostic_dimensions": {},
    }


def install_chain_monkeypatches(monkeypatch, verifier_results: list[dict[str, object]]) -> None:
    queue = list(verifier_results)
    monkeypatch.setattr(
        repair_runner,
        "build_execution_profile",
        lambda generated_sql, schema_store, profile_mode: json.dumps(
            {"status": "OK", "sql": generated_sql, "profile_type": profile_mode}
        ),
    )

    def fake_verify(**_kwargs):
        if not queue:
            raise AssertionError("No fake verifier result queued.")
        return queue.pop(0)

    monkeypatch.setattr(repair_runner, "verify_execution_profile", fake_verify)


def test_generic_chain_accepts_after_first_repair(monkeypatch) -> None:
    install_chain_monkeypatches(monkeypatch, [verifier_dict(True)])

    result = run_generic_semantic_repair_chain(
        row=make_chain_row(),
        schema_text="Table invoices(amount, status)",
        schema_store=make_schema_store(),
        repair_generate_fn=lambda _prompt: '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'paid\';","edit_summary":"Use amount.","confidence":"high"}',
        verifier_generate_fn=lambda _prompt: "{}",
    )

    assert result["stop_reason"] == "verifier_accepts_after_repair"
    assert result["final_sql_source"] == "generic_chain_repair"
    assert result["final_repaired_sql"] == "SELECT SUM(amount) FROM invoices WHERE status = 'paid';"
    assert result["num_repair_attempts"] == 1
    assert result["attempted_mismatch_types"] == ["financial_measure_error"]
    assert result["scope_check_status"] is None
    assert result["allowed_clause_changes"] is None
    assert result["disallowed_clause_changes"] is None


def test_generic_chain_stops_when_same_error_persists(monkeypatch) -> None:
    install_chain_monkeypatches(monkeypatch, [verifier_dict(False, "financial_measure_error")])

    result = run_generic_semantic_repair_chain(
        row=make_chain_row(),
        schema_text="schema",
        schema_store=make_schema_store(),
        repair_generate_fn=lambda _prompt: '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'paid\';","edit_summary":"Use amount.","confidence":"high"}',
        verifier_generate_fn=lambda _prompt: "{}",
    )

    assert result["stop_reason"] == "same_error_persisted_after_repair"
    assert result["final_repaired_sql"] is None
    assert result["final_sql_source"] == "original_generated_sql"
    assert result["num_repair_attempts"] == 1


def test_generic_chain_routes_new_mismatch_without_dimension_specific_prompt(monkeypatch) -> None:
    install_chain_monkeypatches(
        monkeypatch,
        [
            verifier_dict(False, "financial_object_error"),
            verifier_dict(True),
        ],
    )
    prompts: list[str] = []

    def fake_repair(prompt: str) -> str:
        prompts.append(prompt)
        if "financial_object_error" in prompt:
            return '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'open\';","edit_summary":"Change status.","confidence":"high"}'
        return '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'paid\';","edit_summary":"Use amount.","confidence":"high"}'

    result = run_generic_semantic_repair_chain(
        row=make_chain_row(),
        schema_text="schema",
        schema_store=make_schema_store(),
        repair_generate_fn=fake_repair,
        verifier_generate_fn=lambda _prompt: "{}",
    )

    assert result["stop_reason"] == "verifier_accepts_after_repair"
    assert result["final_sql_source"] == "generic_chain_repair"
    assert result["num_repair_attempts"] == 2
    assert result["repair_attempt_sequence"][0]["routing_decision"] == "new_error_detected_and_routed"
    assert "Specialized task" not in prompts[0]
    assert "Scope constraints" not in prompts[0]
    assert result["current_sql_before_each_attempt"][1] == "SELECT SUM(amount) FROM invoices WHERE status = 'paid';"


def test_generic_chain_does_not_apply_scope_or_scalar_group_by_gate(monkeypatch) -> None:
    install_chain_monkeypatches(monkeypatch, [verifier_dict(True)])

    result = run_generic_semantic_repair_chain(
        row=make_chain_row(),
        schema_text="Table invoices(amount, status, customer_id)",
        schema_store=make_schema_store(),
        repair_generate_fn=lambda _prompt: '{"repaired_sql":"SELECT customer_id, SUM(amount) FROM invoices WHERE status = \'open\' GROUP BY customer_id;","edit_summary":"Change measure, filter, and grouping.","confidence":"high"}',
        verifier_generate_fn=lambda _prompt: "{}",
    )

    attempt = result["repair_attempt_sequence"][0]
    assert result["stop_reason"] == "verifier_accepts_after_repair"
    assert result["final_sql_source"] == "generic_chain_repair"
    assert result["final_repaired_sql"] == "SELECT customer_id, SUM(amount) FROM invoices WHERE status = 'open' GROUP BY customer_id;"
    assert attempt["scope_check_status"] is None
    assert attempt["allowed_clause_changes"] is None
    assert attempt["disallowed_clause_changes"] is None
    assert attempt["scalar_group_by_gate_status"] is None


def test_specialized_chain_accepts_after_first_repair(monkeypatch) -> None:
    install_chain_monkeypatches(monkeypatch, [verifier_dict(True)])

    result = run_specialized_semantic_repair_chain(
        row=make_chain_row(),
        schema_text="Table invoices(amount, status)",
        schema_store=make_schema_store(),
        repair_generate_fn=lambda _prompt: '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'paid\';","edit_summary":"Use amount.","confidence":"high"}',
        verifier_generate_fn=lambda _prompt: "{}",
    )

    assert result["stop_reason"] == "verifier_accepts_after_repair"
    assert result["final_repaired_sql"] == "SELECT SUM(amount) FROM invoices WHERE status = 'paid';"
    assert result["num_repair_attempts"] == 1
    assert result["attempted_error_classes"] == ["financial_measure_error"]


def test_specialized_chain_stops_when_same_error_persists(monkeypatch) -> None:
    install_chain_monkeypatches(monkeypatch, [verifier_dict(False, "financial_measure_error")])

    result = run_specialized_semantic_repair_chain(
        row=make_chain_row(),
        schema_text="schema",
        schema_store=make_schema_store(),
        repair_generate_fn=lambda _prompt: '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'paid\';","edit_summary":"Use amount.","confidence":"high"}',
        verifier_generate_fn=lambda _prompt: "{}",
    )

    assert result["stop_reason"] == "same_error_persisted_after_repair"
    assert result["num_repair_attempts"] == 1
    assert result["final_repaired_sql"] is None
    assert result["final_sql_source"] == "original_generated_sql"


def test_specialized_chain_routes_new_unattempted_error(monkeypatch) -> None:
    install_chain_monkeypatches(
        monkeypatch,
        [
            verifier_dict(False, "financial_object_error"),
            verifier_dict(True),
        ],
    )

    def fake_repair(prompt: str) -> str:
        if "financial_object_error" in prompt:
            return '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'open\';","edit_summary":"Change status.","confidence":"high"}'
        return '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'paid\';","edit_summary":"Use amount.","confidence":"high"}'

    result = run_specialized_semantic_repair_chain(
        row=make_chain_row(),
        schema_text="schema",
        schema_store=make_schema_store(),
        repair_generate_fn=fake_repair,
        verifier_generate_fn=lambda _prompt: "{}",
    )

    assert result["stop_reason"] == "verifier_accepts_after_repair"
    assert result["num_repair_attempts"] == 2
    assert result["repair_attempt_sequence"][0]["routing_decision"] == "new_error_detected_and_routed"
    assert result["current_sql_before_each_attempt"][1] == "SELECT SUM(amount) FROM invoices WHERE status = 'paid';"


def test_specialized_chain_stops_after_all_error_classes(monkeypatch) -> None:
    install_chain_monkeypatches(
        monkeypatch,
        [
            verifier_dict(False, "financial_object_error"),
            verifier_dict(False, "computation_logic_error"),
            verifier_dict(False, "financial_measure_error"),
        ],
    )

    def fake_repair(prompt: str) -> str:
        if "financial_object_error" in prompt:
            return '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'open\';","edit_summary":"Change status.","confidence":"high"}'
        if "computation_logic_error" in prompt:
            return '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'open\' AND invoice_date >= \'2024-01-01\';","edit_summary":"Add date filter.","confidence":"high"}'
        return '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'paid\';","edit_summary":"Use amount.","confidence":"high"}'

    result = run_specialized_semantic_repair_chain(
        row=make_chain_row(),
        schema_text="schema",
        schema_store=make_schema_store(),
        repair_generate_fn=fake_repair,
        verifier_generate_fn=lambda _prompt: "{}",
    )

    assert result["stop_reason"] == "all_error_classes_attempted"
    assert result["num_repair_attempts"] == 3
    assert result["final_repaired_sql"] is None
    assert result["final_sql_source"] == "original_generated_sql"
    assert set(result["attempted_error_classes"]) == {
        "financial_measure_error",
        "financial_object_error",
        "computation_logic_error",
    }


def test_specialized_chain_scalar_group_by_gate_rejects_scalar_computation_repair(monkeypatch) -> None:
    install_chain_monkeypatches(monkeypatch, [])

    result = run_specialized_semantic_repair_chain(
        row=make_chain_row("computation_logic_error"),
        schema_text="Table invoices(amount, status, customer_id)",
        schema_store=make_schema_store(),
        repair_generate_fn=lambda _prompt: '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'paid\' GROUP BY customer_id;","edit_summary":"Add grouping.","confidence":"high"}',
        verifier_generate_fn=lambda _prompt: "{}",
    )

    attempt = result["repair_attempt_sequence"][0]
    assert result["stop_reason"] == "repair_rejected_by_scalar_group_by_gate"
    assert result["scope_check_status"] == "accepted"
    assert result["final_repaired_sql"] is None
    assert result["final_sql_source"] == "original_generated_sql"
    assert result["previous_sql_versions"] == ["SELECT COUNT(*) FROM invoices WHERE status = 'paid';"]
    assert attempt["scalar_group_by_gate_status"] == "rejected"
    assert attempt["requires_grouped_output"] is False
    assert attempt["grouped_output_evidence"] == []


def test_specialized_chain_scalar_computation_temporal_repair_is_accepted(monkeypatch) -> None:
    install_chain_monkeypatches(monkeypatch, [verifier_dict(True)])

    result = run_specialized_semantic_repair_chain(
        row=make_chain_row("computation_logic_error"),
        schema_text="Table invoices(amount, status, invoice_date)",
        schema_store=make_schema_store(),
        repair_generate_fn=lambda _prompt: '{"repaired_sql":"SELECT COUNT(*) FROM invoices WHERE status = \'paid\' AND invoice_date >= \'2024-01-01\';","edit_summary":"Add date filter.","confidence":"high"}',
        verifier_generate_fn=lambda _prompt: "{}",
    )

    attempt = result["repair_attempt_sequence"][0]
    assert result["stop_reason"] == "verifier_accepts_after_repair"
    assert result["final_repaired_sql"] == "SELECT COUNT(*) FROM invoices WHERE status = 'paid' AND invoice_date >= '2024-01-01';"
    assert attempt["scalar_group_by_gate_status"] == "not_applicable"
    assert attempt["requires_grouped_output"] is False


def test_specialized_chain_scalar_group_by_gate_allows_explicit_grouped_question(monkeypatch) -> None:
    install_chain_monkeypatches(monkeypatch, [verifier_dict(True)])
    row = make_chain_row("computation_logic_error")
    row["question"] = "Compare paid invoice revenue by customer MTD."
    row["intent_representation"] = {
        "slots": {
            "operation": {
                "comparison": {"required": True},
                "group_by": ["customer"],
            }
        }
    }

    result = run_specialized_semantic_repair_chain(
        row=row,
        schema_text="Table invoices(amount, status, customer_id)",
        schema_store=make_schema_store(),
        repair_generate_fn=lambda _prompt: '{"repaired_sql":"SELECT customer_id, SUM(amount) FROM invoices WHERE status = \'paid\' GROUP BY customer_id;","edit_summary":"Group by customer.","confidence":"high"}',
        verifier_generate_fn=lambda _prompt: "{}",
    )

    attempt = result["repair_attempt_sequence"][0]
    assert result["stop_reason"] == "verifier_accepts_after_repair"
    assert result["final_repaired_sql"] == "SELECT customer_id, SUM(amount) FROM invoices WHERE status = 'paid' GROUP BY customer_id;"
    assert attempt["scalar_group_by_gate_status"] == "accepted"
    assert attempt["requires_grouped_output"] is True
    assert "intent.slots.operation.group_by" in attempt["grouped_output_evidence"]
    assert "question token: by" in attempt["grouped_output_evidence"]


def test_specialized_chain_rejected_scope_does_not_update_current_sql(monkeypatch) -> None:
    install_chain_monkeypatches(monkeypatch, [])

    result = run_specialized_semantic_repair_chain(
        row=make_chain_row(),
        schema_text="schema",
        schema_store=make_schema_store(),
        repair_generate_fn=lambda _prompt: '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'open\';","edit_summary":"Changed too much.","confidence":"high"}',
        verifier_generate_fn=lambda _prompt: "{}",
    )

    assert result["stop_reason"] == "repair_rejected_by_scope_check"
    assert result["scope_check_status"] == "rejected"
    assert result["previous_sql_versions"] == ["SELECT COUNT(*) FROM invoices WHERE status = 'paid';"]
    assert result["final_repaired_sql"] is None


def install_no_reverification_monkeypatches(monkeypatch, profile_status: str = "OK") -> None:
    monkeypatch.setattr(
        repair_runner,
        "build_execution_profile",
        lambda generated_sql, schema_store, profile_mode: json.dumps(
            {"status": profile_status, "sql": generated_sql, "profile_type": profile_mode}
        ),
    )

    def fail_verify(**_kwargs):
        raise AssertionError("verify_execution_profile must not be called.")

    monkeypatch.setattr(repair_runner, "verify_execution_profile", fail_verify)


def test_no_reverification_accepts_first_specialized_repair(monkeypatch) -> None:
    install_no_reverification_monkeypatches(monkeypatch)

    result = run_specialized_first_repair_no_reverification(
        row=make_chain_row(),
        schema_text="Table invoices(amount, status)",
        schema_store=make_schema_store(),
        repair_generate_fn=lambda _prompt: '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'paid\';","edit_summary":"Use amount.","confidence":"high"}',
    )

    assert result["stop_reason"] == "first_repair_accepted_without_reverification"
    assert result["final_sql_source"] == "specialized_first_repair_no_reverification"
    assert result["final_repaired_sql"] == "SELECT SUM(amount) FROM invoices WHERE status = 'paid';"
    assert result["num_repair_attempts"] == 1
    assert result["scope_check_status"] == "accepted"
    assert result["reverification_results"] is None
    assert result["attempted_error_classes"] == ["financial_measure_error"]


def test_no_reverification_scope_rejection_prevents_acceptance(monkeypatch) -> None:
    install_no_reverification_monkeypatches(monkeypatch)

    result = run_specialized_first_repair_no_reverification(
        row=make_chain_row(),
        schema_text="schema",
        schema_store=make_schema_store(),
        repair_generate_fn=lambda _prompt: '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'open\';","edit_summary":"Changed too much.","confidence":"high"}',
    )

    assert result["stop_reason"] == "repair_rejected_by_scope_check"
    assert result["final_sql_source"] == "original_generated_sql"
    assert result["final_repaired_sql"] is None
    assert result["scope_check_status"] == "rejected"
    assert result["previous_sql_versions"] == ["SELECT COUNT(*) FROM invoices WHERE status = 'paid';"]


def test_no_reverification_scalar_group_by_gate_rejection_prevents_acceptance(monkeypatch) -> None:
    install_no_reverification_monkeypatches(monkeypatch)

    result = run_specialized_first_repair_no_reverification(
        row=make_chain_row("computation_logic_error"),
        schema_text="Table invoices(amount, status, customer_id)",
        schema_store=make_schema_store(),
        repair_generate_fn=lambda _prompt: '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'paid\' GROUP BY customer_id;","edit_summary":"Add grouping.","confidence":"high"}',
    )

    attempt = result["repair_attempt_sequence"][0]
    assert result["stop_reason"] == "repair_rejected_by_scalar_group_by_gate"
    assert result["final_sql_source"] == "original_generated_sql"
    assert result["final_repaired_sql"] is None
    assert attempt["scalar_group_by_gate_status"] == "rejected"
    assert attempt["requires_grouped_output"] is False


def make_group_c_row() -> dict[str, object]:
    return {
        "question_id": "q-group-c",
        "question": "What paid invoice revenue was booked in 2024?",
        "generated_sql": "SELECT SUM(amount FROM invoices WHERE status = 'paid';",
        "evaluation_group": "C_non_executable",
        "execution_profile": json.dumps(
            {
                "status": "EXECUTION_ERROR",
                "profile_type": "execution_error",
                "execution_error": "near \"FROM\": syntax error",
            }
        ),
        "verification": {
            "answers_question": False,
            "should_abstain": False,
            "mismatch_type": "non_executable_error",
            "mismatch_detail": "Execution error.",
            "stage2_failed_evidence": ["near \"FROM\": syntax error"],
            "repair_hint": "Fix execution error.",
            "confidence": "high",
        },
    }


def test_group_c_chain_accepts_after_execution_repair(monkeypatch) -> None:
    install_chain_monkeypatches(monkeypatch, [verifier_dict(True)])

    result = run_non_executable_then_semantic_repair_chain(
        row=make_group_c_row(),
        schema_text="Table invoices(amount, status)",
        schema_store=make_schema_store(),
        repair_generate_fn=lambda _prompt: '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'paid\';","edit_summary":"Closed SUM().","confidence":"high"}',
        verifier_generate_fn=lambda _prompt: "{}",
    )

    assert result["stop_reason"] == "verifier_accepts_after_execution_repair"
    assert result["final_sql_source"] == "non_executable_repair"
    assert result["final_repaired_sql"] == "SELECT SUM(amount) FROM invoices WHERE status = 'paid';"
    assert result["post_execution_reverification"]["answers_question"] is True
    assert result["semantic_followup_attempt_sequence"] == []
    assert result["num_repair_attempts"] == 1


def test_group_c_chain_routes_semantic_mismatch_after_execution_repair(monkeypatch) -> None:
    install_chain_monkeypatches(
        monkeypatch,
        [
            verifier_dict(False, "financial_measure_error"),
            verifier_dict(True),
        ],
    )

    def fake_repair(prompt: str) -> str:
        if "Execution error" in prompt:
            return '{"repaired_sql":"SELECT COUNT(*) FROM invoices WHERE status = \'paid\';","edit_summary":"Closed syntax.","confidence":"high"}'
        return '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'paid\';","edit_summary":"Use amount.","confidence":"high"}'

    result = run_non_executable_then_semantic_repair_chain(
        row=make_group_c_row(),
        schema_text="Table invoices(amount, status)",
        schema_store=make_schema_store(),
        repair_generate_fn=fake_repair,
        verifier_generate_fn=lambda _prompt: "{}",
    )

    assert result["stop_reason"] == "semantic_followup_accepts_after_repair"
    assert result["final_sql_source"] == "specialized_chain_repair"
    assert result["final_repaired_sql"] == "SELECT SUM(amount) FROM invoices WHERE status = 'paid';"
    assert result["post_execution_reverification"]["mismatch_type"] == "financial_measure_error"
    assert len(result["semantic_followup_attempt_sequence"]) == 1
    assert result["num_repair_attempts"] == 2


def test_group_c_chain_can_use_generic_semantic_followup(monkeypatch) -> None:
    install_chain_monkeypatches(
        monkeypatch,
        [
            verifier_dict(False, "financial_measure_error"),
            verifier_dict(True),
        ],
    )

    def fake_repair(prompt: str) -> str:
        if "Execution error" in prompt:
            return '{"repaired_sql":"SELECT COUNT(*) FROM invoices WHERE status = \'paid\';","edit_summary":"Closed syntax.","confidence":"high"}'
        assert "generic semantic repair ablation" in prompt
        assert "Scope constraints" not in prompt
        return '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'paid\';","edit_summary":"Use amount.","confidence":"high"}'

    result = run_non_executable_then_semantic_repair_chain(
        row=make_group_c_row(),
        schema_text="Table invoices(amount, status)",
        schema_store=make_schema_store(),
        repair_generate_fn=fake_repair,
        verifier_generate_fn=lambda _prompt: "{}",
        semantic_followup_framework="generic_chain",
    )

    assert result["stop_reason"] == "semantic_followup_accepts_after_repair"
    assert result["final_sql_source"] == "generic_chain_repair"
    assert result["final_repaired_sql"] == "SELECT SUM(amount) FROM invoices WHERE status = 'paid';"
    assert result["repair_attempt_sequence"][0]["routing_decision"] == "post_execution_repair_routed_to_generic_chain"
    assert result["semantic_followup_result"]["final_sql_source"] == "generic_chain_repair"
    assert len(result["semantic_followup_attempt_sequence"]) == 1
    assert result["num_repair_attempts"] == 2


def test_group_c_chain_uses_execution_repair_fallback_when_verifier_abstains(monkeypatch) -> None:
    install_chain_monkeypatches(monkeypatch, [verifier_dict(None)])

    result = run_non_executable_then_semantic_repair_chain(
        row=make_group_c_row(),
        schema_text="Table invoices(amount, status)",
        schema_store=make_schema_store(),
        repair_generate_fn=lambda _prompt: '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'paid\';","edit_summary":"Closed SUM().","confidence":"high"}',
        verifier_generate_fn=lambda _prompt: "{}",
    )

    assert (
        result["stop_reason"]
        == "post_execution_reverification_failed_or_abstained_using_execution_repair_fallback"
    )
    assert result["final_sql_source"] == "non_executable_repair_fallback"
    assert result["final_repaired_sql"] == "SELECT SUM(amount) FROM invoices WHERE status = 'paid';"
    assert result["execution_repaired_sql"] == result["final_repaired_sql"]
    assert result["execution_repair_fallback_available"] is True
    assert result["execution_repair_fallback_profile"] == result["post_execution_repair_profile"]
    assert result["semantic_followup_attempt_sequence"] == []


def test_group_c_chain_uses_execution_repair_fallback_when_semantic_followup_rejects(monkeypatch) -> None:
    install_chain_monkeypatches(
        monkeypatch,
        [
            verifier_dict(False, "financial_measure_error"),
            verifier_dict(False, "financial_measure_error"),
        ],
    )

    def fake_repair(prompt: str) -> str:
        if "Execution error" in prompt:
            return '{"repaired_sql":"SELECT COUNT(*) FROM invoices WHERE status = \'paid\';","edit_summary":"Closed syntax.","confidence":"high"}'
        return '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'paid\';","edit_summary":"Use amount.","confidence":"high"}'

    result = run_non_executable_then_semantic_repair_chain(
        row=make_group_c_row(),
        schema_text="Table invoices(amount, status)",
        schema_store=make_schema_store(),
        repair_generate_fn=fake_repair,
        verifier_generate_fn=lambda _prompt: "{}",
    )

    assert result["stop_reason"] == (
        "semantic_followup_failed_using_execution_repair_fallback:"
        "same_error_persisted_after_repair"
    )
    assert result["final_sql_source"] == "non_executable_repair_fallback"
    assert result["final_repaired_sql"] == "SELECT COUNT(*) FROM invoices WHERE status = 'paid';"
    assert result["semantic_followup_result"]["stop_reason"] == "same_error_persisted_after_repair"
    assert len(result["semantic_followup_attempt_sequence"]) == 1
    assert result["final_execution_profile"] == result["execution_repair_fallback_profile"]


def test_group_c_no_reverification_accepts_profileable_execution_repair(monkeypatch) -> None:
    install_no_reverification_monkeypatches(monkeypatch)

    result = run_non_executable_then_semantic_repair_chain(
        row=make_group_c_row(),
        schema_text="Table invoices(amount, status)",
        schema_store=make_schema_store(),
        repair_generate_fn=lambda _prompt: '{"repaired_sql":"SELECT SUM(amount) FROM invoices WHERE status = \'paid\';","edit_summary":"Closed SUM().","confidence":"high"}',
        verifier_generate_fn=lambda _prompt: "{}",
        accept_execution_repair_without_reverification=True,
    )

    assert result["stop_reason"] == "execution_repair_accepted_without_reverification"
    assert result["final_sql_source"] == "non_executable_repair_no_reverification"
    assert result["final_repaired_sql"] == "SELECT SUM(amount) FROM invoices WHERE status = 'paid';"
    assert result["post_execution_reverification"] is None
    assert result["semantic_followup_attempt_sequence"] == []
    assert result["repair_attempt_sequence"][0]["routing_decision"] == "execution_repair_accepted_without_reverification"
    assert result["num_repair_attempts"] == 1


def test_group_c_chain_keeps_original_when_execution_repair_still_non_executable(monkeypatch) -> None:
    monkeypatch.setattr(
        repair_runner,
        "build_execution_profile",
        lambda generated_sql, schema_store, profile_mode: json.dumps(
            {
                "status": "EXECUTION_ERROR",
                "sql": generated_sql,
                "profile_type": "execution_error",
                "execution_error": "near FROM: syntax error",
            }
        ),
    )

    result = run_non_executable_then_semantic_repair_chain(
        row=make_group_c_row(),
        schema_text="Table invoices(amount, status)",
        schema_store=make_schema_store(),
        repair_generate_fn=lambda _prompt: '{"repaired_sql":"SELECT SUM(amount FROM invoices WHERE status = \'paid\';","edit_summary":"Still broken.","confidence":"low"}',
        verifier_generate_fn=lambda _prompt: "{}",
    )

    assert result["stop_reason"] == "non_executable_repair_still_non_executable"
    assert result["final_sql_source"] == "original_generated_sql"
    assert result["final_repaired_sql"] is None
    assert result["execution_repair_fallback_available"] is False


def test_group_c_no_reverification_keeps_original_when_repair_still_non_executable(monkeypatch) -> None:
    monkeypatch.setattr(
        repair_runner,
        "build_execution_profile",
        lambda generated_sql, schema_store, profile_mode: json.dumps(
            {
                "status": "EXECUTION_ERROR",
                "sql": generated_sql,
                "profile_type": "execution_error",
                "execution_error": "near FROM: syntax error",
            }
        ),
    )

    def fail_verify(**_kwargs):
        raise AssertionError("verify_execution_profile must not be called.")

    monkeypatch.setattr(repair_runner, "verify_execution_profile", fail_verify)

    result = run_non_executable_then_semantic_repair_chain(
        row=make_group_c_row(),
        schema_text="Table invoices(amount, status)",
        schema_store=make_schema_store(),
        repair_generate_fn=lambda _prompt: '{"repaired_sql":"SELECT SUM(amount FROM invoices WHERE status = \'paid\';","edit_summary":"Still broken.","confidence":"low"}',
        verifier_generate_fn=lambda _prompt: "{}",
        accept_execution_repair_without_reverification=True,
    )

    assert result["stop_reason"] == "non_executable_repair_still_non_executable"
    assert result["final_sql_source"] == "original_generated_sql"
    assert result["final_repaired_sql"] is None
    assert result["post_execution_reverification"] is None
    assert result["execution_repair_fallback_available"] is False


def test_final_sql_evaluator_counts_non_executable_repair_fallback_as_applied() -> None:
    source_rows = [
        {
            "question_id": "q-group-c",
            "status": "success",
            "repair_status": "success",
            "evaluation_group": "C_non_executable",
            "original_generated_sql": "SELECT SUM(amount FROM invoices;",
            "repaired_sql": "SELECT COUNT(*) FROM invoices;",
            "final_sql_source": "non_executable_repair_fallback",
        }
    ]
    adapted_rows = adapt_repair_rows(
        rows=source_rows,
        original_sql_key="original_generated_sql",
        repaired_sql_key="repaired_sql",
    )
    evaluated_rows = [{**adapted_rows[0], "evaluation_group": "B_wrong_executable"}]

    summary = build_repair_summary(source_rows, adapted_rows, evaluated_rows)

    assert adapted_rows[0]["generated_sql"] == "SELECT COUNT(*) FROM invoices;"
    assert adapted_rows[0]["final_sql_source"] == "non_executable_repair_fallback"
    assert adapted_rows[0]["final_sql_repaired"] is True
    assert summary["final_sql_source_counts"] == {"non_executable_repair_fallback": 1}
    assert summary["repaired_sql_rows"] == 1
    assert summary["repair_coverage"]["applied_repairs"] == 1
    assert summary["repair_effectiveness"]["executable_repairs"] == 1


def test_final_sql_evaluator_normalizes_net_gain_after_corruption() -> None:
    source_rows = [
        {
            "question_id": "q-a",
            "status": "success",
            "repair_status": "success",
            "evaluation_group": "A_correct_executable",
            "repair_mode": "semantic",
        },
        {
            "question_id": "q-b",
            "status": "success",
            "repair_status": "success",
            "evaluation_group": "B_wrong_executable",
            "repair_mode": "semantic",
        },
        {
            "question_id": "q-c",
            "status": "success",
            "repair_status": "success",
            "evaluation_group": "C_non_executable",
            "repair_mode": "non_executable",
        },
    ]
    adapted_rows = [
        {"question_id": "q-a", "final_sql_repaired": True, "final_sql_source": "repair"},
        {"question_id": "q-b", "final_sql_repaired": True, "final_sql_source": "repair"},
        {"question_id": "q-c", "final_sql_repaired": True, "final_sql_source": "repair"},
    ]
    evaluated_rows = [
        {"question_id": "q-a", "evaluation_group": "B_wrong_executable"},
        {"question_id": "q-b", "evaluation_group": "A_correct_executable"},
        {"question_id": "q-c", "evaluation_group": "A_correct_executable"},
    ]

    summary = build_repair_summary(source_rows, adapted_rows, evaluated_rows)

    assert summary["repair_safety"]["net_gain_after_corruption_count"] == 1
    assert summary["repair_safety"]["net_gain_after_corruption_denominator"] == 3
    assert summary["repair_safety"]["net_gain_after_corruption"] == 1 / 3
    assert summary["headline_metrics"]["net_gain_after_corruption"] == 1 / 3


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
