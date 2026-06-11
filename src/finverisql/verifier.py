"""Two-stage finance-aware semantic verifier for FinVeriSQL.

This module receives:
- a natural-language financial question
- a verifier-facing execution profile describing what the generated SQL computes
- an LLM generation function

Verifier design:
- Stage 1 performs accept / reject / abstain verification.
- Stage 1 also classifies the primary semantic mismatch when the SQL is rejected.
- Stage 2 runs only after a Stage 1 rejection and generates a repair hint.
- Stage 2 does not reclassify and does not generate corrected SQL.

Mismatch taxonomy:
- financial_object_error
  D1: What is being measured?
  Wrong financial object, account class, entity, transaction event, product/service,
  payment status, or financial statement element.

- financial_measure_error
  D2: How is it measured?
  Wrong physical measure, aggregation, quantity/count interpretation, debit/credit
  direction, monetary vector, or unit.

- computation_logic_error
  D3: Over what scope, period, granularity, or computation?
  Wrong grouping, temporal period, analytical grain, ranking, ordering, limit,
  distinctness, threshold logic, or formula.

The verifier is descriptive-and-checking only. It does not execute SQL, inspect
gold SQL, regenerate SQL, or infer a full expected query.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable


ABSTAIN_STATUSES = {
    "PARSE_ERROR",
    "UNSUPPORTED_LINEAGE",
    "AMBIGUOUS_SEMANTIC_MAPPING",
}

VALID_MISMATCH_TYPES = {
    "financial_object_error",
    "financial_measure_error",
    "computation_logic_error",
}

VALID_CONFIDENCE_LEVELS = {
    "high",
    "medium",
    "low",
}

VALID_EVIDENCE_MATCHES = {
    "sufficient",
    "insufficient",
    "unclear",
}

VALID_DIAGNOSTIC_STATUSES = {
    "supported",
    "weak",
    "missing",
    "contradicted",
}


class MaxTokensReachedError(Exception):
    """Raised when LLM generation terminates because the max token limit is hit."""

    pass


@dataclass
class VerificationResult:
    """Normalized output from the two-stage verifier."""

    answers_question: bool | None
    mismatch_type: str | None
    mismatch_detail: str | None
    repair_hint: str | None
    ambiguous: bool
    should_abstain: bool
    abstain_reason: str | None
    confidence: str | None
    raw_output: str | None

    invalid_mismatch_type: str | None = None
    error: str | None = None

    # Stage 1 debug fields.
    stage1_answers_question: bool | None = None
    stage1_ambiguous: bool | None = None
    stage2_ran: bool = False
    stage1_evidence_match: str | None = None
    stage1_primary_mismatch_type: str | None = None
    stage1_mismatch_detail: str | None = None
    stage1_failed_evidence: list[str] | None = None
    stage1_diagnostic_dimensions: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the result as a plain JSON-serialisable dictionary."""
        return asdict(self)


def detect_profile_status(execution_profile: str) -> str | None:
    """Detect profile-level statuses that should bypass LLM verification."""
    text = execution_profile.strip()

    try:
        parsed = json.loads(text)

        if isinstance(parsed, dict):
            top_status = parsed.get("status")

            if top_status in ABSTAIN_STATUSES:
                return top_status

            if parsed.get("unsupported_lineage") is True:
                return "UNSUPPORTED_LINEAGE"

            profile_extraction = parsed.get("profile_extraction") or {}

            if isinstance(profile_extraction, dict):
                extraction_status = profile_extraction.get("status")

                if extraction_status in ABSTAIN_STATUSES:
                    return extraction_status

                unsupported_features = profile_extraction.get("unsupported_features") or []

                if "unsupported_lineage" in unsupported_features:
                    return "UNSUPPORTED_LINEAGE"

    except json.JSONDecodeError:
        pass

    return None


def build_stage1_verdict_prompt(question: str, execution_profile: str) -> str:
    return f"""
You are a finance-aware SQL semantic equivalence verifier.

Your task is to compare the user's financial question against the compact semantic profile of the generated SQL.

The compact semantic profile describes what the generated SQL computes.
It is not gold SQL.
It is not the expected answer.

You must compare meaning across three dimensions:

D1 Financial Object:
What business/financial object is being measured?
Examples: customer, vendor, product/service, invoice, bill, payment, revenue, expense, asset, liability, unpaid status.

D2 Financial Measure:
How is it measured?
Examples: SUM(Quantity), COUNT(Transaction_ID), COUNT(*), SUM(Debit), SUM(Credit), SUM(Amount), AVG(Credit).

D3 Computation Logic:
Over what scope, period, grouping, ranking, or granularity?
Examples: by customer, by vendor, monthly, year_to_date, trailing_12_months, top 1, latest, distinct transaction count.

Your job is NOT just to check whether evidence exists.
Your job is to decide whether the compact profile has the same financial meaning as the user question.

Return ONLY this JSON object:
{{
  "dimension_alignment": {{
    "financial_object": "same | different | unclear",
    "financial_measure": "same | different | unclear",
    "computation_logic": "same | different | unclear"
  }},
  
  "evidence_match": "sufficient | insufficient | unclear",
  "answers_question": true | false | null,
  "ambiguous": true | false,
  "primary_mismatch_type": "financial_object_error | financial_measure_error | computation_logic_error | null",
  "mismatch_detail": "",
  "failed_evidence": [],
  "confidence": "high | medium | low"
}}

Decision rules:
- If all answer-changing dimensions are "same", return evidence_match="sufficient" and answers_question=true.
- If any answer-changing dimension is "different", return evidence_match="insufficient" and answers_question=false.
- If a required dimension cannot be determined from the compact profile, return evidence_match="unclear" unless the missing evidence clearly changes the answer.
- Do not reject because of a missing secondary boundary if the question meaning and profile meaning are still equivalent.
- Do not accept based on partial overlap if the measure or computation means something different.

Important BookSQL meaning rules:
- "How many [product/service] did we sell?" means quantity sold. It requires Quantity or quantity-compatible measurement.
- COUNT(*), COUNT(Transaction_ID), or COUNT(DISTINCT Transaction_ID) means number of rows/transactions, not quantity sold.
- "How many times did we sell [product/service]?" means transaction/event count. COUNT(DISTINCT Transaction_ID) can be equivalent.
- "Number of invoices/bills/payments" means count of those transaction objects. It should not be treated as quantity sold.
- "Spend", "cost", or "expense" usually requires Debit or expense-compatible monetary measurement.
- "Revenue" or "sales amount" usually requires Credit or revenue-compatible monetary measurement.
- "Amount" is not automatically equivalent to Debit or Credit when financial direction matters.
- "Monthly" or "by month" means month-level grouping. A current-month date filter alone is not monthly grouping.
- "This fiscal year" is not automatically equivalent to trailing_1_year.
- "Year to date", "month to date", and "week to date" should match the period_hint when available.

Primary mismatch selection:
- If the object/entity/event/status is different, use financial_object_error.
- If the measure has a different meaning, use financial_measure_error.
- If object and measure are mostly correct but period/grouping/ranking/granularity is different, use computation_logic_error.
- If multiple dimensions differ, choose the one that most directly changes the answer.

Actual case:

User question:
{question}

Compact semantic profile:
{execution_profile}

Return only the JSON object.
""".strip()


def build_stage2_repair_prompt(
    question: str,
    execution_profile: str,
    stage1_verdict: dict[str, Any],
) -> str:
    """Build the Stage 2 repair-hint prompt.

    Stage 2 only writes repair guidance based on Stage 1. It must not reclassify.
    """
    stage1_verdict_json = json.dumps(
        stage1_verdict,
        ensure_ascii=False,
        sort_keys=True,
    )

    return f"""
You are a finance-aware SQL repair planner.

Stage 1 already decided that the candidate SQL does NOT answer the question and already classified the primary mismatch type.

Your task:
- Generate a concise repair hint for a later SQL repair step.
- Base the hint on Stage 1 primary_mismatch_type, mismatch_detail, and failed_evidence.
- Do not reclassify the mismatch type.
- Do not change the Stage 1 verdict.
- Do not generate corrected SQL.

Return only valid JSON with exactly these fields:
{{
  "repair_hint": "",
  "confidence": "high | medium | low"
}}

Examples:

Input primary_mismatch_type: financial_object_error
Input failed_evidence: ["missing invoice transaction scope", "missing reliable unpaid payment-status evidence"]
Output:
{{"repair_hint": "Add or repair the missing business-object filters, such as invoice transaction scope and reliable unpaid/open-balance evidence, while preserving the customer scope.", "confidence": "high"}}

Input primary_mismatch_type: financial_measure_error
Input failed_evidence: ["missing quantity measure", "row_count is not equivalent to quantity sold"]
Output:
{{"repair_hint": "Use the quantity field or quantity-compatible expression for the sold-item measure instead of counting rows, while preserving the product/service scope.", "confidence": "high"}}

Input primary_mismatch_type: computation_logic_error
Input failed_evidence: ["missing temporal_period grouping for monthly breakdown"]
Output:
{{"repair_hint": "Add the required month-level grouping while preserving the vendor scope and spend-compatible measure.", "confidence": "high"}}

Actual case:

User question:
{question}

Execution profile:
{execution_profile}

Stage 1 verdict:
{stage1_verdict_json}

Return only the JSON object.
""".strip()


def build_stage2_classification_prompt(
    question: str,
    execution_profile: str,
    stage1_verdict: dict[str, Any],
) -> str:
    """Backward-compatible alias for the old Stage 2 function name."""
    return build_stage2_repair_prompt(
        question=question,
        execution_profile=execution_profile,
        stage1_verdict=stage1_verdict,
    )


def parse_verifier_json(raw_output: str) -> dict[str, Any]:
    """Parse a JSON object from LLM verifier output."""
    if raw_output is None:
        raise ValueError("Verifier output is None.")

    text = raw_output.strip()

    if not text:
        raise ValueError("Verifier output is empty.")

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()

    decoder = json.JSONDecoder()

    try:
        parsed = json.loads(text)

        if isinstance(parsed, dict):
            return parsed

    except json.JSONDecodeError:
        pass

    for index, char in enumerate(text):
        if char != "{":
            continue

        candidate = text[index:]

        try:
            parsed, _ = decoder.raw_decode(candidate)

            if isinstance(parsed, dict):
                return parsed

        except json.JSONDecodeError:
            continue

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end <= start:
        raise ValueError("No valid JSON object found in verifier output.")

    candidate = text[start : end + 1]

    repaired = re.sub(
        r'(:\s*)\\"([^"\\]+)\\"\\"?\s*([,}])',
        r'\1"\2"\3',
        candidate,
    )

    return json.loads(repaired)


def normalise_bool(value: Any) -> bool | None:
    """Convert common JSON-ish boolean values to True, False, or None."""
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        lowered = value.strip().lower()

        if lowered in {"true", "yes", "y", "1"}:
            return True

        if lowered in {"false", "no", "n", "0"}:
            return False

        if lowered in {"null", "none", "n/a", ""}:
            return None

    if isinstance(value, int):
        if value == 1:
            return True

        if value == 0:
            return False

    return None


def normalise_optional_str(value: Any) -> str | None:
    """Convert model-produced strings to stripped text or None."""
    if value is None:
        return None

    if isinstance(value, str):
        cleaned = value.strip()

        if cleaned.lower() in {"", "null", "none", "n/a"}:
            return None

        return cleaned

    return str(value).strip() or None


def _normalise_confidence(value: Any) -> str | None:
    raw = normalise_optional_str(value)

    if raw is None:
        return None

    confidence = raw.lower()

    if confidence not in VALID_CONFIDENCE_LEVELS:
        return None

    return confidence


def _normalise_mismatch_type(value: Any) -> tuple[str | None, str | None]:
    raw = normalise_optional_str(value)

    if raw is None:
        return None, None

    candidate = raw.strip().lower()

    if candidate in VALID_MISMATCH_TYPES:
        return candidate, None

    return None, raw


def _pack_raw_outputs(
    stage1_raw_output: str | None,
    stage2_raw_output: str | None = None,
) -> str:
    return json.dumps(
        {
            "stage1_raw_output": stage1_raw_output,
            "stage2_raw_output": stage2_raw_output,
        },
        ensure_ascii=False,
    )


def _stage1_evidence_debug(stage1: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage1_evidence_match": stage1.get("evidence_match"),
        "stage1_primary_mismatch_type": stage1.get("primary_mismatch_type"),
        "stage1_mismatch_detail": stage1.get("mismatch_detail"),
        "stage1_failed_evidence": stage1.get("failed_evidence"),
        "stage1_diagnostic_dimensions": stage1.get("diagnostic_dimensions"),
    }


def _normalise_evidence_match(value: Any) -> str:
    raw = normalise_optional_str(value)

    if raw is None:
        return "unclear"

    evidence_match = raw.lower().strip()

    if evidence_match in VALID_EVIDENCE_MATCHES:
        return evidence_match

    return "unclear"


def _normalise_diagnostic_status(value: Any) -> str:
    raw = normalise_optional_str(value)

    if raw is None:
        return "weak"

    status = raw.lower().strip()

    if status in VALID_DIAGNOSTIC_STATUSES:
        return status

    return "weak"


def _normalise_diagnostic_dimensions(value: Any) -> dict[str, str]:
    dimensions = value if isinstance(value, dict) else {}

    return {
        "financial_object": _normalise_diagnostic_status(
            dimensions.get("financial_object")
        ),
        "financial_measure": _normalise_diagnostic_status(
            dimensions.get("financial_measure")
        ),
        "computation_logic": _normalise_diagnostic_status(
            dimensions.get("computation_logic")
        ),
    }


def _normalise_failed_evidence(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, list):
        return [
            item
            for item in (normalise_optional_str(item) for item in value)
            if item
        ]

    text = normalise_optional_str(value)

    return [text] if text else []


def normalise_stage1_output(parsed: dict[str, Any]) -> dict[str, Any]:
    """Normalize and enforce consistency for Stage 1 verifier output.

    `evidence_match` is treated as the source of truth.
    Diagnostic dimensions are preserved for debugging and are not hard gates.
    """
    evidence_match = _normalise_evidence_match(parsed.get("evidence_match"))

    primary_mismatch_type, invalid_mismatch_type = _normalise_mismatch_type(
        parsed.get("primary_mismatch_type")
    )

    mismatch_detail = normalise_optional_str(parsed.get("mismatch_detail"))
    failed_evidence = _normalise_failed_evidence(parsed.get("failed_evidence"))
    diagnostic_dimensions = _normalise_diagnostic_dimensions(
        parsed.get("diagnostic_dimensions")
    )
    confidence = _normalise_confidence(parsed.get("confidence")) or "medium"

    if evidence_match == "sufficient":
        return {
            "evidence_match": "sufficient",
            "answers_question": True,
            "ambiguous": False,
            "primary_mismatch_type": None,
            "mismatch_detail": None,
            "failed_evidence": [],
            "diagnostic_dimensions": diagnostic_dimensions,
            "confidence": confidence,
            "invalid_mismatch_type": None,
        }

    if evidence_match == "insufficient" and primary_mismatch_type is not None:
        return {
            "evidence_match": "insufficient",
            "answers_question": False,
            "ambiguous": False,
            "primary_mismatch_type": primary_mismatch_type,
            "mismatch_detail": mismatch_detail,
            "failed_evidence": failed_evidence,
            "diagnostic_dimensions": diagnostic_dimensions,
            "confidence": confidence,
            "invalid_mismatch_type": invalid_mismatch_type,
        }

    if evidence_match == "insufficient":
        return {
            "evidence_match": "insufficient",
            "answers_question": None,
            "ambiguous": True,
            "primary_mismatch_type": None,
            "mismatch_detail": mismatch_detail,
            "failed_evidence": failed_evidence,
            "diagnostic_dimensions": diagnostic_dimensions,
            "confidence": confidence,
            "invalid_mismatch_type": invalid_mismatch_type,
        }

    return {
        "evidence_match": "unclear",
        "answers_question": None,
        "ambiguous": True,
        "primary_mismatch_type": None,
        "mismatch_detail": mismatch_detail,
        "failed_evidence": failed_evidence,
        "diagnostic_dimensions": diagnostic_dimensions,
        "confidence": confidence,
        "invalid_mismatch_type": invalid_mismatch_type,
    }


def normalise_stage2_output(parsed: dict[str, Any]) -> dict[str, Any]:
    """Normalize Stage 2 repair-hint output.

    Stage 2 is not allowed to reclassify. Any mismatch fields returned by the
    model are ignored by the orchestrator.
    """
    repair_hint = normalise_optional_str(parsed.get("repair_hint"))
    confidence = _normalise_confidence(parsed.get("confidence")) or "medium"

    return {
        "repair_hint": repair_hint,
        "confidence": confidence,
    }


def verify_execution_profile(
    question: str,
    execution_profile: str,
    llm_generate_fn: Callable[[str], str],
) -> VerificationResult:
    """Run the two-stage semantic verifier over an execution profile."""
    status = detect_profile_status(execution_profile)

    if status in ABSTAIN_STATUSES:
        return VerificationResult(
            answers_question=None,
            mismatch_type=None,
            mismatch_detail=None,
            repair_hint=None,
            ambiguous=True,
            should_abstain=True,
            abstain_reason=status,
            confidence=None,
            raw_output=None,
            invalid_mismatch_type=None,
            error=None,
            stage1_answers_question=None,
            stage1_ambiguous=None,
            stage2_ran=False,
        )

    stage1_raw_output = None
    stage2_raw_output = None

    try:
        stage1_prompt = build_stage1_verdict_prompt(
            question=question,
            execution_profile=execution_profile,
        )

        stage1_raw_output = llm_generate_fn(stage1_prompt)
        stage1_parsed = parse_verifier_json(stage1_raw_output)
        stage1 = normalise_stage1_output(stage1_parsed)

    except MaxTokensReachedError:
        return VerificationResult(
            answers_question=None,
            mismatch_type=None,
            mismatch_detail=None,
            repair_hint=None,
            ambiguous=True,
            should_abstain=True,
            abstain_reason="stage1_max_tokens_reached",
            confidence=None,
            raw_output=_pack_raw_outputs(stage1_raw_output, stage2_raw_output),
            invalid_mismatch_type=None,
            error="Stage 1 LLM generation was truncated due to max token limit.",
            stage1_answers_question=None,
            stage1_ambiguous=None,
            stage2_ran=False,
        )

    except Exception as exc:
        return VerificationResult(
            answers_question=None,
            mismatch_type=None,
            mismatch_detail=None,
            repair_hint=None,
            ambiguous=True,
            should_abstain=True,
            abstain_reason="invalid_stage1_verifier_output",
            confidence=None,
            raw_output=_pack_raw_outputs(stage1_raw_output, stage2_raw_output),
            invalid_mismatch_type=None,
            error=str(exc),
            stage1_answers_question=None,
            stage1_ambiguous=None,
            stage2_ran=False,
        )

    if stage1["answers_question"] is True:
        return VerificationResult(
            answers_question=True,
            mismatch_type=None,
            mismatch_detail=None,
            repair_hint=None,
            ambiguous=False,
            should_abstain=False,
            abstain_reason=None,
            confidence=stage1.get("confidence"),
            raw_output=_pack_raw_outputs(stage1_raw_output, stage2_raw_output),
            invalid_mismatch_type=None,
            error=None,
            stage1_answers_question=True,
            stage1_ambiguous=False,
            stage2_ran=False,
            **_stage1_evidence_debug(stage1),
        )

    if stage1["answers_question"] is None or stage1["ambiguous"] is True:
        abstain_reason = "stage1_marked_unclear"

        if stage1.get("evidence_match") == "insufficient":
            abstain_reason = "invalid_stage1_mismatch_type"

        return VerificationResult(
            answers_question=None,
            mismatch_type=None,
            mismatch_detail=None,
            repair_hint=None,
            ambiguous=True,
            should_abstain=True,
            abstain_reason=abstain_reason,
            confidence=stage1.get("confidence") or "low",
            raw_output=_pack_raw_outputs(stage1_raw_output, stage2_raw_output),
            invalid_mismatch_type=stage1.get("invalid_mismatch_type"),
            error=None,
            stage1_answers_question=stage1["answers_question"],
            stage1_ambiguous=stage1["ambiguous"],
            stage2_ran=False,
            **_stage1_evidence_debug(stage1),
        )

    try:
        stage2_prompt = build_stage2_repair_prompt(
            question=question,
            execution_profile=execution_profile,
            stage1_verdict=stage1,
        )

        stage2_raw_output = llm_generate_fn(stage2_prompt)
        stage2_parsed = parse_verifier_json(stage2_raw_output)
        stage2 = normalise_stage2_output(stage2_parsed)

        return VerificationResult(
            answers_question=False,
            mismatch_type=stage1["primary_mismatch_type"],
            mismatch_detail=stage1["mismatch_detail"],
            repair_hint=stage2["repair_hint"],
            ambiguous=False,
            should_abstain=False,
            abstain_reason=None,
            confidence=stage2["confidence"],
            raw_output=_pack_raw_outputs(stage1_raw_output, stage2_raw_output),
            invalid_mismatch_type=None,
            error=None,
            stage1_answers_question=stage1["answers_question"],
            stage1_ambiguous=stage1["ambiguous"],
            stage2_ran=True,
            **_stage1_evidence_debug(stage1),
        )

    except Exception as exc:
        return VerificationResult(
            answers_question=False,
            mismatch_type=stage1["primary_mismatch_type"],
            mismatch_detail=stage1["mismatch_detail"],
            repair_hint=None,
            ambiguous=False,
            should_abstain=False,
            abstain_reason=None,
            confidence=stage1.get("confidence"),
            raw_output=_pack_raw_outputs(stage1_raw_output, stage2_raw_output),
            invalid_mismatch_type=None,
            error=f"Stage 2 repair-hint generation failed: {exc}",
            stage1_answers_question=stage1["answers_question"],
            stage1_ambiguous=stage1["ambiguous"],
            stage2_ran=True,
            **_stage1_evidence_debug(stage1),
        )


def verify_decompiled_sql(
    question: str,
    execution_profile: str,
    llm_generate_fn: Callable[[str], str],
) -> VerificationResult:
    """Backward-compatible alias for older runner imports."""
    return verify_execution_profile(
        question=question,
        execution_profile=execution_profile,
        llm_generate_fn=llm_generate_fn,
    )