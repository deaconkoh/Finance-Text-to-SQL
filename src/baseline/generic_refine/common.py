"""Internal helpers for generic baseline SQL refinement modes."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from src.utils.inference_utils import extract_sql


GROUP_A = "A_correct_executable"
GROUP_B = "B_wrong_executable"
GROUP_C = "C_non_executable"
REFINABLE_GROUPS = {GROUP_A, GROUP_B, GROUP_C}
VALID_CONFIDENCE_LEVELS = {"high", "medium", "low"}
DEFAULT_SCHEMA_PATH = "data/booksql/schema.txt"
DEFAULT_MODEL_NAME = "mlx-community/Llama-3.1-8B-Instruct-4bit"
DEFAULT_BACKEND = "mlx-lm"


@dataclass(frozen=True)
class GenericRefineRequest:
    question_id: str
    question: str
    schema_text: str
    candidate_sql: str
    execution_feedback: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GenericRefineResult:
    status: str
    repaired_sql: str | None
    revised_sql: str | None
    changed: bool | None
    refine_decision: str
    edit_summary: str | None
    confidence: str | None
    raw_output: str | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalise_optional_str(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.lower() in {"", "null", "none", "n/a"}:
            return None
        return cleaned

    return str(value).strip() or None


def normalise_confidence(value: Any) -> str | None:
    candidate = normalise_optional_str(value)
    if candidate is None:
        return None

    candidate = candidate.lower()
    return candidate if candidate in VALID_CONFIDENCE_LEVELS else None


def normalise_sql_for_compare(sql: Any) -> str:
    text = str(sql or "").strip().rstrip(";")
    return " ".join(text.split()).lower()


def stable_hash(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def stable_context_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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


def load_schema_text(schema_path: str | Path) -> str:
    return Path(schema_path).read_text(encoding="utf-8").strip()


def load_completed_keys(output_path: str | Path) -> set[tuple[str, str, str, str, str]]:
    path = Path(output_path)
    if not path.exists():
        return set()

    completed: set[tuple[str, str, str, str, str]] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except Exception:
                continue

            completed.add(
                (
                    str(row.get("question_id") or ""),
                    str(row.get("original_generated_sql_hash") or ""),
                    str(row.get("repair_mode") or ""),
                    str(row.get("model_metadata", {}).get("model_name") or ""),
                    str(row.get("refine_context_hash") or ""),
                )
            )

    return completed


def get_refine_run_key(
    row: dict[str, Any],
    repair_mode: str,
    model_name: str,
    context_hash: str,
) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("question_id") or row.get("id") or ""),
        stable_hash(row.get("generated_sql") or row.get("pred_sql")),
        repair_mode,
        model_name,
        context_hash,
    )


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


def parse_refine_json(raw_output: str) -> dict[str, Any]:
    text = raw_output.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as original_exc:
        candidate = _extract_outer_json_object(text)
        if candidate is None:
            raise original_exc
        parsed = json.loads(_escape_control_chars_in_json_strings(candidate))

    if not isinstance(parsed, dict):
        raise ValueError("Model output JSON must be an object.")

    return parsed


def parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False

    return None


def normalise_refine_output(
    parsed: dict[str, Any],
    raw_output: str,
    original_sql: str,
) -> GenericRefineResult:
    changed = parse_bool(parsed.get("changed"))
    revised_sql = extract_sql(normalise_optional_str(parsed.get("revised_sql")))
    edit_summary = normalise_optional_str(parsed.get("edit_summary"))
    confidence = normalise_confidence(parsed.get("confidence")) or "medium"

    if changed is False:
        return GenericRefineResult(
            status="success",
            repaired_sql=None,
            revised_sql=revised_sql,
            changed=False,
            refine_decision="no_change",
            edit_summary=edit_summary,
            confidence=confidence,
            raw_output=raw_output,
            error=None,
        )

    if not revised_sql:
        return GenericRefineResult(
            status="success",
            repaired_sql=None,
            revised_sql=None,
            changed=changed,
            refine_decision="no_change",
            edit_summary=edit_summary,
            confidence=confidence,
            raw_output=raw_output,
            error="Model did not return a non-empty revised_sql field.",
        )

    if normalise_sql_for_compare(revised_sql) == normalise_sql_for_compare(original_sql):
        return GenericRefineResult(
            status="success",
            repaired_sql=None,
            revised_sql=revised_sql,
            changed=changed,
            refine_decision="no_change",
            edit_summary=edit_summary,
            confidence=confidence,
            raw_output=raw_output,
            error=None,
        )

    return GenericRefineResult(
        status="success",
        repaired_sql=revised_sql,
        revised_sql=revised_sql,
        changed=True if changed is None else changed,
        refine_decision="changed",
        edit_summary=edit_summary,
        confidence=confidence,
        raw_output=raw_output,
        error=None,
    )


def run_refine_request(
    request: GenericRefineRequest,
    llm_generate_fn: Callable[[str], str],
    prompt_builder: Callable[[GenericRefineRequest], str],
) -> GenericRefineResult:
    prompt = prompt_builder(request)

    try:
        raw_output = llm_generate_fn(prompt)
    except Exception as exc:
        return GenericRefineResult(
            status="failed",
            repaired_sql=None,
            revised_sql=None,
            changed=None,
            refine_decision="failed",
            edit_summary=None,
            confidence=None,
            raw_output=None,
            error=str(exc),
        )

    try:
        parsed = parse_refine_json(raw_output)
    except Exception as exc:
        return GenericRefineResult(
            status="failed",
            repaired_sql=None,
            revised_sql=None,
            changed=None,
            refine_decision="failed",
            edit_summary=None,
            confidence=None,
            raw_output=raw_output,
            error=f"Invalid refine JSON output: {exc}",
        )

    return normalise_refine_output(
        parsed=parsed,
        raw_output=raw_output,
        original_sql=request.candidate_sql,
    )


def build_base_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--input-path", required=True, help="Evaluated baseline JSONL input.")
    parser.add_argument("--output-path", required=True, help="Append/resume JSONL output.")
    parser.add_argument("--schema-path", default=DEFAULT_SCHEMA_PATH, help="Plain BookSQL schema text path.")
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME, help="Model used for generic refinement.")
    parser.add_argument("--backend", choices=["ollama", "mlx-lm", "mlx-vlm"], default=DEFAULT_BACKEND)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--num-predict", type=int, default=768)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true", help="Disable resume skipping.")
    return parser


def build_refine_request(
    row: dict[str, Any],
    default_schema_text: str,
    execution_feedback: dict[str, Any] | None,
) -> GenericRefineRequest:
    return GenericRefineRequest(
        question_id=str(row.get("question_id") or row.get("id") or ""),
        question=str(row.get("question") or ""),
        schema_text=normalise_optional_str(row.get("schema")) or default_schema_text,
        candidate_sql=str(row.get("generated_sql") or row.get("pred_sql") or ""),
        execution_feedback=execution_feedback,
    )


def build_output_row(
    source_row: dict[str, Any],
    request: GenericRefineRequest | None,
    result: GenericRefineResult | None,
    repair_mode: str,
    model_metadata: dict[str, Any],
    context_hash: str,
    status: str,
    skip_reason: str | None = None,
) -> dict[str, Any]:
    original_sql = source_row.get("generated_sql") or source_row.get("pred_sql")
    repaired_sql = result.repaired_sql if result else None
    final_sql_source = repair_mode if repaired_sql else "original_generated_sql"

    return {
        "question_id": source_row.get("question_id") or source_row.get("id"),
        "db_id": source_row.get("db_id"),
        "split": source_row.get("split"),
        "level": source_row.get("level"),
        "generator": source_row.get("generator") or source_row.get("model_key"),
        "prompt_setting": source_row.get("prompt_setting"),
        "evaluation_group": source_row.get("evaluation_group"),
        "question": source_row.get("question"),
        "gold_sql": source_row.get("gold_sql"),
        "original_generated_sql": original_sql,
        "original_generated_sql_hash": stable_hash(original_sql),
        "generated_execution_status": source_row.get("generated_execution_status"),
        "generated_error": source_row.get("generated_error"),
        "generated_result": source_row.get("generated_result"),
        "ambiguity_flags": source_row.get("ambiguity_flags"),
        "repair_mode": repair_mode,
        "repair_status": result.status if result else status,
        "status": status,
        "error": (result.error if result and result.status == "failed" else None),
        "skip_reason": skip_reason,
        "repaired_sql": repaired_sql,
        "repaired_sql_hash": stable_hash(repaired_sql),
        "final_sql_source": final_sql_source,
        "raw_refine_output": result.raw_output if result else None,
        "refine_result": result.to_dict() if result else None,
        "refine_decision": result.refine_decision if result else None,
        "refine_request": request.to_dict() if request else None,
        "model_metadata": model_metadata,
        "refine_context_hash": context_hash,
    }


def run_refine_jsonl(
    args: argparse.Namespace,
    repair_mode: str,
    prompt_builder: Callable[[GenericRefineRequest], str],
    execution_feedback_builder: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> None:
    try:
        from src.utils.inference_utils import build_verifier_generate_fn
    except ModuleNotFoundError:
        from utils.inference_utils import build_verifier_generate_fn

    rows = read_jsonl(args.input_path)
    if args.limit is not None:
        rows = rows[: args.limit]

    default_schema_text = load_schema_text(args.schema_path)
    context_hash = stable_context_hash(
        {
            "repair_mode": repair_mode,
            "schema_text": default_schema_text,
            "model_name": args.model_name,
            "backend": args.backend,
            "prompt_version": "generic_baseline_refine_v1",
        }
    )
    model_metadata = {
        "model_name": args.model_name,
        "backend": args.backend,
        "temperature": args.temperature,
        "num_predict": args.num_predict,
        "refine_mode": repair_mode,
    }
    completed_keys = load_completed_keys(args.output_path)
    generate_fn = build_verifier_generate_fn(
        model_name=args.model_name,
        backend=args.backend,
        temperature=args.temperature,
        num_predict=args.num_predict,
        timeout=args.timeout,
    )

    counts = {"attempted": 0, "skipped": 0, "changed": 0, "failed": 0}

    for row in rows:
        run_key = get_refine_run_key(
            row=row,
            repair_mode=repair_mode,
            model_name=args.model_name,
            context_hash=context_hash,
        )
        if not args.overwrite and run_key in completed_keys:
            continue

        skip_reason = None
        if row.get("evaluation_group") not in REFINABLE_GROUPS:
            skip_reason = "unsupported_evaluation_group"
        elif not normalise_optional_str(row.get("question")):
            skip_reason = "missing_question"
        elif not normalise_optional_str(row.get("generated_sql") or row.get("pred_sql")):
            skip_reason = "missing_generated_sql"

        if skip_reason:
            counts["skipped"] += 1
            append_jsonl(
                args.output_path,
                build_output_row(
                    source_row=row,
                    request=None,
                    result=None,
                    repair_mode=repair_mode,
                    model_metadata=model_metadata,
                    context_hash=context_hash,
                    status="skipped",
                    skip_reason=skip_reason,
                ),
            )
            continue

        request = build_refine_request(
            row=row,
            default_schema_text=default_schema_text,
            execution_feedback=execution_feedback_builder(row),
        )
        result = run_refine_request(
            request=request,
            llm_generate_fn=generate_fn,
            prompt_builder=prompt_builder,
        )
        counts["attempted"] += 1
        if result.status == "failed":
            counts["failed"] += 1
        if result.repaired_sql:
            counts["changed"] += 1

        append_jsonl(
            args.output_path,
            build_output_row(
                source_row=row,
                request=request,
                result=result,
                repair_mode=repair_mode,
                model_metadata=model_metadata,
                context_hash=context_hash,
                status="success",
                skip_reason=None,
            ),
        )

    print(f"Saved generic refinement outputs to: {args.output_path}")
    print(
        "Refine summary: "
        f"attempted={counts['attempted']}, "
        f"changed={counts['changed']}, "
        f"failed={counts['failed']}, "
        f"skipped={counts['skipped']}"
    )
