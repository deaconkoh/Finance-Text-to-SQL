#!/usr/bin/env python3
"""
Run FinVeriSQL verifier ablations over generated Text-to-SQL outputs.

This script runs the current three-stage FinVeriSQL verifier:

1. Intent decomposition
   Converts the natural-language question into a structured intent JSON.

2. Semantic verification
   Compares the decomposed intent against the generated SQL execution profile.
   The verifier can run with direct comparison only, probing only, or hybrid
   direct-then-probe verification.

3. Repair hint generation
   Generates repair guidance when semantic verification rejects the SQL.

It also supports three verifier input profile modes:

1. ast
   Uses only the parsed SQL AST.
   Profile pipeline:
       generated SQL
       -> SQL parser
       -> parsed SQL AST
       -> verifier

   Purpose:
       Tests whether SQL syntax/structure alone is enough for verification.

2. semantic
   Uses the full schema-grounded semantic profile.
   Profile pipeline:
       generated SQL
       -> SQL parser
       -> schema-grounded semantic mapper
       -> full semantic profile
       -> verifier

   Purpose:
       Tests whether detailed schema-grounded mapping improves verification.

3. compact
   Uses the compact verifier payload derived from the semantic profile.
   Profile pipeline:
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
- Group-aware orchestration is applied after any optional `--evaluation-group`
  filter: A/B rows are verified, C rows are routed to a repair-queue JSONL, and
  D rows are logged to a skipped/excluded JSONL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable

from tqdm import tqdm


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


try:
    from src.finverisql.intent_decomposer import IntentDecomposer
    from src.finverisql.schema_loader import SchemaAnnotationStore
    from src.finverisql.sql_parser import parse_sql
    from src.finverisql.sql_semantic_mapping import build_sql_financial_semantics
    from src.finverisql.compact_semantic_profile import build_verifier_payload
    from src.finverisql.verifier import (
        build_execution_error_profile,
        build_non_executable_verification_result,
        verify_decompiled_sql,
    )
    from src.utils.inference_utils import build_verifier_generate_fn
    from src.utils.resumable_jsonl import (
        append_jsonl_durable,
        exclusive_jsonl_run,
        recover_incomplete_jsonl_tail,
    )

except ModuleNotFoundError:
    from finverisql.intent_decomposer import IntentDecomposer
    from finverisql.schema_loader import SchemaAnnotationStore
    from finverisql.sql_parser import parse_sql
    from finverisql.sql_semantic_mapping import build_sql_financial_semantics
    from finverisql.compact_semantic_profile import build_verifier_payload
    from finverisql.verifier import (
        build_execution_error_profile,
        build_non_executable_verification_result,
        verify_decompiled_sql,
    )
    from utils.inference_utils import build_verifier_generate_fn
    from utils.resumable_jsonl import (
        append_jsonl_durable,
        exclusive_jsonl_run,
        recover_incomplete_jsonl_tail,
    )


DEFAULT_SCHEMA_PATH = "data/booksql/schema_annotations.json"
DEFAULT_MODEL_NAME = "mlx-community/Llama-3.1-8B-Instruct-4bit"
GROUP_A = "A_correct_executable"
GROUP_B = "B_wrong_executable"
GROUP_C = "C_non_executable"
GROUP_D = "D_ambiguous"
VERIFY_GROUPS = {GROUP_A, GROUP_B}
REPAIR_GROUPS = {GROUP_C}
EXCLUDED_GROUPS = {GROUP_D}


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
    append_jsonl_durable(path, row)


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


def stable_question_hash(question: Any) -> str:
    text = str(question or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def get_question_id(row: dict[str, Any], question_key: str) -> str:
    return str(
        row.get("question_id")
        or row.get("id")
        or row.get(question_key)
    )


def get_route_key(
    row: dict[str, Any],
    question_key: str,
    sql_key: str,
) -> tuple[str, str | None, str | None, str | None, str]:
    return (
        get_question_id(row, question_key),
        row.get("generator") or row.get("model") or row.get("model_key"),
        row.get("prompt_setting"),
        get_evaluation_group(row),
        stable_sql_hash(row.get(sql_key)),
    )


def get_run_key(
    row: dict[str, Any],
    verifier_model: str,
    question_key: str,
    sql_key: str,
    profile_mode: str,
    probing_mode: str,
    intent_mode: str,
    max_probes: int,
) -> tuple[str, str | None, str | None, str | None, str, str, str, str, int, str]:
    question_id = get_question_id(row, question_key)
    generated_sql = row.get(sql_key)

    return (
        question_id,
        row.get("generator") or row.get("model") or row.get("model_key"),
        row.get("prompt_setting"),
        get_evaluation_group(row),
        verifier_model,
        profile_mode,
        probing_mode,
        intent_mode,
        max_probes,
        stable_sql_hash(generated_sql),
    )


def load_completed_keys(
    output_path: str | Path,
) -> set[tuple[str, str | None, str | None, str | None, str, str, str, str, int, str]]:
    output_path = Path(output_path)

    if not output_path.exists():
        return set()

    completed: set[
        tuple[str, str | None, str | None, str | None, str, str, str, str, int, str]
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
                        row.get("probing_mode") or "unknown",
                        row.get("intent_mode") or "unknown",
                        int(row.get("max_probes") or -1),
                        row.get("generated_sql_hash")
                        or stable_sql_hash(row.get("generated_sql")),
                    )
                )
            except Exception:
                continue

    return completed


def load_completed_route_keys(
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
                        row.get("generated_sql_hash")
                        or stable_sql_hash(row.get("generated_sql")),
                    )
                )
            except Exception:
                continue

    return completed


def load_intent_cache(
    intent_cache_path: str | Path | None,
    intent_mode: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    if intent_cache_path is None:
        return {}

    path = Path(intent_cache_path)

    if not path.exists():
        raise FileNotFoundError(f"Intent cache not found: {path}")

    cache: dict[tuple[str, str], dict[str, Any]] = {}

    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid intent cache JSONL at {path}, line {line_number}: {exc}"
                ) from exc

            if row.get("status") and row.get("status") != "success":
                continue

            if row.get("intent_mode") != intent_mode:
                continue

            intent_representation = row.get("intent_representation")

            if not isinstance(intent_representation, dict):
                continue

            question_id = row.get("question_id") or row.get("id")
            question_hash = row.get("question_hash")

            if question_id is not None:
                cache[("question_id", str(question_id))] = intent_representation

            if question_hash is not None:
                cache[("question_hash", str(question_hash))] = intent_representation

    return cache


def get_cached_intent(
    intent_cache: dict[tuple[str, str], dict[str, Any]],
    row: dict[str, Any],
    question_key: str,
) -> dict[str, Any] | None:
    question_id = row.get("question_id") or row.get("id")

    if question_id is not None:
        cached = intent_cache.get(("question_id", str(question_id)))

        if cached is not None:
            return cached

    question = row.get(question_key)

    if question is not None:
        return intent_cache.get(("question_hash", stable_question_hash(question)))

    return None


def row_matches_intent_cache(
    intent_cache: dict[tuple[str, str], dict[str, Any]],
    row: dict[str, Any],
    question_key: str,
    match_mode: str,
) -> bool:
    question_id = row.get("question_id") or row.get("id")
    question = row.get(question_key)

    if match_mode == "question_id":
        return (
            question_id is not None
            and ("question_id", str(question_id)) in intent_cache
        )

    if match_mode == "question_hash":
        return (
            question is not None
            and ("question_hash", stable_question_hash(question)) in intent_cache
        )

    if match_mode == "either":
        return get_cached_intent(intent_cache, row, question_key) is not None

    raise ValueError(f"Unsupported intent cache match mode: {match_mode}")


def maybe_shuffle_rows(
    rows: list[dict[str, Any]],
    sample_seed: int | None,
) -> list[dict[str, Any]]:
    if sample_seed is None:
        return rows

    shuffled = list(rows)
    rng = random.Random(sample_seed)
    rng.shuffle(shuffled)
    return shuffled


def filter_rows_to_intent_cache(
    rows: list[dict[str, Any]],
    intent_cache: dict[tuple[str, str], dict[str, Any]],
    question_key: str,
    match_mode: str,
) -> tuple[list[dict[str, Any]], int]:
    filtered_rows: list[dict[str, Any]] = []
    missing_count = 0

    for row in rows:
        if row_matches_intent_cache(
            intent_cache=intent_cache,
            row=row,
            question_key=question_key,
            match_mode=match_mode,
        ):
            filtered_rows.append(row)
        else:
            missing_count += 1

    return filtered_rows, missing_count


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
    intent_representation: dict[str, Any] | None,
    execution_profile: str,
    verification_result: dict[str, Any],
    verifier_model: str,
    profile_format: str,
    intent_mode: str,
    probing_mode: str,
    max_probes: int,
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
        "intent_representation": intent_representation,
        "execution_profile": execution_profile,
        "profile_status": detect_profile_status(execution_profile),
        "verifier_model": verifier_model,
        "verification": verification_result,
        "profile_format": profile_format,
        "intent_mode": intent_mode,
        "probing_mode": probing_mode,
        "max_probes": max_probes,
    }


def extract_execution_error(row: dict[str, Any]) -> tuple[str | None, str | None]:
    for key in ("generated_error", "error_message", "error"):
        value = row.get(key)
        if value:
            return str(value), key

    status = row.get("generated_execution_status")
    if status and str(status).lower() != "success":
        return str(status), "generated_execution_status"

    return None, None


def derive_group_output_path(output_path: str | Path, suffix: str) -> Path:
    path = Path(output_path)
    return path.with_name(f"{path.stem}_{suffix}{path.suffix}")


def split_rows_by_pipeline(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    verify_rows: list[dict[str, Any]] = []
    repair_rows: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []
    unknown_rows: list[dict[str, Any]] = []

    for row in rows:
        group = get_evaluation_group(row)

        if group in VERIFY_GROUPS:
            verify_rows.append(row)
        elif group in REPAIR_GROUPS:
            repair_rows.append(row)
        elif group in EXCLUDED_GROUPS:
            excluded_rows.append(row)
        else:
            unknown_rows.append(row)

    return verify_rows, repair_rows, excluded_rows, unknown_rows


def make_routed_row(
    source_row: dict[str, Any],
    question_key: str,
    sql_key: str,
    route_target: str,
    route_reason: str,
    status: str = "routed",
    error: str | None = None,
) -> dict[str, Any]:
    generated_sql = source_row.get(sql_key)
    return {
        "question_id": source_row.get("question_id") or source_row.get("id"),
        "db_id": source_row.get("db_id"),
        "split": source_row.get("split"),
        "level": source_row.get("level"),
        "generator": source_row.get("generator") or source_row.get("model") or source_row.get("model_key"),
        "prompt_setting": source_row.get("prompt_setting"),
        "evaluation_group": get_evaluation_group(source_row),
        "question": source_row.get(question_key),
        "gold_sql": source_row.get("gold_sql"),
        "generated_sql": generated_sql,
        "generated_sql_hash": stable_sql_hash(generated_sql),
        "pipeline_route": route_target,
        "route_reason": route_reason,
        "status": status,
        "error": error,
    }


def append_routed_rows(
    rows: list[dict[str, Any]],
    output_path: Path,
    question_key: str,
    sql_key: str,
    route_target: str,
    route_reason: str,
    overwrite: bool,
) -> tuple[int, int]:
    completed_keys = load_completed_route_keys(output_path)
    appended = 0
    skipped = 0

    for row in rows:
        route_key = get_route_key(row, question_key=question_key, sql_key=sql_key)

        if not overwrite and route_key in completed_keys:
            skipped += 1
            continue

        append_jsonl(
            output_path,
            make_routed_row(
                source_row=row,
                question_key=question_key,
                sql_key=sql_key,
                route_target=route_target,
                route_reason=route_reason,
            ),
        )
        completed_keys.add(route_key)
        appended += 1

    return appended, skipped


def run_bounded_rows(
    pending_rows: list[tuple[int, dict[str, Any]]],
    workers: int,
    process_row: Callable[[int, dict[str, Any]], dict[str, Any]],
    commit_row: Callable[[dict[str, Any]], None],
) -> None:
    """Process rows concurrently while the caller remains the only JSONL writer."""
    if workers == 1:
        for input_index, row in tqdm(pending_rows):
            commit_row(process_row(input_index, row))
        return

    iterator = iter(pending_rows)
    futures = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        def submit_next() -> bool:
            try:
                input_index, row = next(iterator)
            except StopIteration:
                return False
            futures[executor.submit(process_row, input_index, row)] = (input_index, row)
            return True

        for _ in range(workers):
            if not submit_next():
                break

        progress = tqdm(total=len(pending_rows))
        try:
            while futures:
                completed, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in completed:
                    futures.pop(future)
                    commit_row(future.result())
                    progress.update(1)
                    submit_next()
        except KeyboardInterrupt:
            print("Interrupt received; draining in-flight rows before exit.")
            for future in futures:
                future.cancel()
            for future in list(futures):
                if future.cancelled():
                    continue
                commit_row(future.result())
                progress.update(1)
            raise
        finally:
            progress.close()


def run_execution_error_rows(
    rows: list[dict[str, Any]],
    output_path: Path,
    args: argparse.Namespace,
) -> int:
    completed_keys = load_completed_keys(output_path)
    pending_rows: list[tuple[int, dict[str, Any]]] = []

    for input_index, row in enumerate(rows):
        run_key = get_run_key(
            row=row,
            verifier_model=args.model_name,
            question_key=args.question_key,
            sql_key=args.sql_key,
            profile_mode=args.profile_mode,
            probing_mode=args.probing_mode,
            intent_mode=args.intent_mode,
            max_probes=args.max_probes,
        )

        if not args.overwrite and run_key in completed_keys:
            continue

        pending_rows.append((input_index, row))

    print(f"Rows selected for Group C execution-error verification: {len(rows)}")
    print(f"Already completed in verification output: {len(rows) - len(pending_rows)}")
    print(f"Pending Group C verification rows: {len(pending_rows)}")

    if not pending_rows:
        return 0

    schema_store = None
    if args.intent_mode == "metadata_guided":
        schema_store = SchemaAnnotationStore.from_json(args.schema_path)

    intent_cache = load_intent_cache(args.intent_cache_path, args.intent_mode)
    llm_generate_fn = None
    decomposer = None

    if not args.intent_cache_path or not args.require_intent_cache:
        llm_generate_fn = build_verifier_generate_fn(
            model_name=args.model_name,
            backend=args.backend,
            temperature=args.temperature,
            num_predict=args.num_predict,
            timeout=args.timeout,
        )
        decomposer = IntentDecomposer(
            llm_call=llm_generate_fn,
            intent_mode=args.intent_mode,
            schema_store=schema_store,
        )

    for input_index, row in tqdm(pending_rows):
        try:
            question = get_question(row, args.question_key)
            generated_sql = get_generated_sql(row, args.sql_key)
            execution_error, error_source = extract_execution_error(row)
            intent_representation = get_cached_intent(
                intent_cache=intent_cache,
                row=row,
                question_key=args.question_key,
            )

            if intent_representation is None:
                if args.require_intent_cache or decomposer is None:
                    raise KeyError(
                        "No cached intent found for "
                        f"question_id={get_question_id(row, args.question_key)!r}. "
                        "Run scripts/precompute_finverisql_intents.py first or "
                        "omit --require-intent-cache."
                    )

                intent_representation = decomposer.decompose(question)

            execution_profile = build_execution_error_profile(
                generated_sql=generated_sql,
                execution_error=execution_error,
                error_source=error_source,
            )
            verification = build_non_executable_verification_result(execution_error)
            output_row = make_output_row(
                source_row=row,
                question=question,
                generated_sql=generated_sql,
                intent_representation=intent_representation,
                execution_profile=execution_profile,
                verification_result=verification.to_dict(),
                verifier_model=args.model_name,
                profile_format=args.profile_mode,
                intent_mode=args.intent_mode,
                probing_mode=args.probing_mode,
                max_probes=args.max_probes,
            )
            output_row["status"] = "success"
            output_row["error"] = None
            output_row["pipeline_route"] = "non_executable_verification"

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
                "intent_representation": None,
                "execution_profile": None,
                "profile_status": None,
                "verifier_model": args.model_name,
                "verification": None,
                "profile_format": args.profile_mode,
                "intent_mode": args.intent_mode,
                "probing_mode": args.probing_mode,
                "max_probes": args.max_probes,
                "status": "failed",
                "error": str(exc),
                "pipeline_route": "non_executable_verification",
            }

        output_row["input_index"] = input_index
        append_jsonl(output_path, output_row)
        completed_keys.add(
            get_run_key(
                row=row,
                verifier_model=args.model_name,
                question_key=args.question_key,
                sql_key=args.sql_key,
                profile_mode=args.profile_mode,
                probing_mode=args.probing_mode,
                intent_mode=args.intent_mode,
                max_probes=args.max_probes,
            )
        )

    print(f"Saved Group C verification outputs to: {output_path}")
    return len(pending_rows)


def run_verification_rows(
    rows: list[dict[str, Any]],
    output_path: Path,
    args: argparse.Namespace,
) -> int:
    completed_keys = load_completed_keys(output_path)

    pending_rows: list[tuple[int, dict[str, Any]]] = []

    for input_index, row in enumerate(rows):
        run_key = get_run_key(
            row=row,
            verifier_model=args.model_name,
            question_key=args.question_key,
            sql_key=args.sql_key,
            profile_mode=args.profile_mode,
            probing_mode=args.probing_mode,
            intent_mode=args.intent_mode,
            max_probes=args.max_probes,
        )

        if not args.overwrite and run_key in completed_keys:
            continue

        pending_rows.append((input_index, row))

    print(f"Rows selected for verification: {len(rows)}")
    print(f"Already completed in verification output: {len(rows) - len(pending_rows)}")
    print(f"Pending verification rows: {len(pending_rows)}")
    print(f"Verifier backend: {args.backend}")
    print(f"Verifier model: {args.model_name}")
    print(f"Intent mode: {args.intent_mode}")
    print(f"Profile mode: {args.profile_mode}")
    print(f"Probing mode: {args.probing_mode}")
    print(f"Max probes: {args.max_probes}")
    print(f"Concurrent workers: {args.workers}")
    print(f"Intent cache: {args.intent_cache_path or 'disabled'}")
    print(f"Sample seed: {args.sample_seed if args.sample_seed is not None else 'disabled'}")

    if not pending_rows:
        print("Nothing left to verify.")
        return 0

    schema_store = SchemaAnnotationStore.from_json(args.schema_path)
    intent_cache = load_intent_cache(args.intent_cache_path, args.intent_mode)

    llm_generate_fn = build_verifier_generate_fn(
        model_name=args.model_name,
        backend=args.backend,
        temperature=args.temperature,
        num_predict=args.num_predict,
        timeout=args.timeout,
    )

    decomposer = None

    if not args.intent_cache_path or not args.require_intent_cache:
        decomposer = IntentDecomposer(
            llm_call=llm_generate_fn,
            intent_mode=args.intent_mode,
            schema_store=schema_store,
        )

    def process_row(input_index: int, row: dict[str, Any]) -> dict[str, Any]:
        try:
            question = get_question(row, args.question_key)
            generated_sql = get_generated_sql(row, args.sql_key)

            intent_representation = get_cached_intent(
                intent_cache=intent_cache,
                row=row,
                question_key=args.question_key,
            )

            if intent_representation is None:
                if args.require_intent_cache or decomposer is None:
                    raise KeyError(
                        "No cached intent found for "
                        f"question_id={get_question_id(row, args.question_key)!r}. "
                        "Run scripts/precompute_finverisql_intents.py first or "
                        "omit --require-intent-cache."
                    )

                intent_representation = decomposer.decompose(question)

            execution_profile = build_execution_profile(
                generated_sql=generated_sql,
                schema_store=schema_store,
                profile_mode=args.profile_mode,
            )

            verification = verify_decompiled_sql(
                question=question,
                execution_profile=execution_profile,
                llm_generate_fn=llm_generate_fn,
                intent_representation=intent_representation,
                probing_mode=args.probing_mode,
                max_probes=args.max_probes,
                profile_mode=args.profile_mode,
            )

            output_row = make_output_row(
                source_row=row,
                question=question,
                generated_sql=generated_sql,
                intent_representation=intent_representation,
                execution_profile=execution_profile,
                verification_result=verification.to_dict(),
                verifier_model=args.model_name,
                profile_format=args.profile_mode,
                intent_mode=args.intent_mode,
                probing_mode=args.probing_mode,
                max_probes=args.max_probes,
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
                "intent_representation": None,
                "execution_profile": None,
                "profile_status": None,
                "verifier_model": args.model_name,
                "verification": None,
                "profile_format": args.profile_mode,
                "intent_mode": args.intent_mode,
                "probing_mode": args.probing_mode,
                "max_probes": args.max_probes,
                "status": "failed",
                "error": str(exc),
            }

        output_row["input_index"] = input_index
        return output_row

    def commit_row(output_row: dict[str, Any]) -> None:
        append_jsonl(output_path, output_row)
        completed_keys.add(
            get_run_key(
                row=output_row,
                verifier_model=args.model_name,
                question_key=args.question_key,
                sql_key=args.sql_key,
                profile_mode=args.profile_mode,
                probing_mode=args.probing_mode,
                intent_mode=args.intent_mode,
                max_probes=args.max_probes,
            )
        )

    run_bounded_rows(
        pending_rows=pending_rows,
        workers=args.workers,
        process_row=process_row,
        commit_row=commit_row,
    )

    print(f"Saved verification outputs to: {output_path}")
    return len(pending_rows)


def run_verification(args: argparse.Namespace) -> None:
    input_path = Path(args.input_path)
    output_path = Path(args.output_path)

    rows = read_jsonl(input_path)
    original_row_count = len(rows)
    rows_after_group_filter = original_row_count

    if args.evaluation_group:
        rows = [
            row for row in rows
            if get_evaluation_group(row) == args.evaluation_group
        ]
        rows_after_group_filter = len(rows)

    cache_filtered_out = 0
    rows_before_cache_filter = len(rows)
    if args.restrict_to_intent_cache:
        if not args.intent_cache_path:
            raise ValueError(
                "--restrict-to-intent-cache requires --intent-cache-path."
            )

        intent_cache = load_intent_cache(args.intent_cache_path, args.intent_mode)
        rows, cache_filtered_out = filter_rows_to_intent_cache(
            rows=rows,
            intent_cache=intent_cache,
            question_key=args.question_key,
            match_mode=args.intent_cache_filter_key,
        )

    rows = maybe_shuffle_rows(rows, args.sample_seed)

    rows_before_limit = len(rows)
    if args.limit is not None:
        rows = rows[: args.limit]

    verify_rows, repair_rows, excluded_rows, unknown_rows = split_rows_by_pipeline(rows)

    repair_output_path = (
        Path(args.repair_output_path)
        if args.repair_output_path
        else derive_group_output_path(output_path, "repair_queue")
    )
    skipped_output_path = (
        Path(args.skipped_output_path)
        if args.skipped_output_path
        else derive_group_output_path(output_path, "skipped")
    )

    print(f"Input rows loaded: {original_row_count}")
    if args.evaluation_group:
        print(f"Rows after evaluation-group filter: {rows_after_group_filter}")
    if args.restrict_to_intent_cache:
        print(
            f"Rows matched by intent cache ({args.intent_cache_filter_key}): "
            f"{rows_before_cache_filter} -> {rows_before_limit} "
            f"(filtered_out={cache_filtered_out})"
        )
    if args.limit is not None:
        print(f"Rows after limit: {len(rows)} / {rows_before_limit}")
    print(f"Input rows selected: {len(rows)}")
    print(
        "Group routing counts: "
        f"verify(A/B)={len(verify_rows)}, "
        f"repair(C)={len(repair_rows)}, "
        f"excluded(D)={len(excluded_rows)}, "
        f"unknown={len(unknown_rows)}"
    )

    if unknown_rows:
        raise ValueError(
            "Found rows with unsupported or missing evaluation_group values: "
            f"{sorted({get_evaluation_group(row) for row in unknown_rows})}"
        )

    with exclusive_jsonl_run((output_path, repair_output_path, skipped_output_path)):
        for artifact_path in (output_path, repair_output_path, skipped_output_path):
            recover_incomplete_jsonl_tail(artifact_path)

        verified_count = run_verification_rows(
            rows=verify_rows,
            output_path=output_path,
            args=args,
        )
        group_c_verified_count = run_execution_error_rows(
            rows=repair_rows,
            output_path=output_path,
            args=args,
        )

        repair_appended, repair_skipped = append_routed_rows(
            rows=repair_rows,
            output_path=repair_output_path,
            question_key=args.question_key,
            sql_key=args.sql_key,
            route_target="repair_queue",
            route_reason="evaluation_group=C_non_executable routes directly to repair.",
            overwrite=args.overwrite,
        )
        skipped_appended, skipped_skipped = append_routed_rows(
            rows=excluded_rows,
            output_path=skipped_output_path,
            question_key=args.question_key,
            sql_key=args.sql_key,
            route_target="excluded",
            route_reason="evaluation_group=D_ambiguous is excluded from the pipeline.",
            overwrite=args.overwrite,
        )

    if repair_rows:
        print(
            f"Saved repair-queue rows to: {repair_output_path} "
            f"(appended={repair_appended}, already_present={repair_skipped})"
        )

    if excluded_rows:
        print(
            f"Saved excluded rows to: {skipped_output_path} "
            f"(appended={skipped_appended}, already_present={skipped_skipped})"
        )

    print(
        "Pipeline summary: "
        f"verified={verified_count}, "
        f"group_c_verified={group_c_verified_count}, "
        f"repair_queued={repair_appended}, "
        f"excluded_logged={skipped_appended}"
    )


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
        help=(
            "Output JSONL path for FinVeriSQL verification results. "
            "A/B rows and deterministic Group C execution-error rows are written here."
        ),
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
        "--intent-mode",
        choices=["none", "nl_only", "metadata_guided"],
        default="metadata_guided",
        help=(
            "Intent decomposition mode. 'none' skips decomposition and uses "
            "the raw question; 'nl_only' uses only the question; "
            "'metadata_guided' also uses schema annotation metadata."
        ),
    )
    parser.add_argument(
        "--probing-mode",
        choices=["none", "hybrid", "probe"],
        default="hybrid",
        help=(
            "Semantic probing mode. 'none' runs direct comparison only; "
            "'hybrid' probes only after ambiguous direct comparison; "
            "'probe' allows the probing workflow whenever the verifier suggests probes."
        ),
    )
    parser.add_argument(
        "--max-probes",
        type=int,
        default=7,
        help="Maximum number of semantic probing questions to ask.",
    )
    parser.add_argument(
        "--intent-cache-path",
        default=None,
        help=(
            "Optional JSONL produced by scripts/precompute_finverisql_intents.py. "
            "When provided, matching rows reuse precomputed intent representations."
        ),
    )
    parser.add_argument(
        "--require-intent-cache",
        action="store_true",
        help=(
            "Fail rows with missing cached intents instead of falling back to online "
            "intent decomposition."
        ),
    )
    parser.add_argument(
        "--restrict-to-intent-cache",
        action="store_true",
        help=(
            "Pre-filter input rows to only those present in --intent-cache-path. "
            "Use this to run the verifier on a cached subset even when "
            "--input-path contains the full evaluation set."
        ),
    )
    parser.add_argument(
        "--intent-cache-filter-key",
        choices=["question_id", "question_hash", "either"],
        default="question_id",
        help=(
            "Key used when --restrict-to-intent-cache is enabled. "
            "'question_id' restricts to the exact cached row IDs; "
            "'question_hash' restricts by normalized question text hash; "
            "'either' matches either form."
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
        "--workers",
        type=int,
        default=1,
        help=(
            "Concurrent row workers. Values above one require the Ollama backend; "
            "completed rows are committed durably in completion order."
        ),
    )
    parser.add_argument(
        "--evaluation-group",
        default=None,
        help=(
            "Optional pre-routing filter, e.g. A_correct_executable or "
            "B_wrong_executable. Selected rows still follow the group-aware "
            "pipeline."
        ),
    )
    parser.add_argument(
        "--repair-output-path",
        default=None,
        help=(
            "Optional JSONL path for rows routed to the repair queue. "
            "Defaults to '<output-path stem>_repair_queue.jsonl'."
        ),
    )
    parser.add_argument(
        "--skipped-output-path",
        default=None,
        help=(
            "Optional JSONL path for rows excluded from the pipeline. "
            "Defaults to '<output-path stem>_skipped.jsonl'."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Optional number of rows to verify after filtering and optional "
            "seeded shuffling."
        ),
    )
    parser.add_argument(
        "--sample-seed",
        type=int,
        default=None,
        help=(
            "Optional random seed for reproducible row shuffling before "
            "applying --limit."
        ),
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
    if args.workers < 1:
        raise ValueError("--workers must be at least 1.")
    if args.workers > 1 and args.backend != "ollama":
        raise ValueError("--workers > 1 is supported only with --backend ollama.")
    run_verification(args)


if __name__ == "__main__":
    main()
