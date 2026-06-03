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


@dataclass
class VerificationResult:
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

    # Debug fields for the two-stage verifier.
    stage1_answers_question: bool | None = None
    stage1_ambiguous: bool | None = None
    stage2_ran: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_profile_status(execution_profile: str) -> str | None:
    for line in execution_profile.splitlines():
        if line.startswith("[Status]"):
            return line.replace("[Status]", "").strip()
    return None


def build_stage1_verdict_prompt(question: str, execution_profile: str) -> str:
    return f"""
You are a finance-aware SQL semantic verifier.

Task:
Decide whether the DECOMPILED SQL EXECUTION PROFILE answers the USER QUESTION.

You do not see raw SQL, gold SQL, execution results, evaluation labels, or database contents.
Treat the execution profile as the full meaning of the candidate SQL.

Stage 1 output:
Return only valid JSON with exactly these fields:
{{
  "answers_question": true,
  "ambiguous": false
}}

Rules:
- answers_question=true only if all required dimensions in the question are satisfied by the profile.
- answers_question=false if any required dimension is missing, mismatched, contradicted, or unsupported by the profile.
- answers_question=null only if the question or profile is genuinely too unclear to judge.
- ambiguous=true only when the question/profile is genuinely unclear.
- Do not use ambiguity to avoid clear mismatches.
- Do not classify the error type in Stage 1.
- Do not generate a repair hint in Stage 1.
- Do not mark true just because some filters, columns, or values overlap with the question.
- Do not infer missing filters, joins, account scopes, transaction scopes, grouping, ordering, formulas, or measures if they are not shown in the profile.
- Only check dimensions required by the question.

Financial checking guide:
- Row count is not the same as quantity sold unless the profile clearly shows each row represents one unit.
- Gross amount is not the same as debit-normal, credit-normal, payable, receivable, sales, or expense value.
- Product/service scope does not automatically satisfy account/category scope.
- A payment status flag alone does not establish the required AP/AR/account scope.
- If the question asks for a total, payable, receivable, sales, expense, or AP/AR value, raw transaction rows are not sufficient unless transaction-level rows are explicitly requested.
- If the question asks for invoice-level order quantity statistics, row-level quantity statistics may be insufficient.

Few-shot examples:

Example 1
Question: How many invoices are still outstanding for Danielle Lara as of This month?
Profile evidence:
- COUNT(DISTINCT transaction_id)
- transaction type: invoice
- customer/entity value: danielle lara
- AR_paid = No
- transaction_date from start of current month to now
Expected:
{{
  "answers_question": true,
  "ambiguous": false
}}

Example 2
Question: Show the minimum, average, maximum order quantity of all invoices.
Profile evidence:
- MIN(Quantity), AVG(Quantity), MAX(Quantity)
- transaction type: invoice
- No GROUP BY detected
- profile computes row-level quantity statistics
Expected:
{{
  "answers_question": false,
  "ambiguous": false
}}

Example 3
Question: When was the first time we received bill for Drilling oil and gas wells?
Profile evidence:
- MIN(transaction_date)
- transaction type: bill
- Product_Service = Drilling oil and gas wells
Expected:
{{
  "answers_question": false,
  "ambiguous": false
}}

Example 4
Question: What are my AP This week to date?
Profile evidence:
- No aggregation detected
- Selected columns: amount, transaction_id
- Measure type: flow
- Sign convention: gross amount with no debit/credit direction
- AP_paid = Yes
Expected:
{{
  "answers_question": false,
  "ambiguous": false
}}

Actual case:

User question:
{question}

Decompiled SQL execution profile:
{execution_profile}

Return only the JSON object.
""".strip()


def build_stage2_classification_prompt(
    question: str,
    execution_profile: str,
    stage1_verdict: dict[str, Any],
) -> str:
    stage1_verdict_json = json.dumps(
        stage1_verdict,
        ensure_ascii=False,
        sort_keys=True,
    )

    return f"""
You are a finance-aware SQL error classifier.

Stage 1 already decided that the candidate SQL does NOT answer the question.

Your task:
Classify the PRIMARY financial semantic error and generate a concise repair hint.

Allowed mismatch_type values:
- financial_object_error
- financial_measure_error
- computation_logic_error

Do not output any other mismatch_type.

Definitions:
- financial_object_error:
  Wrong or missing financial object, account class, transaction type, business scope, entity scope, product/service scope, customer/vendor scope, account scope, or required literal filter.

- financial_measure_error:
  Wrong numeric or monetary measure, wrong debit/credit/amount sign convention, wrong quantity/count interpretation, missing aggregate financial measure, or raw rows returned when an aggregate financial measure is required.

- computation_logic_error:
  Wrong aggregation logic, grouping, ranking, temporal logic, formula, comparison, ordering, limit, or output granularity.

Return only valid JSON with exactly these fields:
{{
  "mismatch_type": "financial_object_error",
  "mismatch_detail": "",
  "repair_hint": "",
  "confidence": "high"
}}

Few-shot examples:

Example 1 — D3 / Computation Logic Constraint
Question: Show the minimum, average, maximum order quantity of all invoices.
Profile evidence:
- MIN(Quantity), AVG(Quantity), MAX(Quantity)
- transaction type: invoice
- No GROUP BY detected
Annotation:
Primary flagged dimension is D3 / Computation Logic Constraint.
The issue is not the invoice filter. The issue is that the SQL computes row-level quantity statistics instead of invoice-level order quantity statistics.
Expected:
{{
  "mismatch_type": "computation_logic_error",
  "mismatch_detail": "The profile applies MIN/AVG/MAX directly to row-level Quantity instead of computing invoice-level order quantities first.",
  "repair_hint": "Aggregate quantity by invoice or transaction first, then compute MIN, AVG, and MAX over those invoice-level quantities.",
  "confidence": "high"
}}

Example 2 — D1 / Financial Object Constraint
Question: When was the first time we received bill for Drilling oil and gas wells?
Profile evidence:
- MIN(transaction_date)
- transaction type: bill
- Product_Service = Drilling oil and gas wells
Annotation:
Primary flagged dimension is D1 / Financial Object Constraint.
The issue is that the profile filters Drilling oil and gas wells as product_service, but the expected financial/account object scope is different.
Expected:
{{
  "mismatch_type": "financial_object_error",
  "mismatch_detail": "The profile uses product_service scope where the question requires the relevant financial/account object scope.",
  "repair_hint": "Filter using the appropriate account or financial object while keeping the bill transaction scope and first-date logic.",
  "confidence": "high"
}}

Example 3 — D2 / Financial Measure Constraint
Question: What are my AP This week to date?
Profile evidence:
- No aggregation detected
- Selected columns: amount, transaction_id
- Measure type: flow
- Sign convention: gross amount with no debit/credit direction
- AP_paid = Yes
- No schema-grounded account type, transaction type, or entity scope filter detected
Annotation:
Primary flagged dimension is D2 / Financial Measure Constraint.
Important: Although the profile also lacks full AP/account scope, the main teaching signal is D2.
The SQL returns raw transaction_id + gross amount rows instead of computing the required AP financial measure.
Do not classify this example as D1 merely because AP scope is incomplete.
Expected:
{{
  "mismatch_type": "financial_measure_error",
  "mismatch_detail": "The profile returns raw transaction_id and gross amount rows instead of computing the required accounts payable measure.",
  "repair_hint": "Use the appropriate AP/payable measure and aggregate it over the requested week-to-date period.",
  "confidence": "medium"
}}

Actual case:

User question:
{question}

Decompiled SQL execution profile:
{execution_profile}

Stage 1 verdict:
{stage1_verdict_json}

Return only the JSON object.
""".strip()


# Backward-compatible alias.
# Older code may still import/call build_verification_prompt().
# In the two-stage design, this returns the Stage 1 prompt only.
def build_verification_prompt(question: str, execution_profile: str) -> str:
    return build_stage1_verdict_prompt(
        question=question,
        execution_profile=execution_profile,
    )


def parse_verifier_json(raw_output: str) -> dict[str, Any]:
    if raw_output is None:
        raise ValueError("Verifier output is None.")

    text = raw_output.strip()

    if not text:
        raise ValueError("Verifier output is empty.")

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()

    decoder = json.JSONDecoder()

    # Try parsing the whole output first.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Then scan for the first valid JSON object.
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

    # Final repair attempt for escaped scalar values.
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


def normalise_stage1_output(parsed: dict[str, Any]) -> dict[str, Any]:
    answers_question = normalise_bool(parsed.get("answers_question"))

    ambiguous = normalise_bool(parsed.get("ambiguous"))
    if ambiguous is None:
        ambiguous = False

    if ambiguous:
        return {
            "answers_question": None,
            "ambiguous": True,
        }

    if answers_question is True:
        return {
            "answers_question": True,
            "ambiguous": False,
        }

    if answers_question is False:
        return {
            "answers_question": False,
            "ambiguous": False,
        }

    return {
        "answers_question": None,
        "ambiguous": True,
    }


def normalise_stage2_output(parsed: dict[str, Any]) -> dict[str, Any]:
    mismatch_type, invalid_mismatch_type = _normalise_mismatch_type(
        parsed.get("mismatch_type")
    )

    mismatch_detail = normalise_optional_str(parsed.get("mismatch_detail"))
    repair_hint = normalise_optional_str(parsed.get("repair_hint"))
    confidence = _normalise_confidence(parsed.get("confidence")) or "medium"

    return {
        "mismatch_type": mismatch_type,
        "mismatch_detail": mismatch_detail,
        "repair_hint": repair_hint,
        "confidence": confidence,
        "invalid_mismatch_type": invalid_mismatch_type,
    }


def verify_decompiled_sql(
    question: str,
    execution_profile: str,
    llm_generate_fn: Callable[[str], str],
) -> VerificationResult:
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
            confidence=None,
            raw_output=_pack_raw_outputs(stage1_raw_output, stage2_raw_output),
            invalid_mismatch_type=None,
            error=None,
            stage1_answers_question=True,
            stage1_ambiguous=False,
            stage2_ran=False,
        )

    if stage1["answers_question"] is None or stage1["ambiguous"] is True:
        return VerificationResult(
            answers_question=None,
            mismatch_type=None,
            mismatch_detail=None,
            repair_hint=None,
            ambiguous=True,
            should_abstain=True,
            abstain_reason="stage1_marked_ambiguous",
            confidence="low",
            raw_output=_pack_raw_outputs(stage1_raw_output, stage2_raw_output),
            invalid_mismatch_type=None,
            error=None,
            stage1_answers_question=stage1["answers_question"],
            stage1_ambiguous=stage1["ambiguous"],
            stage2_ran=False,
        )

    try:
        stage2_prompt = build_stage2_classification_prompt(
            question=question,
            execution_profile=execution_profile,
            stage1_verdict=stage1,
        )

        stage2_raw_output = llm_generate_fn(stage2_prompt)
        stage2_parsed = parse_verifier_json(stage2_raw_output)
        stage2 = normalise_stage2_output(stage2_parsed)

        if stage2["mismatch_type"] is None:
            return VerificationResult(
                answers_question=None,
                mismatch_type=None,
                mismatch_detail=stage2["mismatch_detail"],
                repair_hint=stage2["repair_hint"],
                ambiguous=True,
                should_abstain=True,
                abstain_reason="invalid_stage2_mismatch_type",
                confidence=stage2["confidence"],
                raw_output=_pack_raw_outputs(stage1_raw_output, stage2_raw_output),
                invalid_mismatch_type=stage2["invalid_mismatch_type"],
                error="Stage 2 did not return a valid mismatch_type.",
                stage1_answers_question=stage1["answers_question"],
                stage1_ambiguous=stage1["ambiguous"],
                stage2_ran=True,
            )

        return VerificationResult(
            answers_question=False,
            mismatch_type=stage2["mismatch_type"],
            mismatch_detail=stage2["mismatch_detail"],
            repair_hint=stage2["repair_hint"],
            ambiguous=False,
            should_abstain=False,
            abstain_reason=None,
            confidence=stage2["confidence"],
            raw_output=_pack_raw_outputs(stage1_raw_output, stage2_raw_output),
            invalid_mismatch_type=stage2["invalid_mismatch_type"],
            error=None,
            stage1_answers_question=stage1["answers_question"],
            stage1_ambiguous=stage1["ambiguous"],
            stage2_ran=True,
        )

    except Exception as exc:
        return VerificationResult(
            answers_question=None,
            mismatch_type=None,
            mismatch_detail=None,
            repair_hint=None,
            ambiguous=True,
            should_abstain=True,
            abstain_reason="invalid_stage2_verifier_output",
            confidence=None,
            raw_output=_pack_raw_outputs(stage1_raw_output, stage2_raw_output),
            invalid_mismatch_type=None,
            error=str(exc),
            stage1_answers_question=stage1["answers_question"],
            stage1_ambiguous=stage1["ambiguous"],
            stage2_ran=True,
        )