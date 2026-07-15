# FinVeriSQL: A Three-Dimensional Taxonomy and Verification for Semantic Errors in Financial Text-to-SQL

[![arXiv](https://img.shields.io/badge/arXiv-paper%20link%20TBD-b31b1b.svg)](https://arxiv.org/abs/PLACEHOLDER)
[![License](https://img.shields.io/badge/License-TBD-lightgrey.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

This repository contains the official implementation of **FinVeriSQL**, a finance-aware verification and repair framework for Text-to-SQL. FinVeriSQL identifies semantic mismatches between a financial question and generated SQL, probes ambiguous cases, and applies constrained targeted repairs before producing the final query.

## Overview

![FinVeriSQL architectural overview](fig/FinVeriSQL%20Architectural%20Overview.png)

## Repository Layout

| Path                 | Purpose                                                                                                                                                                              |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `2_run_ablations.sh` | Full labeled evaluation runner and official BookSQL test-submission generator.                                                                                                        |
| `4_run_repair_ablation.sh` | SFT/RL repair-adapter training, inference, and repair-strategy ablation runner.                                                                                             |
| `5_build_development_excluded_report.sh` | Inference-free publication report over the validation complement excluding the frozen development sample.                                                                  |
| `src/finverisql/`    | Intent decomposition, semantic profiling, verification, and repair implementation.                                                                                                   |
| `src/eval/`          | Execution and accounting-semantic evaluation utilities.                                                                                                                              |
| `scripts/`           | Experiment orchestration, official-test preparation, and submission export.                                                                                                          |
| `data/booksql/`      | Local BookSQL data, database, schema text, and schema annotations.                                                                                                                   |
| `data/protocol/`     | Frozen protocol artifacts, including the 2,000 validation development IDs excluded from reportable local metrics.                                                                    |
| `supplement/`        | Supplementary information, including prompt templates `(prompts/)`, model run parameters `(configs/)`, and detailed reward functions / objectives for RL/SFT training `(training/)`. |

## Environment Setup

### 1. Clone and create an environment

```bash
git clone https://github.com/deaconkoh/Finance-Text-to-SQL.git
cd Finance-Text-to-SQL

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-linux.txt
```

This repository supports one publication environment: Linux with CUDA. `requirements-linux.txt` is the canonical dependency manifest for both the FinVeriSQL pipeline and pretrained SFT/RL adapter inference.

### Hardware and software

The reported experiments were run on:

```text
GPU: NVIDIA RTX 3090 (24 GB VRAM)
OS: Ubuntu 22.04.5 LTS
CUDA: 13.0 (Driver: 580.159.03)
Python: 3.10.12
```

The Ollama-based baseline and verifier may run on other hardware, but the published reproduction target is the RTX 3090 Linux/CUDA environment above.

### 2. Prepare BookSQL

BookSQL access is gated. Authenticate with Hugging Face and accept the dataset conditions before downloading it.

```bash
huggingface-cli login
python scripts/dev/setup_booksql.py
```

This prepares the labeled train/validation input, SQLite database, and prompt schema under `data/booksql/`.

### 3. Start Ollama and pull the fixed models

```bash
./1_setup_env.sh
```

The default publication configuration uses:

```text
Baseline generator: qwen2.5-coder:7b-instruct
Verifier/repair model: llama3.1:8b
Temperature: 0
Seed: 42
```

## Reproducing Publication Results

The publication workflow separates full artifact production from the final
development-excluded metrics. The full labeled runners preserve every validation
artifact for auditability. The final report then filters all systems by the same
committed 2,000 question IDs and recomputes only local evaluation metrics on the
disjoint complement. The development IDs are intentionally stored as a small,
versioned ID-only protocol file, not as a diagnostic-output artifact.

### 1. Produce the full labeled artifacts

Run the generator, generic baselines, Full FinVeriSQL, and internal ablations on
the labeled BookSQL validation split:

```bash
RUN_ID=eval_publication_YYYYMMDD_HHMMSS \
RUN_REPAIR_STRATEGY_ABLATION=0 \
./2_run_ablations.sh --mode labeled
```

The run produces baseline, generic-refinement, Full FinVeriSQL, internal-ablation,
ASA, and full-split artifact outputs under:

```text
data/outputs/finverisql/<RUN_ID>/
```

To resume a previous run, reuse its ID. Existing baseline evaluation artifacts are
validated and reused automatically. Set `FORCE_BASELINE_EVALUATION=1` only when
you intentionally need to recompute baseline SQL and ASA metrics.

```bash
RUN_ID=eval_publication_YYYYMMDD_HHMMSS ./2_run_ablations.sh --mode labeled
```

### Repair Strategy Ablation

Train the SFT and RL repair adapters only on the BookSQL train split, then run
the fixed-verifier repair-strategy comparison on the completed labeled run:

```bash
RUN_ID=eval_publication_YYYYMMDD_HHMMSS ./4_run_repair_ablation.sh
```

For inference-only reproduction with released adapters, set `RUN_TRAINING=0` and
place the released SFT and RL adapter directories at the checkpoint paths used by
the runner:

```text
data/outputs/finverisql/<RUN_ID>/debug/repair_strategy_ablation/full_fixed_verifier/checkpoints/sft_llama31_8b/
data/outputs/finverisql/<RUN_ID>/debug/repair_strategy_ablation/full_fixed_verifier/checkpoints/rl_llama31_8b/
```

The release should publish immutable archive locations and SHA-256 checksums for
both directories. This lets a reviewer reproduce repair inference without
retraining the adapters.

### 2. Generate the official BookSQL test submission

The official BookSQL test split does not expose gold SQL or gold execution
results. Generate the Full FinVeriSQL submission separately:

```bash
RUN_ID=official_test_YYYYMMDD_HHMMSS ./2_run_ablations.sh --mode official-test
```

### 3. Build the reportable tables

After both labeled runners and the official submission complete, build the final
tables without any model inference or adapter training:

```bash
RUN_ID=eval_publication_YYYYMMDD_HHMMSS \
OFFICIAL_TEST_RUN_ID=official_test_YYYYMMDD_HHMMSS \
./5_build_development_excluded_report.sh
```

This writes separate Main Comparison, Internal Ablation, Repair Strategy Ablation,
and Official BookSQL Test tables to:

```text
data/outputs/finverisql/<RUN_ID>/development_excluded_publication_report/publication_tables/
```

The three local tables use only validation examples outside
`data/protocol/booksql_validation_development_2000_ids.jsonl`. The official-test
table reports `Pending official BookSQL leaderboard submission` until the
leaderboard returns the official Test EX score.

## Official BookSQL Test Submission

The official BookSQL test split does not expose gold SQL. This repository therefore generates a leaderboard submission only; it does not report locally computed EX, ASA, correction rate, or ablation metrics for the test set.

```bash
RUN_ID=official_test_YYYYMMDD ./2_run_ablations.sh --mode official-test
```

The submission artifact is:

```text
data/outputs/finverisql/<RUN_ID>/official_test/submission.csv
```

It has the exact columns required by the BookSQL leaderboard:

```csv
id,pred_sql
0,SELECT ...
```

Upload this file to the official BookSQL leaderboard to obtain the official test execution score. The runner also writes `official_test_table.md` with a pending-score placeholder for the publication result table.

## Full FinVeriSQL Configuration

Run both raw labeled artifact generation and official-test submission generation in
one invocation. Run `5_build_development_excluded_report.sh` afterward to produce
the reportable tables:

```bash
./2_run_ablations.sh --mode all
```

The Full FinVeriSQL configuration is:

```text
intent mode: nl_only
profile mode: compact
probing mode: probe
repair framework: specialized_chain
```

## Reproducibility Notes

Each labeled run writes metadata containing the run ID, Git revision, dirty-worktree state, model configuration, data paths, token limits, timeout, seed, and evaluation settings. The derived report also records hashes of the frozen development-ID file, every source artifact, every filtered artifact, and the official submission CSV. Store the released adapter checksums and final output artifacts alongside the paper to make the reported result traceable.

Model output can still vary across inference runtime, model build, and hardware versions. The committed configuration and archived experiment outputs are the reference for reported results.

## Citation

Please cite our work if you found it useful!

```bibtex
@article{PLACEHOLDER,
  title={FinVeriSQL: A Three-Dimensional Taxonomy and Verification for Semantic Errors in Financial Text-to-SQL},
  author={PLACEHOLDER},
  year={2026}
}
```
