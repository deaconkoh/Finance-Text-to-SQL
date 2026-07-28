# Workstation official BookSQL test

Accept the gated `Exploration-Lab/BookSQL` terms in a browser before running
the official test. On each machine, create the environment and authenticate:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-linux.txt
hf auth login
```

Both machines require these prepared local files:

```text
data/booksql/accounting.sqlite
data/booksql/schema.txt
data/booksql/schema_annotations.json
data/booksql/booksql_normalized.jsonl
```

The four-A100 SFT/PPO job may run concurrently with workstation internal
ablations:

```bash
chmod +x run_a100_repair_ablation_only.sh
./run_a100_repair_ablation_only.sh --setup-ollama
./run_a100_repair_ablation_only.sh
```

Run the workstation jobs sequentially:

```bash
chmod +x run_workstation_internal_ablations_only.sh
./run_workstation_internal_ablations_only.sh --setup-ollama
./run_workstation_internal_ablations_only.sh

chmod +x run_workstation_official_test_only.sh
./run_workstation_official_test_only.sh --setup-ollama
./run_workstation_official_test_only.sh
```

The setup command validates and reuses `adir_ws_ollama_0` and
`adir_ws_ollama_1`. Normal official-test execution does not invoke Docker,
`sudo`, or the internal-ablation runner.

Upload these files separately to the BookSQL leaderboard:

```text
data/outputs/finverisql/eval_publication_20260706_154755/
  official_test_experiments/qwen25_coder_7b_instruct/
    qwen_only/submission.csv
    adir_full/submission.csv
```

No gold SQL is available locally for the hidden test. The generated
`comparison_summary.json` and `run_manifest.json` therefore report official
test EX as pending manual leaderboard submission.
