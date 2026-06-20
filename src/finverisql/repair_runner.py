"""Orchestration helpers for additive FinVeriSQL repair experiments."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.finverisql.compact_semantic_profile import build_verifier_payload
from src.finverisql.repair import (
    NonExecutableRepairRequest,
    SemanticRepairRequest,
    SemanticRepairResult,
)
from src.finverisql.schema_loader import SchemaAnnotationStore
from src.finverisql.sql_parser import parse_sql
from src.finverisql.sql_semantic_mapping import build_sql_financial_semantics


GROUP_B = "B_wrong_executable"
GROUP_C = "C_non_executable"
GROUP_A = "A_correct_executable"


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    path = Path(path)

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()

            if not text:
                continue

            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSONL at {path}, line {line_number}: {exc}"
                ) from exc

            if not isinstance(row, dict):
                raise ValueError(
                    f"Expected JSON object at {path}, line {line_number}, "
                    f"got {type(row).__name__}."
                )

            rows.append(row)

    return rows


def append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def stable_sql_hash(sql: Any) -> str:
    return hashlib.sha256(str(sql or "").encode("utf-8")).hexdigest()[:16]


def to_jsonable(obj: Any) -> Any:
    if obj is None:
        return None

    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return to_jsonable(obj.to_dict())

    if isinstance(obj, dict):
        return {str(key): to_jsonable(value) for key, value in obj.items()}

    if isinstance(obj, list):
        return [to_jsonable(value) for value in obj]

    if isinstance(obj, tuple):
        return [to_jsonable(value) for value in obj]

    if isinstance(obj, (str, int, float, bool)):
        return obj

    return str(obj)


def render_json_profile(profile: dict[str, Any]) -> str:
    return json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True)


def build_execution_profile(
    generated_sql: str,
    schema_store: SchemaAnnotationStore,
    profile_mode: str,
) -> str:
    try:
        parsed_sql = parse_sql(generated_sql)
        parsed_dict = to_jsonable(parsed_sql)

        if profile_mode == "ast":
            return render_json_profile(
                {
                    "status": "OK",
                    "profile_type": "parsed_ast",
                    "parsed_sql": parsed_dict,
                }
            )

        semantics = build_sql_financial_semantics(
            parsed_sql=parsed_sql,
            schema_store=schema_store,
        )
        semantic_dict = to_jsonable(semantics)

        if profile_mode == "semantic":
            return render_json_profile(
                {
                    "status": "OK",
                    "profile_type": "semantic_profile",
                    **semantic_dict,
                }
            )

        if profile_mode == "compact":
            compact_payload = build_verifier_payload(semantics)
            return render_json_profile(
                {
                    "status": "OK",
                    "profile_type": "compact_semantic_profile",
                    **compact_payload,
                }
            )

        raise ValueError(f"Unsupported profile_mode: {profile_mode}")

    except Exception as exc:
        return render_json_profile(
            {
                "status": "PARSE_ERROR",
                "profile_type": profile_mode,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "warnings": [
                    "SQL parse/profile pipeline failed before verification: "
                    f"{type(exc).__name__}: {exc}"
                ],
            }
        )


def detect_profile_status(execution_profile: str) -> str | None:
    try:
        parsed = json.loads(execution_profile)
    except Exception:
        return None

    if not isinstance(parsed, dict):
        return None

    extraction = parsed.get("profile_extraction") or {}
    return parsed.get("status") or extraction.get("status")


def classify_candidate_row(row: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    group = row.get("evaluation_group")

    if group in {GROUP_A, GROUP_B}:
        return _classify_semantic_candidate(row)

    if group == GROUP_C:
        return _classify_group_c_candidate(row)

    return False, None, "unsupported_evaluation_group"


def _classify_semantic_candidate(row: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    if row.get("status") not in {None, "success"}:
        return False, None, "upstream_verification_failed"

    verification = row.get("verification")

    if not isinstance(verification, dict):
        return False, None, "missing_verification"

    if verification.get("answers_question") is not False:
        return False, None, "verification_not_rejected"

    if verification.get("should_abstain") is True:
        return False, None, "verification_abstained"

    if not verification.get("mismatch_type"):
        return False, None, "missing_mismatch_type"

    failed_evidence = verification.get("stage2_failed_evidence")
    if not isinstance(failed_evidence, list) or not failed_evidence:
        return False, None, "missing_failed_evidence"

    if not str(verification.get("repair_hint") or "").strip():
        return False, None, "missing_repair_hint"

    if not isinstance(row.get("intent_representation"), dict):
        return False, None, "missing_intent_representation"

    if not row.get("execution_profile"):
        return False, None, "missing_execution_profile"

    return True, "semantic", None


def _classify_group_c_candidate(row: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    generated_sql = str(row.get("generated_sql") or "").strip()
    question = str(row.get("question") or "").strip()

    if not generated_sql:
        return False, None, "missing_generated_sql"

    if not question:
        return False, None, "missing_question"

    return True, "non_executable", None


def build_semantic_repair_request(row: dict[str, Any]) -> SemanticRepairRequest:
    verification = row.get("verification") or {}

    return SemanticRepairRequest(
        question_id=str(row.get("question_id") or row.get("id") or ""),
        question=str(row.get("question") or ""),
        generated_sql=str(row.get("generated_sql") or ""),
        intent_representation=row.get("intent_representation") or {},
        execution_profile=row.get("execution_profile") or {},
        primary_mismatch_type=str(verification.get("mismatch_type") or ""),
        mismatch_detail=verification.get("mismatch_detail"),
        failed_evidence=verification.get("stage2_failed_evidence") or [],
        repair_hint=verification.get("repair_hint"),
        diagnostic_dimensions=verification.get("stage2_diagnostic_dimensions"),
        confidence=verification.get("confidence"),
    )


def build_non_executable_repair_request(
    row: dict[str, Any],
    schema_text: str,
    intent_representation: dict[str, Any] | None,
) -> NonExecutableRepairRequest:
    return NonExecutableRepairRequest(
        question_id=str(row.get("question_id") or row.get("id") or ""),
        question=str(row.get("question") or ""),
        generated_sql=str(row.get("generated_sql") or ""),
        execution_error=_extract_execution_error(row),
        schema_text=schema_text,
        intent_representation=intent_representation,
    )


def _extract_execution_error(row: dict[str, Any]) -> str | None:
    for key in ("generated_error", "error_message", "error", "route_reason"):
        value = row.get(key)
        if value:
            return str(value)
    return None


def stable_context_hash(*values: Any) -> str:
    payload = json.dumps(
        [to_jsonable(value) for value in values],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_completed_keys(output_path: str | Path) -> set[tuple[str, str, str, str, str, str]]:
    path = Path(output_path)

    if not path.exists():
        return set()

    completed: set[tuple[str, str, str, str, str, str]] = set()

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except Exception:
                continue

            completed.add(
                (
                    str(row.get("question_id") or row.get("id") or ""),
                    str(row.get("original_generated_sql_hash") or ""),
                    str(row.get("repair_mode") or ""),
                    str(row.get("repair_model") or ""),
                    str(row.get("intent_mode") or ""),
                    str(row.get("repair_context_hash") or ""),
                )
            )

    return completed


def get_repair_run_key(
    row: dict[str, Any],
    repair_mode: str,
    repair_model: str,
    intent_mode: str,
    repair_context_hash: str,
) -> tuple[str, str, str, str, str, str]:
    return (
        str(row.get("question_id") or row.get("id") or ""),
        stable_sql_hash(row.get("generated_sql")),
        repair_mode,
        repair_model,
        intent_mode,
        repair_context_hash,
    )


def build_attempt_output_row(
    source_row: dict[str, Any],
    repair_request: SemanticRepairRequest | NonExecutableRepairRequest | None,
    repair_result: SemanticRepairResult | None,
    intent_representation_used: dict[str, Any] | None,
    repair_mode: str | None,
    status: str,
    skip_reason: str | None,
    repair_model: str,
    intent_mode: str,
    repair_context_hash: str,
) -> dict[str, Any]:
    repaired_sql = repair_result.repaired_sql if repair_result else None

    return {
        "question_id": source_row.get("question_id") or source_row.get("id"),
        "db_id": source_row.get("db_id"),
        "split": source_row.get("split"),
        "level": source_row.get("level"),
        "generator": source_row.get("generator") or source_row.get("model") or source_row.get("model_key"),
        "prompt_setting": source_row.get("prompt_setting"),
        "evaluation_group": source_row.get("evaluation_group"),
        "question": source_row.get("question"),
        "gold_sql": source_row.get("gold_sql"),
        "original_generated_sql": source_row.get("generated_sql"),
        "original_generated_sql_hash": stable_sql_hash(source_row.get("generated_sql")),
        "original_execution_profile": source_row.get("execution_profile"),
        "original_verification": source_row.get("verification"),
        "intent_representation_used": intent_representation_used,
        "repair_mode": repair_mode,
        "repair_request": repair_request.to_dict() if repair_request else None,
        "repair_result": repair_result.to_dict() if repair_result else None,
        "repair_status": repair_result.status if repair_result else status,
        "repair_error": repair_result.error if repair_result else None,
        "repair_model": repair_model,
        "intent_mode": intent_mode,
        "repair_context_hash": repair_context_hash,
        "repaired_sql": repaired_sql,
        "repaired_sql_hash": stable_sql_hash(repaired_sql),
        "status": status,
        "skip_reason": skip_reason,
    }
