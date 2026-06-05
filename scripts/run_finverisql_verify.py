from __future__ import annotations

import argparse
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
    from src.finverisql.sql_decompiler import decompile_semantics
    from src.finverisql.verifier import verify_decompiled_sql
    from src.utils.inference_utils import build_verifier_generate_fn
    from src.finverisql.fsir_builder import build_fsir, render_fsir_for_verifier
    
except ModuleNotFoundError:
    from finverisql.schema_loader import SchemaAnnotationStore
    from finverisql.sql_parser import parse_sql
    from finverisql.sql_semantic_mapping import build_sql_financial_semantics
    from finverisql.sql_decompiler import decompile_semantics
    from finverisql.verifier import verify_decompiled_sql
    from utils.inference_utils import build_verifier_generate_fn
    from src.finverisql.fsir_builder import build_fsir, render_fsir_for_verifier


DEFAULT_SCHEMA_PATH = "data/booksql/schema_annotations.json"
DEFAULT_MODEL_NAME = "mlx-community/gemma-4-e4b-it-4bit"


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
) -> tuple[str, str | None, str | None, str | None, str]:
    question_id = str(
        row.get("question_id")
        or row.get("id")
        or row.get(question_key)
    )

    return (
        question_id,
        row.get("generator") or row.get("model") or row.get("model_key"),
        row.get("prompt_setting"),
        get_evaluation_group(row),
        verifier_model,
    )


def load_completed_keys(
    output_path: str | Path,
) -> set[tuple[str, str | None, str | None, str | None, str]]:
    output_path = Path(output_path)

    if not output_path.exists():
        return set()

    completed: set[tuple[str, str | None, str | None, str | None, str]] = set()

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
                    )
                )
            except Exception:
                continue

    return completed

def load_completed_question_ids(output_path: str | Path) -> set[str]:
    """
    Resume helper for verifier runs.

    If a question_id already appears in the output JSONL, treat it as done.
    This is intentionally less strict than load_completed_keys().
    """
    output_path = Path(output_path)

    if not output_path.exists():
        return set()

    completed: set[str] = set()

    with output_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                question_id = row.get("question_id") or row.get("id")

                if question_id is not None:
                    completed.add(str(question_id))

            except Exception:
                continue

    return completed

def build_execution_profile(
    generated_sql: str,
    schema_store: SchemaAnnotationStore,
) -> tuple[str, str | None]:
    """
    Build the verifier-facing FSIR profile, the old text decompiler
    is kept as debug evidence.

    Returns:
        execution_profile:
            FSIR JSON string passed to the verifier.

        debug_decompiled_profile:
            Old human-readable decompiler output for debugging only.
    """
    try:
        parsed_sql = parse_sql(generated_sql)
        semantics = build_sql_financial_semantics(
            parsed_sql=parsed_sql,
            schema_store=schema_store,
        )

        fsir = build_fsir(semantics)
        execution_profile = render_fsir_for_verifier(fsir)

        try:
            debug_decompiled_profile = decompile_semantics(semantics)
        except Exception as debug_exc:
            debug_decompiled_profile = (
                "[Status] DEBUG_DECOMPILER_FAILED\n"
                f"{type(debug_exc).__name__}: {debug_exc}"
            )

        return execution_profile, debug_decompiled_profile

    except Exception as exc:
        error_profile = {
            "status": "PARSE_ERROR",
            "profile_extraction": {
                "status": "PARSE_ERROR",
                "unsupported_features": [],
                "extraction_warnings": [
                    f"SQL parse/FSIR pipeline failed before verification: {type(exc).__name__}: {exc}"
                ],
            },
            "financial_concept_layer": {
                "scope_constraints": [],
                "scope_coverage": {
                    "has_scope_constraints": False,
                    "status": "unknown_due_to_parse_error",
                    "ambiguous_scope_count": 0,
                    "note": "No scope constraints can be extracted from a parse error.",
                },
            },
            "measurement_layer": {
                "measurements": [],
            },
            "reporting_topology_layer": {
                "analytical_grain": "unknown",
                "grouping_dimensions": [],
                "temporal_resolution": {
                    "source_dialect": "sqlite",
                    "parser_scope": "sqlite_date_arithmetic",
                    "representation_level": "symbolic_temporal_boundary",
                    "date_predicates": [],
                    "normalization_status": "unknown",
                },
                "filter_topology": {
                    "where_measure_threshold_filters": [],
                    "post_aggregation_filters": [],
                    "post_aggregation_filter_extraction_status": "not_supported_in_fsir_v0",
                    "threshold_filtering_risk": "unknown_due_to_parse_error",
                },
                "ordering": [],
                "limit": None,
            },
        }

        return (
            json.dumps(error_profile, ensure_ascii=False, indent=2, sort_keys=True),
            (
                "[Status] PARSE_ERROR\n"
                f"SQL parse/FSIR pipeline failed before verification: {type(exc).__name__}: {exc}"
            ),
        )


def detect_profile_status(execution_profile: str) -> str | None:
    """
    Supports both:
    - old text decompiler profiles with [Status]
    - new FSIR JSON profiles with status/profile_extraction.status
    """
    try:
        parsed = json.loads(execution_profile)

        if isinstance(parsed, dict):
            profile_extraction = parsed.get("profile_extraction") or {}

            return (
                profile_extraction.get("status")
                or parsed.get("status")
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
    debug_decompiled_profile: str | None = None,
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
        "execution_profile": execution_profile,
        "profile_status": detect_profile_status(execution_profile),
        "verifier_model": verifier_model,
        "verification": verification_result,
        "debug_decompiled_profile": debug_decompiled_profile,
        "profile_format": "fsir_json",
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

        completed_question_ids = load_completed_question_ids(output_path)

    pending_rows: list[dict[str, Any]] = []

    for row in rows:
        question_id = str(
            row.get("question_id")
            or row.get("id")
            or row.get(args.question_key)
        )

        if not args.overwrite and question_id in completed_question_ids:
            continue

        pending_rows.append(row)

    print(f"Input rows selected: {len(rows)}")
    print(f"Already completed in output file: {len(rows) - len(pending_rows)}")
    print(f"Pending rows: {len(pending_rows)}")
    print(f"Verifier backend: {args.backend}")
    print(f"Verifier model: {args.model_name}")

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

            execution_profile, debug_decompiled_profile = build_execution_profile(
                generated_sql=generated_sql,
                schema_store=schema_store,
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
                debug_decompiled_profile=debug_decompiled_profile,
                verification_result=verification.to_dict(),
                verifier_model=args.model_name,
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
                "execution_profile": None,
                "profile_status": None,
                "verifier_model": args.model_name,
                "verification": None,
                "debug_decompiled_profile": None,
                "profile_format": "fsir_json",
                "status": "failed",
                "error": str(exc),
            }

        append_jsonl(output_path, output_row)
        completed_question_ids.add(question_id)

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
        "--model-name",
        default=DEFAULT_MODEL_NAME,
        help="Verifier model name. Hugging Face MLX models use mlx-community/...; Ollama models use tag names like deepseek-r1:8b.",
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
        help="Do not skip rows already present in output path.",
    )

    parser.add_argument(
        "--backend",
        choices=["auto", "ollama", "mlx-lm", "mlx-vlm"],
        default="auto",
        help="Verifier backend. Use 'auto' to infer from model name, or explicitly choose ollama, mlx-lm, or mlx-vlm.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_verification(args)


if __name__ == "__main__":
    main()