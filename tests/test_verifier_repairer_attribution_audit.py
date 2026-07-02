from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.audit_verifier_repairer_attribution import build_audit_row, build_summary


def asa_row(
    question_id: str,
    *,
    ex: int | None,
    inv: int | None,
    asa_strict: int | None = None,
    asa_lower_bound: int | None = None,
    codes: list[str] | None = None,
) -> dict[str, Any]:
    if asa_strict is None:
        asa_strict = 1 if ex == 1 and inv == 1 else 0 if ex == 0 or inv == 0 else None
    if asa_lower_bound is None:
        asa_lower_bound = 1 if asa_strict == 1 else 0
    return {
        "question_id": question_id,
        "gold_sql": "SELECT gold",
        "generated_sql": f"SELECT generated_{question_id}",
        "EX": ex,
        "Inv": inv,
        "asa_strict": asa_strict,
        "asa_lower_bound": asa_lower_bound,
        "fcr_hard_finding_codes": codes or (["posting_side_reversal"] if inv == 0 else []),
    }


def verifier(
    *,
    answers_question: bool,
    repair_hint: str | None = "Fix posting side",
    should_abstain: bool = False,
) -> dict[str, Any]:
    return {
        "question_id": "qid",
        "evaluation_group": "A_correct_executable",
        "question": "question",
        "verification": {
            "answers_question": answers_question,
            "should_abstain": should_abstain,
            "repair_hint": repair_hint,
            "mismatch_type": "financial_measure_error" if not answers_question else None,
            "mismatch_detail": "detail",
            "confidence": "high",
            "failed_evidence": ["evidence"],
        },
    }


def repair(
    *,
    status: str = "success",
    mode: str | None = "semantic",
    sql: str | None = "SELECT repaired",
) -> dict[str, Any]:
    return {
        "question_id": "qid",
        "repair_status": status,
        "repair_mode": mode,
        "repaired_sql": sql,
        "repair_result": {
            "edit_summary": "edit",
            "confidence": "high",
            "error": None,
        },
    }


def final_eval(*, repaired: bool = True, status: str = "success") -> dict[str, Any]:
    return {
        "question_id": "qid",
        "final_sql_repaired": repaired,
        "repair_status": status,
        "repair_mode": "semantic" if repaired else None,
        "evaluation_group": "A_correct_executable",
        "question": "question",
    }


def audit_case(
    *,
    before_inv: int | None = 0,
    before_ex: int | None = 1,
    after_inv: int | None = 0,
    after_ex: int | None = 1,
    verifier_row: dict[str, Any] | None = None,
    repair_row: dict[str, Any] | None = None,
    final_eval_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_audit_row(
        "qid",
        asa_row("qid", ex=before_ex, inv=before_inv),
        asa_row("qid", ex=after_ex, inv=after_inv),
        verifier_row,
        repair_row,
        final_eval_row,
    )


def test_verifier_misses_baseline_hard_failure() -> None:
    row = audit_case(verifier_row=verifier(answers_question=True))

    assert row["missed_by_verifier"] is True
    assert row["primary_bottleneck"] == "verifier_miss"


def test_verifier_catches_but_has_no_repair_hint() -> None:
    row = audit_case(verifier_row=verifier(answers_question=False, repair_hint=None))

    assert row["verifier_caught"] is True
    assert row["verifier_actionable"] is False
    assert row["caught_but_not_actionable"] is True
    assert row["primary_bottleneck"] == "weak_verifier_signal"


def test_verifier_catches_and_repair_succeeds_but_inv_remains_zero() -> None:
    row = audit_case(
        after_inv=0,
        verifier_row=verifier(answers_question=False),
        repair_row=repair(),
        final_eval_row=final_eval(),
    )

    assert row["repair_generated"] is True
    assert row["generated_but_inv_not_fixed"] is True
    assert row["primary_bottleneck"] == "repair_did_not_fix_inv"


def test_repair_fixes_inv_and_preserves_ex() -> None:
    row = audit_case(
        after_inv=1,
        after_ex=1,
        verifier_row=verifier(answers_question=False),
        repair_row=repair(),
        final_eval_row=final_eval(),
    )

    assert row["inv_fixed"] is True
    assert row["fixed_and_ex_preserved"] is True
    assert row["primary_bottleneck"] == "not_bottleneck_fixed"


def test_repair_fixes_inv_but_breaks_ex() -> None:
    row = audit_case(
        after_inv=1,
        after_ex=0,
        verifier_row=verifier(answers_question=False),
        repair_row=repair(),
        final_eval_row=final_eval(),
    )

    assert row["inv_fixed"] is True
    assert row["fixed_but_ex_broken"] is True
    assert row["harmful_gate_accept"] is True
    assert row["primary_bottleneck"] == "repair_fixed_inv_but_broke_ex"


def test_repair_makes_inv_not_evaluable() -> None:
    row = audit_case(
        after_inv=None,
        verifier_row=verifier(answers_question=False),
        repair_row=repair(),
        final_eval_row=final_eval(),
    )

    assert row["_inv_became_not_evaluable"] is True
    assert row["harmful_gate_accept"] is True


def test_gate_accepts_repair_that_worsens_ex_or_inv() -> None:
    worsens_ex = audit_case(
        after_ex=0,
        after_inv=0,
        verifier_row=verifier(answers_question=False),
        repair_row=repair(),
        final_eval_row=final_eval(),
    )
    worsens_inv = audit_case(
        before_inv=1,
        after_inv=0,
        verifier_row=verifier(answers_question=False),
        repair_row=repair(),
        final_eval_row=final_eval(),
    )

    assert worsens_ex["harmful_gate_accept"] is True
    assert worsens_inv["harmful_gate_accept"] is True


def test_non_hard_repaired_and_worsened_is_only_over_repair_not_primary() -> None:
    row = audit_case(
        before_inv=1,
        before_ex=1,
        after_inv=1,
        after_ex=0,
        verifier_row=verifier(answers_question=False),
        repair_row=repair(),
        final_eval_row=final_eval(),
    )
    summary = build_summary(
        [row],
        {
            "asa_unique_question_ids": 1,
            "dedupe_policy": "last",
        },
    )

    assert row["over_repair_candidate"] is True
    assert row["primary_bottleneck"] == "not_primary_cohort"
    assert summary["funnel"]["baseline_fcr_hard_failures"] == 0
    assert summary["funnel"]["over_repaired_worse_outside_primary_cohort"] == 1
