#!/usr/bin/env python3
"""Generate additive Group B and Group C SQL repairs for FinVeriSQL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tqdm import tqdm


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


try:
    from src.finverisql.intent_decomposer import IntentDecomposer
    from src.finverisql.repair import (
        SemanticRepairResult,
        repair_non_executable_sql,
        repair_semantic_sql,
    )
    from src.finverisql.repair_runner import (
        append_jsonl,
        build_attempt_output_row,
        build_non_executable_repair_request,
        build_semantic_repair_request,
        classify_candidate_row,
        get_repair_run_key,
        load_completed_keys,
        read_jsonl,
        run_generic_semantic_repair_chain,
        run_non_executable_then_semantic_repair_chain,
        run_specialized_first_repair_no_reverification,
        run_specialized_semantic_repair_chain,
        stable_context_hash,
    )
    from src.finverisql.schema_loader import SchemaAnnotationStore
    from src.utils.data_utils import load_booksql_schema
    from src.utils.inference_utils import build_verifier_generate_fn
except ModuleNotFoundError:
    from finverisql.intent_decomposer import IntentDecomposer
    from finverisql.repair import (
        SemanticRepairResult,
        repair_non_executable_sql,
        repair_semantic_sql,
    )
    from finverisql.repair_runner import (
        append_jsonl,
        build_attempt_output_row,
        build_non_executable_repair_request,
        build_semantic_repair_request,
        classify_candidate_row,
        get_repair_run_key,
        load_completed_keys,
        read_jsonl,
        run_generic_semantic_repair_chain,
        run_non_executable_then_semantic_repair_chain,
        run_specialized_first_repair_no_reverification,
        run_specialized_semantic_repair_chain,
        stable_context_hash,
    )
    from finverisql.schema_loader import SchemaAnnotationStore
    from utils.data_utils import load_booksql_schema
    from utils.inference_utils import build_verifier_generate_fn


DEFAULT_SCHEMA_PATH = "data/booksql/schema_annotations.json"
DEFAULT_MODEL_NAME = "mlx-community/Llama-3.1-8B-Instruct-4bit"
DEFAULT_BACKEND = "mlx-lm"
REPAIR_CONTEXT_VERSION = "schema_sqlite_v1"
CHAIN_REPAIR_FRAMEWORKS = {"specialized_chain", "generic_chain"}
SCHEMA_ANNOTATION_FRAMEWORKS = {"specialized_chain", "generic_chain", "no_reverification"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate additive Group B and Group C repair candidates over FinVeriSQL rows.",
    )
    parser.add_argument("--input-path", required=True, help="Input JSONL containing Group B verified rows and/or Group C evaluated rows.")
    parser.add_argument("--output-path", required=True, help="Standalone repair-generation JSONL output.")
    parser.add_argument("--schema-path", default=DEFAULT_SCHEMA_PATH, help="Schema annotation JSON path for metadata-guided intent, or schema text path for Group C repair prompts.")
    parser.add_argument("--repair-model-name", default=DEFAULT_MODEL_NAME, help="Model used to generate repaired SQL.")
    parser.add_argument("--repair-backend", choices=["ollama", "mlx-lm", "mlx-vlm"], default=DEFAULT_BACKEND)
    parser.add_argument("--semantic-repair-framework", choices=["single", "specialized_chain", "generic_chain", "no_reverification"], default="single", help="Semantic repair framework for verifier-rejected executable rows.")
    parser.add_argument("--verifier-model-name", default=DEFAULT_MODEL_NAME, help="Model used to re-verify chain repairs.")
    parser.add_argument("--verifier-backend", choices=["ollama", "mlx-lm", "mlx-vlm"], default=DEFAULT_BACKEND)
    parser.add_argument("--profile-mode", choices=["ast", "semantic", "compact"], default="compact", help="Execution profile mode for chain re-verification.")
    parser.add_argument("--probing-mode", choices=["none", "probe", "hybrid"], default="probe", help="Verifier probing mode for chain re-verification.")
    parser.add_argument("--max-probes", type=int, default=7, help="Maximum verifier probes for chain re-verification.")
    parser.add_argument("--intent-mode", choices=["none", "nl_only", "metadata_guided"], default="nl_only", help="Intent decomposition mode used when the input row does not already contain intent_representation.")
    parser.add_argument("--intent-cache-path", default=None, help="Optional precomputed intent JSONL cache.")
    parser.add_argument("--require-intent-cache", action="store_true", help="Fail rows that need intent decomposition unless they exist in --intent-cache-path.")
    parser.add_argument("--temperature", type=float, default=0.0, help="Model temperature.")
    parser.add_argument("--num-predict", type=int, default=768, help="Maximum generation tokens.")
    parser.add_argument("--timeout", type=int, default=300, help="Ollama HTTP timeout in seconds.")
    parser.add_argument("--limit", type=int, default=None, help="Optional cap on processed input rows.")
    parser.add_argument("--overwrite", action="store_true", help="Disable resume skipping and append duplicate experiment rows.")
    return parser.parse_args()


def stable_question_hash(question: object) -> str:
    import hashlib

    text = str(question or "")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_intent_cache(intent_cache_path: str | None, intent_mode: str) -> dict[tuple[str, str], dict[str, object]]:
    if intent_cache_path is None:
        return {}

    path = Path(intent_cache_path)
    cache: dict[tuple[str, str], dict[str, object]] = {}

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue

            try:
                row = json.loads(text)
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
    intent_cache: dict[tuple[str, str], dict[str, object]],
    row: dict[str, object],
) -> dict[str, object] | None:
    question_id = row.get("question_id") or row.get("id")
    if question_id is not None:
        cached = intent_cache.get(("question_id", str(question_id)))
        if cached is not None:
            return cached

    question = row.get("question")
    if question is not None:
        return intent_cache.get(("question_hash", stable_question_hash(question)))

    return None


def load_schema_text_for_repair(schema_path: str | None) -> str:
    if schema_path and Path(schema_path).suffix.lower() != ".json":
        return Path(schema_path).read_text(encoding="utf-8").strip()

    return load_booksql_schema()


def main() -> None:
    args = parse_args()

    rows = read_jsonl(args.input_path)
    if args.limit is not None:
        rows = rows[: args.limit]

    schema_text = load_schema_text_for_repair(args.schema_path)
    repair_context_hash = stable_context_hash(
        {
            "schema_text": schema_text,
            "intent_mode": args.intent_mode,
            "repair_context_version": REPAIR_CONTEXT_VERSION,
            "semantic_repair_framework": args.semantic_repair_framework,
            "verifier_model_name": args.verifier_model_name if args.semantic_repair_framework in CHAIN_REPAIR_FRAMEWORKS else None,
            "verifier_backend": args.verifier_backend if args.semantic_repair_framework in CHAIN_REPAIR_FRAMEWORKS else None,
            "profile_mode": args.profile_mode if args.semantic_repair_framework in CHAIN_REPAIR_FRAMEWORKS else None,
            "probing_mode": args.probing_mode if args.semantic_repair_framework in CHAIN_REPAIR_FRAMEWORKS else None,
            "max_probes": args.max_probes if args.semantic_repair_framework in CHAIN_REPAIR_FRAMEWORKS else None,
        }
    )

    completed_keys = load_completed_keys(args.output_path)
    pending_rows = []

    for row in rows:
        is_candidate, repair_mode, _ = classify_candidate_row(row)
        run_repair_mode = (
            args.semantic_repair_framework
            if args.semantic_repair_framework in SCHEMA_ANNOTATION_FRAMEWORKS
            and repair_mode == "semantic"
            else repair_mode
        )
        run_key = get_repair_run_key(
            row=row,
            repair_mode=run_repair_mode or "unknown",
            repair_model=args.repair_model_name,
            intent_mode=args.intent_mode,
            repair_context_hash=repair_context_hash,
        )

        if is_candidate and not args.overwrite and run_key in completed_keys:
            continue

        pending_rows.append(row)

    print(f"Input rows selected: {len(rows)}")
    print(f"Pending repair rows: {len(pending_rows)}")
    print(f"Repair model: {args.repair_model_name} ({args.repair_backend})")
    print(f"Semantic repair framework: {args.semantic_repair_framework}")
    print(f"Intent mode for missing intents: {args.intent_mode}")

    if not pending_rows:
        print("Nothing left to repair.")
        return

    schema_store = None
    if args.intent_mode == "metadata_guided" or args.semantic_repair_framework in SCHEMA_ANNOTATION_FRAMEWORKS:
        if Path(args.schema_path).suffix.lower() == ".json":
            schema_store = SchemaAnnotationStore.from_json(args.schema_path)
    repair_generate_fn = build_verifier_generate_fn(
        model_name=args.repair_model_name,
        backend=args.repair_backend,
        temperature=args.temperature,
        num_predict=args.num_predict,
        timeout=args.timeout,
    )
    verifier_generate_fn = None
    if args.semantic_repair_framework in CHAIN_REPAIR_FRAMEWORKS:
        verifier_generate_fn = build_verifier_generate_fn(
            model_name=args.verifier_model_name,
            backend=args.verifier_backend,
            temperature=args.temperature,
            num_predict=args.num_predict,
            timeout=args.timeout,
        )

    intent_cache = load_intent_cache(args.intent_cache_path, args.intent_mode)
    decomposer = None

    if not args.intent_cache_path or not args.require_intent_cache:
        decomposer = IntentDecomposer(
            llm_call=repair_generate_fn,
            intent_mode=args.intent_mode,
            schema_store=schema_store,
        )

    counts = {
        "attempted": 0,
        "skipped": 0,
        "generated": 0,
        "group_b_attempted": 0,
        "group_c_attempted": 0,
    }

    for row in tqdm(pending_rows):
        is_candidate, repair_mode, skip_reason = classify_candidate_row(row)

        if not is_candidate:
            counts["skipped"] += 1
            output_row = build_attempt_output_row(
                source_row=row,
                repair_request=None,
                repair_result=None,
                intent_representation_used=row.get("intent_representation") if isinstance(row.get("intent_representation"), dict) else None,
                repair_mode=repair_mode,
                status="skipped",
                skip_reason=skip_reason,
                repair_model=args.repair_model_name,
                intent_mode=args.intent_mode,
                repair_context_hash=repair_context_hash,
            )
            append_jsonl(args.output_path, output_row)
            continue

        counts["attempted"] += 1
        if row.get("evaluation_group") == "B_wrong_executable":
            counts["group_b_attempted"] += 1
        elif row.get("evaluation_group") == "C_non_executable":
            counts["group_c_attempted"] += 1

        intent_representation = (
            row.get("intent_representation")
            if isinstance(row.get("intent_representation"), dict)
            else get_cached_intent(intent_cache, row)
        )

        repair_request = None
        repair_result = None

        if intent_representation is None and (
            repair_mode != "non_executable"
            or args.semantic_repair_framework in SCHEMA_ANNOTATION_FRAMEWORKS
        ):
            if args.require_intent_cache or decomposer is None:
                repair_result = None
            else:
                try:
                    intent_representation = decomposer.decompose(str(row.get("question") or ""))
                except Exception:
                    intent_representation = None

        if repair_mode == "non_executable" and args.semantic_repair_framework in SCHEMA_ANNOTATION_FRAMEWORKS:
            if schema_store is None:
                chain_result = {
                    "initial_repair_mode": "non_executable",
                    "stop_reason": "reverification_failed_or_abstained",
                    "final_repaired_sql": None,
                    "final_sql_source": "original_generated_sql",
                    "scope_check_status": None,
                    "scope_check_error": f"{args.semantic_repair_framework} requires schema annotations",
                    "num_repair_attempts": 0,
                    "repair_attempt_sequence": [],
                }
            else:
                chain_result = run_non_executable_then_semantic_repair_chain(
                    row=row,
                    schema_text=schema_text,
                    schema_store=schema_store,
                    repair_generate_fn=repair_generate_fn,
                    verifier_generate_fn=verifier_generate_fn or (lambda _prompt: ""),
                    intent_representation=intent_representation if isinstance(intent_representation, dict) else None,
                    profile_mode=args.profile_mode,
                    probing_mode=args.probing_mode,
                    max_probes=args.max_probes,
                    semantic_followup_framework=args.semantic_repair_framework,
                    accept_execution_repair_without_reverification=args.semantic_repair_framework == "no_reverification",
                )

            repaired_sql = chain_result.get("final_repaired_sql")
            repair_result = (
                SemanticRepairResult(
                    status="success",
                    repaired_sql=str(repaired_sql),
                    edit_summary="Execution-first non-executable repair chain final SQL.",
                    confidence=None,
                    raw_output=None,
                    error=None,
                )
                if repaired_sql
                else None
            )
            output_row = build_attempt_output_row(
                source_row=row,
                repair_request=None,
                repair_result=repair_result,
                intent_representation_used=intent_representation if isinstance(intent_representation, dict) else None,
                repair_mode="non_executable",
                status="success",
                skip_reason=None,
                repair_model=args.repair_model_name,
                intent_mode=args.intent_mode,
                repair_context_hash=repair_context_hash,
            )
            output_row.update(chain_result)
            output_row["semantic_repair_framework"] = args.semantic_repair_framework
            output_row["verifier_model"] = args.verifier_model_name
            output_row["verifier_backend"] = args.verifier_backend
            output_row["profile_mode"] = args.profile_mode
            output_row["probing_mode"] = args.probing_mode
            output_row["max_probes"] = args.max_probes
            append_jsonl(args.output_path, output_row)

            if repaired_sql:
                counts["generated"] += 1

            continue

        if repair_mode == "semantic" and args.semantic_repair_framework in SCHEMA_ANNOTATION_FRAMEWORKS:
            if schema_store is None:
                chain_result = {
                    "stop_reason": "reverification_failed_or_abstained",
                    "final_repaired_sql": None,
                    "final_sql_source": "original_generated_sql",
                    "scope_check_status": None,
                    "scope_check_error": f"{args.semantic_repair_framework} requires schema annotations",
                    "num_repair_attempts": 0,
                    "repair_attempt_sequence": [],
                }
            else:
                if args.semantic_repair_framework == "generic_chain":
                    assert verifier_generate_fn is not None
                    chain_result = run_generic_semantic_repair_chain(
                        row=row,
                        schema_text=schema_text,
                        schema_store=schema_store,
                        repair_generate_fn=repair_generate_fn,
                        verifier_generate_fn=verifier_generate_fn,
                        profile_mode=args.profile_mode,
                        probing_mode=args.probing_mode,
                        max_probes=args.max_probes,
                    )
                elif args.semantic_repair_framework == "no_reverification":
                    chain_result = run_specialized_first_repair_no_reverification(
                        row=row,
                        schema_text=schema_text,
                        schema_store=schema_store,
                        repair_generate_fn=repair_generate_fn,
                        profile_mode=args.profile_mode,
                    )
                else:
                    assert verifier_generate_fn is not None
                    chain_result = run_specialized_semantic_repair_chain(
                        row=row,
                        schema_text=schema_text,
                        schema_store=schema_store,
                        repair_generate_fn=repair_generate_fn,
                        verifier_generate_fn=verifier_generate_fn,
                        profile_mode=args.profile_mode,
                        probing_mode=args.probing_mode,
                        max_probes=args.max_probes,
                    )

            repaired_sql = chain_result.get("final_repaired_sql")
            repair_result = (
                SemanticRepairResult(
                    status="success",
                    repaired_sql=str(repaired_sql),
                    edit_summary=f"{args.semantic_repair_framework} semantic repair chain final SQL.",
                    confidence=None,
                    raw_output=None,
                    error=None,
                )
                if repaired_sql
                else None
            )
            output_row = build_attempt_output_row(
                source_row=row,
                repair_request=None,
                repair_result=repair_result,
                intent_representation_used=intent_representation if isinstance(intent_representation, dict) else None,
                repair_mode=args.semantic_repair_framework,
                status="success",
                skip_reason=None,
                repair_model=args.repair_model_name,
                intent_mode=args.intent_mode,
                repair_context_hash=repair_context_hash,
            )
            output_row.update(chain_result)
            output_row["semantic_repair_framework"] = args.semantic_repair_framework
            output_row["verifier_model"] = args.verifier_model_name
            output_row["verifier_backend"] = args.verifier_backend
            output_row["profile_mode"] = args.profile_mode
            output_row["probing_mode"] = args.probing_mode
            output_row["max_probes"] = args.max_probes
            append_jsonl(args.output_path, output_row)

            if repaired_sql:
                counts["generated"] += 1

            continue

        if repair_mode == "semantic":
            repair_request = build_semantic_repair_request(
                row=row,
                schema_text=schema_text,
            )
            repair_result = repair_semantic_sql(repair_request, repair_generate_fn)
        elif repair_mode == "non_executable":
            repair_request = build_non_executable_repair_request(
                row=row,
                schema_text=schema_text,
                intent_representation=intent_representation if isinstance(intent_representation, dict) else None,
            )
            repair_result = repair_non_executable_sql(repair_request, repair_generate_fn)
        else:
            repair_result = None

        if repair_result is not None and repair_result.status == "success":
            counts["generated"] += 1

        output_row = build_attempt_output_row(
            source_row=row,
            repair_request=repair_request,
            repair_result=repair_result,
            intent_representation_used=intent_representation if isinstance(intent_representation, dict) else None,
            repair_mode=repair_mode,
            status="success",
            skip_reason=None,
            repair_model=args.repair_model_name,
            intent_mode=args.intent_mode,
            repair_context_hash=repair_context_hash,
        )
        append_jsonl(args.output_path, output_row)

    print(f"Saved repair outputs to: {args.output_path}")
    print(
        "Repair summary: "
        f"attempted={counts['attempted']}, "
        f"group_b_attempted={counts['group_b_attempted']}, "
        f"group_c_attempted={counts['group_c_attempted']}, "
        f"skipped={counts['skipped']}, "
        f"generated={counts['generated']}"
    )


if __name__ == "__main__":
    main()
