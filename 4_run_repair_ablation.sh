#!/bin/bash

# Exit immediately on errors, unset values, or failed pipelines.
set -euo pipefail

echo "Starting SFT/RL Training and Inference pipeline..."

# Install only when explicitly requested; publication runs should use the
# already-provisioned, pinned Linux environment.
if [[ "${INSTALL_DEPS:-0}" == "1" ]]; then
  echo "Installing dependencies..."
  pip install -r requirements-linux.txt
fi

: "${RUN_ID:?Set RUN_ID to the labeled evaluation run used for the repair ablation.}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0,1}"
export SFT_PER_DEVICE_BATCH_SIZE="${SFT_PER_DEVICE_BATCH_SIZE:-4}"
export SFT_GRADIENT_ACCUMULATION_STEPS="${SFT_GRADIENT_ACCUMULATION_STEPS:-1}"
export RL_BATCH_SIZE="${RL_BATCH_SIZE:-4}"
export RL_MINI_BATCH_SIZE="${RL_MINI_BATCH_SIZE:-4}"
export DATASET_NUM_PROC="${DATASET_NUM_PROC:-4}"
export DATALOADER_NUM_WORKERS="${DATALOADER_NUM_WORKERS:-4}"
export REWARD_WORKERS="${REWARD_WORKERS:-4}"
export ADAPTER_INFERENCE_BATCH_SIZE="${ADAPTER_INFERENCE_BATCH_SIZE:-4}"
export OLLAMA_REPAIR_WORKERS="${OLLAMA_REPAIR_WORKERS:-4}"
export TRAIN_SEED="${TRAIN_SEED:-42}"
export RUN_TRAINING="${RUN_TRAINING:-1}"

if ! command -v accelerate >/dev/null 2>&1; then
  echo "accelerate is required. Install requirements-linux.txt first." >&2
  exit 1
fi
if ! command -v tee >/dev/null 2>&1; then
  echo "tee is required for stage logs." >&2
  exit 1
fi
if ! command -v nvidia-smi >/dev/null 2>&1 || [[ "$(nvidia-smi -L | wc -l | tr -d ' ')" -lt 2 ]]; then
  echo "This runner requires two visible NVIDIA GPUs." >&2
  exit 1
fi

# 2. Set paths
echo "Setting paths..."
export VAL_OUT_ROOT="data/outputs/finverisql/${RUN_ID}"
export FIXED_VERIFIER_JSONL="${VAL_OUT_ROOT}/debug/internal_ablation/full/full_verify.jsonl"
export BASELINE_EVAL_JSONL="${VAL_OUT_ROOT}/debug/baseline/qwen_few_shot_validation_evaluated.jsonl"

export TRAIN_DIR="data/outputs/finverisql/train_repair_learning"
export REPAIR_ABLATION_DIR="${VAL_OUT_ROOT}/debug/repair_strategy_ablation/full_fixed_verifier"

echo "Creating directories..."
mkdir -p "$TRAIN_DIR" "$REPAIR_ABLATION_DIR"

PIPELINE_LOG="${REPAIR_ABLATION_DIR}/run.log"
exec > >(tee -a "$PIPELINE_LOG") 2>&1

run_stage_logged() {
  local log_path="$1"
  shift
  {
    echo
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] $*"
    "$@"
  } 2>&1 | tee -a "$log_path"
}

if [[ "$RUN_TRAINING" == "1" ]]; then
# 3. Generate train-split baseline SQL
echo "Generating train-split baseline SQL..."
python -m src.baseline.baseline_runner \
  --model qwen \
  --backend ollama \
  --ollama-model-name qwen2.5-coder:7b-instruct \
  --temperature 0 \
  --timeout 300 \
  --max-new-tokens 128 \
  --split train \
  --prompt-setting few_shot \
  --data-path data/booksql/booksql_normalized.jsonl \
  --db-path data/booksql/accounting.sqlite \
  --schema-path data/booksql/schema.txt \
  --output-path "$TRAIN_DIR/qwen_few_shot_train.jsonl"

# 4. Evaluate train baseline
echo "Evaluating train baseline..."
python -m src.eval.evaluate_baseline_sql \
  --input-jsonl "$TRAIN_DIR/qwen_few_shot_train.jsonl" \
  --output-jsonl "$TRAIN_DIR/qwen_few_shot_train_evaluated.jsonl" \
  --metrics-json "$TRAIN_DIR/qwen_few_shot_train_metrics.json" \
  --db-path data/booksql/accounting.sqlite \
  --workers 4

# 5. Run the full verifier setting on train
echo "Running full verifier on train..."
python scripts/run_finverisql_verify.py \
  --input-path "$TRAIN_DIR/qwen_few_shot_train_evaluated.jsonl" \
  --output-path "$TRAIN_DIR/full_train_verify.jsonl" \
  --repair-output-path "$TRAIN_DIR/full_train_repair_queue.jsonl" \
  --skipped-output-path "$TRAIN_DIR/full_train_skipped.jsonl" \
  --schema-path data/booksql/schema_annotations.json \
  --profile-mode compact \
  --intent-mode nl_only \
  --probing-mode probe \
  --max-probes 7 \
  --backend ollama \
  --model-name llama3.1:8b \
  --temperature 0 \
  --num-predict 1024 \
  --timeout 300

# 6. Build SFT/RL repair-learning data
echo "Building SFT/RL repair-learning data..."
python scripts/dev/build_repair_learning_data.py \
  --fixed-verifier-jsonl "$TRAIN_DIR/full_train_verify.jsonl" \
  --output-jsonl "$REPAIR_ABLATION_DIR/sft_train_examples.jsonl" \
  --manifest-json "$REPAIR_ABLATION_DIR/sft_train_examples_manifest.json" \
  --schema-text-path data/booksql/schema.txt \
  --split train

# 7. Train SFT Llama-3.1-8B repairer across both GPUs
echo "Training SFT repairer..."
run_stage_logged "$REPAIR_ABLATION_DIR/sft_training.log" \
  accelerate launch --multi_gpu --num_processes 2 --mixed_precision bf16 scripts/dev/train_sft_repairer.py \
  --train-jsonl "$REPAIR_ABLATION_DIR/sft_train_examples.jsonl" \
  --output-dir "$REPAIR_ABLATION_DIR/checkpoints/sft_llama31_8b" \
  --base-model meta-llama/Meta-Llama-3.1-8B-Instruct \
  --per-device-train-batch-size "$SFT_PER_DEVICE_BATCH_SIZE" \
  --gradient-accumulation-steps "$SFT_GRADIENT_ACCUMULATION_STEPS" \
  --dataset-num-proc "$DATASET_NUM_PROC" \
  --dataloader-num-workers "$DATALOADER_NUM_WORKERS" \
  --seed "$TRAIN_SEED"

# 8. Train RL repairer from SFT across both GPUs
echo "Training RL repairer..."
run_stage_logged "$REPAIR_ABLATION_DIR/rl_training.log" \
  accelerate launch --multi_gpu --num_processes 2 --mixed_precision bf16 scripts/dev/train_rl_repairer.py \
  --train-jsonl "$REPAIR_ABLATION_DIR/sft_train_examples.jsonl" \
  --sft-adapter-path "$REPAIR_ABLATION_DIR/checkpoints/sft_llama31_8b" \
  --output-dir "$REPAIR_ABLATION_DIR/checkpoints/rl_llama31_8b" \
  --base-model meta-llama/Meta-Llama-3.1-8B-Instruct \
  --db-path data/booksql/accounting.sqlite \
  --schema-annotations-path data/booksql/schema_annotations.json \
  --batch-size "$RL_BATCH_SIZE" \
  --mini-batch-size "$RL_MINI_BATCH_SIZE" \
  --dataset-num-proc "$DATASET_NUM_PROC" \
  --reward-workers "$REWARD_WORKERS" \
  --seed "$TRAIN_SEED"
fi

# 9. Run final fixed-verifier repair strategy comparison
echo "Running repair strategy ablation..."
run_stage_logged "$REPAIR_ABLATION_DIR/repair_strategy_ablation.log" \
  python scripts/run_repair_strategy_ablation.py \
  --fixed-verifier-jsonl "$FIXED_VERIFIER_JSONL" \
  --baseline-eval-jsonl "$BASELINE_EVAL_JSONL" \
  --output-dir "$REPAIR_ABLATION_DIR" \
  --prompt-model-name llama3.1:8b \
  --base-model meta-llama/Meta-Llama-3.1-8B-Instruct \
  --sft-adapter-path "$REPAIR_ABLATION_DIR/checkpoints/sft_llama31_8b" \
  --rl-adapter-path "$REPAIR_ABLATION_DIR/checkpoints/rl_llama31_8b" \
  --workers 4 \
  --adapter-inference-batch-size "$ADAPTER_INFERENCE_BATCH_SIZE" \
  --ollama-workers "$OLLAMA_REPAIR_WORKERS" \
  --parallel-adapter-strategies

# 10. Print final table
echo "Pipeline complete. Final Table:"
echo "--------------------------------------------------------"
cat "$REPAIR_ABLATION_DIR/repair_strategy_ablation_table.md"
