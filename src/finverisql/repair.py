"""Semantic SQL repair generation for FinVeriSQL experiments."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Callable

from src.finverisql.verifier import MaxTokensReachedError, parse_verifier_json
from src.utils.inference_utils import extract_sql


VALID_CONFIDENCE_LEVELS = {"high", "medium", "low"}


@dataclass
class SemanticRepairRequest:
    question_id: str
    question: str
    generated_sql: str
    intent_representation: dict[str, Any]
    execution_profile: str | dict[str, Any]
    primary_mismatch_type: str
    mismatch_detail: str | None
    failed_evidence: list[str]
    repair_hint: str | None
    diagnostic_dimensions: dict[str, Any] | None
    confidence: str | None
    schema_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NonExecutableRepairRequest:
    question_id: str
    question: str
    generated_sql: str
    execution_error: str | None
    schema_text: str
    intent_representation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SemanticRepairResult:
    status: str
    repaired_sql: str | None
    edit_summary: str | None
    confidence: str | None
    raw_output: str | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalise_optional_str(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.lower() in {"", "null", "none", "n/a"}:
            return None
        return cleaned

    return str(value).strip() or None


def _normalise_confidence(value: Any) -> str | None:
    candidate = _normalise_optional_str(value)

    if candidate is None:
        return None

    candidate = candidate.lower()
    return candidate if candidate in VALID_CONFIDENCE_LEVELS else None


def _render_targeted_json(value: str | dict[str, Any]) -> str:
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)

    text = str(value or "").strip()

    if not text:
        return "{}"

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text

    return json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True)


def build_semantic_repair_prompt(request: SemanticRepairRequest) -> str:
    intent_json = json.dumps(
        request.intent_representation,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    diagnostic_json = json.dumps(
        request.diagnostic_dimensions or {},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    evidence_json = json.dumps(
        request.failed_evidence,
        ensure_ascii=False,
        indent=2,
    )
    execution_profile = _render_targeted_json(request.execution_profile)
    schema_text = _normalise_optional_str(request.schema_text) or "Not provided."

    return f"""
You are repairing a finance-related SQL query using trusted verifier mismatch evidence.

Your job:
- The repaired SQL must execute in SQLite.
- Use only tables and columns present in the provided schema metadata.
- Use SQLite-compatible date functions such as date(...) and strftime(...).
- Do not use unsupported SQL syntax such as EXTRACT(...), DATE_TRUNC, INTERVAL,
  ILIKE, BOOL_OR, BOOL_AND, or vendor-specific functions.
- Trust the provided mismatch diagnosis. Do not re-verify or reinterpret the mismatch type.
- Preserve all correct parts of the original SQL.
- Make the minimum semantic edits needed to fix the stated mismatch.
- Return exactly one repaired SQL candidate.
- Return a short edit summary describing only the semantic change you made.
- Do not mention gold SQL, evaluation history, or alternate candidates.

Return only valid JSON with exactly these fields:
{{
  "repaired_sql": "<single repaired SQL query>",
  "edit_summary": "<short edit summary>",
  "confidence": "high | medium | low"
}}
- `repaired_sql` must be a single JSON string. Escape any SQL newlines as `\\n`,
  or return the SQL on one line.
- Do not use Markdown fences.

Question ID:
{request.question_id}

Question:
{request.question}

Original SQL:
{request.generated_sql}

Structured intent:
{intent_json}

Execution profile:
{execution_profile}

Schema metadata:
{schema_text}

Primary mismatch type:
{request.primary_mismatch_type}

Mismatch detail:
{request.mismatch_detail or "null"}

Failed evidence:
{evidence_json}

Repair hint:
{request.repair_hint or "null"}

Diagnostic dimensions:
{diagnostic_json}

Verifier confidence:
{request.confidence or "null"}

Return only the JSON object.
""".strip()


def build_non_executable_repair_prompt(request: NonExecutableRepairRequest) -> str:
    intent_json = json.dumps(
        request.intent_representation or {},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    return f"""
You are repairing a finance-related SQL query that currently does not execute.

Your job:
- Fix the SQL so it becomes executable in SQLite.
- Use only tables and columns present in the provided schema.
- Use SQLite-compatible date functions such as date(...) and strftime(...).
- Do not use unsupported SQL syntax such as EXTRACT(...), DATE_TRUNC, INTERVAL,
  ILIKE, BOOL_OR, BOOL_AND, or vendor-specific functions.
- Preserve the original business intent from the question.
- Make the minimum changes needed to correct syntax, invalid functions, invalid date expressions, wrong column references, or other execution-breaking issues.
- Do not broaden or rewrite the query unless that is required to make it executable and aligned with the question.
- Return exactly one repaired SQL candidate.
- Return a short edit summary describing what was fixed.

Return only valid JSON with exactly these fields:
{{
  "repaired_sql": "<single repaired SQL query>",
  "edit_summary": "<short edit summary>",
  "confidence": "high | medium | low"
}}
- `repaired_sql` must be a single JSON string. Escape any SQL newlines as `\\n`,
  or return the SQL on one line.
- Do not use Markdown fences.

Question ID:
{request.question_id}

Question:
{request.question}

Original SQL:
{request.generated_sql}

Execution error:
{request.execution_error or "unknown"}

Structured intent:
{intent_json}

Schema:
{request.schema_text}

Return only the JSON object.
""".strip()


def _extract_outer_json_object(text: str) -> str | None:
    """Extract the first balanced JSON object candidate from model output."""
    start = text.find("{")

    if start == -1:
        return None

    in_string = False
    escaped = False
    depth = 0

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1

            if depth == 0:
                return text[start : index + 1]

    return None


def _escape_control_chars_in_json_strings(text: str) -> str:
    """Escape raw control characters that JSON forbids inside strings."""
    escaped_chars: list[str] = []
    in_string = False
    escaped = False

    for char in text:
        if in_string:
            if escaped:
                escaped_chars.append(char)
                escaped = False
                continue

            if char == "\\":
                escaped_chars.append(char)
                escaped = True
                continue

            if char == '"':
                escaped_chars.append(char)
                in_string = False
                continue

            if char == "\n":
                escaped_chars.append("\\n")
            elif char == "\r":
                escaped_chars.append("\\r")
            elif char == "\t":
                escaped_chars.append("\\t")
            elif ord(char) < 0x20:
                escaped_chars.append(f"\\u{ord(char):04x}")
            else:
                escaped_chars.append(char)
            continue

        escaped_chars.append(char)

        if char == '"':
            in_string = True

    return "".join(escaped_chars)


def _parse_repair_json(raw_output: str) -> dict[str, Any]:
    """Parse repair JSON, tolerating raw multiline strings in object fields."""
    try:
        return parse_verifier_json(raw_output)
    except Exception as original_exc:
        candidate = _extract_outer_json_object(raw_output)

        if candidate is None:
            raise original_exc

        sanitized = _escape_control_chars_in_json_strings(candidate)

        try:
            parsed = json.loads(sanitized)
        except json.JSONDecodeError:
            raise original_exc

        if not isinstance(parsed, dict):
            raise original_exc

        return parsed


def _normalise_repair_output(parsed: dict[str, Any], raw_output: str) -> SemanticRepairResult:
    repaired_sql = extract_sql(_normalise_optional_str(parsed.get("repaired_sql")))
    edit_summary = _normalise_optional_str(parsed.get("edit_summary"))
    confidence = _normalise_confidence(parsed.get("confidence")) or "medium"

    if not repaired_sql:
        return SemanticRepairResult(
            status="abstained",
            repaired_sql=None,
            edit_summary=edit_summary,
            confidence=confidence,
            raw_output=raw_output,
            error="Model did not return a repaired_sql field.",
        )

    return SemanticRepairResult(
        status="success",
        repaired_sql=repaired_sql,
        edit_summary=edit_summary,
        confidence=confidence,
        raw_output=raw_output,
        error=None,
    )


def repair_semantic_sql(
    request: SemanticRepairRequest,
    llm_generate_fn: Callable[[str], str],
) -> SemanticRepairResult:
    prompt = build_semantic_repair_prompt(request)

    try:
        raw_output = llm_generate_fn(prompt)
    except MaxTokensReachedError as exc:
        return SemanticRepairResult(
            status="failed",
            repaired_sql=None,
            edit_summary=None,
            confidence=None,
            raw_output=None,
            error=f"LLM generation reached max token limit: {exc}",
        )
    except Exception as exc:
        return SemanticRepairResult(
            status="failed",
            repaired_sql=None,
            edit_summary=None,
            confidence=None,
            raw_output=None,
            error=str(exc),
        )

    try:
        parsed = _parse_repair_json(raw_output)
    except Exception as exc:
        return SemanticRepairResult(
            status="failed",
            repaired_sql=None,
            edit_summary=None,
            confidence=None,
            raw_output=raw_output,
            error=f"Invalid repair JSON output: {exc}",
        )

    return _normalise_repair_output(parsed, raw_output)


def repair_non_executable_sql(
    request: NonExecutableRepairRequest,
    llm_generate_fn: Callable[[str], str],
) -> SemanticRepairResult:
    prompt = build_non_executable_repair_prompt(request)

    try:
        raw_output = llm_generate_fn(prompt)
    except MaxTokensReachedError as exc:
        return SemanticRepairResult(
            status="failed",
            repaired_sql=None,
            edit_summary=None,
            confidence=None,
            raw_output=None,
            error=f"LLM generation reached max token limit: {exc}",
        )
    except Exception as exc:
        return SemanticRepairResult(
            status="failed",
            repaired_sql=None,
            edit_summary=None,
            confidence=None,
            raw_output=None,
            error=str(exc),
        )

    try:
        parsed = _parse_repair_json(raw_output)
    except Exception as exc:
        return SemanticRepairResult(
            status="failed",
            repaired_sql=None,
            edit_summary=None,
            confidence=None,
            raw_output=raw_output,
            error=f"Invalid repair JSON output: {exc}",
        )

    return _normalise_repair_output(parsed, raw_output)
