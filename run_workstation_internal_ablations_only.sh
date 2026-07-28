#!/usr/bin/env bash
set -euo pipefail

RUN_ID="eval_publication_20260706_154755"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

IMAGE="ollama/ollama:latest"
MODEL="llama3.1:8b"
MODEL_VOLUME="/home/ranjan/finveri_ollama_workspace"
OLLAMA_HOSTS="http://127.0.0.1:11434,http://127.0.0.1:11435"
export OLLAMA_HOSTS
export FINVERISQL_VERIFY_WORKERS=8
export FINVERISQL_REPAIR_WORKERS=8
EVALUATION_WORKERS="${EVALUATION_WORKERS:-8}"

RUN_ROOT="data/outputs/finverisql/${RUN_ID}"
SOURCE_ROOT="${RUN_ROOT}/debug"
OUTPUT_ROOT="${RUN_ROOT}/development_excluded_experiments/internal_ablation"
COHORT_DIR="${OUTPUT_ROOT}/cohort"
COHORT="${COHORT_DIR}/cohort_baseline.jsonl"
INTENTS="${COHORT_DIR}/cohort_intents_nl_only.jsonl"
OLLAMA_MANIFEST="${OUTPUT_ROOT}/ollama_workstation_manifest.json"
BASELINE="${SOURCE_ROOT}/baseline/qwen_few_shot_validation_evaluated.jsonl"
INTENT_SOURCE="${SOURCE_ROOT}/intents/intents_nl_only.jsonl"
DEVELOPMENT_IDS="data/protocol/booksql_validation_development_2000_ids.jsonl"
DB_PATH="data/booksql/accounting.sqlite"
SCHEMA_JSON="data/booksql/schema_annotations.json"

usage() {
  echo "Usage: $0 [--setup-ollama]" >&2
}

docker_value() {
  sudo docker inspect --format "$2" "$1"
}

setup_container() {
  local name="$1" port="$2" gpu="$3" parallel="$4" image_id="$5"
  if sudo docker inspect "$name" >/dev/null 2>&1; then
    [[ "$(docker_value "$name" '{{.Image}}')" == "$image_id" ]] || { echo "$name has an incompatible image." >&2; exit 1; }
    [[ "$(docker_value "$name" '{{(index (index .NetworkSettings.Ports "11434/tcp") 0).HostPort}}')" == "$port" ]] || { echo "$name has an incompatible host port." >&2; exit 1; }
    [[ "$(docker_value "$name" '{{range .Mounts}}{{if eq .Destination "/root/.ollama"}}{{.Source}}{{end}}{{end}}')" == "$MODEL_VOLUME" ]] || { echo "$name has an incompatible model volume." >&2; exit 1; }
    [[ "$(docker_value "$name" '{{range .Config.Env}}{{println .}}{{end}}')" == *"OLLAMA_NUM_PARALLEL=${parallel}"* ]] || { echo "$name has incompatible parallelism." >&2; exit 1; }
    [[ "$(docker_value "$name" '{{range .HostConfig.DeviceRequests}}{{range .DeviceIDs}}{{println .}}{{end}}{{end}}')" == *"$gpu"* ]] || { echo "$name has an incompatible GPU." >&2; exit 1; }
    if [[ "$(docker_value "$name" '{{.State.Running}}')" != "true" ]]; then
      sudo docker start "$name" >/dev/null
    fi
  else
    sudo docker run -d --name "$name" --restart unless-stopped \
      --gpus "device=${gpu}" -p "${port}:11434" \
      -e "OLLAMA_NUM_PARALLEL=${parallel}" \
      -v "${MODEL_VOLUME}:/root/.ollama" "$IMAGE" >/dev/null
  fi
  sudo docker exec "$name" ollama pull "$MODEL" >/dev/null
}

setup_ollama() {
  command -v sudo >/dev/null
  command -v docker >/dev/null
  if sudo docker inspect debug_ollama >/dev/null 2>&1; then
    local debug_port
    debug_port="$(docker_value debug_ollama '{{range $p, $bindings := .NetworkSettings.Ports}}{{range $bindings}}{{println .HostPort}}{{end}}{{end}}')"
    if [[ "$debug_port" == *"11434"* ]]; then
      echo "debug_ollama conflicts with required port 11434. This runner will not modify it." >&2
      echo "sudo docker stop debug_ollama" >&2
      echo "sudo docker rm debug_ollama" >&2
      exit 1
    fi
  fi
  sudo docker pull "$IMAGE" >/dev/null
  local image_id
  image_id="$(sudo docker image inspect --format '{{.Id}}' "$IMAGE")"
  setup_container adir_ws_ollama_0 11434 0 4 "$image_id"
  setup_container adir_ws_ollama_1 11435 1 4 "$image_id"
  python3 scripts/hardware_ablation_utils.py ollama-health \
    --hosts "$OLLAMA_HOSTS" --model "$MODEL" --manifest "$OLLAMA_MANIFEST" \
    --record --image-id "$image_id"
}

if [[ $# -gt 1 ]]; then usage; exit 2; fi
if [[ "${1:-}" == "--setup-ollama" ]]; then
  setup_ollama
  exit 0
elif [[ $# -ne 0 ]]; then
  usage
  exit 2
fi

for path in "$BASELINE" "$INTENT_SOURCE" "$DEVELOPMENT_IDS" "$DB_PATH" "$SCHEMA_JSON" "$OLLAMA_MANIFEST"; do
  [[ -f "$path" ]] || { echo "Required file not found: $path" >&2; exit 1; }
done
python3 scripts/hardware_ablation_utils.py ollama-health \
  --hosts "$OLLAMA_HOSTS" --model "$MODEL" --manifest "$OLLAMA_MANIFEST"
python3 scripts/hardware_ablation_utils.py prepare-cohort \
  --baseline "$BASELINE" --intents "$INTENT_SOURCE" \
  --development-ids "$DEVELOPMENT_IDS" --output-dir "$COHORT_DIR"

run_cached_final() {
  local key="$1" repair="$2" out_dir="$3"
  local evaluated="${out_dir}/${key}_final_evaluated.jsonl"
  local metrics="${out_dir}/${key}_final_metrics.json"
  local metrics_md="${out_dir}/${key}_final_metrics.md"
  local adapted="${out_dir}/${key}_adapted_final_input.jsonl"
  local manifest="${out_dir}/${key}_final_evaluation_manifest.json"
  if ! python3 scripts/dev/baseline_evaluation_cache.py \
    --stage evaluation --evaluation-kind final --input-jsonl "$repair" \
    --db-path "$DB_PATH" --schema-path "$SCHEMA_JSON" --manifest "$manifest" \
    --output-jsonl "$evaluated" --metrics-json "$metrics" \
    --metrics-md "$metrics_md" --required-output "$adapted"; then
    python3 -m src.eval.evaluate_final_sql \
      --input-jsonl "$repair" --output-jsonl "$evaluated" \
      --metrics-json "$metrics" --metrics-md "$metrics_md" \
      --adapted-jsonl "$adapted" --db-path "$DB_PATH" --workers "$EVALUATION_WORKERS"
    python3 scripts/dev/baseline_evaluation_cache.py \
      --stage evaluation --evaluation-kind final --input-jsonl "$repair" \
      --db-path "$DB_PATH" --schema-path "$SCHEMA_JSON" --manifest "$manifest" \
      --output-jsonl "$evaluated" --metrics-json "$metrics" \
      --metrics-md "$metrics_md" --required-output "$adapted" --refresh
  fi
}

run_cached_asa() {
  local key="$1" out_dir="$2"
  local evaluated="${out_dir}/${key}_final_evaluated.jsonl"
  local metrics="${out_dir}/${key}_asa_metrics.json"
  local metrics_md="${out_dir}/${key}_asa_metrics.md"
  local rows="${out_dir}/${key}_asa_rows.jsonl"
  local manifest="${out_dir}/${key}_asa_manifest.json"
  if ! python3 scripts/dev/baseline_evaluation_cache.py \
    --stage asa --evaluation-kind final --input-jsonl "$evaluated" \
    --before-jsonl "$COHORT" --after-jsonl "$evaluated" \
    --db-path "$DB_PATH" --schema-path "$SCHEMA_JSON" --manifest "$manifest" \
    --output-jsonl "$rows" --metrics-json "$metrics" --metrics-md "$metrics_md" \
    --row-output-jsonl "$rows"; then
    python3 -m src.eval.evaluate_asa \
      --before-jsonl "$COHORT" --after-jsonl "$evaluated" \
      --schema-path "$SCHEMA_JSON" --output-json "$metrics" \
      --output-md "$metrics_md" --row-output-jsonl "$rows" \
      --dedupe error --workers "$EVALUATION_WORKERS"
    python3 scripts/dev/baseline_evaluation_cache.py \
      --stage asa --evaluation-kind final --input-jsonl "$evaluated" \
      --before-jsonl "$COHORT" --after-jsonl "$evaluated" \
      --db-path "$DB_PATH" --schema-path "$SCHEMA_JSON" --manifest "$manifest" \
      --output-jsonl "$rows" --metrics-json "$metrics" --metrics-md "$metrics_md" \
      --row-output-jsonl "$rows" --refresh
  fi
}

run_variant() {
  local key="$1" intent_mode="$2" profile="$3" probing="$4" framework="$5"
  local out_dir="${OUTPUT_ROOT}/${key}"
  local verify="${out_dir}/${key}_verify.jsonl"
  local repair="${out_dir}/${key}_repairs.jsonl"
  local legacy="${SOURCE_ROOT}/internal_ablation/${key}"
  local cache_args=()
  mkdir -p "$out_dir"
  if [[ "$intent_mode" == "nl_only" ]]; then
    cache_args=(--intent-cache-path "$INTENTS" --require-intent-cache)
  fi
  python3 scripts/hardware_ablation_utils.py import-artifact \
    --source "${legacy}/${key}_verify.jsonl" --destination "$verify" --cohort "$COHORT" \
    --expect "verifier_model=\"${MODEL}\"" --expect "intent_mode=\"${intent_mode}\"" \
    --expect "profile_format=\"${profile}\"" --expect "probing_mode=\"${probing}\"" \
    --expect "max_probes=7"
  python3 scripts/run_finverisql_verify.py \
    --input-path "$COHORT" --output-path "$verify" \
    --repair-output-path "${out_dir}/${key}_repair_queue.jsonl" \
    --skipped-output-path "${out_dir}/${key}_skipped.jsonl" \
    --schema-path "$SCHEMA_JSON" --profile-mode "$profile" \
    --intent-mode "$intent_mode" --probing-mode "$probing" --max-probes 7 \
    --backend ollama --model-name "$MODEL" --temperature 0 --num-predict 1024 \
    --timeout 300 --workers "$FINVERISQL_VERIFY_WORKERS" "${cache_args[@]}"
  python3 scripts/hardware_ablation_utils.py validate-artifact \
    --path "$verify" --cohort "$COHORT" --complete \
    --expect "verifier_model=\"${MODEL}\"" --expect "intent_mode=\"${intent_mode}\"" \
    --expect "profile_format=\"${profile}\"" --expect "probing_mode=\"${probing}\"" \
    --expect "max_probes=7"
  python3 scripts/hardware_ablation_utils.py import-artifact \
    --source "${legacy}/${key}_repairs.jsonl" --destination "$repair" --cohort "$COHORT" \
    --expect "repair_model=\"${MODEL}\"" --expect "intent_mode=\"${intent_mode}\""
  python3 scripts/run_finverisql_repair.py \
    --input-path "$verify" --output-path "$repair" --schema-path "$SCHEMA_JSON" \
    --semantic-repair-framework "$framework" --intent-mode "$intent_mode" \
    "${cache_args[@]}" --repair-backend ollama --repair-model-name "$MODEL" \
    --verifier-backend ollama --verifier-model-name "$MODEL" \
    --profile-mode "$profile" --probing-mode "$probing" --max-probes 7 \
    --temperature 0 --num-predict 768 --timeout 300 \
    --workers "$FINVERISQL_REPAIR_WORKERS" --strict-resume
  python3 scripts/hardware_ablation_utils.py validate-artifact \
    --path "$repair" --cohort "$COHORT" --complete \
    --expect "repair_model=\"${MODEL}\"" --expect "intent_mode=\"${intent_mode}\""
  python3 -m src.eval.evaluate_verifier_diagnostics \
    --input-path "$verify" --output-md "${out_dir}/${key}_verifier_diagnostics.md"
  run_cached_final "$key" "$repair" "$out_dir"
  run_cached_asa "$key" "$out_dir"
}

# Import the already completed Full ADiR row; it is never rerun here.
mkdir -p "${OUTPUT_ROOT}/full"
python3 scripts/hardware_ablation_utils.py import-artifact \
  --source "${SOURCE_ROOT}/internal_ablation/full/full_verify.jsonl" \
  --destination "${OUTPUT_ROOT}/full/full_verify.jsonl" --cohort "$COHORT" \
  --expect 'verifier_model="llama3.1:8b"' --expect 'intent_mode="nl_only"' \
  --expect 'profile_format="compact"' --expect 'probing_mode="probe"' --expect 'max_probes=7'
python3 scripts/hardware_ablation_utils.py import-artifact \
  --source "${SOURCE_ROOT}/internal_ablation/full/full_repairs.jsonl" \
  --destination "${OUTPUT_ROOT}/full/full_repairs.jsonl" --cohort "$COHORT" \
  --expect 'repair_model="llama3.1:8b"' --expect 'intent_mode="nl_only"'
python3 scripts/hardware_ablation_utils.py validate-artifact \
  --path "${OUTPUT_ROOT}/full/full_verify.jsonl" --cohort "$COHORT" --complete
python3 scripts/hardware_ablation_utils.py validate-artifact \
  --path "${OUTPUT_ROOT}/full/full_repairs.jsonl" --cohort "$COHORT" --complete
run_cached_final full "${OUTPUT_ROOT}/full/full_repairs.jsonl" "${OUTPUT_ROOT}/full"
run_cached_asa full "${OUTPUT_ROOT}/full"

ALL_VARIANTS="wo_intent_decomposer,direct_only,wo_compact_semantic_profile,wo_scope_constraints,wo_reverification_loop"
SELECTED="${ABLATION_VARIANTS:-$ALL_VARIANTS}"
SELECTED="${SELECTED//,/ }"
read -r -a VARIANTS <<< "$SELECTED"
for variant in "${VARIANTS[@]}"; do
  case "$variant" in
    wo_intent_decomposer) run_variant "$variant" none compact probe specialized_chain ;;
    direct_only) run_variant "$variant" nl_only compact none specialized_chain ;;
    wo_compact_semantic_profile) run_variant "$variant" nl_only ast probe specialized_chain ;;
    wo_scope_constraints) run_variant "$variant" nl_only compact probe generic_chain ;;
    wo_reverification_loop) run_variant "$variant" nl_only compact probe no_reverification ;;
    *) echo "Unknown ABLATION_VARIANTS entry: $variant" >&2; exit 2 ;;
  esac
done

python3 scripts/dev/build_internal_ablation_table.py \
  --ablation-dir "$OUTPUT_ROOT" \
  --output-md "${OUTPUT_ROOT}/internal_ablation_table.md" \
  --output-json "${OUTPUT_ROOT}/internal_ablation_table.json"
denominator_args=()
for variant in full wo_intent_decomposer direct_only wo_compact_semantic_profile wo_scope_constraints wo_reverification_loop; do
  if [[ -f "${OUTPUT_ROOT}/${variant}/${variant}_final_metrics.json" ]]; then
    denominator_args+=(--metrics "${OUTPUT_ROOT}/${variant}/${variant}_final_metrics.json")
    denominator_args+=(--metrics "${OUTPUT_ROOT}/${variant}/${variant}_asa_metrics.json")
  fi
done
python3 scripts/hardware_ablation_utils.py assert-denominator \
  "${denominator_args[@]}" --expected 4874
echo "Completed workstation internal ablations in ${OUTPUT_ROOT}"
