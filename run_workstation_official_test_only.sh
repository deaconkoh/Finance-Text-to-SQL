#!/usr/bin/env bash
set -euo pipefail

RUN_ID="eval_publication_20260706_154755"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

IMAGE="ollama/ollama:latest"
MODEL_VOLUME="/home/ranjan/finveri_ollama_workspace"
QWEN_MODEL="qwen2.5-coder:7b-instruct"
ADIR_MODEL="llama3.1:8b"
OLLAMA_HOSTS="http://127.0.0.1:11434,http://127.0.0.1:11435"
export OLLAMA_HOSTS
WORKERS=8

RUN_ROOT="data/outputs/finverisql/${RUN_ID}"
OUTPUT_ROOT="${RUN_ROOT}/official_test_experiments/qwen25_coder_7b_instruct"
ARTIFACT_ROOT="${OUTPUT_ROOT}/artifacts"
QWEN_DIR="${OUTPUT_ROOT}/qwen_only"
ADIR_DIR="${OUTPUT_ROOT}/adir_full"
SOURCE="${ARTIFACT_ROOT}/booksql_official_test.jsonl"
SOURCE_MANIFEST="${ARTIFACT_ROOT}/booksql_official_test_source_manifest.json"
QWEN_ATTEMPTS="${ARTIFACT_ROOT}/qwen_baseline_attempts.jsonl"
QWEN_CANONICAL="${ARTIFACT_ROOT}/qwen_baseline.jsonl"
ROUTED="${ARTIFACT_ROOT}/qwen_baseline_routed.jsonl"
INTENT_ATTEMPTS="${ARTIFACT_ROOT}/adir_intent_attempts.jsonl"
INTENTS="${ARTIFACT_ROOT}/adir_intents.jsonl"
VERIFY_ATTEMPTS="${ARTIFACT_ROOT}/adir_verify_attempts.jsonl"
VERIFIED="${ARTIFACT_ROOT}/adir_verified.jsonl"
REPAIR_ATTEMPTS="${ARTIFACT_ROOT}/adir_repair_attempts.jsonl"
REPAIRED="${ARTIFACT_ROOT}/adir_repairs.jsonl"
DB_PATH="data/booksql/accounting.sqlite"
SCHEMA_TEXT="data/booksql/schema.txt"
SCHEMA_JSON="data/booksql/schema_annotations.json"
TRAIN_DATA="data/booksql/booksql_normalized.jsonl"
QWEN_OLLAMA_MANIFEST="${ARTIFACT_ROOT}/ollama_qwen_manifest.json"
ADIR_OLLAMA_MANIFEST="${ARTIFACT_ROOT}/ollama_adir_manifest.json"

usage() {
  echo "Usage: $0 [--setup-ollama]" >&2
}

docker_value() {
  sudo docker inspect --format "$2" "$1"
}

validate_container() {
  local name="$1" port="$2" gpu="$3" image_id="$4"
  sudo docker inspect "$name" >/dev/null 2>&1 || {
    echo "Required existing Ollama container not found: $name" >&2
    echo "Run the workstation internal-ablation setup first." >&2
    exit 1
  }
  [[ "$(docker_value "$name" '{{.Image}}')" == "$image_id" ]] || {
    echo "$name has an incompatible Ollama image." >&2
    exit 1
  }
  [[ "$(docker_value "$name" '{{(index (index .NetworkSettings.Ports "11434/tcp") 0).HostPort}}')" == "$port" ]] || {
    echo "$name has an incompatible host port." >&2
    exit 1
  }
  [[ "$(docker_value "$name" '{{range .Mounts}}{{if eq .Destination "/root/.ollama"}}{{.Source}}{{end}}{{end}}')" == "$MODEL_VOLUME" ]] || {
    echo "$name has an incompatible model volume." >&2
    exit 1
  }
  [[ "$(docker_value "$name" '{{range .Config.Env}}{{println .}}{{end}}')" == *"OLLAMA_NUM_PARALLEL=4"* ]] || {
    echo "$name must use OLLAMA_NUM_PARALLEL=4." >&2
    exit 1
  }
  [[ "$(docker_value "$name" '{{range .HostConfig.DeviceRequests}}{{range .DeviceIDs}}{{println .}}{{end}}{{end}}')" == *"$gpu"* ]] || {
    echo "$name is not assigned to GPU $gpu." >&2
    exit 1
  }
  if [[ "$(docker_value "$name" '{{.State.Running}}')" != "true" ]]; then
    sudo docker start "$name" >/dev/null
  fi
  sudo docker exec "$name" ollama pull "$QWEN_MODEL" >/dev/null
  sudo docker exec "$name" ollama pull "$ADIR_MODEL" >/dev/null
}

setup_ollama() {
  command -v sudo >/dev/null
  command -v docker >/dev/null
  local image_id
  image_id="$(sudo docker image inspect --format '{{.Id}}' "$IMAGE")"
  validate_container adir_ws_ollama_0 11434 0 "$image_id"
  validate_container adir_ws_ollama_1 11435 1 "$image_id"
  python3 scripts/hardware_ablation_utils.py ollama-health \
    --hosts "$OLLAMA_HOSTS" --model "$QWEN_MODEL" \
    --manifest "$QWEN_OLLAMA_MANIFEST" --record --image-id "$image_id"
  python3 scripts/hardware_ablation_utils.py ollama-health \
    --hosts "$OLLAMA_HOSTS" --model "$ADIR_MODEL" \
    --manifest "$ADIR_OLLAMA_MANIFEST" --record --image-id "$image_id"
}

if [[ $# -gt 1 ]]; then usage; exit 2; fi
if [[ "${1:-}" == "--setup-ollama" ]]; then
  setup_ollama
  exit 0
elif [[ $# -ne 0 ]]; then
  usage
  exit 2
fi

for path in "$DB_PATH" "$SCHEMA_TEXT" "$SCHEMA_JSON" "$TRAIN_DATA" \
  "$QWEN_OLLAMA_MANIFEST" "$ADIR_OLLAMA_MANIFEST"; do
  [[ -f "$path" ]] || { echo "Required file not found: $path" >&2; exit 1; }
done
mkdir -p "$ARTIFACT_ROOT" "$QWEN_DIR" "$ADIR_DIR"

python3 scripts/hardware_ablation_utils.py ollama-health \
  --hosts "$OLLAMA_HOSTS" --model "$QWEN_MODEL" --manifest "$QWEN_OLLAMA_MANIFEST"
python3 scripts/hardware_ablation_utils.py ollama-health \
  --hosts "$OLLAMA_HOSTS" --model "$ADIR_MODEL" --manifest "$ADIR_OLLAMA_MANIFEST"

python3 scripts/prepare_official_booksql_test.py \
  --output-path "$SOURCE" --manifest-path "$SOURCE_MANIFEST"

python3 -m src.baseline.run_baseline_qwen \
  --split test --prompt-setting few_shot \
  --data-path "$SOURCE" --few-shot-data-path "$TRAIN_DATA" \
  --schema-path "$SCHEMA_TEXT" --db-path "$DB_PATH" \
  --allow-missing-gold-sql --output-path "$QWEN_CANONICAL" \
  --attempt-log-path "$QWEN_ATTEMPTS" --require-all-success \
  --backend ollama --ollama-model-name "$QWEN_MODEL" \
  --temperature 0 --seed 42 --max-new-tokens 128 --timeout 300 \
  --workers "$WORKERS"
python3 scripts/official_test_artifacts.py validate \
  --source "$SOURCE" --path "$QWEN_CANONICAL" --require-sql

python3 scripts/hardware_ablation_utils.py unload-ollama \
  --hosts "$OLLAMA_HOSTS" --model "$QWEN_MODEL"

python3 scripts/prepare_official_test_candidates.py \
  --input-jsonl "$QWEN_CANONICAL" --output-jsonl "$ROUTED" --db-path "$DB_PATH"

python3 scripts/precompute_finverisql_intents.py \
  --input-path "$ROUTED" --output-path "$INTENTS" \
  --attempt-log-path "$INTENT_ATTEMPTS" --require-all-success \
  --schema-path "$SCHEMA_JSON" --intent-mode nl_only \
  --backend ollama --model-name "$ADIR_MODEL" \
  --temperature 0 --num-predict 1024 --timeout 300 --workers "$WORKERS" \
  --strict-resume
python3 scripts/official_test_artifacts.py validate \
  --source "$ROUTED" --path "$INTENTS" --require-intent

python3 scripts/run_finverisql_verify.py \
  --input-path "$ROUTED" --output-path "$VERIFY_ATTEMPTS" \
  --repair-output-path "${ARTIFACT_ROOT}/adir_repair_queue.jsonl" \
  --skipped-output-path "${ARTIFACT_ROOT}/adir_verify_skipped.jsonl" \
  --schema-path "$SCHEMA_JSON" --profile-mode compact \
  --intent-mode nl_only --intent-cache-path "$INTENTS" --require-intent-cache \
  --probing-mode probe --max-probes 7 \
  --backend ollama --model-name "$ADIR_MODEL" \
  --temperature 0 --num-predict 1024 --timeout 300 --workers "$WORKERS"
python3 scripts/official_test_artifacts.py canonicalize \
  --source "$ROUTED" --attempts "$VERIFY_ATTEMPTS" --output "$VERIFIED"

python3 scripts/run_finverisql_repair.py \
  --input-path "$VERIFIED" --output-path "$REPAIR_ATTEMPTS" \
  --schema-path "$SCHEMA_JSON" \
  --semantic-repair-framework specialized_chain --intent-mode nl_only \
  --intent-cache-path "$INTENTS" --require-intent-cache \
  --repair-backend ollama --repair-model-name "$ADIR_MODEL" \
  --verifier-backend ollama --verifier-model-name "$ADIR_MODEL" \
  --profile-mode compact --probing-mode probe --max-probes 7 \
  --temperature 0 --num-predict 768 --timeout 300 \
  --workers "$WORKERS" --strict-resume
python3 scripts/official_test_artifacts.py canonicalize \
  --source "$VERIFIED" --attempts "$REPAIR_ATTEMPTS" --output "$REPAIRED" \
  --accept-status skipped
python3 scripts/official_test_artifacts.py validate \
  --source "$VERIFIED" --path "$REPAIRED" --accept-status skipped --require-sql

python3 scripts/export_official_test_submission.py \
  --input-jsonl "$QWEN_CANONICAL" \
  --submission-csv "${QWEN_DIR}/submission.csv" \
  --predictions-jsonl "${QWEN_DIR}/predictions.jsonl" \
  --table-md "${QWEN_DIR}/official_test_status.md" \
  --summary-json "${QWEN_DIR}/official_test_status.json" \
  --expected-source-jsonl "$SOURCE" --system-name "Qwen2.5-Coder-7B-Instruct"
python3 scripts/export_official_test_submission.py \
  --input-jsonl "$REPAIRED" \
  --submission-csv "${ADIR_DIR}/submission.csv" \
  --predictions-jsonl "${ADIR_DIR}/predictions.jsonl" \
  --table-md "${ADIR_DIR}/official_test_status.md" \
  --summary-json "${ADIR_DIR}/official_test_status.json" \
  --expected-source-jsonl "$SOURCE" --system-name "Qwen2.5-Coder-7B-Instruct + Full ADiR"
python3 scripts/official_test_artifacts.py validate-submissions \
  --source "$SOURCE" \
  --submission "${QWEN_DIR}/submission.csv" \
  --submission "${ADIR_DIR}/submission.csv"

CONFIG='{"baseline":{"model":"qwen2.5-coder:7b-instruct","prompt_setting":"few_shot","temperature":0,"seed":42,"max_new_tokens":128},"adir":{"model":"llama3.1:8b","intent_mode":"nl_only","profile_mode":"compact","probing_mode":"probe","max_probes":7,"semantic_repair_framework":"specialized_chain"},"workers":8,"ollama_hosts":["http://127.0.0.1:11434","http://127.0.0.1:11435"]}'
python3 scripts/official_test_artifacts.py summarize \
  --source "$SOURCE" --source-manifest "$SOURCE_MANIFEST" \
  --baseline "$QWEN_CANONICAL" --routed "$ROUTED" --repaired "$REPAIRED" \
  --failure-label baseline --attempt-log "$QWEN_ATTEMPTS" \
  --failure-label intent --attempt-log "$INTENT_ATTEMPTS" \
  --failure-label verifier --attempt-log "$VERIFY_ATTEMPTS" \
  --failure-label repair --attempt-log "$REPAIR_ATTEMPTS" \
  --artifact "${QWEN_DIR}/submission.csv" \
  --artifact "${ADIR_DIR}/submission.csv" \
  --output "${OUTPUT_ROOT}/comparison_summary.json" \
  --run-manifest "${OUTPUT_ROOT}/run_manifest.json" \
  --run-id "$RUN_ID" --config "$CONFIG"

echo "Created two validated submissions in ${OUTPUT_ROOT}"
echo "Official hidden-test EX is pending manual BookSQL leaderboard submission."
