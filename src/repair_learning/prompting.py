"""Prompt and output helpers for learned SQL repair baselines."""

from __future__ import annotations

import json
from typing import Any

from src.finverisql.repair import (
    NonExecutableRepairRequest,
    SemanticRepairRequest,
    build_non_executable_repair_prompt,
    build_semantic_repair_prompt,
)
from src.finverisql.repair_runner import (
    REPAIR_SCOPE_POLICIES,
    build_non_executable_repair_request,
    build_semantic_repair_request,
    classify_candidate_row,
    route_mismatch_to_repair_mode,
)
from src.utils.inference_utils import extract_sql


def repair_completion_json(
    repaired_sql: str,
    edit_summary: str = "Replaced the generated SQL with the corrected SQL.",
    confidence: str = "high",
) -> str:
    """Return the JSON repair response used as the SFT/RL target."""

    return json.dumps(
        {
            "repaired_sql": repaired_sql,
            "edit_summary": edit_summary,
            "confidence": confidence,
        },
        ensure_ascii=False,
    )


def build_specialized_semantic_prompt(
    row: dict[str, Any],
    schema_text: str | None,
) -> tuple[str, str]:
    """Build the same first-attempt specialized repair prompt as FinVeriSQL."""

    verification = row.get("verification") if isinstance(row.get("verification"), dict) else {}
    repair_mode, stop_reason = route_mismatch_to_repair_mode(
        verification.get("mismatch_type"),
        attempted_error_classes=set(),
    )
    if repair_mode is None:
        raise ValueError(stop_reason or "unsupported_mismatch_type")

    policy = REPAIR_SCOPE_POLICIES[repair_mode]
    base_request = build_semantic_repair_request(row=row, schema_text=schema_text)
    request = SemanticRepairRequest(
        **{
            **base_request.to_dict(),
            "repair_mode": repair_mode,
            "current_sql": str(row.get("generated_sql") or ""),
            "original_sql": str(row.get("generated_sql") or ""),
            "allowed_clause_changes": list(policy.allowed_clause_changes),
            "disallowed_clause_changes": list(policy.disallowed_clause_changes),
        }
    )
    return build_semantic_repair_prompt(request), repair_mode


def build_prompt_for_candidate(
    row: dict[str, Any],
    schema_text: str | None,
) -> tuple[str, str]:
    """Build repair prompt text for a fixed-verifier candidate row."""

    is_candidate, repair_kind, skip_reason = classify_candidate_row(row)
    if not is_candidate:
        raise ValueError(skip_reason or "row is not repairable")

    if repair_kind == "semantic":
        return build_specialized_semantic_prompt(row=row, schema_text=schema_text)

    if repair_kind == "non_executable":
        request: NonExecutableRepairRequest = build_non_executable_repair_request(
            row=row,
            schema_text=schema_text or "",
            intent_representation=(
                row.get("intent_representation")
                if isinstance(row.get("intent_representation"), dict)
                else None
            ),
        )
        return build_non_executable_repair_prompt(request), "non_executable"

    raise ValueError(f"Unsupported repair kind: {repair_kind}")


def parse_repaired_sql_from_text(text: str) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """Parse model repair output and return `(sql, parsed_json, error)`."""

    raw = str(text or "").strip()
    if not raw:
        return None, None, "empty model output"

    parsed: dict[str, Any] | None = None
    try:
        candidate = raw
        if not candidate.startswith("{"):
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start != -1 and end != -1 and end > start:
                candidate = candidate[start : end + 1]
        loaded = json.loads(candidate)
        if isinstance(loaded, dict):
            parsed = loaded
            sql = extract_sql(str(loaded.get("repaired_sql") or ""))
            return (sql or None), parsed, None if sql else "missing repaired_sql"
    except Exception:
        pass

    sql = extract_sql(raw)
    return (sql or None), parsed, None if sql else "could not parse repaired SQL"

