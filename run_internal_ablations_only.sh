#!/usr/bin/env bash
# Run only the five internal FinVeriSQL ablations for an existing labeled run.
# This intentionally never invokes the baseline, main-comparison, or full
# FinVeriSQL paths owned by 2_run_ablations.sh.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

: "${RUN_ID:?Set RUN_ID to the existing labeled evaluation run.}"

POSTGEN_MODEL="${POSTGEN_MODEL:-llama3.1:8b}"
TEMPERATURE="${TEMPERATURE:-0}"
TIMEOUT="${TIMEOUT:-300}"
INTENT_NUM_PREDICT="${INTENT_NUM_PREDICT:-1024}"
VERIFY_NUM_PREDICT="${VERIFY_NUM_PREDICT:-1024}"
REPAIR_NUM_PREDICT="${REPAIR_NUM_PREDICT:-768}"
MAX_PROBES="${MAX_PROBES:-7}"
FINVERISQL_VERIFY_WORKERS="${FINVERISQL_VERIFY_WORKERS:-1}"
FINVERISQL_REPAIR_WORKERS="${FINVERISQL_REPAIR_WORKERS:-1}"
EVALUATION_WORKERS="${EVALUATION_WORKERS:-4}"

RUN_ROOT="data/outputs/finverisql/${RUN_ID}"
DEBUG_DIR="${RUN_ROOT}/debug"
BASELINE_EVAL_JSONL="${DEBUG_DIR}/baseline/qwen_few_shot_validation_evaluated.jsonl"
INTENT_CACHE_JSONL="${DEBUG_DIR}/intents/intents_nl_only.jsonl"
ABLATION_DIR="${DEBUG_DIR}/internal_ablation"
DB_PATH="data/booksql/accounting.sqlite"
SCHEMA_JSON="data/booksql/schema_annotations.json"
ROOT_LOG="${ABLATION_DIR}/run_internal_ablations.log"

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "Required file not found: $path" >&2
    exit 1
  fi
}

run_cmd_logged() {
  local log_path="$1"
  shift
  mkdir -p "$(dirname "$log_path")"
  {
    echo
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] $*"
    "$@"
  } 2>&1 | tee -a "$log_path"
}

require_file "$BASELINE_EVAL_JSONL"
require_file "$DB_PATH"
require_file "$SCHEMA_JSON"
mkdir -p "$ABLATION_DIR"
exec > >(tee -a "$ROOT_LOG") 2>&1

echo "Run ID: ${RUN_ID}"
echo "Running internal ablations only. Full FinVeriSQL is not invoked."
echo "Verifier workers: ${FINVERISQL_VERIFY_WORKERS}"
echo "Repair workers: ${FINVERISQL_REPAIR_WORKERS}"

# The intent precompute script is resume-aware: it adds only missing cache rows.
run_cmd_logged "$ROOT_LOG" python3 scripts/precompute_finverisql_intents.py \
  --input-path "$BASELINE_EVAL_JSONL" \
  --output-path "$INTENT_CACHE_JSONL" \
  --schema-path "$SCHEMA_JSON" \
  --intent-mode nl_only \
  --backend ollama \
  --model-name "$POSTGEN_MODEL" \
  --temperature "$TEMPERATURE" \
  --num-predict "$INTENT_NUM_PREDICT" \
  --timeout "$TIMEOUT"

run_variant() {
  local key="$1"
  local intent_mode="$2"
  local profile_mode="$3"
  local probing_mode="$4"
  local repair_framework="$5"
  local out_dir="${ABLATION_DIR}/${key}"
  local stage_log="${out_dir}/run.log"
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

  mkdir -p "$out_dir"
  if [[ "$intent_mode" == "nl_only" ]]; then
    intent_cache_args=(--intent-cache-path "$INTENT_CACHE_JSONL" --require-intent-cache)
  fi

  echo "Running internal ablation: ${key}"
  run_cmd_logged "$stage_log" python3 scripts/run_finverisql_verify.py \
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
    --workers "$FINVERISQL_VERIFY_WORKERS" \
    "${intent_cache_args[@]}"

  run_cmd_logged "$stage_log" python3 -m src.eval.evaluate_verifier_diagnostics \
    --input-path "$verify_jsonl" \
    --output-md "$diagnostics_md"

  run_cmd_logged "$stage_log" python3 scripts/run_finverisql_repair.py \
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
    --timeout "$TIMEOUT" \
    --workers "$FINVERISQL_REPAIR_WORKERS"

  # These evaluators write fresh outputs from this variant's repair JSONL.
  # They do not reuse 2_run_ablations.sh cache manifests or append rows.
  run_cmd_logged "$stage_log" python3 -m src.eval.evaluate_final_sql \
    --input-jsonl "$repair_jsonl" \
    --output-jsonl "$final_eval_jsonl" \
    --metrics-json "$final_metrics_json" \
    --metrics-md "$final_metrics_md" \
    --adapted-jsonl "$adapted_jsonl" \
    --db-path "$DB_PATH" \
    --workers "$EVALUATION_WORKERS"

  run_cmd_logged "$stage_log" python3 -m src.eval.evaluate_asa \
    --before-jsonl "$BASELINE_EVAL_JSONL" \
    --after-jsonl "$final_eval_jsonl" \
    --schema-path "$SCHEMA_JSON" \
    --output-json "$asa_json" \
    --output-md "$asa_md" \
    --row-output-jsonl "$asa_rows"
}

run_variant "wo_intent_decomposer" "none" "compact" "probe" "specialized_chain"
run_variant "direct_only" "nl_only" "compact" "none" "specialized_chain"
run_variant "wo_compact_semantic_profile" "nl_only" "ast" "probe" "specialized_chain"
run_variant "wo_scope_constraints" "nl_only" "compact" "probe" "generic_chain"
run_variant "wo_reverification_loop" "nl_only" "compact" "probe" "no_reverification"

echo "Completed internal ablations for ${RUN_ID}."
