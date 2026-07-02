#!/usr/bin/env bash
set -euo pipefail

MODE="${MODE:-smoke}"
INPUT="data/outputs/finverisql/dev_diagnostics/exp05_sample_2000/verifier_identified/verified_sample_seed42_nl_only_compact_probe.jsonl"
SCHEMA_PATH="data/booksql/schema_annotations.json"
MODEL_NAME="mlx-community/Llama-3.1-8B-Instruct-4bit"
REUSE_RUN_DIR="${REUSE_RUN_DIR:-data/outputs/finverisql/debug/specialized_chain_smoke_split_20260628_134330}"

B_LIMIT="${B_LIMIT:-100}"
A_LIMIT="${A_LIMIT:-100}"

run_repair() {
    local input_path="$1"
    local repairs="$2"

    python scripts/run_finverisql_repair.py \
        --input-path "$input_path" \
        --output-path "$repairs" \
        --schema-path "$SCHEMA_PATH" \
        --semantic-repair-framework specialized_chain \
        --repair-model-name "$MODEL_NAME" \
        --repair-backend mlx-lm \
        --verifier-model-name "$MODEL_NAME" \
        --verifier-backend mlx-lm \
        --profile-mode compact \
        --probing-mode probe \
        --max-probes 7 \
        --intent-mode nl_only \
        --temperature 0.0 \
        --num-predict 768
}

evaluate_repairs() {
    local repairs="$1"
    local output_dir="$2"

    python -m src.eval.evaluate_final_sql \
        --input-jsonl "$repairs" \
        --output-jsonl "$output_dir/final_sql_evaluated.jsonl" \
        --metrics-json "$output_dir/final_sql_metrics.json" \
        --metrics-md "$output_dir/final_sql_metrics.md" \
        --workers 4
}

print_repair_counters() {
    local repairs="$1"

    python - <<PY
import json, collections
rows = [json.loads(l) for l in open("$repairs") if l.strip()]
print("stop_reason", collections.Counter(r.get("stop_reason") for r in rows))
print("scope", collections.Counter(r.get("scope_check_status") for r in rows))
print("scalar_group_by_gate", collections.Counter(
    (r.get("repair_attempt_sequence") or [{}])[-1].get("scalar_group_by_gate_status")
    for r in rows
))
print("final_sql_source", collections.Counter(r.get("final_sql_source") for r in rows))
PY
}

run_split() {
    local label="$1"
    local group="$2"
    local limit="$3"
    local split_dir="$OUT_DIR/$label"
    local split_input="$split_dir/${label}_input.jsonl"
    local repairs="$split_dir/specialized_chain_repairs.jsonl"

    mkdir -p "$split_dir"

    echo "=================================================="
    echo "Preparing $label: group=$group limit=$limit"
    echo "Output: $split_dir"
    echo "=================================================="

    python - <<PY
import json
from pathlib import Path

src = Path("$INPUT")
dst = Path("$split_input")
target_group = "$group"
limit = int("$limit")
rows = []

for line in src.open():
    row = json.loads(line)
    v = row.get("verification") or {}
    if row.get("evaluation_group") != target_group:
        continue
    if v.get("answers_question") is not False:
        continue
    if not v.get("repair_hint") or not v.get("stage2_failed_evidence"):
        continue

    rows.append(row)
    if len(rows) >= limit:
        break

with dst.open("w") as f:
    for row in rows:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

print(f"wrote {len(rows)} rows for {target_group} -> {dst}")
if len(rows) < limit:
    print(f"warning: requested {limit} rows but only found {len(rows)} eligible rows")
PY

    echo "Running specialized-chain repair for $label"
    run_repair "$split_input" "$repairs"

    echo "Evaluating final SQL for $label"
    evaluate_repairs "$repairs" "$split_dir"

    echo "Completed $label"
    echo "Metrics: $split_dir/final_sql_metrics.md"
    echo "--------------------------------------------------"
    cat "$split_dir/final_sql_metrics.md"
    print_repair_counters "$repairs"
}

run_smoke() {
    RUN_ID="specialized_chain_smoke_split_$(date +%Y%m%d_%H%M%S)"
    OUT_DIR="data/outputs/finverisql/debug/${RUN_ID}"
    mkdir -p "$OUT_DIR"

    echo "Run ID: $RUN_ID"
    echo "Root output directory: $OUT_DIR"
    echo "B_LIMIT=$B_LIMIT"
    echo "A_LIMIT=$A_LIMIT"

    run_split "b_only" "B_wrong_executable" "$B_LIMIT"
    run_split "a_only" "A_correct_executable" "$A_LIMIT"

    echo "Pipeline done. Root output directory: $OUT_DIR"
}

run_sample2000() {
    RUN_ID="exp06_scalar_group_by_gate_sample2000_$(date +%Y%m%d_%H%M%S)"
    OUT_DIR="data/outputs/finverisql/dev_diagnostics/${RUN_ID}"
    local repairs="$OUT_DIR/specialized_chain_repairs.jsonl"
    local reuse_a="$REUSE_RUN_DIR/a_only/specialized_chain_repairs.jsonl"
    local reuse_b="$REUSE_RUN_DIR/b_only/specialized_chain_repairs.jsonl"

    mkdir -p "$OUT_DIR"

    echo "Run ID: $RUN_ID"
    echo "Root output directory: $OUT_DIR"
    echo "Mode: sample2000"
    echo "Reuse source: $REUSE_RUN_DIR"

    if [[ ! -f "$reuse_a" || ! -f "$reuse_b" ]]; then
        echo "Missing reuse repair outputs:" >&2
        echo "  $reuse_a" >&2
        echo "  $reuse_b" >&2
        exit 1
    fi

    if [[ ! -s "$repairs" ]]; then
        echo "Seeding completed smoke repairs into $repairs"
        cat "$reuse_a" "$reuse_b" > "$repairs"
    else
        echo "Using existing repair output for resume: $repairs"
    fi

    echo "Seeded/existing repair rows: $(wc -l < "$repairs")"
    echo "Running specialized-chain repair for sample 2000 with resume"
    run_repair "$INPUT" "$repairs" 2>&1 | tee "$OUT_DIR/repair_run.log"

    echo "Evaluating final SQL for sample 2000"
    evaluate_repairs "$repairs" "$OUT_DIR" 2>&1 | tee "$OUT_DIR/evaluate_final_sql.log"

    echo "Completed sample 2000"
    echo "Metrics: $OUT_DIR/final_sql_metrics.md"
    echo "--------------------------------------------------"
    cat "$OUT_DIR/final_sql_metrics.md"
    print_repair_counters "$repairs"
    echo "Pipeline done. Root output directory: $OUT_DIR"
}

case "$MODE" in
    smoke)
        run_smoke
        ;;
    sample2000)
        run_sample2000
        ;;
    *)
        echo "Unsupported MODE: $MODE" >&2
        echo "Use MODE=smoke or MODE=sample2000." >&2
        exit 2
        ;;
esac
