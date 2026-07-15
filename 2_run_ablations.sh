#!/usr/bin/env bash
set -euo pipefail

MODE="labeled"
if [[ "${1:-}" == "--mode" ]]; then
  MODE="${2:-}"
  shift 2
fi
if [[ $# -ne 0 || ! "$MODE" =~ ^(labeled|official-test|all)$ ]]; then
  echo "Usage: $0 [--mode labeled|official-test|all]" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
cd "$PROJECT_ROOT"

RUN_ID="${RUN_ID:-eval_publication_$(date +%Y%m%d_%H%M%S)}"
SPLIT="validation"
PROMPT_SETTING="few_shot"
BACKEND="ollama"

BASELINE_MODEL="${BASELINE_MODEL:-qwen2.5-coder:7b-instruct}"
POSTGEN_MODEL="${POSTGEN_MODEL:-llama3.1:8b}"
TEMPERATURE="${TEMPERATURE:-0}"
SEED="${SEED:-42}"
NUM_CTX="${NUM_CTX:-8192}"
TIMEOUT="${TIMEOUT:-300}"

BASELINE_MAX_NEW_TOKENS="${BASELINE_MAX_NEW_TOKENS:-128}"
INTENT_NUM_PREDICT="${INTENT_NUM_PREDICT:-1024}"
VERIFY_NUM_PREDICT="${VERIFY_NUM_PREDICT:-1024}"
REPAIR_NUM_PREDICT="${REPAIR_NUM_PREDICT:-768}"
REFINE_NUM_PREDICT="${REFINE_NUM_PREDICT:-768}"
MAX_PROBES="${MAX_PROBES:-7}"
WORKERS="4"
GENERIC_REFINE_WORKERS="${GENERIC_REFINE_WORKERS:-2}"
RUN_REPAIR_STRATEGY_ABLATION="${RUN_REPAIR_STRATEGY_ABLATION:-1}"
SFT_ADAPTER_PATH="${SFT_ADAPTER_PATH:-}"
RL_ADAPTER_PATH="${RL_ADAPTER_PATH:-}"
SFT_ADAPTER_URL="${SFT_ADAPTER_URL:-}"
RL_ADAPTER_URL="${RL_ADAPTER_URL:-}"
SFT_ADAPTER_SHA256="${SFT_ADAPTER_SHA256:-}"
RL_ADAPTER_SHA256="${RL_ADAPTER_SHA256:-}"

check_ollama_model() {
  local model_name="$1"
  python3 - "$model_name" <<'PY'
import json
import sys
import urllib.request

with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=10) as response:
    payload = json.loads(response.read().decode("utf-8"))
names = {str(item.get("name")) for item in payload.get("models", []) if isinstance(item, dict)}
if sys.argv[1] not in names:
    raise SystemExit(f"Ollama model '{sys.argv[1]}' is not available.")
PY
}

run_official_test() {
  local test_dir="${OUT_ROOT}/official_test"
  local test_input="${test_dir}/booksql_official_test.jsonl"
  local baseline_jsonl="${test_dir}/qwen_few_shot_test.jsonl"
  local routed_jsonl="${test_dir}/routed_candidates.jsonl"
  local intent_jsonl="${test_dir}/intents_nl_only.jsonl"
  local verify_jsonl="${test_dir}/full_verify.jsonl"
  local repair_queue_jsonl="${test_dir}/full_repair_queue.jsonl"
  local skipped_jsonl="${test_dir}/full_skipped.jsonl"
  local repair_jsonl="${test_dir}/full_repairs.jsonl"

  mkdir -p "$test_dir"
  command -v python3 >/dev/null 2>&1 || { echo "Required command not found: python3" >&2; exit 1; }
  command -v tee >/dev/null 2>&1 || { echo "Required command not found: tee" >&2; exit 1; }
  [[ -f "$DB_PATH" && -f "$SCHEMA_TXT" && -f "$SCHEMA_JSON" && -f "$DATA_PATH" ]] || { echo "BookSQL train/schema/database assets are required." >&2; exit 1; }
  check_ollama_model "$BASELINE_MODEL"
  check_ollama_model "$POSTGEN_MODEL"

  run_cmd python3 scripts/prepare_official_booksql_test.py --output-path "$test_input"
  run_cmd python3 -m src.baseline.baseline_runner \
    --model qwen --backend ollama --ollama-model-name "$BASELINE_MODEL" \
    --temperature "$TEMPERATURE" --seed "$SEED" --timeout "$TIMEOUT" \
    --max-new-tokens "$BASELINE_MAX_NEW_TOKENS" --split test \
    --prompt-setting "$PROMPT_SETTING" --allow-missing-gold-sql \
    --data-path "$test_input" --few-shot-data-path "$DATA_PATH" \
    --db-path "$DB_PATH" --schema-path "$SCHEMA_TXT" --output-path "$baseline_jsonl"
  run_cmd python3 scripts/prepare_official_test_candidates.py \
    --input-jsonl "$baseline_jsonl" --output-jsonl "$routed_jsonl" --db-path "$DB_PATH"
  run_cmd python3 scripts/precompute_finverisql_intents.py \
    --input-path "$routed_jsonl" --output-path "$intent_jsonl" --schema-path "$SCHEMA_JSON" \
    --intent-mode nl_only --backend ollama --model-name "$POSTGEN_MODEL" \
    --temperature "$TEMPERATURE" --num-predict "$INTENT_NUM_PREDICT" --timeout "$TIMEOUT"
  run_cmd python3 scripts/run_finverisql_verify.py \
    --input-path "$routed_jsonl" --output-path "$verify_jsonl" \
    --repair-output-path "$repair_queue_jsonl" --skipped-output-path "$skipped_jsonl" \
    --schema-path "$SCHEMA_JSON" --profile-mode compact --intent-mode nl_only \
    --probing-mode probe --max-probes "$MAX_PROBES" --backend ollama --model-name "$POSTGEN_MODEL" \
    --temperature "$TEMPERATURE" --num-predict "$VERIFY_NUM_PREDICT" --timeout "$TIMEOUT" \
    --intent-cache-path "$intent_jsonl" --require-intent-cache
  run_cmd python3 scripts/run_finverisql_repair.py \
    --input-path "$verify_jsonl" --output-path "$repair_jsonl" --schema-path "$SCHEMA_JSON" \
    --semantic-repair-framework specialized_chain --intent-mode nl_only \
    --intent-cache-path "$intent_jsonl" --require-intent-cache \
    --repair-backend ollama --repair-model-name "$POSTGEN_MODEL" \
    --verifier-backend ollama --verifier-model-name "$POSTGEN_MODEL" \
    --profile-mode compact --probing-mode probe --max-probes "$MAX_PROBES" \
    --temperature "$TEMPERATURE" --num-predict "$REPAIR_NUM_PREDICT" --timeout "$TIMEOUT"
  run_cmd python3 scripts/export_official_test_submission.py \
    --input-jsonl "$repair_jsonl" --submission-csv "$test_dir/submission.csv" \
    --predictions-jsonl "$test_dir/final_predictions.jsonl" \
    --table-md "$test_dir/official_test_table.md" --summary-json "$test_dir/official_test_summary.json"
}

if [[ "$MODE" == "official-test" ]]; then
  OUT_ROOT="data/outputs/finverisql/${RUN_ID}"
  DB_PATH="data/booksql/accounting.sqlite"
  SCHEMA_TXT="data/booksql/schema.txt"
  SCHEMA_JSON="data/booksql/schema_annotations.json"
  DATA_PATH="data/booksql/booksql_normalized.jsonl"
  run_cmd() { echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] $*"; "$@"; }
  run_official_test
  exit 0
fi

DATA_PATH="data/booksql/booksql_normalized.jsonl"
DB_PATH="data/booksql/accounting.sqlite"
SCHEMA_TXT="data/booksql/schema.txt"
SCHEMA_JSON="data/booksql/schema_annotations.json"

OUT_ROOT="data/outputs/finverisql/${RUN_ID}"
PUB_DIR="${OUT_ROOT}/publication_tables"
DEBUG_DIR="${OUT_ROOT}/debug"
TABLE_DEBUG_DIR="${DEBUG_DIR}/tables"
LOG_PATH="${DEBUG_DIR}/run.log"

BASELINE_DIR="${DEBUG_DIR}/baseline"
MAIN_DIR="${DEBUG_DIR}/main_comparison"
ABLATION_DIR="${DEBUG_DIR}/internal_ablation"
INTENT_DIR="${DEBUG_DIR}/intents"

mkdir -p \
  "$PUB_DIR" \
  "$TABLE_DEBUG_DIR" \
  "$BASELINE_DIR" \
  "$MAIN_DIR" \
  "$ABLATION_DIR" \
  "$INTENT_DIR"

exec > >(tee -a "$LOG_PATH") 2>&1

echo "Run ID: ${RUN_ID}"
echo "Project root: ${PROJECT_ROOT}"
echo "Output root: ${OUT_ROOT}"

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "Required file not found: $path" >&2
    exit 1
  fi
}

require_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "Required command not found: $name" >&2
    exit 1
  fi
}

run_cmd() {
  echo
  echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] $*"
  "$@"
}

check_ollama_model() {
  local model_name="$1"
  python3 - "$model_name" <<'PY'
import json
import sys
import urllib.request

model_name = sys.argv[1]
try:
    with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
except Exception as exc:
    raise SystemExit(f"Ollama server is not reachable at localhost:11434: {exc}")

models = payload.get("models") or []
names = {str(item.get("name")) for item in models if isinstance(item, dict)}
if model_name not in names:
    available = ", ".join(sorted(names)) or "<none>"
    raise SystemExit(
        f"Ollama model '{model_name}' is not available. "
        f"Available models: {available}"
    )
PY
}

require_command python3
require_command tee
require_file "$DATA_PATH"
require_file "$DB_PATH"
require_file "$SCHEMA_TXT"
require_file "$SCHEMA_JSON"
check_ollama_model "$BASELINE_MODEL"
check_ollama_model "$POSTGEN_MODEL"

LIMIT_ARGS=()
if [[ -n "${LIMIT:-}" ]]; then
  LIMIT_ARGS=(--limit "$LIMIT")
  echo "LIMIT enabled: ${LIMIT}"
fi

python3 - "$DEBUG_DIR/run_metadata.json" <<PY
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

metadata_path = pathlib.Path(sys.argv[1])

def git_output(args):
    try:
        return subprocess.check_output(args, text=True).strip()
    except Exception:
        return None

metadata = {
    "run_id": "${RUN_ID}",
    "created_at_utc": datetime.now(timezone.utc).isoformat(),
    "split": "${SPLIT}",
    "prompt_setting": "${PROMPT_SETTING}",
    "inference_backend": "${BACKEND}",
    "baseline_model_name": "${BASELINE_MODEL}",
    "post_generation_model_name": "${POSTGEN_MODEL}",
    "temperature": float("${TEMPERATURE}"),
    "seed": int("${SEED}"),
    "num_ctx": int("${NUM_CTX}"),
    "timeout": int("${TIMEOUT}"),
    "baseline_max_new_tokens": int("${BASELINE_MAX_NEW_TOKENS}"),
    "intent_num_predict": int("${INTENT_NUM_PREDICT}"),
    "verify_num_predict": int("${VERIFY_NUM_PREDICT}"),
    "repair_num_predict": int("${REPAIR_NUM_PREDICT}"),
    "refine_num_predict": int("${REFINE_NUM_PREDICT}"),
    "max_probes": int("${MAX_PROBES}"),
    "workers": int("${WORKERS}"),
    "limit": None if "${LIMIT:-}" == "" else int("${LIMIT:-0}"),
    "data_path": "${DATA_PATH}",
    "db_path": "${DB_PATH}",
    "schema_text_path": "${SCHEMA_TXT}",
    "schema_annotations_path": "${SCHEMA_JSON}",
    "git_commit": git_output(["git", "rev-parse", "HEAD"]),
    "git_status_short": git_output(["git", "status", "--short"]),
    "note": "num_ctx is recorded for metadata; current Python Ollama calls do not pass num_ctx.",
}
metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Wrote run metadata to {metadata_path}")
PY

BASELINE_JSONL="${BASELINE_DIR}/qwen_few_shot_validation.jsonl"
BASELINE_EVAL_JSONL="${BASELINE_DIR}/qwen_few_shot_validation_evaluated.jsonl"
BASELINE_METRICS_JSON="${BASELINE_DIR}/qwen_few_shot_validation_metrics.json"
BASELINE_ASA_JSON="${BASELINE_DIR}/qwen_few_shot_validation_asa_metrics.json"
BASELINE_ASA_MD="${BASELINE_DIR}/qwen_few_shot_validation_asa_metrics.md"
BASELINE_ASA_ROWS="${BASELINE_DIR}/qwen_few_shot_validation_asa_rows.jsonl"

run_cmd python3 -m src.baseline.baseline_runner \
  --model qwen \
  --backend ollama \
  --ollama-model-name "$BASELINE_MODEL" \
  --temperature "$TEMPERATURE" \
  --seed "$SEED" \
  --timeout "$TIMEOUT" \
  --max-new-tokens "$BASELINE_MAX_NEW_TOKENS" \
  --split "$SPLIT" \
  --prompt-setting "$PROMPT_SETTING" \
  --data-path "$DATA_PATH" \
  --db-path "$DB_PATH" \
  --schema-path "$SCHEMA_TXT" \
  --output-path "$BASELINE_JSONL" \
  "${LIMIT_ARGS[@]}"

run_cmd python3 -m src.eval.evaluate_baseline_sql \
  --input-jsonl "$BASELINE_JSONL" \
  --output-jsonl "$BASELINE_EVAL_JSONL" \
  --metrics-json "$BASELINE_METRICS_JSON" \
  --db-path "$DB_PATH" \
  --workers "$WORKERS"

run_cmd python3 -m src.eval.evaluate_asa \
  --before-jsonl "$BASELINE_EVAL_JSONL" \
  --schema-path "$SCHEMA_JSON" \
  --output-json "$BASELINE_ASA_JSON" \
  --output-md "$BASELINE_ASA_MD" \
  --row-output-jsonl "$BASELINE_ASA_ROWS"

run_generic_refine() {
  local key="$1"
  local module="$2"
  local out_dir="${MAIN_DIR}/${key}"
  mkdir -p "$out_dir"

  local refine_jsonl="${out_dir}/${key}.jsonl"
  local final_eval_jsonl="${out_dir}/${key}_final_evaluated.jsonl"
  local final_metrics_json="${out_dir}/${key}_final_metrics.json"
  local final_metrics_md="${out_dir}/${key}_final_metrics.md"
  local adapted_jsonl="${out_dir}/${key}_adapted_final_input.jsonl"
  local asa_json="${out_dir}/${key}_asa_metrics.json"
  local asa_md="${out_dir}/${key}_asa_metrics.md"
  local asa_rows="${out_dir}/${key}_asa_rows.jsonl"

  run_cmd python3 -m "$module" \
    --input-path "$BASELINE_EVAL_JSONL" \
    --output-path "$refine_jsonl" \
    --schema-path "$SCHEMA_TXT" \
    --model-name "$POSTGEN_MODEL" \
    --backend ollama \
    --temperature "$TEMPERATURE" \
    --num-predict "$REFINE_NUM_PREDICT" \
    --timeout "$TIMEOUT" \
    --workers "$GENERIC_REFINE_WORKERS"

  run_cmd python3 -m src.eval.evaluate_final_sql \
    --input-jsonl "$refine_jsonl" \
    --output-jsonl "$final_eval_jsonl" \
    --metrics-json "$final_metrics_json" \
    --metrics-md "$final_metrics_md" \
    --adapted-jsonl "$adapted_jsonl" \
    --db-path "$DB_PATH" \
    --workers "$WORKERS"

  run_cmd python3 -m src.eval.evaluate_asa \
    --before-jsonl "$BASELINE_EVAL_JSONL" \
    --after-jsonl "$final_eval_jsonl" \
    --schema-path "$SCHEMA_JSON" \
    --output-json "$asa_json" \
    --output-md "$asa_md" \
    --row-output-jsonl "$asa_rows"
}

run_generic_refine "generic_self_refine" "src.baseline.generic_refine.self_refine"
run_generic_refine "generic_execution_guided_refine" "src.baseline.generic_refine.execution_guided"

INTENT_NL_ONLY_JSONL="${INTENT_DIR}/intents_nl_only.jsonl"
run_cmd python3 scripts/precompute_finverisql_intents.py \
  --input-path "$BASELINE_EVAL_JSONL" \
  --output-path "$INTENT_NL_ONLY_JSONL" \
  --schema-path "$SCHEMA_JSON" \
  --intent-mode nl_only \
  --backend ollama \
  --model-name "$POSTGEN_MODEL" \
  --temperature "$TEMPERATURE" \
  --num-predict "$INTENT_NUM_PREDICT" \
  --timeout "$TIMEOUT"

run_finverisql_variant() {
  local key="$1"
  local intent_mode="$2"
  local profile_mode="$3"
  local probing_mode="$4"
  local repair_framework="$5"
  local out_dir="${ABLATION_DIR}/${key}"
  mkdir -p "$out_dir"

  local verify_jsonl="${out_dir}/${key}_verify.jsonl"
  local repair_queue_jsonl="${out_dir}/${key}_repair_queue.jsonl"
  local skipped_jsonl="${out_dir}/${key}_skipped.jsonl"
  local diagnostics_md="${out_dir}/${key}_verifier_diagnostics.md"
  local repair_jsonl="${out_dir}/${key}_repairs.jsonl"
  local final_eval_jsonl="${out_dir}/${key}_final_evaluated.jsonl"
  local final_metrics_json="${out_dir}/${key}_final_metrics.json"
  local final_metrics_md="${out_dir}/${key}_final_metrics.md"
  local adapted_jsonl="${out_dir}/${key}_adapted_final_input.jsonl"
  local asa_json="${out_dir}/${key}_asa_metrics.json"
  local asa_md="${out_dir}/${key}_asa_metrics.md"
  local asa_rows="${out_dir}/${key}_asa_rows.jsonl"

  local intent_cache_args=()
  if [[ "$intent_mode" == "nl_only" ]]; then
    intent_cache_args=(--intent-cache-path "$INTENT_NL_ONLY_JSONL" --require-intent-cache)
  fi

  run_cmd python3 scripts/run_finverisql_verify.py \
    --input-path "$BASELINE_EVAL_JSONL" \
    --output-path "$verify_jsonl" \
    --repair-output-path "$repair_queue_jsonl" \
    --skipped-output-path "$skipped_jsonl" \
    --schema-path "$SCHEMA_JSON" \
    --profile-mode "$profile_mode" \
    --intent-mode "$intent_mode" \
    --probing-mode "$probing_mode" \
    --max-probes "$MAX_PROBES" \
    --backend ollama \
    --model-name "$POSTGEN_MODEL" \
    --temperature "$TEMPERATURE" \
    --num-predict "$VERIFY_NUM_PREDICT" \
    --timeout "$TIMEOUT" \
    "${intent_cache_args[@]}"

  run_cmd python3 -m src.eval.evaluate_verifier_diagnostics \
    --input-path "$verify_jsonl" \
    --output-md "$diagnostics_md"

  run_cmd python3 scripts/run_finverisql_repair.py \
    --input-path "$verify_jsonl" \
    --output-path "$repair_jsonl" \
    --schema-path "$SCHEMA_JSON" \
    --semantic-repair-framework "$repair_framework" \
    --intent-mode "$intent_mode" \
    "${intent_cache_args[@]}" \
    --repair-backend ollama \
    --repair-model-name "$POSTGEN_MODEL" \
    --verifier-backend ollama \
    --verifier-model-name "$POSTGEN_MODEL" \
    --profile-mode "$profile_mode" \
    --probing-mode "$probing_mode" \
    --max-probes "$MAX_PROBES" \
    --temperature "$TEMPERATURE" \
    --num-predict "$REPAIR_NUM_PREDICT" \
    --timeout "$TIMEOUT"

  run_cmd python3 -m src.eval.evaluate_final_sql \
    --input-jsonl "$repair_jsonl" \
    --output-jsonl "$final_eval_jsonl" \
    --metrics-json "$final_metrics_json" \
    --metrics-md "$final_metrics_md" \
    --adapted-jsonl "$adapted_jsonl" \
    --db-path "$DB_PATH" \
    --workers "$WORKERS"

  run_cmd python3 -m src.eval.evaluate_asa \
    --before-jsonl "$BASELINE_EVAL_JSONL" \
    --after-jsonl "$final_eval_jsonl" \
    --schema-path "$SCHEMA_JSON" \
    --output-json "$asa_json" \
    --output-md "$asa_md" \
    --row-output-jsonl "$asa_rows"
}

run_finverisql_variant "full" "nl_only" "compact" "probe" "specialized_chain"
run_finverisql_variant "wo_intent_decomposer" "none" "compact" "probe" "specialized_chain"
run_finverisql_variant "direct_only" "nl_only" "compact" "none" "specialized_chain"
run_finverisql_variant "wo_compact_semantic_profile" "nl_only" "ast" "probe" "specialized_chain"
run_finverisql_variant "wo_scope_constraints" "nl_only" "compact" "probe" "generic_chain"
run_finverisql_variant "wo_reverification_loop" "nl_only" "compact" "probe" "no_reverification"

resolve_released_adapter() {
  local name="$1"
  local path="$2"
  local url="$3"
  local expected_sha="$4"
  local download_dir="${OUT_ROOT}/released_adapters"
  if [[ -n "$path" && -d "$path" ]]; then
    printf '%s\n' "$path"
    return
  fi
  if [[ -z "$url" || -z "$expected_sha" ]]; then
    echo "${name} adapter is required. Set ${name}_ADAPTER_PATH or ${name}_ADAPTER_URL and ${name}_ADAPTER_SHA256." >&2
    exit 1
  fi
  require_command curl
  require_command shasum
  require_command tar
  mkdir -p "$download_dir"
  local archive="${download_dir}/${name}.tar.gz"
  local destination="${download_dir}/${name}"
  if [[ ! -f "$archive" ]]; then
    curl --fail --location --retry 3 --output "$archive" "$url"
  fi
  local actual_sha
  actual_sha="$(shasum -a 256 "$archive" | awk '{print $1}')"
  if [[ "$actual_sha" != "$expected_sha" ]]; then
    echo "Checksum mismatch for ${name} adapter archive." >&2
    exit 1
  fi
  if [[ ! -d "$destination" ]]; then
    mkdir -p "$destination"
    tar -xzf "$archive" -C "$destination" --strip-components=1
  fi
  printf '%s\n' "$destination"
}

if [[ "$RUN_REPAIR_STRATEGY_ABLATION" == "1" ]]; then
  SFT_ADAPTER_PATH="$(resolve_released_adapter SFT "$SFT_ADAPTER_PATH" "$SFT_ADAPTER_URL" "$SFT_ADAPTER_SHA256")"
  RL_ADAPTER_PATH="$(resolve_released_adapter RL "$RL_ADAPTER_PATH" "$RL_ADAPTER_URL" "$RL_ADAPTER_SHA256")"
  REPAIR_ABLATION_DIR="${DEBUG_DIR}/repair_strategy_ablation/full_fixed_verifier"
  run_cmd python3 scripts/run_repair_strategy_ablation.py \
    --fixed-verifier-jsonl "${ABLATION_DIR}/full/full_verify.jsonl" \
    --baseline-eval-jsonl "$BASELINE_EVAL_JSONL" \
    --output-dir "$REPAIR_ABLATION_DIR" \
    --prompt-model-name "$POSTGEN_MODEL" \
    --sft-adapter-path "$SFT_ADAPTER_PATH" \
    --rl-adapter-path "$RL_ADAPTER_PATH" \
    --temperature "$TEMPERATURE" --num-predict "$REPAIR_NUM_PREDICT" \
    --timeout "$TIMEOUT" --workers "$WORKERS"
fi

MANIFEST_JSON="${DEBUG_DIR}/run_manifest.json"
python - "$MANIFEST_JSON" "$OUT_ROOT" <<'PY'
import json
import pathlib
import sys

manifest_path = pathlib.Path(sys.argv[1])
out_root = pathlib.Path(sys.argv[2])
debug = out_root / "debug"

def s(path: pathlib.Path) -> str:
    return str(path)

def main_system(key, label, kind, metrics, asa):
    return {
        "key": key,
        "label": label,
        "kind": kind,
        "metrics_json": s(metrics),
        "asa_metrics_json": s(asa),
    }

def ablation(key, label):
    base = debug / "internal_ablation" / key
    return {
        "key": key,
        "label": label,
        "verify_jsonl": s(base / f"{key}_verify.jsonl"),
        "metrics_json": s(base / f"{key}_final_metrics.json"),
        "asa_metrics_json": s(base / f"{key}_asa_metrics.json"),
    }

manifest = {
    "main_systems": [
        main_system(
            "generator_only",
            "Generator only",
            "generator",
            debug / "baseline" / "qwen_few_shot_validation_metrics.json",
            debug / "baseline" / "qwen_few_shot_validation_asa_metrics.json",
        ),
        main_system(
            "generic_self_refine",
            "Generator + generic self-refine",
            "repair",
            debug / "main_comparison" / "generic_self_refine" / "generic_self_refine_final_metrics.json",
            debug / "main_comparison" / "generic_self_refine" / "generic_self_refine_asa_metrics.json",
        ),
        main_system(
            "generic_execution_guided_refine",
            "Generator + generic execution-guided refine",
            "repair",
            debug / "main_comparison" / "generic_execution_guided_refine" / "generic_execution_guided_refine_final_metrics.json",
            debug / "main_comparison" / "generic_execution_guided_refine" / "generic_execution_guided_refine_asa_metrics.json",
        ),
        main_system(
            "finverisql_full",
            "Generator + FinVeriSQL full",
            "repair",
            debug / "internal_ablation" / "full" / "full_final_metrics.json",
            debug / "internal_ablation" / "full" / "full_asa_metrics.json",
        ),
    ],
    "ablations": [
        ablation("full", "FinVeriSQL full"),
        ablation("wo_intent_decomposer", "w/o Intent Decomposer"),
        ablation("direct_only", "w/o Probing / direct only"),
        ablation("wo_compact_semantic_profile", "w/o Compact Semantic Profile"),
        ablation("wo_scope_constraints", "w/o Scope Constraints in Repair"),
        ablation("wo_reverification_loop", "w/o re-verification loop"),
    ],
}

manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Wrote run manifest to {manifest_path}")
PY

run_cmd python3 scripts/build_publication_tables.py \
  --manifest "$MANIFEST_JSON" \
  --publication-dir "$PUB_DIR" \
  --debug-dir "$TABLE_DEBUG_DIR"

echo
echo "Completed run: ${RUN_ID}"
echo "Publication tables:"
echo "  ${PUB_DIR}/main_comparison_table.md"
echo "  ${PUB_DIR}/internal_ablation_table.md"
echo "Debug artifacts: ${DEBUG_DIR}"

if [[ "$MODE" == "all" ]]; then
  run_official_test
fi
