from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.baseline.generic_refine.execution_guided import (
    REPAIR_MODE as EXECUTION_GUIDED_MODE,
    build_execution_feedback,
    build_generic_execution_guided_refine_prompt,
)
from src.baseline.generic_refine.common import (
    GenericRefineRequest,
    build_output_row,
    get_refine_run_key,
    normalise_refine_output,
    parse_refine_json,
    run_refine_request,
    stable_context_hash,
)
from src.baseline.generic_refine.self_refine import (
    REPAIR_MODE as SELF_REFINE_MODE,
    build_generic_self_refine_prompt,
)
from src.eval.evaluate_final_sql import adapt_repair_rows


def make_request(
    execution_feedback: dict[str, object] | None = None,
) -> GenericRefineRequest:
    return GenericRefineRequest(
        question_id="q1",
        question="How much revenue did we book?",
        schema_text="Table invoices(id, amount)",
        candidate_sql="SELECT COUNT(*) FROM invoices;",
        execution_feedback=execution_feedback,
    )


def make_evaluated_row(group: str = "B_wrong_executable") -> dict[str, object]:
    return {
        "question_id": "q1",
        "db_id": "booksql",
        "split": "validation",
        "level": "easy",
        "generator": "qwen",
        "prompt_setting": "zero_shot",
        "evaluation_group": group,
        "question": "How much revenue did we book?",
        "schema": "Table invoices(id, amount)",
        "gold_sql": "SELECT SUM(amount) FROM invoices;",
        "generated_sql": "SELECT COUNT(*) FROM invoices;",
        "generated_execution_status": "success",
        "generated_error": None,
        "generated_result": {"row_count": 1, "rows": [[10]], "truncated": False},
        "ambiguity_flags": [],
    }


def test_self_refine_prompt_uses_only_generic_inputs() -> None:
    prompt = build_generic_self_refine_prompt(make_request())

    assert "Question:" in prompt
    assert "Schema:" in prompt
    assert "Candidate SQL:" in prompt
    assert "SELECT COUNT(*) FROM invoices;" in prompt
    assert "gold" not in prompt.lower()
    assert "finverisql" not in prompt.lower()
    assert "taxonomy" not in prompt.lower()
    assert "schema annotation" not in prompt.lower()
    assert "intent decomposition" not in prompt.lower()
    assert "adaptive probing" not in prompt.lower()
    assert "targeted repair" not in prompt.lower()


def test_execution_guided_prompt_includes_feedback_without_gold_result() -> None:
    row = make_evaluated_row("C_non_executable")
    row["generated_execution_status"] = "error"
    row["generated_error"] = "no such column: bad_col"
    row["error_message"] = "generated SQL error: no such column: bad_col"
    feedback = build_execution_feedback(row)
    prompt = build_generic_execution_guided_refine_prompt(
        make_request(execution_feedback=feedback)
    )

    assert "Generated SQL execution feedback" in prompt
    assert "no such column: bad_col" in prompt
    assert "generated_result" in prompt
    assert "gold_sql" not in prompt
    assert "gold_result" not in prompt
    assert "reference answer" not in prompt.lower()
    assert "finverisql" not in prompt.lower()


def test_execution_guided_feedback_describes_successful_execution() -> None:
    feedback = build_execution_feedback(make_evaluated_row("A_correct_executable"))

    assert feedback["generated_execution_status"] == "success"
    assert feedback["generated_error"] is None
    assert "executed successfully" in str(feedback["summary"])
    assert feedback["generated_result"] == {"row_count": 1, "rows": [[10]], "truncated": False}


def test_parse_refine_json_accepts_plain_and_wrapped_json() -> None:
    parsed = parse_refine_json(
        '```json\n{"changed": true, "revised_sql": "SELECT SUM(amount) FROM invoices;", "edit_summary": "Use SUM.", "confidence": "high"}\n```'
    )

    assert parsed["changed"] is True
    assert parsed["revised_sql"] == "SELECT SUM(amount) FROM invoices;"


def test_refine_output_no_change_for_changed_false_empty_or_same_sql() -> None:
    original = "SELECT COUNT(*) FROM invoices;"

    changed_false = normalise_refine_output(
        {
            "changed": False,
            "revised_sql": "SELECT SUM(amount) FROM invoices;",
            "edit_summary": "No change.",
            "confidence": "high",
        },
        raw_output="{}",
        original_sql=original,
    )
    empty_sql = normalise_refine_output(
        {
            "changed": True,
            "revised_sql": "",
            "edit_summary": "No SQL.",
            "confidence": "medium",
        },
        raw_output="{}",
        original_sql=original,
    )
    same_sql = normalise_refine_output(
        {
            "changed": True,
            "revised_sql": "SELECT COUNT(*) FROM invoices",
            "edit_summary": "Same SQL.",
            "confidence": "low",
        },
        raw_output="{}",
        original_sql=original,
    )

    assert changed_false.refine_decision == "no_change"
    assert changed_false.repaired_sql is None
    assert empty_sql.refine_decision == "no_change"
    assert empty_sql.repaired_sql is None
    assert same_sql.refine_decision == "no_change"
    assert same_sql.repaired_sql is None


def test_run_refine_request_changed_sql_and_malformed_json() -> None:
    changed = run_refine_request(
        request=make_request(),
        llm_generate_fn=lambda _prompt: '{"changed": true, "revised_sql": "SELECT SUM(amount) FROM invoices;", "edit_summary": "Use SUM.", "confidence": "high"}',
        prompt_builder=build_generic_self_refine_prompt,
    )
    failed = run_refine_request(
        request=make_request(),
        llm_generate_fn=lambda _prompt: "not-json",
        prompt_builder=build_generic_self_refine_prompt,
    )

    assert changed.status == "success"
    assert changed.refine_decision == "changed"
    assert changed.repaired_sql == "SELECT SUM(amount) FROM invoices;"
    assert failed.status == "failed"
    assert failed.repaired_sql is None
    assert failed.error is not None


def test_output_row_sets_mode_source_only_when_repaired() -> None:
    result = run_refine_request(
        request=make_request(),
        llm_generate_fn=lambda _prompt: '{"changed": true, "revised_sql": "SELECT SUM(amount) FROM invoices;", "edit_summary": "Use SUM.", "confidence": "high"}',
        prompt_builder=build_generic_self_refine_prompt,
    )
    output_row = build_output_row(
        source_row=make_evaluated_row(),
        request=make_request(),
        result=result,
        repair_mode=SELF_REFINE_MODE,
        model_metadata={"model_name": "model"},
        context_hash="context",
        status="success",
    )

    assert output_row["repair_mode"] == SELF_REFINE_MODE
    assert output_row["repair_status"] == "success"
    assert output_row["repaired_sql"] == "SELECT SUM(amount) FROM invoices;"
    assert output_row["final_sql_source"] == SELF_REFINE_MODE

    adapted = adapt_repair_rows(
        rows=[output_row],
        original_sql_key="original_generated_sql",
        repaired_sql_key="repaired_sql",
    )
    assert adapted[0]["generated_sql"] == "SELECT SUM(amount) FROM invoices;"
    assert adapted[0]["final_sql_repaired"] is True
    assert adapted[0]["final_sql_source"] == SELF_REFINE_MODE


def test_output_row_keeps_original_source_for_no_change() -> None:
    result = run_refine_request(
        request=make_request(),
        llm_generate_fn=lambda _prompt: '{"changed": false, "revised_sql": "SELECT COUNT(*) FROM invoices;", "edit_summary": "No change.", "confidence": "medium"}',
        prompt_builder=build_generic_self_refine_prompt,
    )
    output_row = build_output_row(
        source_row=make_evaluated_row(),
        request=make_request(),
        result=result,
        repair_mode=SELF_REFINE_MODE,
        model_metadata={"model_name": "model"},
        context_hash="context",
        status="success",
    )

    assert output_row["repaired_sql"] is None
    assert output_row["final_sql_source"] == "original_generated_sql"


def test_resume_key_separates_refine_modes() -> None:
    row = make_evaluated_row()
    self_context = stable_context_hash({"repair_mode": SELF_REFINE_MODE})
    execution_context = stable_context_hash({"repair_mode": EXECUTION_GUIDED_MODE})

    self_key = get_refine_run_key(row, SELF_REFINE_MODE, "model", self_context)
    execution_key = get_refine_run_key(
        row,
        EXECUTION_GUIDED_MODE,
        "model",
        execution_context,
    )

    assert self_key != execution_key
