# AWS Parallel Execution Runbook

This runbook completes the remaining publication experiments with the maximum
safe top-level concurrency supported by the existing runners. It does not
change prompts, model settings, experiment stages, output paths, or published
training hyperparameters.

The target fleet in `us-east-1` is:

| Instance | Count | Assignment |
| --- | ---: | --- |
| `p4d.24xlarge` | 1 | Train-baseline bootstrap and A100 repair-strategy runner |
| `g5.12xlarge` | 5 | One internal-ablation variant per instance |
| `g5.12xlarge` | 1 | Official-test runner |

Request at least 96 On-Demand P-family vCPUs and 288 On-Demand G/VT-family
vCPUs. If the G quota is lower, reserve one available G5 instance for the
official test and schedule the five variants through the remaining instances.

Use On-Demand instances. PPO output is atomic and cannot safely recover its
optimizer state after a Spot interruption.

## 1. Shared AWS preparation

Create these resources before launching the fleet:

1. A private, versioned, encrypted S3 bucket in `us-east-1`.
2. An EC2 instance role limited to the experiment bucket prefix.
3. A security group that allows SSH only from the operator's IP, or use SSM.
   Do not expose ports 11434 through 11437.
4. Budget notifications at the agreed thresholds. Seven instances have a much
   higher peak hourly cost than the nominal $250 monthly budget.
5. A launch template based on the same x86 Ubuntu 22.04 NVIDIA Driver GPU DLAMI,
   with encrypted EBS storage and the same IAM role and security group.

Use this S3 layout, replacing `BUCKET` with the actual bucket name:

```text
s3://BUCKET/finverisql/eval_publication_20260706_154755/
  inputs/
  setup-manifests/
  internal/
  official-test/
  repair-strategy/
  final/
```

Upload the ignored local database and its checksum to `inputs/`:

```bash
(cd data/booksql && shasum -a 256 accounting.sqlite) > accounting.sqlite.sha256
```

Upload both `data/booksql/accounting.sqlite` and
`accounting.sqlite.sha256` through the S3 console.

## 2. Common instance bootstrap

Run this on all seven instances, using the same pushed Git commit:

```bash
git clone REPOSITORY_URL Finance-Text-to-SQL
cd Finance-Text-to-SQL
git checkout AWS_PORTABILITY_COMMIT

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-linux.txt

sudo mkdir -p /home/ranjan/finveri_ollama_workspace
sudo chown -R "$(id -u):$(id -g)" /home/ranjan

aws s3 cp \
  s3://BUCKET/finverisql/eval_publication_20260706_154755/inputs/accounting.sqlite \
  data/booksql/accounting.sqlite
aws s3 cp \
  s3://BUCKET/finverisql/eval_publication_20260706_154755/inputs/accounting.sqlite.sha256 \
  /tmp/accounting.sqlite.sha256
(cd data/booksql && sha256sum -c /tmp/accounting.sqlite.sha256)

nvidia-smi -L
sudo docker ps
```

Authenticate with Hugging Face interactively on the P4d and official-test
instances. Do not place the token in Git, EC2 user data, or this runbook:

```bash
hf auth login
```

Run long jobs inside `tmux`. Assign every instance a unique S3 output prefix;
never synchronize the entire run root from multiple instances to one prefix.

## 3. Concurrent setup barrier

Run setup concurrently on all nodes before starting inference.

On each of the five internal-variant G5 nodes:

```bash
chmod +x run_workstation_internal_ablations_only.sh
./run_workstation_internal_ablations_only.sh --setup-ollama
```

On the official-test G5 node, the internal setup creates the two containers;
it does not run an internal ablation:

```bash
chmod +x run_workstation_internal_ablations_only.sh
chmod +x run_workstation_official_test_only.sh
./run_workstation_internal_ablations_only.sh --setup-ollama
./run_workstation_official_test_only.sh --setup-ollama
```

On the P4d node, select the first four EC2 GPU UUIDs:

```bash
export ADIR_GPU_UUIDS="$(
  nvidia-smi --query-gpu=uuid --format=csv,noheader |
  head -n 4 |
  paste -sd, -
)"

chmod +x run_a100_repair_ablation_only.sh
./run_a100_repair_ablation_only.sh --setup-ollama

export OLLAMA_HOSTS="http://127.0.0.1:11434,http://127.0.0.1:11435,http://127.0.0.1:11436,http://127.0.0.1:11437"
TRAIN_DIR="data/outputs/finverisql/train_repair_learning"
mkdir -p "$TRAIN_DIR"
for index in 0 1 2 3; do
  sudo docker exec "adir_a100_ollama_${index}" \
    ollama pull qwen2.5-coder:7b-instruct
done

IMAGE_ID="$(sudo docker image inspect --format '{{.Id}}' ollama/ollama:latest)"
python3 scripts/hardware_ablation_utils.py ollama-health \
  --hosts "$OLLAMA_HOSTS" \
  --model qwen2.5-coder:7b-instruct \
  --manifest "$TRAIN_DIR/ollama_qwen_manifest.json" \
  --record --image-id "$IMAGE_ID"
```

Upload each generated Ollama manifest to `setup-manifests/` under a unique
instance name. Before starting inference, require every node to have:

- the same Git commit and source-input checksums;
- the same Ollama image ID and API version;
- the same `llama3.1:8b` model digest;
- the expected endpoint count and per-container parallelism.

The official node and the P4d train bootstrap must also report the same
`qwen2.5-coder:7b-instruct` digest. If any value differs, do not start that
node's experiment.

## 4. Five internal variants in parallel

Run exactly one variant on each G5 node. For example:

```bash
ABLATION_VARIANTS=wo_intent_decomposer \
  ./run_workstation_internal_ablations_only.sh
```

Use one of these values on each of the other four nodes:

```text
direct_only
wo_compact_semantic_profile
wo_scope_constraints
wo_reverification_loop
```

When a node finishes, upload only its named directory. Example:

```bash
aws s3 sync \
  data/outputs/finverisql/eval_publication_20260706_154755/development_excluded_experiments/internal_ablation/wo_intent_decomposer/ \
  s3://BUCKET/finverisql/eval_publication_20260706_154755/internal/wo_intent_decomposer/
```

Designate the `wo_intent_decomposer` node as the coordinator and also preserve
its `cohort/`, `full/`, and `ollama_workstation_manifest.json` artifacts.

## 5. Official test in parallel

The official test is independent of all five internal variants and the A100
pipeline. Start it as soon as the setup-manifest barrier passes:

```bash
./run_workstation_official_test_only.sh
```

Synchronize only this directory to `official-test/`:

```text
data/outputs/finverisql/eval_publication_20260706_154755/
  official_test_experiments/qwen25_coder_7b_instruct/
```

Also preserve the Hugging Face source file referenced by
`booksql_official_test_source_manifest.json`. A replacement instance must
restore that file at its recorded cache path before resuming.

## 6. P4d train-baseline bootstrap

The A100 runner requires this missing immutable input:

```text
data/outputs/finverisql/train_repair_learning/
  qwen_few_shot_train_evaluated.jsonl
```

Generate it once on the P4d with the existing baseline implementation. Do not
add a bootstrap mode to the A100 runner.

```bash
export OLLAMA_HOSTS="http://127.0.0.1:11434,http://127.0.0.1:11435,http://127.0.0.1:11436,http://127.0.0.1:11437"
TRAIN_DIR="data/outputs/finverisql/train_repair_learning"
mkdir -p "$TRAIN_DIR"

python3 -m src.baseline.baseline_runner \
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
  --output-path "$TRAIN_DIR/qwen_few_shot_train.jsonl" \
  --attempt-log-path "$TRAIN_DIR/qwen_few_shot_train_attempts.jsonl" \
  --require-all-success \
  --workers 32

python3 -m src.eval.evaluate_baseline_sql \
  --input-jsonl "$TRAIN_DIR/qwen_few_shot_train.jsonl" \
  --output-jsonl "$TRAIN_DIR/qwen_few_shot_train_evaluated.jsonl" \
  --metrics-json "$TRAIN_DIR/qwen_few_shot_train_metrics.json" \
  --db-path data/booksql/accounting.sqlite \
  --workers 32
```

Validate the frozen artifact before continuing:

```bash
python3 - <<'PY'
import json
from pathlib import Path

path = Path("data/outputs/finverisql/train_repair_learning/qwen_few_shot_train_evaluated.jsonl")
rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
ids = [str(row.get("question_id")) for row in rows]
assert len(rows) == 70828, len(rows)
assert len(ids) == len(set(ids)), "duplicate train question_id"
assert all(row.get("status") in (None, "success") for row in rows)
assert all(row.get("generator") == "qwen" for row in rows)
assert all(row.get("prompt_setting") == "few_shot" for row in rows)
print("Validated 70,828 unique evaluated train-baseline rows")
PY

sha256sum \
  "$TRAIN_DIR/qwen_few_shot_train.jsonl" \
  "$TRAIN_DIR/qwen_few_shot_train_attempts.jsonl" \
  "$TRAIN_DIR/qwen_few_shot_train_evaluated.jsonl" \
  > "$TRAIN_DIR/qwen_few_shot_train.sha256"

python3 scripts/hardware_ablation_utils.py unload-ollama \
  --hosts "$OLLAMA_HOSTS" --model qwen2.5-coder:7b-instruct
```

Upload the complete `train_repair_learning/` directory to the P4d S3 prefix,
then start the existing A100 pipeline:

```bash
./run_a100_repair_ablation_only.sh
```

Keep `ADIR_GPU_UUIDS` exported in the shell that launches the runner.

## 7. Consolidate and verify

After all five internal nodes finish, download the four remote variant
directories onto the designated coordinator beside its local variant. Do not
copy partial per-node table files.

Run the internal runner once with all variants selected:

```bash
ABLATION_VARIANTS="wo_intent_decomposer,direct_only,wo_compact_semantic_profile,wo_scope_constraints,wo_reverification_loop" \
  ./run_workstation_internal_ablations_only.sh
```

All inference artifacts are already complete, so strict resume and evaluation
cache validation should skip model work and build the final combined table.

Before terminating any instance, verify:

- cohort derivation is exactly `7,605 -> 6,603 -> 4,874`;
- all internal and repair-strategy EX/ASA denominators are 4,874;
- there are no duplicate, missing, foreign, or context-mismatched IDs;
- the train baseline has 70,828 unique evaluated rows;
- SFT and RL fingerprints match their input data and configurations;
- both official CSVs have the official source ID set and `,id,pred_sql` shape;
- all final artifacts, logs, manifests, adapters, and checksums exist in S3.

Terminate each On-Demand instance as soon as its verified S3 upload completes.
Do not wait for the slowest fleet member before releasing finished workers.
