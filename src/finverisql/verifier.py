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

class MaxTokensReachedError(Exception):
    """Raised when the LLM generation terminates due to hitting the max token limit."""
    pass

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
You do not see raw SQL, gold SQL, execution results, or database contents.
Treat the execution profile as the full meaning of the candidate SQL.

Output:
Return ONLY this JSON object — no explanation, no error classification, no repair hint:
{{
  "answers_question": true | false | null,
  "ambiguous": true | false
}}

Decision rules:
- true: profile satisfies all main required dimensions of the question.
- false: clear semantic mismatch or clearly wrong required dimension.
- null: profile is incomplete or under-specified, but not clearly wrong.
- ambiguous=true: question or profile is genuinely unclear; insufficient evidence to judge.
- Do not reject for missing minor supporting evidence that is not required by the question.
- However, if the missing or wrong evidence changes the financial meaning of the answer, treat it as a clear mismatch.
- Central dimensions include the requested financial concept/event, semantic grounding, transaction scope, entity role, measure, time period, and computation logic.
- Do not invent dimensions not shown in the profile; do not accept based on partial overlap alone.

Financial checking guide:
- Row count, quantity, gross amount, debit, credit, payable, receivable, sales, and expense are not interchangeable.
- Financial object checking has two internal parts:
  D1a financial concept/event: whether the profile uses the correct business concept, account class, transaction event, or financial scope requested by the question.
  D1b semantic grounding: whether the profile uses the correct schema role for required entities, literals, and filters.
- A literal match is not enough if the value is grounded to the wrong schema role.
- A correct customer/vendor/product/account value is not enough if the transaction event or financial concept is wrong.
- Product/service, account, transaction type, customer, vendor, employee, and payment status are different semantic roles. Do not treat them as interchangeable.
- Raw transaction rows are not sufficient when the question asks for an aggregate financial measure.
- Correct column or filter is not enough if the computation is at the wrong granularity.
- Required breakdowns such as by month, by customer, by vendor, by account, or by product are part of computation logic, not optional presentation details.

Semantic dimensions to check:
- D1a financial concept/event: correct business concept, transaction event, account class, AP/AR, revenue/expense, invoice/payment/bill/sales scope.
- D1b semantic grounding: correct schema role for required entities, literals, filters, customer/vendor/employee, product/service, account, or transaction type.
- D2 financial measure: count, quantity, amount, debit, credit, payable, receivable, balance, raw rows, or aggregate measure.
- D3 computation logic: aggregation, grouping/breakdown, ranking, ordering, formula, temporal logic, comparison, limit, or unit of analysis.

Examples:
Example 1 — ACCEPT
Question: How many invoices are still outstanding for Danielle Lara as of This month?
Profile: COUNT(DISTINCT transaction_id), transaction type: invoice, customer: danielle lara, AR_paid=No, transaction_date from start of current month to now.
Verdict: true — all required dimensions satisfied (count, invoice scope, customer filter, unpaid status, current-month period).
{{"answers_question": true, "ambiguous": false}}

Example 2 — REJECT (computation_logic_error)
Question: Show the minimum, average, maximum order quantity of all invoices.
Profile: MIN(Quantity), AVG(Quantity), MAX(Quantity), transaction type: invoice, no GROUP BY detected, row-level quantity statistics.
Verdict: false — correct column and scope, but aggregation is at row level rather than the required business-level order quantity granularity.
{{"answers_question": false, "ambiguous": false}}

Example 3 — REJECT (financial_object_error)
Question: When was the first time we received bill for Drilling oil and gas wells?
Profile: MIN(transaction_date), transaction type: bill, Product_Service = Drilling oil and gas wells.
Verdict: false — the required value is present but grounded to product/service scope; the question requires it as the financial account/object identifier.
{{"answers_question": false, "ambiguous": false}}

Example 4 — REJECT (financial_measure_error)
Question: What are my AP This week to date?
Profile: No aggregation detected, selected columns: amount, transaction_id, gross amount with no debit/credit direction, AP_paid=Yes.
Verdict: false — returns raw rows with gross amount instead of an aggregated payable/AP measure; debit/credit direction is absent.
{{"answers_question": false, "ambiguous": false}}

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
  The profile fails to match the financial concept, business event, or semantic grounding required by the question.

  Internal subtype D1a — financial concept/event mismatch:
  Use this reasoning when the profile operates on the wrong financial concept, account class, business event, or transaction scope.
  Examples include revenue vs expense, receivable vs payable, invoice vs payment, bill vs sales receipt, sales scope vs purchase scope.

  Internal subtype D1b — semantic grounding mismatch:
  Use this reasoning when the profile uses the wrong schema role to represent a required value, entity, or scope.
  Examples include product/service where account scope is required, customer where vendor is required, employee where customer/vendor is required, transaction type where account class is required.

  For both D1a and D1b, output:
  "financial_object_error".
  
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

Dimension-level rule:
D3 / Computation Logic Constraint covers wrong aggregation order, grouping level, unit of analysis, ranking logic, formula, comparison, temporal computation, or output granularity. A query can use the correct column and object scope but still fail if it computes the answer at the wrong level.

Case application:
The issue is not the invoice filter. The issue is that the profile computes row-level quantity statistics instead of computing the required business-level order quantity statistics. Therefore, the primary mismatch is D3.

Expected:
{{
  "mismatch_type": "computation_logic_error",
  "mismatch_detail": "The profile applies the aggregation at the wrong granularity. It uses row-level quantity statistics instead of computing the required business-level order quantity logic.",
  "repair_hint": "Apply the aggregation at the correct business level first, then compute the requested minimum, average, and maximum values.",
  "confidence": "high"
}}

Example 2 — D1 / Financial Object Constraint
Question: When was the first time we received bill for Drilling oil and gas wells?

Profile evidence:
- MIN(transaction_date)
- transaction type: bill
- Product_Service = Drilling oil and gas wells

Dimension-level rule:
D1 / Financial Object Constraint has two internal subtypes. D1a covers wrong financial concept or transaction event. D1b covers wrong semantic grounding, where the right-looking value is attached to the wrong schema role. The same literal value can still be wrong if it is grounded as a product/service when the question requires account, transaction, entity, or financial-object scope.

Case application:
The profile uses the target literal as a product/service filter. The question requires the value to identify the relevant financial/account object for the bill. Therefore, the primary mismatch is D1.

Expected:
{{
  "mismatch_type": "financial_object_error",
  "mismatch_detail": "The profile grounds the required value to the wrong semantic role. It uses product/service scope where the question requires the relevant financial/account object scope.",
  "repair_hint": "Filter using the appropriate financial object, account, or schema role while preserving the bill transaction scope and first-date logic.",
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

Dimension-level rule:
D2 / Financial Measure Constraint covers wrong numeric value, wrong monetary column, wrong sign convention, wrong quantity/count interpretation, missing aggregate financial measure, or returning raw rows when a financial measure is required. Correct filters are not enough if the selected value does not represent the requested financial concept.

Case application:
The profile returns raw transaction_id and gross amount rows. The question asks for an AP value. Although the profile also lacks full AP/account scope, the main failure is that it does not compute the required payable/AP financial measure. Therefore, the primary mismatch is D2, not merely D1.

Expected:
{{
  "mismatch_type": "financial_measure_error",
  "mismatch_detail": "The profile returns raw transaction_id and gross amount rows instead of computing the required accounts payable financial measure.",
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
    
    except MaxTokensReachedError as exc:
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
            error="Stage 1 LLM generation was truncated due to max tokens limit.",
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
                mismatch_detail=None,
                repair_hint=None,
                ambiguous=True,
                should_abstain=True,
                abstain_reason="invalid_stage2_mismatch_type",
                confidence=stage2["confidence"],
                raw_output=_pack_raw_outputs(stage1_raw_output, stage2_raw_output),
                invalid_mismatch_type=stage2["invalid_mismatch_type"],
                error=(
                    "Stage 2 did not return a valid mismatch_type. "
                    "Stage 2 mismatch_detail and repair_hint were suppressed because this row is an abstention. "
                    "Inspect raw_output for debugging."
                ),
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