# FinVeriSQL: A Three-Dimensional Taxonomy and Verification for Semantic Errors in Financial Text-to-SQL

[![arXiv](https://img.shields.io/badge/arXiv-paper%20link%20TBD-b31b1b.svg)](https://arxiv.org/abs/PLACEHOLDER)
[![License](https://img.shields.io/badge/License-TBD-lightgrey.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

This repository contains the official implementation of **FinVeriSQL**, a finance-aware verification and repair framework for Text-to-SQL. FinVeriSQL identifies semantic mismatches between a financial question and generated SQL, optionally probes ambiguous cases, and applies constrained repairs before producing the final query.

> **Publication placeholders:** replace the arXiv URL, license badge/link, adapter-release URLs and checksums, and the architecture figure below before publishing the repository.

## Overview

![FinVeriSQL architectural overview](figs/FinVeriSQL%20Architectural%20Overview.png)

FinVeriSQL uses three complementary dimensions when checking financial SQL:

1. **Financial object**: the business entity, account class, transaction type, or financial scope.
2. **Financial measure**: the amount, posting side, aggregation target, or unit being measured.
3. **Computation logic**: grouping, temporal scope, ranking, limiting, and analytical grain.

The full system uses an NL-only intent representation, a compact semantic SQL profile, targeted probing, and a specialized repair chain:

```text
Question + generated SQL
        -> intent decomposition + compact semantic profile
        -> finance-aware verification and probing
        -> specialized repair and re-verification
        -> final SQL
```

## Repository Layout

| Path | Purpose |
| --- | --- |
| `2_run_ablations.sh` | Publication runner for labeled evaluation, official test submission generation, or both. |
| `src/finverisql/` | Intent decomposition, semantic profiling, verification, and repair implementation. |
| `src/eval/` | Execution and accounting-semantic evaluation utilities. |
| `scripts/` | Experiment orchestration, official-test preparation, and submission export. |
| `data/booksql/` | Local BookSQL data, database, schema text, and schema annotations. |

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
OS: [Ubuntu version placeholder]
CUDA: [CUDA version placeholder]
Python: [Python version placeholder]
```

The Ollama-based baseline and verifier may run on other hardware, but the published reproduction target is the RTX 3090 Linux/CUDA environment above.

Docker must have NVIDIA GPU access through the NVIDIA Container Toolkit. Verify this before setup:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

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

## Reproducing Labeled Evaluation Results

Run the labeled BookSQL validation experiments:

```bash
./2_run_ablations.sh --mode labeled
```

Generic refinement uses two concurrent Ollama requests by default. On the RTX 3090 publication environment, begin with two workers and increase only after checking GPU memory and Ollama stability:

```bash
GENERIC_REFINE_WORKERS=2 ./2_run_ablations.sh --mode labeled
```

The run produces baseline, generic-refinement, Full FinVeriSQL, internal-ablation, ASA, and publication-table outputs under:

```text
data/outputs/finverisql/<RUN_ID>/
```

To resume a previous run, reuse its ID:

```bash
RUN_ID=eval_publication_YYYYMMDD_HHMMSS ./2_run_ablations.sh --mode labeled
```

### Pretrained SFT/RL Repair Adapters

The publication runner does not train SFT or RL repair adapters. It expects released, pretrained adapters. Provide either local directories:

```bash
SFT_ADAPTER_PATH=/path/to/sft_adapter \
RL_ADAPTER_PATH=/path/to/rl_adapter \
./2_run_ablations.sh --mode labeled
```

or immutable release archives and SHA-256 values:

```bash
SFT_ADAPTER_URL=https://PLACEHOLDER/sft_adapter.tar.gz \
SFT_ADAPTER_SHA256=PLACEHOLDER \
RL_ADAPTER_URL=https://PLACEHOLDER/rl_adapter.tar.gz \
RL_ADAPTER_SHA256=PLACEHOLDER \
./2_run_ablations.sh --mode labeled
```

To reproduce only the existing labeled artifacts without running the repair-strategy comparison:

```bash
RUN_ID=eval_publication_YYYYMMDD_HHMMSS \
RUN_REPAIR_STRATEGY_ABLATION=0 \
./2_run_ablations.sh --mode labeled
```

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

## Full Configuration

Run both labeled reproduction and official-test submission generation in one invocation:

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

Each labeled run writes metadata containing the run ID, Git revision, dirty-worktree state, model configuration, data paths, token limits, timeout, seed, and evaluation settings. Store the released adapter checksums and final output artifacts alongside the paper to make the reported result traceable.

Model output can still vary across inference runtime, model build, and hardware versions. The committed configuration and archived experiment outputs are the reference for reported results.

## Citation

```bibtex
@article{PLACEHOLDER,
  title={FinVeriSQL: A Three-Dimensional Taxonomy and Verification for Semantic Errors in Financial Text-to-SQL},
  author={PLACEHOLDER},
  year={2026}
}
```

## License

License information will be added before the public release. BookSQL is distributed under its own research-use license and access terms.
