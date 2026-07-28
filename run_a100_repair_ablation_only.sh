#!/usr/bin/env bash
set -euo pipefail

RUN_ID="eval_publication_20260706_154755"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

IMAGE="ollama/ollama:latest"
MODEL="llama3.1:8b"
MODEL_VOLUME="/home/ranjan/finveri_ollama_workspace"
GPU_UUIDS=(
  "GPU-3f5077d2-54da-5bdf-3bad-df04ea1f3582"
  "GPU-3e5d4bba-023c-a3c2-a664-3213f70179b6"
  "GPU-7c2d8d6f-333c-3f57-e9d7-e3c0a76a1981"
  "GPU-d05d84bd-d9d0-6666-7fdf-eaa2fe5bb2f2"
)
OLLAMA_HOSTS="http://127.0.0.1:11434,http://127.0.0.1:11435,http://127.0.0.1:11436,http://127.0.0.1:11437"
PROMPT_OLLAMA_HOSTS="http://127.0.0.1:11436,http://127.0.0.1:11437"
export OLLAMA_HOSTS

RUN_ROOT="data/outputs/finverisql/${RUN_ID}"
SOURCE_ROOT="${RUN_ROOT}/debug"
OUTPUT_ROOT="${RUN_ROOT}/development_excluded_experiments/repair_strategy_ablation"
COHORT_DIR="${OUTPUT_ROOT}/cohort"
COHORT="${COHORT_DIR}/cohort_baseline.jsonl"
COHORT_INTENTS="${COHORT_DIR}/cohort_intents_nl_only.jsonl"
FIXED_VERIFIER="${OUTPUT_ROOT}/full_fixed_verifier.jsonl"
OLLAMA_MANIFEST="${OUTPUT_ROOT}/ollama_a100_manifest.json"
BASELINE="${SOURCE_ROOT}/baseline/qwen_few_shot_validation_evaluated.jsonl"
INTENT_SOURCE="${SOURCE_ROOT}/intents/intents_nl_only.jsonl"
DEVELOPMENT_IDS="data/protocol/booksql_validation_development_2000_ids.jsonl"
DB_PATH="data/booksql/accounting.sqlite"
SCHEMA_JSON="data/booksql/schema_annotations.json"
SCHEMA_TEXT="data/booksql/schema.txt"

TRAIN_DIR="data/outputs/finverisql/train_repair_learning"
TRAIN_BASELINE="${TRAIN_DIR}/qwen_few_shot_train_evaluated.jsonl"
TRAIN_PRIMARY="${OUTPUT_ROOT}/train_primary.jsonl"
TRAIN_VERIFY="${TRAIN_DIR}/full_train_verify.jsonl"
TRAIN_EXAMPLES="${OUTPUT_ROOT}/sft_train_examples.jsonl"
TRAIN_EXAMPLES_MANIFEST="${OUTPUT_ROOT}/sft_train_examples_manifest.json"
SFT_DIR="${OUTPUT_ROOT}/checkpoints/sft_llama31_8b"
RL_DIR="${OUTPUT_ROOT}/checkpoints/rl_llama31_8b"
SFT_FINGERPRINT="${SFT_DIR}/adir_training_manifest.json"
RL_FINGERPRINT="${RL_DIR}/adir_training_manifest.json"
BASE_MODEL="meta-llama/Meta-Llama-3.1-8B-Instruct"
TRAIN_GPUS="$(IFS=,; echo "${GPU_UUIDS[*]}")"

usage() { echo "Usage: $0 [--setup-ollama]" >&2; }
docker_value() { sudo docker inspect --format "$2" "$1"; }

setup_container() {
  local name="$1" port="$2" gpu="$3" image_id="$4"
  if sudo docker inspect "$name" >/dev/null 2>&1; then
    [[ "$(docker_value "$name" '{{.Image}}')" == "$image_id" ]] || { echo "$name has an incompatible image." >&2; exit 1; }
    [[ "$(docker_value "$name" '{{(index (index .NetworkSettings.Ports "11434/tcp") 0).HostPort}}')" == "$port" ]] || { echo "$name has an incompatible host port." >&2; exit 1; }
    [[ "$(docker_value "$name" '{{range .Mounts}}{{if eq .Destination "/root/.ollama"}}{{.Source}}{{end}}{{end}}')" == "$MODEL_VOLUME" ]] || { echo "$name has an incompatible model volume." >&2; exit 1; }
    [[ "$(docker_value "$name" '{{range .Config.Env}}{{println .}}{{end}}')" == *"OLLAMA_NUM_PARALLEL=8"* ]] || { echo "$name has incompatible parallelism." >&2; exit 1; }
    [[ "$(docker_value "$name" '{{range .HostConfig.DeviceRequests}}{{range .DeviceIDs}}{{println .}}{{end}}{{end}}')" == *"$gpu"* ]] || { echo "$name has an incompatible GPU." >&2; exit 1; }
    if [[ "$(docker_value "$name" '{{.State.Running}}')" != "true" ]]; then
      sudo docker start "$name" >/dev/null
    fi
  else
    sudo docker run -d --name "$name" --restart unless-stopped \
      --gpus "device=${gpu}" -p "${port}:11434" -e OLLAMA_NUM_PARALLEL=8 \
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
  for index in 0 1 2 3; do
    setup_container "adir_a100_ollama_${index}" "$((11434 + index))" "${GPU_UUIDS[$index]}" "$image_id"
  done
  python3 scripts/hardware_ablation_utils.py ollama-health \
    --hosts "$OLLAMA_HOSTS" --model "$MODEL" --manifest "$OLLAMA_MANIFEST" \
    --record --image-id "$image_id"
}

if [[ $# -gt 1 ]]; then usage; exit 2; fi
if [[ "${1:-}" == "--setup-ollama" ]]; then setup_ollama; exit 0
elif [[ $# -ne 0 ]]; then usage; exit 2
fi

for path in "$BASELINE" "$INTENT_SOURCE" "$DEVELOPMENT_IDS" "$DB_PATH" "$SCHEMA_JSON" "$SCHEMA_TEXT" "$TRAIN_BASELINE" "$OLLAMA_MANIFEST" "${SOURCE_ROOT}/internal_ablation/full/full_verify.jsonl"; do
  [[ -f "$path" ]] || { echo "Required existing artifact not found: $path" >&2; exit 1; }
done
for command in python3 accelerate nvidia-smi; do
  command -v "$command" >/dev/null || { echo "Required command not found: $command" >&2; exit 1; }
done

python3 scripts/hardware_ablation_utils.py ollama-health \
  --hosts "$OLLAMA_HOSTS" --model "$MODEL" --manifest "$OLLAMA_MANIFEST"
python3 scripts/hardware_ablation_utils.py prepare-cohort \
  --baseline "$BASELINE" --intents "$INTENT_SOURCE" \
  --development-ids "$DEVELOPMENT_IDS" --output-dir "$COHORT_DIR"
python3 scripts/hardware_ablation_utils.py import-artifact \
  --source "${SOURCE_ROOT}/internal_ablation/full/full_verify.jsonl" \
  --destination "$FIXED_VERIFIER" --cohort "$COHORT" \
  --expect 'verifier_model="llama3.1:8b"' --expect 'intent_mode="nl_only"' \
  --expect 'profile_format="compact"' --expect 'probing_mode="probe"' --expect 'max_probes=7'
python3 scripts/hardware_ablation_utils.py validate-artifact \
  --path "$FIXED_VERIFIER" --cohort "$COHORT" --complete
python3 scripts/hardware_ablation_utils.py prepare-primary \
  --input "$TRAIN_BASELINE" --output "$TRAIN_PRIMARY"

# The train baseline is a required immutable input. It is never regenerated or reevaluated.
if [[ -f "$TRAIN_VERIFY" ]]; then
  python3 scripts/hardware_ablation_utils.py validate-artifact \
    --path "$TRAIN_VERIFY" --cohort "$TRAIN_PRIMARY" \
    --expect 'verifier_model="llama3.1:8b"' --expect 'intent_mode="nl_only"' \
    --expect 'profile_format="compact"' --expect 'probing_mode="probe"' --expect 'max_probes=7'
fi
python3 scripts/run_finverisql_verify.py \
  --input-path "$TRAIN_PRIMARY" --output-path "$TRAIN_VERIFY" \
  --repair-output-path "${TRAIN_DIR}/full_train_repair_queue.jsonl" \
  --skipped-output-path "${TRAIN_DIR}/full_train_skipped.jsonl" \
  --schema-path "$SCHEMA_JSON" --profile-mode compact --intent-mode nl_only \
  --probing-mode probe --max-probes 7 --backend ollama --model-name "$MODEL" \
  --temperature 0 --num-predict 1024 --timeout 300 --workers 32
python3 scripts/hardware_ablation_utils.py validate-artifact \
  --path "$TRAIN_VERIFY" --cohort "$TRAIN_PRIMARY" --complete \
  --expect 'verifier_model="llama3.1:8b"' --expect 'intent_mode="nl_only"' \
  --expect 'profile_format="compact"' --expect 'probing_mode="probe"' --expect 'max_probes=7'

python3 scripts/dev/build_repair_learning_data.py \
  --fixed-verifier-jsonl "$TRAIN_VERIFY" --output-jsonl "$TRAIN_EXAMPLES" \
  --manifest-json "$TRAIN_EXAMPLES_MANIFEST" --schema-text-path "$SCHEMA_TEXT" --split train

SFT_CONFIG='{"base_model":"meta-llama/Meta-Llama-3.1-8B-Instruct","max_seq_length":4096,"epochs":1.0,"learning_rate":0.0002,"per_device_batch":4,"gradient_accumulation":1,"world_size":4,"seed":42}'
RL_CONFIG='{"base_model":"meta-llama/Meta-Llama-3.1-8B-Instruct","learning_rate":0.000001,"rollout_batch_size":8,"mini_batch_size":1,"ppo_epochs":1,"world_size":4,"seed":42}'

adapter_complete() {
  local directory="$1"
  [[ -f "${directory}/adapter_config.json" ]] && compgen -G "${directory}/adapter_model.*" >/dev/null
}

SFT_READY=0
if adapter_complete "$SFT_DIR" && [[ -f "$SFT_FINGERPRINT" ]]; then
  if python3 scripts/hardware_ablation_utils.py fingerprint \
    --input "$TRAIN_EXAMPLES" --config "$SFT_CONFIG" --manifest "$SFT_FINGERPRINT" --check; then
    SFT_READY=1
  else
    echo "Existing SFT adapter manifest is incompatible; move it aside explicitly." >&2
    exit 1
  fi
elif adapter_complete "$SFT_DIR"; then
  echo "Existing SFT adapter has no ADiR training manifest; move it aside explicitly." >&2
  exit 1
fi

python3 scripts/hardware_ablation_utils.py unload-ollama --hosts "$OLLAMA_HOSTS" --model "$MODEL"
export CUDA_VISIBLE_DEVICES="$TRAIN_GPUS"
if [[ "$SFT_READY" != "1" ]]; then
  resume_args=()
  latest_checkpoint=""
  if [[ -d "$SFT_DIR" ]]; then
    latest_checkpoint="$(find "$SFT_DIR" -maxdepth 1 -type d -name 'checkpoint-*' -print | sort -V | tail -n 1)"
  fi
  if [[ -n "$latest_checkpoint" && -f "${latest_checkpoint}/trainer_state.json" ]] && adapter_complete "$latest_checkpoint"; then
    resume_args=(--resume-from-checkpoint "$latest_checkpoint")
  fi
  accelerate launch --multi_gpu --num_processes 4 --mixed_precision bf16 \
    scripts/dev/train_sft_repairer.py \
    --train-jsonl "$TRAIN_EXAMPLES" --output-dir "$SFT_DIR" --base-model "$BASE_MODEL" \
    --per-device-train-batch-size 4 --gradient-accumulation-steps 1 \
    --dataset-num-proc 4 --dataloader-num-workers 4 --seed 42 "${resume_args[@]}"
  python3 scripts/hardware_ablation_utils.py fingerprint \
    --input "$TRAIN_EXAMPLES" --config "$SFT_CONFIG" --manifest "$SFT_FINGERPRINT"
fi

RL_READY=0
if adapter_complete "$RL_DIR" && [[ -f "$RL_FINGERPRINT" ]]; then
  if python3 scripts/hardware_ablation_utils.py fingerprint \
    --input "$TRAIN_EXAMPLES" --input "$SFT_FINGERPRINT" \
    --config "$RL_CONFIG" --manifest "$RL_FINGERPRINT" --check; then
    RL_READY=1
  else
    echo "Existing RL adapter manifest is incompatible; move it aside explicitly." >&2
    exit 1
  fi
fi
if [[ "$RL_READY" != "1" ]]; then
  if [[ -d "$RL_DIR" && -n "$(find "$RL_DIR" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "Partial PPO output exists at $RL_DIR. PPO is atomic; move it aside explicitly." >&2
    exit 1
  fi
  accelerate launch --multi_gpu --num_processes 4 --mixed_precision bf16 \
    scripts/dev/train_rl_repairer.py \
    --train-jsonl "$TRAIN_EXAMPLES" --sft-adapter-path "$SFT_DIR" \
    --output-dir "$RL_DIR" --base-model "$BASE_MODEL" --db-path "$DB_PATH" \
    --schema-annotations-path "$SCHEMA_JSON" --learning-rate 1e-6 \
    --batch-size 8 --mini-batch-size 1 --ppo-epochs 1 \
    --dataset-num-proc 4 --reward-workers 4 --seed 42
  python3 scripts/hardware_ablation_utils.py fingerprint \
    --input "$TRAIN_EXAMPLES" --input "$SFT_FINGERPRINT" \
    --config "$RL_CONFIG" --manifest "$RL_FINGERPRINT"
fi

# Restarting/loading models is deliberately not a Docker operation; Ollama reloads
# llama3.1:8b on the first prompted request after distributed training.
python3 scripts/run_repair_strategy_ablation.py \
  --fixed-verifier-jsonl "$FIXED_VERIFIER" \
  --baseline-eval-jsonl "$COHORT" --output-dir "$OUTPUT_ROOT" \
  --prompt-model-name "$MODEL" --prompt-ollama-hosts "$PROMPT_OLLAMA_HOSTS" \
  --base-model "$BASE_MODEL" --sft-adapter-path "$SFT_DIR" --rl-adapter-path "$RL_DIR" \
  --workers 8 --adapter-inference-batch-size 4 --ollama-workers 16 \
  --parallel-adapter-strategies

for strategy in prompt_llama31_8b sft_llama31_8b rl_llama31_8b; do
  python3 scripts/hardware_ablation_utils.py validate-artifact \
    --path "${OUTPUT_ROOT}/${strategy}_repairs.jsonl" --cohort "$COHORT" --complete \
    --expect "repair_strategy=\"${strategy}\""
done
python3 scripts/hardware_ablation_utils.py assert-denominator \
  --metrics "${OUTPUT_ROOT}/prompt_llama31_8b_final_metrics.json" \
  --metrics "${OUTPUT_ROOT}/prompt_llama31_8b_asa_metrics.json" \
  --metrics "${OUTPUT_ROOT}/sft_llama31_8b_final_metrics.json" \
  --metrics "${OUTPUT_ROOT}/sft_llama31_8b_asa_metrics.json" \
  --metrics "${OUTPUT_ROOT}/rl_llama31_8b_final_metrics.json" \
  --metrics "${OUTPUT_ROOT}/rl_llama31_8b_asa_metrics.json" \
  --expected 4874
echo "Completed A100 repair-strategy ablation in ${OUTPUT_ROOT}"
