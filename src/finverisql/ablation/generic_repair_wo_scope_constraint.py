"""Generic unconstrained semantic SQL repair for ablation experiments."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Callable

from src.finverisql.repair import SemanticRepairResult
from src.finverisql.verifier import MaxTokensReachedError, parse_verifier_json
from src.utils.inference_utils import extract_sql


VALID_CONFIDENCE_LEVELS = {"high", "medium", "low"}


@dataclass
class GenericSemanticRepairRequest:
    question_id: str
    question: str
    original_sql: str
    current_sql: str
    intent_representation: dict[str, Any]
    execution_profile: str | dict[str, Any]
    mismatch_type: str | None
    mismatch_detail: str | None
    failed_evidence: list[str]
    repair_hint: str | None
    diagnostic_dimensions: dict[str, Any] | None
    confidence: str | None
    schema_text: str | None = None

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


def _render_json_or_text(value: str | dict[str, Any]) -> str:
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


def build_generic_semantic_repair_prompt(
    request: GenericSemanticRepairRequest,
) -> str:
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
    execution_profile = _render_json_or_text(request.execution_profile)
    schema_text = _normalise_optional_str(request.schema_text) or "Not provided."

    return f"""
You are repairing a finance-related SQL query using trusted verifier mismatch evidence.

This is a generic semantic repair ablation. Repair the SQL regardless of which
semantic error class the verifier identified. Do not use dimension-specific
repair routing or clause-scope restrictions.

Your job:
- The repaired SQL must execute in SQLite.
- Use only tables and columns present in the provided schema metadata.
- Use SQLite-compatible date functions such as date(...) and strftime(...).
- Do not use unsupported SQL syntax such as EXTRACT(...), DATE_TRUNC, INTERVAL,
  ILIKE, BOOL_OR, BOOL_AND, or vendor-specific functions.
- Trust the provided mismatch diagnosis. Do not re-verify or reinterpret the mismatch type.
- Preserve correct parts of the current SQL when they do not conflict with the verifier evidence.
- Make the semantic edits needed to fix the stated mismatch.
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
{request.original_sql}

Current SQL:
{request.current_sql}

Structured intent:
{intent_json}

Current execution profile:
{execution_profile}

Schema metadata:
{schema_text}

Verifier mismatch type:
{request.mismatch_type or "null"}

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


def _extract_outer_json_object(text: str) -> str | None:
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


def _normalise_repair_output(
    parsed: dict[str, Any],
    raw_output: str,
) -> SemanticRepairResult:
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


def repair_generic_semantic_sql(
    request: GenericSemanticRepairRequest,
    llm_generate_fn: Callable[[str], str],
) -> SemanticRepairResult:
    prompt = build_generic_semantic_repair_prompt(request)

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
