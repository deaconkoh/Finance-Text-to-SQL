#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "Starting SFT/RL Training and Inference pipeline..."

# 1. Install dependencies
echo "Installing dependencies..."
pip install -r requirements-linux.txt

# IMPORTANT: Replace the placeholder below before running this script
export RUN_ID="<your_completed_2_run_ablations_run_id>"

# 2. Set paths
echo "Setting paths..."
export VAL_OUT_ROOT="data/outputs/finverisql/${RUN_ID}"
export FIXED_VERIFIER_JSONL="${VAL_OUT_ROOT}/debug/internal_ablation/full/full_verify.jsonl"
export BASELINE_EVAL_JSONL="${VAL_OUT_ROOT}/debug/baseline/qwen_few_shot_validation_evaluated.jsonl"

export TRAIN_DIR="data/outputs/finverisql/train_repair_learning"
export REPAIR_ABLATION_DIR="${VAL_OUT_ROOT}/debug/repair_strategy_ablation/full_fixed_verifier"

echo "Creating directories..."
mkdir -p "$TRAIN_DIR" "$REPAIR_ABLATION_DIR"

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

# 7. Train SFT Llama-3.1-8B repairer
echo "Training SFT repairer..."
python scripts/dev/train_sft_repairer.py \
  --train-jsonl "$REPAIR_ABLATION_DIR/sft_train_examples.jsonl" \
  --output-dir "$REPAIR_ABLATION_DIR/checkpoints/sft_llama31_8b" \
  --base-model meta-llama/Meta-Llama-3.1-8B-Instruct

# 8. Train RL repairer from SFT
echo "Training RL repairer..."
python scripts/dev/train_rl_repairer.py \
  --train-jsonl "$REPAIR_ABLATION_DIR/sft_train_examples.jsonl" \
  --sft-adapter-path "$REPAIR_ABLATION_DIR/checkpoints/sft_llama31_8b" \
  --output-dir "$REPAIR_ABLATION_DIR/checkpoints/rl_llama31_8b" \
  --base-model meta-llama/Meta-Llama-3.1-8B-Instruct \
  --db-path data/booksql/accounting.sqlite \
  --schema-annotations-path data/booksql/schema_annotations.json

# 9. Run final fixed-verifier repair strategy comparison
echo "Running repair strategy ablation..."
python scripts/run_repair_strategy_ablation.py \
  --fixed-verifier-jsonl "$FIXED_VERIFIER_JSONL" \
  --baseline-eval-jsonl "$BASELINE_EVAL_JSONL" \
  --output-dir "$REPAIR_ABLATION_DIR" \
  --prompt-model-name llama3.1:8b \
  --base-model meta-llama/Meta-Llama-3.1-8B-Instruct \
  --sft-adapter-path "$REPAIR_ABLATION_DIR/checkpoints/sft_llama31_8b" \
  --rl-adapter-path "$REPAIR_ABLATION_DIR/checkpoints/rl_llama31_8b" \
  --workers 4

# 10. Print final table
echo "Pipeline complete. Final Table:"
echo "--------------------------------------------------------"
cat "$REPAIR_ABLATION_DIR/repair_strategy_ablation_table.md"