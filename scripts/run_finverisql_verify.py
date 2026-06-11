#!/usr/bin/env python3
"""
Run FinVeriSQL verifier ablations over generated Text-to-SQL outputs.

This script supports three verifier input profile modes:

1. ast
   Uses only the parsed SQL AST.
   Pipeline:
       generated SQL
       -> SQL parser
       -> parsed SQL AST
       -> verifier

   Purpose:
       Tests whether SQL syntax/structure alone is enough for verification.

2. semantic
   Uses the full schema-grounded semantic profile.
   Pipeline:
       generated SQL
       -> SQL parser
       -> schema-grounded semantic mapper
       -> full semantic profile
       -> verifier

   Purpose:
       Tests whether detailed schema-grounded mapping improves verification.

3. compact
   Uses the compact verifier payload derived from the semantic profile.
   Pipeline:
       generated SQL
       -> SQL parser
       -> schema-grounded semantic mapper
       -> compact semantic profile / verifier payload
       -> verifier

   Purpose:
       Tests whether a compact profile preserves useful semantic evidence while
       reducing redundant/debug-heavy context.

Recommended ablation commands from project root:

AST-only ablation:
    python scripts/run_finverisql_verify.py \
      --input-path data/outputs/baseline/baseline_qwen_train_sample_50_few_shot.jsonl \
      --output-path data/outputs/verify/verify_ast.jsonl \
      --schema-path data/booksql/schema_annotations.json \
      --profile-mode ast \
      --limit 50

Full semantic profile ablation:
    python scripts/run_finverisql_verify.py \
      --input-path data/outputs/baseline/baseline_qwen_train_sample_50_few_shot.jsonl \
      --output-path data/outputs/verify/verify_semantic.jsonl \
      --schema-path data/booksql/schema_annotations.json \
      --profile-mode semantic \
      --limit 50

Compact semantic profile ablation:
    python scripts/run_finverisql_verify.py \
      --input-path data/outputs/baseline/baseline_qwen_train_sample_50_few_shot.jsonl \
      --output-path data/outputs/verify/verify_compact.jsonl \
      --schema-path data/booksql/schema_annotations.json \
      --profile-mode compact \
      --limit 50

Notes:
- Use separate output files for each ablation mode for cleaner analysis.
- This script is non-destructive. It appends to output JSONL files.
- By default, completed rows are skipped using a profile-aware run key.
- Passing --overwrite does not delete the old output file. It only disables skipping,
  so duplicate rows may be appended.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from tqdm import tqdm


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


try:
    from src.finverisql.schema_loader import SchemaAnnotationStore
    from src.finverisql.sql_parser import parse_sql
    from src.finverisql.sql_semantic_mapping import build_sql_financial_semantics
    from src.finverisql.compact_semantic_profile import build_verifier_payload
    from src.finverisql.verifier import verify_decompiled_sql
    from src.utils.inference_utils import build_verifier_generate_fn

except ModuleNotFoundError:
    from finverisql.schema_loader import SchemaAnnotationStore
    from finverisql.sql_parser import parse_sql
    from finverisql.sql_semantic_mapping import build_sql_financial_semantics
    from finverisql.compact_semantic_profile import build_verifier_payload
    from finverisql.verifier import verify_decompiled_sql
    from utils.inference_utils import build_verifier_generate_fn


DEFAULT_SCHEMA_PATH = "data/booksql/schema_annotations.json"
DEFAULT_MODEL_NAME = "mlx-community/Llama-3.1-8B-Instruct-4bit"


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    rows: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSONL at {path}, line {line_number}: {exc}"
                ) from exc

    return rows


def append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


def to_jsonable(obj: Any) -> Any:
    """
    Convert dataclass-like outputs to JSON-serialisable objects.
    """
    if obj is None:
        return None

    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return to_jsonable(obj.to_dict())

    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}

    if isinstance(obj, list):
        return [to_jsonable(v) for v in obj]

    if isinstance(obj, tuple):
        return [to_jsonable(v) for v in obj]

    if isinstance(obj, (str, int, float, bool)):
        return obj

    return str(obj)


def render_json_profile(profile: dict[str, Any]) -> str:
    """
    Render execution profile as stable JSON for the verifier prompt.
    """
    return json.dumps(
        profile,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def stable_sql_hash(sql: Any) -> str:
    """
    Stable short hash for generated SQL.

    This prevents resume collisions when multiple generated SQLs share the same
    question_id, generator, and prompt setting.
    """
    text = str(sql or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def get_evaluation_group(row: dict[str, Any]) -> str | None:
    return (
        row.get("evaluation_group")
        or row.get("eval_group")
        or row.get("group")
    )


def get_question(row: dict[str, Any], question_key: str) -> str:
    value = row.get(question_key)

    if value is None:
        raise KeyError(f"Question key '{question_key}' not found in row.")

    return str(value)


def get_generated_sql(row: dict[str, Any], sql_key: str) -> str:
    value = row.get(sql_key)

    if value is None:
        raise KeyError(f"Generated SQL key '{sql_key}' not found in row.")

    return str(value)


def get_run_key(
    row: dict[str, Any],
    verifier_model: str,
    question_key: str,
    sql_key: str,
    profile_mode: str,
) -> tuple[str, str | None, str | None, str | None, str, str, str]:
    question_id = str(
        row.get("question_id")
        or row.get("id")
        or row.get(question_key)
    )

    generated_sql = row.get(sql_key)

    return (
        question_id,
        row.get("generator") or row.get("model") or row.get("model_key"),
        row.get("prompt_setting"),
        get_evaluation_group(row),
        verifier_model,
        profile_mode,
        stable_sql_hash(generated_sql),
    )


def load_completed_keys(
    output_path: str | Path,
) -> set[tuple[str, str | None, str | None, str | None, str, str, str]]:
    output_path = Path(output_path)

    if not output_path.exists():
        return set()

    completed: set[
        tuple[str, str | None, str | None, str | None, str, str, str]
    ] = set()

    with output_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)

                completed.add(
                    (
                        str(row.get("question_id") or row.get("id")),
                        row.get("generator") or row.get("model") or row.get("model_key"),
                        row.get("prompt_setting"),
                        row.get("evaluation_group"),
                        row.get("verifier_model"),
                        row.get("profile_format") or "unknown",
                        row.get("generated_sql_hash")
                        or stable_sql_hash(row.get("generated_sql")),
                    )
                )
            except Exception:
                continue

    return completed


def build_execution_profile(
    generated_sql: str,
    schema_store: SchemaAnnotationStore,
    profile_mode: str,
) -> str:
    """
    Build the verifier-facing execution profile according to the ablation mode.

    profile_mode options:
    - ast: parsed SQL AST only
    - semantic: full schema-grounded semantic profile
    - compact: compact verifier payload derived from semantic profile
    """
    try:
        parsed_sql = parse_sql(generated_sql)
        parsed_dict = to_jsonable(parsed_sql)

        if profile_mode == "ast":
            execution_profile = {
                "status": "OK",
                "profile_type": "parsed_ast",
                "parsed_sql": parsed_dict,
            }

            return render_json_profile(execution_profile)

        semantics = build_sql_financial_semantics(
            parsed_sql=parsed_sql,
            schema_store=schema_store,
        )
        semantic_dict = to_jsonable(semantics)

        if profile_mode == "semantic":
            execution_profile = {
                "status": "OK",
                "profile_type": "semantic_profile",
                **semantic_dict,
            }

            return render_json_profile(execution_profile)

        if profile_mode == "compact":
            compact_payload = build_verifier_payload(semantics)

            execution_profile = {
                "status": "OK",
                "profile_type": "compact_semantic_profile",
                **compact_payload,
            }

            return render_json_profile(execution_profile)

        raise ValueError(f"Unsupported profile_mode: {profile_mode}")

    except Exception as exc:
        error_profile = {
            "status": "PARSE_ERROR",
            "profile_type": profile_mode,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "warnings": [
                "SQL parse/profile pipeline failed before verification: "
                f"{type(exc).__name__}: {exc}"
            ],
        }

        return render_json_profile(error_profile)


def detect_profile_status(execution_profile: str) -> str | None:
    """
    Detect profile extraction status from JSON execution profiles.
    """
    try:
        parsed = json.loads(execution_profile)

        if isinstance(parsed, dict):
            profile_extraction = parsed.get("profile_extraction") or {}

            return (
                parsed.get("status")
                or profile_extraction.get("status")
            )

    except Exception:
        pass

    for line in execution_profile.splitlines():
        if line.startswith("[Status]"):
            return line.replace("[Status]", "").strip()

    return None


def make_output_row(
    source_row: dict[str, Any],
    question: str,
    generated_sql: str,
    execution_profile: str,
    verification_result: dict[str, Any],
    verifier_model: str,
    profile_format: str,
) -> dict[str, Any]:
    return {
        "question_id": source_row.get("question_id") or source_row.get("id"),
        "db_id": source_row.get("db_id"),
        "split": source_row.get("split"),
        "level": source_row.get("level"),
        "generator": source_row.get("generator") or source_row.get("model") or source_row.get("model_key"),
        "prompt_setting": source_row.get("prompt_setting"),
        "evaluation_group": get_evaluation_group(source_row),
        "question": question,
        "gold_sql": source_row.get("gold_sql"),
        "generated_sql": generated_sql,
        "generated_sql_hash": stable_sql_hash(generated_sql),
        "execution_profile": execution_profile,
        "profile_status": detect_profile_status(execution_profile),
        "verifier_model": verifier_model,
        "verification": verification_result,
        "profile_format": profile_format,
    }


def run_verification(args: argparse.Namespace) -> None:
    input_path = Path(args.input_path)
    output_path = Path(args.output_path)

    rows = read_jsonl(input_path)

    if args.evaluation_group:
        rows = [
            row for row in rows
            if get_evaluation_group(row) == args.evaluation_group
        ]

    if args.limit is not None:
        rows = rows[: args.limit]

    completed_keys = load_completed_keys(output_path)

    pending_rows: list[dict[str, Any]] = []

    for row in rows:
        run_key = get_run_key(
            row=row,
            verifier_model=args.model_name,
            question_key=args.question_key,
            sql_key=args.sql_key,
            profile_mode=args.profile_mode,
        )

        if not args.overwrite and run_key in completed_keys:
            continue

        pending_rows.append(row)

    print(f"Input rows selected: {len(rows)}")
    print(f"Already completed in output file: {len(rows) - len(pending_rows)}")
    print(f"Pending rows: {len(pending_rows)}")
    print(f"Verifier backend: {args.backend}")
    print(f"Verifier model: {args.model_name}")
    print(f"Profile mode: {args.profile_mode}")

    if not pending_rows:
        print("Nothing left to verify.")
        return

    schema_store = SchemaAnnotationStore.from_json(args.schema_path)

    llm_generate_fn = build_verifier_generate_fn(
        model_name=args.model_name,
        backend=args.backend,
        temperature=args.temperature,
        num_predict=args.num_predict,
        timeout=args.timeout,
    )

    for row in tqdm(pending_rows):
        question_id = str(
            row.get("question_id")
            or row.get("id")
            or row.get(args.question_key)
        )

        try:
            question = get_question(row, args.question_key)
            generated_sql = get_generated_sql(row, args.sql_key)

            execution_profile = build_execution_profile(
                generated_sql=generated_sql,
                schema_store=schema_store,
                profile_mode=args.profile_mode,
            )

            verification = verify_decompiled_sql(
                question=question,
                execution_profile=execution_profile,
                llm_generate_fn=llm_generate_fn,
            )

            output_row = make_output_row(
                source_row=row,
                question=question,
                generated_sql=generated_sql,
                execution_profile=execution_profile,
                verification_result=verification.to_dict(),
                verifier_model=args.model_name,
                profile_format=args.profile_mode,
            )

            output_row["status"] = "success"
            output_row["error"] = None

        except Exception as exc:
            output_row = {
                "question_id": row.get("question_id") or row.get("id"),
                "db_id": row.get("db_id"),
                "split": row.get("split"),
                "level": row.get("level"),
                "generator": row.get("generator") or row.get("model") or row.get("model_key"),
                "prompt_setting": row.get("prompt_setting"),
                "evaluation_group": get_evaluation_group(row),
                "question": row.get(args.question_key),
                "gold_sql": row.get("gold_sql"),
                "generated_sql": row.get(args.sql_key),
                "generated_sql_hash": stable_sql_hash(row.get(args.sql_key)),
                "execution_profile": None,
                "profile_status": None,
                "verifier_model": args.model_name,
                "verification": None,
                "profile_format": args.profile_mode,
                "status": "failed",
                "error": str(exc),
            }

        append_jsonl(output_path, output_row)

        completed_keys.add(
            get_run_key(
                row=row,
                verifier_model=args.model_name,
                question_key=args.question_key,
                sql_key=args.sql_key,
                profile_mode=args.profile_mode,
            )
        )

    print(f"Saved verification outputs to: {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run FinVeriSQL verification over generated Text-to-SQL outputs.",
    )

    parser.add_argument(
        "--input-path",
        required=True,
        help="Input JSONL containing generated SQL outputs.",
    )
    parser.add_argument(
        "--output-path",
        required=True,
        help="Output JSONL path for FinVeriSQL verification results.",
    )
    parser.add_argument(
        "--schema-path",
        default=DEFAULT_SCHEMA_PATH,
        help="Path to schema annotation JSON.",
    )
    parser.add_argument(
        "--profile-mode",
        choices=["ast", "semantic", "compact"],
        default="compact",
        help=(
            "Verifier input profile for ablation. "
            "'ast' passes parsed SQL AST only; "
            "'semantic' passes the full schema-grounded semantic profile; "
            "'compact' passes the compact semantic verifier payload."
        ),
    )
    parser.add_argument(
        "--model-name",
        default=DEFAULT_MODEL_NAME,
        help=(
            "Verifier model name. Hugging Face MLX models use mlx-community/...; "
            "Ollama models use tag names like deepseek-r1:8b."
        ),
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Verifier model temperature.",
    )
    parser.add_argument(
        "--num-predict",
        type=int,
        default=1024,
        help="Maximum tokens to generate from verifier model.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Ollama request timeout in seconds. Ignored for MLX-VLM backends.",
    )
    parser.add_argument(
        "--evaluation-group",
        default=None,
        help="Optional filter, e.g. A_correct_executable or B_wrong_executable.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional number of rows to verify after filtering.",
    )
    parser.add_argument(
        "--question-key",
        default="question",
        help="JSONL key containing the natural language question.",
    )
    parser.add_argument(
        "--sql-key",
        default="generated_sql",
        help="JSONL key containing the generated SQL.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Do not skip rows already present in output path. "
            "This appends duplicate rows; it does not delete or replace the file."
        ),
    )
    parser.add_argument(
        "--backend",
        choices=["auto", "ollama", "mlx-lm", "mlx-vlm"],
        default="auto",
        help=(
            "Verifier backend. Use 'auto' to infer from model name, or explicitly "
            "choose ollama, mlx-lm, or mlx-vlm."
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_verification(args)


if __name__ == "__main__":
    main()