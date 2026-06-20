"""Multi-stage finance-aware semantic verifier for FinVeriSQL.

This module receives:
- a natural-language financial question
- a decomposed user intent representation (Stage 1)
- a verifier-facing execution profile describing what the generated SQL computes
- an LLM generation function

Verifier design:
- Stage 2 performs accept / reject / abstain verification (direct comparison and optional probing).
- Stage 2 also classifies the primary semantic mismatch when the SQL is rejected.
- Stage 3 runs only after a Stage 2 rejection and generates a repair hint.
- Stage 3 does not reclassify and does not generate corrected SQL.

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

    # Stage 2 debug fields.
    stage2_answers_question: bool | None = None
    stage2_ambiguous: bool | None = None
    stage3_ran: bool = False
    stage2_evidence_match: str | None = None
    stage2_primary_mismatch_type: str | None = None
    stage2_mismatch_detail: str | None = None
    stage2_failed_evidence: list[str] | None = None
    stage2_diagnostic_dimensions: dict[str, str] | None = None
    
    # Probing tracking
    probes_used: int = 0
    probe_trajectory: list[dict[str, Any]] | None = None

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


def build_stage2_verification_prompt(
    intent_representation: dict[str, Any], 
    execution_profile: str,
    include_probe: bool = True,
) -> str:
    intent_json = json.dumps(intent_representation, ensure_ascii=False, indent=2)
    
    probe_field = '  "suggested_probe": "<If ambiguous, write a single specific question to query the semantic profile (e.g., \'Does column master_txn_table.Amount represent a monetary value?\'). DO NOT ask questions already asked in the probe_context> | null",\n' if include_probe else ""
    probe_rule = '- If evidence is missing or ambiguous, return evidence_match="unclear", ambiguous=true, and provide a `suggested_probe`.' if include_probe else '- If evidence is missing or ambiguous, return evidence_match="unclear" and ambiguous=true.'
    
    return f"""
You are a finance-aware SQL semantic equivalence verifier.

Your task is to compare the required semantic checks in the Decomposed Intent against the evidence in the Compact Semantic Profile.

The compact semantic profile describes what the generated SQL computes.
It is not gold SQL.
It is not the expected answer.

You must evaluate alignment across three dimensions:
D1 Financial Object: Does the `scope` and object evidence align with the intended entity/object?
D2 Financial Measure: Does the `measurement` align with the intended measure kind and aggregation?
D3 Computation Logic: Does the `topology` align with intended grouping, ordering, limit, and temporal filters?

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
  "mismatch_detail": "<explain the mismatch or ambiguity>",
{probe_field}
  "failed_evidence": ["<list of string evidence>"],
  "confidence": "high | medium | low"
}}

Decision rules:
- If all dimensions are "same", return evidence_match="sufficient" and answers_question=true.
- If any dimension is "different", return evidence_match="insufficient" and answers_question=false.
- Do not reject based on strict terminology differences. Focus on actual financial equivalence.
- EQUIVALENCE RULE: An intent `measure_kind: "monetary"` or `"amount"` is satisfied by a profile `measure_type: "flow"` or `unit: "monetary"`.
- EQUIVALENCE RULE: An intent `measure_kind: "count"` is satisfied by a profile `semantic_operation: "row_count"` or `"distinct_transaction_count"`.
- STRICT RULE: An intent requiring `measure_kind: "quantity"` (summing units sold) is DIFFERENT from a profile measuring `row_count` or `distinct_transaction_count`. Do not conflate them.
- STRICT RULE: If the intent requires grouping (e.g., `requires_group_by_period: true` or `group_by` is not empty) but the profile topology has `group_by: "none"`, then `computation_logic` is "different".
- EQUIVALENCE RULE: An intent requiring `MAX` or `MIN` aggregation is satisfied by a profile using `ORDER BY ... DESC LIMIT 1` or `ASC LIMIT 1`.
{probe_rule}

Decomposed Intent:
{intent_json}

Compact Semantic Profile:
{execution_profile}

Return only the JSON object.
""".strip()


def build_stage2_semantic_verification_prompt(
    intent_representation: dict[str, Any],
    execution_profile: str,
    include_probe: bool = True,
) -> str:
    intent_json = json.dumps(intent_representation, ensure_ascii=False, indent=2)

    probe_field = '  "suggested_probe": "<If ambiguous, write a single specific question to query the full semantic profile (e.g., \'Does measure_usage show a quantity measure or transaction count?\'). DO NOT ask questions already asked in the probe_context> | null",\n' if include_probe else ""
    probe_rule = '- If evidence is missing or ambiguous, return evidence_match="unclear", ambiguous=true, and provide a `suggested_probe`.' if include_probe else '- If evidence is missing or ambiguous, return evidence_match="unclear" and ambiguous=true.'

    return f"""
You are a finance-aware SQL semantic equivalence verifier.

Your task is to compare the required semantic checks in the Decomposed Intent against the evidence in the Full Semantic Profile.

The full semantic profile describes what the generated SQL computes.
It is not gold SQL.
It is not the expected answer.

You must evaluate alignment across three dimensions:
D1 Financial Object: Does `object_scope` align with the intended entity/object, transaction type, account type, customer/vendor, product/service, or status constraints?
D2 Financial Measure: Does `measure_usage` align with the intended measure kind and aggregation?
D3 Computation Logic: Does `logic` align with intended grouping, ordering, limit, and temporal filters?

Use these full semantic profile fields:
- `object_scope.has_transaction_type_filter`, `transaction_type_values`, `has_account_type_filter`, `account_type_values`, and `scope_constraints` for financial object/scope evidence.
- `measure_usage.aggregated_columns`, `selected_columns`, `aggregation_functions`, `measure_types`, `units`, and `financial_roles` for measure evidence.
- `logic.filter_conditions`, `date_conditions`, `group_by_columns`, `order_by_expressions`, and `limit` for computation logic evidence.
- `table_context` for table grain and transaction grouping context.
- `warnings` for profile extraction caveats.

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
  "mismatch_detail": "<explain the mismatch or ambiguity>",
{probe_field}
  "failed_evidence": ["<list of string evidence>"],
  "confidence": "high | medium | low"
}}

Decision rules:
- If all dimensions are "same", return evidence_match="sufficient" and answers_question=true.
- If any dimension is "different", return evidence_match="insufficient" and answers_question=false.
- Do not reject based on strict terminology differences. Focus on actual financial equivalence.
- EQUIVALENCE RULE: An intent `measure_kind: "monetary"` or `"amount"` is satisfied by a profile `measure_type: "flow"` or `unit: "monetary"`.
- EQUIVALENCE RULE: An intent `measure_kind: "count"` is satisfied by a profile `semantic_operation: "row_count"` or `"distinct_transaction_count"`.
- STRICT RULE: An intent requiring `measure_kind: "quantity"` (summing units sold) is DIFFERENT from a profile measuring `row_count` or `distinct_transaction_count`. Do not conflate them.
- STRICT RULE: If the intent requires grouping (e.g., `requires_group_by_period: true` or `group_by` is not empty) but `logic.group_by_columns` is empty, then `computation_logic` is "different".
- EQUIVALENCE RULE: An intent requiring `MAX` or `MIN` aggregation is satisfied by a profile using `ORDER BY ... DESC LIMIT 1` or `ASC LIMIT 1`.
- Missing required filters are mismatches, not matches. For example, if the intent requires a payment/invoice/sale event but `object_scope.has_transaction_type_filter` is false, treat financial object or computation logic as different unless the profile has equivalent evidence elsewhere.
- If the intent requires a broad period such as last fiscal year, last month, or MTD, but `logic.date_conditions` show a single-day or incompatible date range, treat computation logic as different.
{probe_rule}

Decomposed Intent:
{intent_json}

Full Semantic Profile:
{execution_profile}

Return only the JSON object.
""".strip()


def build_stage2_prompt_for_profile(
    profile_mode: str,
    intent_representation: dict[str, Any],
    execution_profile: str,
    include_probe: bool = True,
) -> str:
    if profile_mode == "semantic":
        return build_stage2_semantic_verification_prompt(
            intent_representation=intent_representation,
            execution_profile=execution_profile,
            include_probe=include_probe,
        )

    return build_stage2_verification_prompt(
        intent_representation=intent_representation,
        execution_profile=execution_profile,
        include_probe=include_probe,
    )


def build_stage2_probe_prompt(
    probe_question: str, 
    intent_representation: dict[str, Any],
    execution_profile: str
) -> str:
    intent_json = json.dumps(intent_representation, ensure_ascii=False, indent=2)
    return f"""
You are a finance-aware SQL semantic expert.

Your task is to answer a specific question about the meaning of the generated SQL to resolve an ambiguity.

Decomposed Intent:
{intent_json}

Compact semantic profile:
{execution_profile}

Question to resolve:
{probe_question}

Evaluate the profile carefully and answer the question.
Return ONLY this JSON object:
{{
  "reasoning": "<write your step-by-step logic here, focusing only on the specific question>",
  "probe_resolution": "matches_intent | contradicts_intent | unclear"
}}

Return only the JSON object.
""".strip()


def build_stage2_semantic_probe_prompt(
    probe_question: str,
    intent_representation: dict[str, Any],
    execution_profile: str
) -> str:
    intent_json = json.dumps(intent_representation, ensure_ascii=False, indent=2)
    return f"""
You are a finance-aware SQL semantic expert.

Your task is to answer a specific question about the meaning of the generated SQL to resolve an ambiguity.

Decomposed Intent:
{intent_json}

Full semantic profile:
{execution_profile}

Question to resolve:
{probe_question}

Evaluate the full semantic profile carefully. Use `object_scope`, `measure_usage`, `logic`, `table_context`, and `warnings` as evidence.
Return ONLY this JSON object:
{{
  "reasoning": "<write your step-by-step logic here, focusing only on the specific question>",
  "probe_resolution": "matches_intent | contradicts_intent | unclear"
}}

Return only the JSON object.
""".strip()


def build_stage2_probe_prompt_for_profile(
    profile_mode: str,
    probe_question: str,
    intent_representation: dict[str, Any],
    execution_profile: str,
) -> str:
    if profile_mode == "semantic":
        return build_stage2_semantic_probe_prompt(
            probe_question=probe_question,
            intent_representation=intent_representation,
            execution_profile=execution_profile,
        )

    return build_stage2_probe_prompt(
        probe_question=probe_question,
        intent_representation=intent_representation,
        execution_profile=execution_profile,
    )


def build_stage3_repair_prompt(
    question: str,
    intent_representation: dict[str, Any],
    execution_profile: str,
    stage2_verdict: dict[str, Any],
) -> str:
    """Build the Stage 3 repair-hint prompt.

    Stage 3 only writes repair guidance based on Stage 2. It must not reclassify.
    """
    intent_json = json.dumps(intent_representation, ensure_ascii=False, indent=2)
    stage2_verdict_json = json.dumps(
        stage2_verdict,
        ensure_ascii=False,
        sort_keys=True,
    )

    return f"""
You are a finance-aware SQL repair planner.

Stage 2 already decided that the candidate SQL does NOT answer the question and classified the primary mismatch type.

Your task:
- Generate a concise repair hint for a later SQL repair step.
- Base the hint on Stage 2 primary_mismatch_type, mismatch_detail, and failed_evidence.
- Do not reclassify the mismatch type.
- Do not change the Stage 2 verdict.
- Do not generate corrected SQL.

Return only valid JSON with exactly these fields:
{{
  "repair_hint": "",
  "confidence": "high | medium | low"
}}

User question:
{question}

Decomposed Intent:
{intent_json}

Execution profile:
{execution_profile}

Stage 2 verdict:
{stage2_verdict_json}

Return only the JSON object.
""".strip()


def build_stage3_classification_prompt(
    question: str,
    intent_representation: dict[str, Any],
    execution_profile: str,
    stage2_verdict: dict[str, Any],
) -> str:
    """Backward-compatible alias for the old classification prompt name."""
    return build_stage3_repair_prompt(
        question=question,
        intent_representation=intent_representation,
        execution_profile=execution_profile,
        stage2_verdict=stage2_verdict,
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
    stage2_raw_output: str | None = None,
    stage3_raw_output: str | None = None,
) -> str:
    return json.dumps(
        {
            "stage2_raw_output": stage2_raw_output,
            "stage3_raw_output": stage3_raw_output,
        },
        ensure_ascii=False,
    )


def _stage2_evidence_debug(stage2: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage2_evidence_match": stage2.get("evidence_match"),
        "stage2_primary_mismatch_type": stage2.get("primary_mismatch_type"),
        "stage2_mismatch_detail": stage2.get("mismatch_detail"),
        "stage2_failed_evidence": stage2.get("failed_evidence"),
        "stage2_diagnostic_dimensions": stage2.get("diagnostic_dimensions"),
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


def normalise_stage2_verification_output(parsed: dict[str, Any]) -> dict[str, Any]:
    """Normalize and enforce consistency for Stage 2 verifier output.

    `evidence_match` is treated as the source of truth.
    Diagnostic dimensions are preserved for debugging and are not hard gates.
    """
    evidence_match = _normalise_evidence_match(parsed.get("evidence_match"))
    primary_mismatch_type, invalid_mismatch_type = _normalise_mismatch_type(
        parsed.get("primary_mismatch_type")
    )
    mismatch_detail = normalise_optional_str(parsed.get("mismatch_detail"))
    suggested_probe = normalise_optional_str(parsed.get("suggested_probe"))
    failed_evidence = _normalise_failed_evidence(parsed.get("failed_evidence"))
    diagnostic_dimensions = _normalise_diagnostic_dimensions(
        parsed.get("diagnostic_dimensions")
    )
    confidence = _normalise_confidence(parsed.get("confidence")) or "medium"

    is_ambiguous = normalise_bool(parsed.get("ambiguous"))
    if is_ambiguous is None:
        is_ambiguous = False

    base_dict = {
        "evidence_match": evidence_match,
        "answers_question": None,
        "ambiguous": is_ambiguous,
        "primary_mismatch_type": None,
        "mismatch_detail": mismatch_detail,
        "suggested_probe": suggested_probe,
        "failed_evidence": failed_evidence,
        "diagnostic_dimensions": diagnostic_dimensions,
        "confidence": confidence,
        "invalid_mismatch_type": invalid_mismatch_type,
    }

    if evidence_match == "sufficient":
        base_dict.update({"answers_question": True, "invalid_mismatch_type": None})
    elif evidence_match == "insufficient" and primary_mismatch_type is not None:
        base_dict.update({"answers_question": False, "primary_mismatch_type": primary_mismatch_type})
    elif evidence_match == "insufficient":
        base_dict.update({"ambiguous": True})
    else:
        base_dict.update({"evidence_match": "unclear", "ambiguous": True})

    return base_dict


def normalise_stage3_repair_output(parsed: dict[str, Any]) -> dict[str, Any]:
    """Normalize Stage 3 repair-hint output.

    Stage 3 is not allowed to reclassify. Any mismatch fields returned by the
    model are ignored by the orchestrator.
    """
    repair_hint = normalise_optional_str(parsed.get("repair_hint"))
    confidence = _normalise_confidence(parsed.get("confidence")) or "medium"

    return {
        "repair_hint": repair_hint,
        "confidence": confidence,
    }


class SemanticVerifier:
    def __init__(
        self,
        llm_generate_fn: Callable[[str], str],
        probing_mode: str = "hybrid",
        max_probes: int = 7,
        profile_mode: str = "compact",
    ):
        self.llm_generate_fn = llm_generate_fn
        self.probing_mode = probing_mode
        self.max_probes = max_probes
        self.profile_mode = profile_mode

    def verify(self, question: str, intent: dict[str, Any], execution_profile: str) -> VerificationResult:
        """Run the multi-stage semantic verifier over an execution profile."""
        status = detect_profile_status(execution_profile)

        if status in ABSTAIN_STATUSES:
            return VerificationResult(
                answers_question=None, mismatch_type=None, mismatch_detail=None, repair_hint=None,
                ambiguous=True, should_abstain=True, abstain_reason=status, confidence=None,
                raw_output=None, invalid_mismatch_type=None, error=None, probes_used=0,
                probe_trajectory=[], stage2_answers_question=None, stage2_ambiguous=None, stage3_ran=False
            )

        probes_used = 0
        probe_trajectory = []
        raw_outputs = []
        current_intent = dict(intent)
        stage2_verification = None

        try:
            while probes_used <= self.max_probes:
                stage2_prompt = build_stage2_prompt_for_profile(
                    profile_mode=self.profile_mode,
                    intent_representation=current_intent,
                    execution_profile=execution_profile,
                    include_probe=(self.probing_mode != "none"),
                )

                raw_stage2 = self.llm_generate_fn(stage2_prompt)
                raw_outputs.append({"step": f"stage2_{probes_used}", "raw": raw_stage2})
                
                stage2_parsed = parse_verifier_json(raw_stage2)
                stage2_verification = normalise_stage2_verification_output(stage2_parsed)

                # --- Probing loop control logic ---
                if self.probing_mode == "none":
                    # 'none' mode (direct comparison) never probes.
                    break
                    
                if self.probing_mode == "hybrid":
                    # 'hybrid' mode is adaptive. It only probes if the initial
                    # verification is ambiguous or unclear. If confident, stop.
                    if not stage2_verification["ambiguous"] and stage2_verification["evidence_match"] != "unclear":
                        break
                
                # 'probe' mode is eager. It does not check for ambiguity and will
                # proceed to the next check, continuing to probe as long as the
                # LLM can suggest a question.

                suggested_probe = stage2_verification.get("suggested_probe")
                if not suggested_probe or probes_used >= self.max_probes:
                    # For all probing modes, stop if there's no more questions to ask or the limit is reached.
                    break
                    
                probes_used += 1
                probe_prompt = build_stage2_probe_prompt_for_profile(
                    profile_mode=self.profile_mode,
                    probe_question=suggested_probe,
                    intent_representation=current_intent,
                    execution_profile=execution_profile,
                )
                raw_probe = self.llm_generate_fn(probe_prompt)
                raw_outputs.append({"step": f"probe_{probes_used}", "raw": raw_probe})
                
                probe_parsed = parse_verifier_json(raw_probe)
                probe_resolution = probe_parsed.get("probe_resolution", "unclear")
                
                probe_trajectory.append({
                    "probe_question": suggested_probe,
                    "resolution": probe_resolution,
                    "reasoning": probe_parsed.get("reasoning", "")
                })
                
                if "probe_context" not in current_intent:
                    current_intent["probe_context"] = []
                    
                current_intent["probe_context"].append({
                    "question": suggested_probe,
                    "answer": probe_parsed.get("reasoning", ""),
                    "resolution": probe_resolution
                })

        except MaxTokensReachedError:
            return VerificationResult(
                answers_question=None, mismatch_type=None, mismatch_detail=None, repair_hint=None,
                ambiguous=True, should_abstain=True, abstain_reason="max_tokens_reached", confidence=None,
                raw_output=json.dumps(raw_outputs, ensure_ascii=False), invalid_mismatch_type=None,
                error="LLM generation was truncated due to max token limit.", probes_used=probes_used,
                probe_trajectory=probe_trajectory, stage2_answers_question=None, stage2_ambiguous=None, stage3_ran=False,
            )

        except Exception as exc:
            return VerificationResult(
                answers_question=None, mismatch_type=None, mismatch_detail=None, repair_hint=None,
                ambiguous=True, should_abstain=True, abstain_reason="invalid_verifier_output", confidence=None,
                raw_output=json.dumps(raw_outputs, ensure_ascii=False), invalid_mismatch_type=None,
                error=str(exc), probes_used=probes_used, probe_trajectory=probe_trajectory,
                stage2_answers_question=None, stage2_ambiguous=None, stage3_ran=False,
            )

        if stage2_verification["answers_question"] is True:
            return VerificationResult(
                answers_question=True, mismatch_type=None, mismatch_detail=None, repair_hint=None,
                ambiguous=False, should_abstain=False, abstain_reason=None, confidence=stage2_verification.get("confidence"),
                raw_output=json.dumps(raw_outputs, ensure_ascii=False), invalid_mismatch_type=None, error=None,
                probes_used=probes_used, probe_trajectory=probe_trajectory, stage2_answers_question=True,
                stage2_ambiguous=False, stage3_ran=False, **_stage2_evidence_debug(stage2_verification),
            )

        if stage2_verification["answers_question"] is None or stage2_verification["ambiguous"] is True:
            abstain_reason = "marked_unclear"

            if stage2_verification.get("evidence_match") == "insufficient":
                abstain_reason = "invalid_mismatch_type"

            return VerificationResult(
                answers_question=None, mismatch_type=None, mismatch_detail=None, repair_hint=None,
                ambiguous=True, should_abstain=True, abstain_reason=abstain_reason,
                confidence=stage2_verification.get("confidence") or "low", raw_output=json.dumps(raw_outputs, ensure_ascii=False),
                invalid_mismatch_type=stage2_verification.get("invalid_mismatch_type"), error=None, probes_used=probes_used,
                probe_trajectory=probe_trajectory, stage2_answers_question=stage2_verification["answers_question"],
                stage2_ambiguous=stage2_verification["ambiguous"], stage3_ran=False, **_stage2_evidence_debug(stage2_verification),
            )

        try:
            stage3_prompt = build_stage3_repair_prompt(
                question=question,
                intent_representation=current_intent,
                execution_profile=execution_profile,
                stage2_verdict=stage2_verification,
            )

            raw_stage3 = self.llm_generate_fn(stage3_prompt)
            raw_outputs.append({"step": "stage3_repair", "raw": raw_stage3})
            
            stage3_parsed = parse_verifier_json(raw_stage3)
            stage3_repair = normalise_stage3_repair_output(stage3_parsed)

            return VerificationResult(
                answers_question=False, mismatch_type=stage2_verification["primary_mismatch_type"],
                mismatch_detail=stage2_verification["mismatch_detail"], repair_hint=stage3_repair["repair_hint"],
                ambiguous=False, should_abstain=False, abstain_reason=None, confidence=stage3_repair["confidence"],
                raw_output=json.dumps(raw_outputs, ensure_ascii=False), invalid_mismatch_type=None, error=None,
                probes_used=probes_used, probe_trajectory=probe_trajectory, stage2_answers_question=stage2_verification["answers_question"],
                stage2_ambiguous=stage2_verification["ambiguous"], stage3_ran=True, **_stage2_evidence_debug(stage2_verification),
            )

        except Exception as exc:
            return VerificationResult(
                answers_question=False, mismatch_type=stage2_verification["primary_mismatch_type"],
                mismatch_detail=stage2_verification["mismatch_detail"], repair_hint=None,
                ambiguous=False, should_abstain=False, abstain_reason=None, confidence=stage2_verification.get("confidence"),
                raw_output=json.dumps(raw_outputs, ensure_ascii=False), invalid_mismatch_type=None,
                error=f"Stage 3 repair-hint generation failed: {exc}", probes_used=probes_used,
                probe_trajectory=probe_trajectory, stage2_answers_question=stage2_verification["answers_question"],
                stage2_ambiguous=stage2_verification["ambiguous"], stage3_ran=True, **_stage2_evidence_debug(stage2_verification),
            )


def verify_execution_profile(
    question: str,
    execution_profile: str,
    llm_generate_fn: Callable[[str], str],
    intent_representation: dict[str, Any] | None = None,
    probing_mode: str = "hybrid",
    max_probes: int = 7,
    profile_mode: str = "compact",
) -> VerificationResult:
    """Run the multi-stage semantic verifier over an execution profile."""
    if intent_representation is None:
        intent_representation = {"question": question, "_warning": "Legacy fallback: No intent provided."}

    verifier = SemanticVerifier(
        llm_generate_fn=llm_generate_fn,
        probing_mode=probing_mode,
        max_probes=max_probes,
        profile_mode=profile_mode,
    )
    return verifier.verify(
        question=question,
        intent=intent_representation,
        execution_profile=execution_profile,
    )


def verify_decompiled_sql(
    question: str,
    execution_profile: str,
    llm_generate_fn: Callable[[str], str],
    **kwargs,
) -> VerificationResult:
    """Backward-compatible alias for older runner imports."""
    return verify_execution_profile(
        question=question,
        execution_profile=execution_profile,
        llm_generate_fn=llm_generate_fn,
        **kwargs,
    )
