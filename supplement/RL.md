# RL Repairer

Code references:
- `src/repair_learning/rl.py`
- `src/repair_learning/data.py`
- `src/repair_learning/prompting.py`
- `scripts/dev/train_rl_repairer.py`
- `scripts/run_repair_strategy_ablation.py`

## Initialization

The RL repairer starts from:

```yaml
base_model: meta-llama/Meta-Llama-3.1-8B-Instruct
initial_adapter: SFT adapter from scripts/dev/train_sft_repairer.py
adapter_loading: PeftModel.from_pretrained(base, sft_adapter_path, is_trainable=True)
```

This makes the learned repair comparison:

```text
Prompted Llama-3.1-8B vs SFT Llama-3.1-8B adapter vs RL-refined Llama-3.1-8B adapter
```

under the same fixed verifier outputs.

## Training Data and Model Input

RL uses the same repair-learning JSONL as SFT. Each example contains:

```json
{
  "prompt": "...same repair-time evidence prompt...",
  "completion": "...SFT target JSON...",
  "original_generated_sql": "...",
  "gold_sql": "...",
  "evaluation_group": "A_correct_executable|B_wrong_executable|C_non_executable",
  "verification": {...}
}
```

For rollouts, only the `prompt` is sent to the model as a user message:

```python
[{"role": "user", "content": example["prompt"]}]
```

The model generates a repair response. The expected generated content is the
same repair JSON schema used by prompted repair and SFT:

```json
{
  "repaired_sql": "<single repaired SQL query>",
  "edit_summary": "<short edit summary>",
  "confidence": "high | medium | low"
}
```

## Reward Function

Source: `src/repair_learning/rl.py::compute_repair_reward`.

Parsing:
- First parse JSON and read `repaired_sql`.
- If JSON parsing fails, fall back to extracting SQL from raw text.
- If no repaired SQL is recovered, return `invalid_penalty`.

Default weights:

```yaml
correction_reward: 2.0
asa_bonus: 0.5
corruption_penalty: -2.0
invalid_penalty: -1.0
remaining_wrong_penalty: -0.25
```

Reward logic:

```text
if repaired_sql is missing, invalid, or non-executable:
  reward = -1.0
elif original row is not Group A and repaired SQL is EX-incorrect:
  reward = -0.25
else:
  reward = 0

  if original evaluation_group == A_correct_executable and repaired SQL is not EX-correct:
    reward += -2.0

  else if original evaluation_group != A_correct_executable and repaired SQL is EX-correct:
    reward += +2.0

  if repaired SQL is EX-correct and ASA strict passes:
    reward += +0.5
```

| Outcome | Reward |
| --- | ---: |
| Invalid or non-executable repair | -1.0 |
| Initially wrong and remains EX-incorrect | -0.25 |
| Initially wrong and becomes EX-correct | +2.0 |
| Initially wrong, EX-correct and ASA-valid | +2.5 |
| Initially correct and becomes EX-incorrect | -2.0 |
| Initially correct, remains EX-correct and ASA-valid | +0.5 |

The remaining-wrong penalty is based on execution outcome, not SQL text equality:
an arbitrary changed-but-wrong executable query receives the same `-0.25` as an
unchanged wrong query. Invalid and non-executable repairs take precedence.

EX correctness is computed by executing repaired SQL and gold SQL on
`data/booksql/accounting.sqlite` and comparing fetched result tuples. ASA bonus
uses `evaluate_asa_row` with `execution_match=true`.

## Evaluation

The RL repairer is evaluated by `scripts/run_repair_strategy_ablation.py`.
It receives the fixed validation/test verifier output JSONL from the full
FinVeriSQL setting (`nl_only`, `compact`, `probe`, `specialized_chain`) and does
not rerun the verifier. Evaluation outputs are:
- `<strategy>_repairs.jsonl`
- `<strategy>_final_evaluated.jsonl`
- `<strategy>_final_metrics.json`
- `<strategy>_asa_metrics.json`
- `repair_strategy_ablation_table.md`

The table reports correction, corruption, net repair gain, and ASA under the
same fixed verifier candidate set used for prompted and SFT repair. For every
publication-facing repair-movement metric, the denominator is the shared
execution-evaluable population \(N = |A \cup B \cup C|\), excluding ambiguous
Group D rows:

\[
\mathrm{Correction} = \frac{\#(B/C \rightarrow A)}{N}, \qquad
\mathrm{Corruption} = \frac{\#(A \rightarrow B/C)}{N}, \qquad
\mathrm{NRG} = \frac{\#(B/C \rightarrow A) - \#(A \rightarrow B/C)}{N}.
\]

Conditional rates, such as correction among initially incorrect rows and
corruption among touched initially correct rows, remain available in detailed
diagnostics but are not used as primary cross-system comparison columns.
