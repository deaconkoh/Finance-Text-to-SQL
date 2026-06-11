# Research Experiments Steps

## FinVeriSQL: finance-aware semantic verification and repair for Text-to-SQL

**Core Research Question:** Can a verifier that understands financial semantics catch and repair executable-but-wrong SQL before results reach users?

**Research Gap:** BookSQL shows that financial Text-to-SQL models make recurring domain-semantic errors, not just generic SQL errors. In its error analysis, models confuse credit, debit, and amount, apply filters to the wrong financial field such as product_service instead of account, and misuse transaction-type or account-type filters such as invoice, expense, income, accounts receivable, and accounts payable. These mistakes are especially problematic in finance because the generated SQL can still execute successfully while returning a financially wrong answer. For example, using amount instead of credit for invoice value, or filtering by product/service instead of account, may produce a plausible numeric result but answer a different financial question.

Recent work has begun to address semantic error detection and correction in Text-to-SQL. Frameworks such as SQLens, SQLFixAgent, and ErrorLLM show that generated SQL can be syntactically valid yet semantically wrong, and that post-generation refinement can improve reliability. However, these methods remain largely domain-general. They verify whether SQL aligns with the natural language question, schema, and execution behaviour, but they do not explicitly check whether the SQL is financially valid.

The gap is not the absence of SQL verification in general. The gap is the absence of a finance-aware SQL verification layer that targets recurring accounting and financial semantic errors such as credit/debit inversion, wrong account class, wrong statement family, wrong denominator, or wrong period basis. FinVeriSQL addresses this by checking generated SQL against explicit financial constraints and repairing queries that are executable but financially incorrect.

**Methodology:** FinVeriSQL is a post-generation verification and repair system for financial Text-to-SQL. Rather than modifying the generation process, it adds a finance-aware verification layer between the SQL generator and the user, inspecting candidate queries for financial semantic validity before results are returned. The current methodology is structured around: a fixed baseline SQL generator, a SQL parser, a schema-grounded semantic mapper, a compact semantic profile, a two-stage semantic equivalence verifier, and a downstream repairer.

## Experimental Setup and Dataset

## Dataset Selection & Preparation

Experiments are conducted on BookSQL, the primary benchmark for financial and accounting Text-to-SQL, which provides natural language questions, schema definitions, and gold SQL over a realistic bookkeeping database. BookSQL is selected because its published error analysis directly motivates the three constraint dimensions in this work and provides empirical grounding for the claim that financial semantic errors are structurally recurring rather than random.

**Note:** Evaluation on a second financial dataset (e.g. BULL) is treated as a stretch goal. If pursued, it would require separately annotating the new schema using the same schema annotation framework, and reporting results independently. This is not required for the core contribution claim but would strengthen generalisability.

Every BookSQL entry will be converted into a unified JSON format to maintain format consistency between experiments.

#### E.g.

```text
{
 "question_id": "booksql_0001",
  "db_id":        "company_finance",
  "question":    "What is the total invoice amount for April?",
  "gold_sql":    "SELECT SUM(credit) FROM transactions WHERE ...",
  "schema":      "...",  // serialised schema string, see below
  "split":       "validation"
}
```

#### Serialised Schema Format Example:

```text
Database schema:

Table: transactions
Columns:
  - transaction_id   : integer, primary key
  - transaction_type : text
  - account          : text
  - account_type     : text
  - debit            : numeric
  - credit           : numeric
  - amount           : numeric
  - transaction_date : date

Table: accounts
Columns:
  - account_id   : integer, primary key
  - account_name : text
  - account_type : text  [asset | liability | equity | income | expense]
  - account_code : text

Foreign keys:
  transactions.account → accounts.account_name
```

**Note:** BookSQL provides an internal train/val/test split, hence no manual splitting required

## Baseline SQL Generator

The purpose of this stage is to ensure that gains from verification are not attributable to generator quality. The generator is held fixed across all experimental conditions so that differences in final output quality can be attributed solely to the verification and repair layer.

Two pretrained generative models will be used:

| Model                     | Purpose                             |
| ------------------------- | ----------------------------------- |
| Arctic-Text2SQL-R1-7B     | SQL-specific Text-to-SQL baseline   |
| Qwen2.5-Coder-7B-Instruct | General coding-capable LLM baseline |

Every generated SQL and gold SQL will be executed against the BookSQL database. The primary target is the group where execution_status == “success” but execution_match == False

```text
result["execution_status"] = "success"
result["generated_result"] = gen_result
result["gold_result"]       = gold_result
result["execution_match"]   = (gen_result == gold_result)
```

Each Baseline Model will be run using zero-shot and few-shot settings, while keeping prompts identical across models.

#### E.g. Zero-Shot Prompt

```text
Instruction:
You are given a database schema and a natural language question.
Generate a valid SQL query that answers the question.
Return only the SQL query. No explanation.

Schema:
{schema}

Question:
{question}
```

#### E.g. Few-Shot Prompt

```text
Instruction:
You are given a database schema and a natural language question.
Generate a valid SQL query that answers the question.
Return only the SQL query. No explanation.

Here are some examples:

Question: {example_question_1}
SQL: {example_sql_1}

Schema:
{schema}
Question: {example_question_2}
SQL: {example_sql_2}

Now answer:
Schema:
{schema}
Question: {question}
SQL:
```

**Note:** Few-shot examples will come from the train split, using a sample of 3-5 diverse examples per query type and logging the selected examples for reproducibility. These generator few-shot examples are separate from the verifier. The current verifier does not use a Stage 3 retrieval pool or D3-specific few-shot selector. Instead, Stage 1 performs compact-profile semantic equivalence checking and Stage 2 generates a repair hint from the Stage 1 mismatch output.

## Build Error Subset & Labels

Using the execution results from the baseline models, partition validation output into 4 sub-groups.

| Group                  | Definition                                                             | Use in experiment                                            |
| ---------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------ |
| A - Correct Executable | Executes & matches gold SQL                                            | Test false rejection / over-correction risk                  |
| B - Wrong Executable   | Executes but does not match gold SQL                                   | Primary target of FinVeriSQL                                 |
| C - Non-executable     | Throws runtime/syntax error                                            | Report separately; not the main semantic-verification target |
| D - Ambiguous          | Partial match, null-only results, or cases requiring manual inspection | Exclude from primary metric or inspect manually              |

For Group B errors (wrong but executable SQL), assign one primary semantic error label. The current verifier taxonomy uses three labels aligned with the three financial equivalence dimensions.

| Error Label               | Dimension              | Meaning                                                                                                                  | Compact Example                                                                                                       |
| ------------------------- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| `financial_object_error`  | D1 - Financial object  | The SQL targets the wrong business object, entity, account class, transaction event, product/service, or payment status. | Question asks for vendor spend, but profile filters a customer name.                                                  |
| `financial_measure_error` | D2 - Financial measure | The SQL measures the wrong quantity, monetary vector, posting side, unit, or aggregation target.                         | Question asks "How many products were sold?", but profile counts distinct transactions instead of summing `Quantity`. |
| `computation_logic_error` | D3 - Computation logic | The SQL uses the wrong grouping, temporal period, ranking, ordering, limit, formula, or analytical grain.                | Question asks for monthly spend, but profile only applies a current-month filter and does not group by month.         |

Optional sublabels can still be recorded for analysis, especially under `computation_logic_error`.

| Optional Sublabel      | Meaning                                                           |
| ---------------------- | ----------------------------------------------------------------- |
| `aggregation_error`    | Wrong aggregation function, such as `AVG` where `SUM` is required |
| `formula_error`        | Wrong numerator, denominator, or arithmetic expression            |
| `temporal_scope_error` | Wrong date filter, period boundary, or period interpretation      |
| `grouping_grain_error` | Wrong or missing grouping level                                   |
| `ranking_limit_error`  | Wrong or missing `ORDER BY`, `LIMIT`, `MAX`, or `MIN` logic       |
| `stock_flow_mismatch`  | Point-in-time stock item treated as a period flow, or vice versa  |

Generic SQL failures and literal/entity issues should be handled separately:

- syntax/runtime failures belong in Group C;
- wrong literal values can be noted as an annotation detail, or treated as `financial_object_error` when the wrong value changes the financial object or entity being measured.

These labels support later error analysis and verifier evaluation.

Consider using Cohen’s Kappa to show that the taxonomy is clear enough for independent annotation. Give 20-30 already-labelled examples to another annotator using only the annotation guide, then compare agreement beyond chance.

#### Example Label:

```text
{
  "question_id":     "booksql_0142",
  "generator":       "qwen2.5-coder",
  "generated_sql":   "SELECT COUNT(DISTINCT Transaction_ID) FROM master_txn_table WHERE Product_Service = 'AI Courses';",
  "gold_sql":        "SELECT SUM(Quantity) FROM master_txn_table WHERE Product_Service = 'AI Courses';",
  "error_label":     "financial_measure_error",
  "error_sublabel":  null,
  "annotation_note": "The question asks for quantity sold, but the generated SQL counts transactions."
}
```

### Annotating the BookSQL Schema Columns

Generic SQL verifiers treat all columns as structurally equivalent. They cannot distinguish credit from amount because both are numeric columns, and they cannot tell whether a categorical filter such as `Account_type = 'Expense'` represents an expense scope or simply a generic text filter.

To make FinVeriSQL financially aware, we attach a fixed set of machine-readable semantic attributes to each BookSQL schema column. These annotations are written once, frozen before test evaluation, and used during SQL semantic mapping. The annotation layer does not contain question-specific information or gold SQL logic. It only describes what each schema column and selected categorical values mean.

It is not data leakage because it encodes schema-level domain knowledge, not the expected answer for any question.

| Attributes       | Purpose                                                                        | Example                                                                                                      |
| ---------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| semantic_role    | Main machine-readable role of the column                                       | financial_measure, account_type_classifier, transaction_type_classifier, transaction_date, entity_identifier |
| statement_family | Financial statement family associated with the column, if applicable           | income_statement, balance_sheet, cash_flow_statement, none                                                   |
| account_class    | IFRS-style account class represented by the column, if applicable              | asset, liability, equity, income, expense, none                                                              |
| measure_type     | Whether the column represents a flow, stock, categorical value, or non-measure | flow, stock, categorical, none                                                                               |
| sign_convention  | Double-entry bookkeeping direction or ambiguity                                | debit_normal, credit_normal, ambiguous, none                                                                 |
| unit             | Type of value stored in the column                                             | monetary, ratio, count, text, none                                                                           |
| temporal_grain   | Time granularity or date role                                                  | transaction_level, period_level, point_in_time, date_field, none                                             |
| entity_scope     | Business entity associated with the column                                     | account, transaction, customer, vendor, employee, product_service, none                                      |
| value_concepts   | Optional mapping from categorical values to financial concepts                 | Expense → expense, Income → revenue, Payment → payment                                                       |

For classifier columns, value-level annotations are included where appropriate. For example, `Account_type = 'Expense'` can be mapped to the financial concept `expense`, while `Transaction_TYPE = 'Payment'` can be mapped to the transaction concept `payment`.

This allows FinVeriSQL to ground SQL meaning in the schema annotation layer rather than relying on hidden hardcoded checks inside the verifier or decompiler.

## Building FinVeriSQL 😀

### Parse SQL Abstract Syntax Tree (AST) with sqlglot

FinVeriSQL first parses the candidate SQL generated by the baseline model. The parser extracts the SQL structure without deciding whether the SQL is financially correct.

The parser captures selected columns, aggregation expressions, tables, joins, filters, group-by columns, order-by expressions, limits, and parse errors. For filter predicates, all column references inside the predicate are preserved rather than only the first column. This prevents computed or column-to-column conditions from losing context.

#### Example Output:

```text
{
  "selected_columns": ["Amount"],
  "aggregations": [
    {
      "func": "SUM",
      "expression": "SUM(Amount)",
      "columns": ["Amount"]
    }
  ],
  "tables": ["master_txn_table"],
  "filters": [
    {
      "expression": "Quantity * Rate > 10000",
      "operator": ">",
      "columns": ["Quantity", "Rate"],
      "values": ["10000"]
    }
  ],
  "group_by": [],
  "order_by": [],
  "limit": null,
  "unsupported_lineage": false
}
```

Queries containing CTEs, subqueries, or derived tables are marked with unsupported_lineage = true. FinVeriSQL does not attempt full lineage tracing in v1 because resolving CTE output columns back to base schema columns requires more complex query lineage analysis. If unsupported lineage is detected, the verifier abstains rather than making confident semantic claims.

### Map SQL AST to Schema-Grounded Semantics

After parsing, the extracted SQL components are resolved against the annotated BookSQL schema. This stage converts raw SQL structure into schema-grounded financial semantics.

For example:

```text
SUM(Debit)
→ financial_measure column
→ measure_type = flow
→ sign_convention = debit_normal

Account_type = 'Expense'
→ account_type_classifier
→ value_concept = expense

Transaction_TYPE = 'Payment'
→ transaction_type_classifier
→ value_concept = payment
```

The semantic mapping layer produces a structured representation of what the SQL actually computes. It does not look at the natural language question and does not predict what the SQL should have done.

Example Semantic Mapping Output:

```text
{
  "object_scope": {
    "has_account_type_filter": true,
    "account_type_values": ["expense"],
    "account_type_concepts": ["expense"],
    "has_transaction_type_filter": false,
    "transaction_type_values": [],
    "transaction_type_concepts": [],
    "entity_filter_values": []
  },
  "measure_usage": {
    "aggregation_functions": ["sum"],
    "measure_types": ["flow"],
    "sign_conventions": ["debit_normal"],
    "ambiguous_measure_columns": []
  },
  "logic": {
    "date_conditions": [],
    "filter_conditions": [
      {
        "expression": "Account_type = 'Expense'",
        "operator": "=",
        "columns": ["Account_type"],
        "values": ["Expense"],
        "is_ambiguous": false
      }
    ],
    "group_by_columns": [],
    "order_by_expressions": [],
    "limit": null
  },
  "unsupported_lineage": false
}
```

Ambiguous column resolution is isolated. If an unqualified column can refer to multiple annotated schema columns, the ambiguity is recorded, but its possible meanings are not added to confirmed semantic fields. This prevents ambiguous SQL from polluting the verifier with false semantic claims.

### Build Compact Semantic Profile / Verifier Payload

The full schema-grounded semantic profile can be verbose because it preserves parser details, schema grounding traces, warnings, and intermediate mapping fields. For verifier prompting, FinVeriSQL now uses a deterministic compact semantic profile rather than the older FSIR layer.

The compact semantic profile is a verifier-facing JSON payload that describes what the candidate SQL computes. It is a projection of the schema-grounded semantic profile, not a new evaluative model. It removes empty, repeated, and verifier-irrelevant fields while preserving the evidence needed for finance-aware semantic verification.

Current workflow:

```text
Predicted SQL
→ SQL AST parsing
→ schema-grounded semantic mapping
→ compact semantic profile
→ two-stage semantic equivalence verification
→ repair hint / accept / abstain
```

The compact profile keeps six main evidence groups:

| Compact Field     | Purpose                                                                                                                        |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `tables`          | Table grain, transaction key, and row-grain evidence                                                                           |
| `scope`           | Business object filters such as customer, vendor, product/service, account, account type, transaction type, and payment status |
| `measurement`     | Selected metric, aggregation, physical measure, posting side, quantity/count interpretation, and table grain                   |
| `topology`        | Analytical grain, grouping, ordering, limit, temporal filters, and period hints                                                |
| `absence_signals` | Important missing structures such as missing transaction type, account type, grouping, temporal filter, ordering, or limit     |
| `warnings`        | Mapping warnings such as ambiguous values, unobserved literals, missing account context, or `COUNT(*)` grain risks             |

The compact profile is descriptive, not evaluative. It does not decide whether the SQL is correct. It only records the meaning of the SQL in a form that the verifier can compare against the natural-language question.

#### Why the old FSIR layer was removed

Earlier versions converted the semantic mapping into a Financial Semantic Intermediate Representation (FSIR) with separate financial concept, measurement, and reporting topology layers. That helped move away from plain-text SQL decompilation, but it became redundant once the schema-grounded semantic mapper already produced the same evidence.

The latest architecture therefore treats the schema-grounded semantic profile as the main intermediate representation. The compact profile is only a deterministic verifier payload for context efficiency and cleaner prompting. It should not be presented as a separate research contribution.

#### Example Compact Semantic Profile Snippet

Candidate SQL:

```sql
SELECT COUNT(DISTINCT Transaction_ID)
FROM master_txn_table
WHERE Product_Service = 'AI Courses';
```

Compact semantic profile snippet:

```text
{
  "tables": {
    "master_txn_table": {
      "grain": "transaction_line",
      "transaction_key": "Transaction_ID"
    }
  },
  "scope": [
    {
      "role": "product_service",
      "column": "master_txn_table.Product_Service",
      "operator": "=",
      "values": ["AI Courses"],
      "semantic_role": "product_service_identifier",
      "entity_scope": "product_service",
      "value_status": "no_value_map"
    }
  ],
  "measurement": [
    {
      "source": "aggregation",
      "expression": "COUNT(DISTINCT Transaction_ID)",
      "function": "COUNT",
      "semantic_operation": "distinct_transaction_count",
      "table_grain": "transaction_line",
      "transaction_key": "Transaction_ID",
      "distinct": true
    }
  ],
  "topology": {
    "analytical_grain": "global_summary",
    "group_by": "none",
    "order_by": "none",
    "limit": "none",
    "temporal_filter": []
  },
  "absence_signals": {
    "transaction_type_filter": "missing",
    "account_type_filter": "missing",
    "grouping": "none",
    "temporal_filter": "missing",
    "ordering": "none",
    "limit": "none"
  },
  "warnings": []
}
```

This profile means "count distinct transactions involving AI Courses." It does not mean "sum the quantity of AI Courses sold." The verifier should therefore reject it for a question such as "How many AI Courses did we sell?" because `COUNT(DISTINCT Transaction_ID)` is not equivalent to `SUM(Quantity)`.

### Apply Semantic Verification

The verifier compares the compact semantic profile against the original natural language question using a two-stage semantic equivalence task.

The verifier does not regenerate SQL and does not independently predict a full expected query. It checks whether the SQL meaning represented by the compact profile is semantically equivalent to the question meaning across three finance-specific dimensions.

| Dimension              | Question Asked                                       | Profile Checked                                           | Example Mismatch                                                    |
| ---------------------- | ---------------------------------------------------- | --------------------------------------------------------- | ------------------------------------------------------------------- |
| D1 - Financial Object  | What business or financial object is being measured? | `scope`, `absence_signals`, and object-related `warnings` | Question asks for vendor spend, but profile filters a customer.     |
| D2 - Financial Measure | How is it measured?                                  | `measurement` and measure-related `warnings`              | Question asks for quantity sold, but profile counts transactions.   |
| D3 - Computation Logic | Over what period, grouping, ranking, or granularity? | `topology`, `absence_signals`, and temporal hints         | Question asks for monthly spend, but profile has no month grouping. |

#### Stage 1 - Semantic Equivalence Verification and Error Classification

Stage 1 asks whether the compact profile has the same financial meaning as the user question. It is not only checking whether evidence exists; it compares the meaning of the question and the meaning of the generated SQL profile.

Stage 1 returns:

- `dimension_alignment`: whether D1, D2, and D3 are the same, different, or unclear;
- `evidence_match`: sufficient, insufficient, or unclear;
- `answers_question`: true, false, or null;
- `primary_mismatch_type`: one of `financial_object_error`, `financial_measure_error`, or `computation_logic_error` when rejected;
- `mismatch_detail`, `failed_evidence`, and `confidence`.

Compact Stage 1 output snippet:

```text
{
  "dimension_alignment": {
    "financial_object": "same",
    "financial_measure": "different",
    "computation_logic": "same"
  },
  "evidence_match": "insufficient",
  "answers_question": false,
  "ambiguous": false,
  "primary_mismatch_type": "financial_measure_error",
  "mismatch_detail": "The question asks for quantity sold, but the profile counts distinct transactions.",
  "failed_evidence": [
    "quantity sold is required",
    "COUNT(DISTINCT Transaction_ID) is not equivalent to SUM(Quantity)"
  ],
  "confidence": "high"
}
```

Important BookSQL interpretation rules used by Stage 1:

| Question Pattern                                | Required Meaning                                                       | Non-equivalent Profile Meaning                                                   |
| ----------------------------------------------- | ---------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| "How many [product/service] did we sell?"       | Quantity sold, usually `SUM(Quantity)`                                 | `COUNT(*)`, `COUNT(Transaction_ID)`, or `COUNT(DISTINCT Transaction_ID)`         |
| "How many times did we sell [product/service]?" | Sale-event or transaction count                                        | Quantity sold                                                                    |
| "Monthly spend by vendor"                       | Vendor/payee scope, spend-compatible measure, and month-level grouping | Customer scope, generic `Amount`, or current-month filter without month grouping |
| "Revenue" / "sales amount"                      | Revenue-compatible or credit-side monetary measure                     | Generic `Amount` when direction matters                                          |
| "Expense" / "cost" / "spend"                    | Expense-compatible or debit-side monetary measure                      | Generic `Amount` when direction matters                                          |
| "This fiscal year"                              | Fiscal-year period                                                     | `trailing_1_year` unless explicitly equivalent in the data setup                 |

#### Stage 2 - Repair Hint Generation

Stage 2 runs only when Stage 1 returns a definite rejection with a valid primary mismatch type. Stage 2 receives the Stage 1 mismatch type, mismatch detail, and failed evidence, then produces a targeted repair hint.

Stage 2 does not reclassify the error and does not generate corrected SQL.

Example Stage 2 output:

```text
{
  "repair_hint": "Use a quantity-compatible expression such as SUM(Quantity) instead of counting distinct transactions, while preserving the Product_Service filter.",
  "confidence": "high"
}
```

If the profile has parse errors, unsupported lineage, unresolved ambiguity, or insufficient grounding, FinVeriSQL abstains instead of issuing a confident repair instruction.

### Route to repair or abstention

Based on the two-stage verifier output, each candidate SQL is routed to one of three paths.

| Verifier Output                                               | Route   | Action                                                                                                                 |
| ------------------------------------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------- |
| `answers_question = true`                                     | Accept  | Keep the generated SQL unchanged.                                                                                      |
| `answers_question = false` with valid `primary_mismatch_type` | Repair  | Send the original question, generated SQL, compact profile, Stage 1 mismatch, and Stage 2 repair hint to the repairer. |
| `answers_question = null` or `should_abstain = true`          | Abstain | Do not repair automatically; mark the case for manual review or clarification.                                         |

The repairer is downstream from the verifier. The verifier identifies what is wrong and produces a repair hint. The repairer attempts to revise the SQL.

Repairer inputs:

```text
{
  "question": "{question}",
  "generated_sql": "{generated_sql}",
  "compact_semantic_profile": "{compact_profile}",
  "primary_mismatch_type": "financial_measure_error",
  "mismatch_detail": "The SQL counts transactions instead of measuring quantity sold.",
  "failed_evidence": [
    "quantity sold is required",
    "COUNT(DISTINCT Transaction_ID) is not equivalent to SUM(Quantity)"
  ],
  "repair_hint": "Use SUM(Quantity) instead of COUNT(DISTINCT Transaction_ID)."
}
```

#### Example Repair Prompt:

```text
The SQL below was rejected by a finance-aware semantic verifier.

Question:
{question}

Original SQL:
{generated_sql}

Verifier finding:
{primary_mismatch_type}: {mismatch_detail}

Failed evidence:
{failed_evidence}

Repair hint:
{repair_hint}

Revise the SQL so that it answers the question while preserving correct parts of the original query.
Return only the corrected SQL. No explanation.
```

#### Example Abstention Message:

```text
The generated SQL could not be confidently verified against your financial question.
The query may involve unsupported lineage, unresolved ambiguity, or insufficient semantic grounding.

Please inspect this case manually or provide additional clarification.
```

After repairs, execute the repaired SQL and compare against gold. Store the full before/after record for every repaired query.

```text
{
  "question_id":          "booksql_0142",
  "generator":            "qwen2.5-coder",
  "profile_format":       "compact",
  "before_exec_match":    false,
  "answers_question":     false,
  "mismatch_type":        "financial_measure_error",
  "repair_hint":          "Use SUM(Quantity) instead of counting transactions.",
  "repaired_sql":         "SELECT SUM(Quantity) FROM master_txn_table WHERE Product_Service = 'AI Courses';",
  "after_exec_match":     true,
  "repair_success":       true,
  "route":                "repair"
}
```

## Ablations

There will be 2 main forms of comparison system: a main comparison table and an internal ablation table.

### Main Comparison Table (Test Set):

| System                                             | Purpose                               |
| -------------------------------------------------- | ------------------------------------- |
| Generator only                                     | Unverified baseline                   |
| Generator + generic self-refine                    | Generic reflection baseline           |
| Generator + execution-only repair                  | Show execution signal is insufficient |
| Generator + LLM-only verifier (no compact profile) | Show LLM alone is not enough          |
| Generator + FinVeriSQL full                        | Main contribution                     |

### Internal Ablation Table (Evaluation Set):

The current runner supports profile-level ablations using `--profile-mode`.

| System / Mode               | Purpose                                                                                                         |
| --------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `--profile-mode ast`        | Tests whether parsed SQL structure alone is enough for verification.                                            |
| `--profile-mode semantic`   | Tests whether the full schema-grounded semantic profile improves verification.                                  |
| `--profile-mode compact`    | Tests whether the compact verifier payload preserves useful semantic evidence while reducing redundant context. |
| Remove schema annotations   | Tests whether column-level semantic grounding matters.                                                          |
| Remove value-level concepts | Tests whether classifier value mappings improve financial object and event understanding.                       |
| No abstention mechanism     | Tests whether explicit uncertainty handling improves reliability.                                               |

Example compact-mode verifier command:

```bash
python scripts/run_finverisql_verify.py \
  --input-path data/outputs/evaluated/qwen_few_shot_validation_evaluated.jsonl \
  --output-path data/outputs/finverisql/dev_diagnostics/compact_groupB_llama31.jsonl \
  --schema-path data/booksql/schema_annotations.json \
  --backend mlx-lm \
  --model-name mlx-community/Llama-3.1-8B-Instruct-4bit \
  --evaluation-group B_wrong_executable \
  --profile-mode compact \
  --limit 40 \
  --num-predict 1024
```

Example internal ablation commands:

```bash
# AST-only verifier input
python scripts/run_finverisql_verify.py ... --profile-mode ast

# Full semantic profile verifier input
python scripts/run_finverisql_verify.py ... --profile-mode semantic

# Compact semantic profile verifier input
python scripts/run_finverisql_verify.py ... --profile-mode compact
```

### Evaluation Metrics:

| Metric                                   | Purpose                                                                                                           |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Execution Accuracy <br>(Before vs After) | Verifies whether the contribution improves financial SQL correctness overall.                                     |
| End-to-end Correction Rate               | Measures how often wrong executable SQL is successfully repaired.                                                 |
| Wrong-SQL Detection F1                   | Measures whether the verifier detects executable-wrong SQL without relying on repair success alone.               |
| Harmful Over-correction Rate             | Measures whether FinVeriSQL incorrectly modifies SQL that was already execution-correct.                          |
| Abstention Rate                          | Measures how often the verifier refuses to make a confident judgement. Report separately for Group A and Group B. |

## Error Analysis

There will be reports on errors in three layers.

| Layer                         | Purpose                                                                                                                                                                                  |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Generator Distribution        | Report % executable-correct, % executable-wrong, and % non-executable per model. The execution script already classifies outputs into these groups, so aggregate counts and percentages. |
| Error Type Breakdown          | Count and percentage of each manually labelled error type in the executable-wrong group: `financial_object_error`, `financial_measure_error`, and `computation_logic_error`.             |
| FinVeriSQL Behaviour per Type | Report detected, missed, repaired, over-corrected, and abstained cases per error dimension. Cross-reference the labelled error subset against FinVeriSQL output records.                 |

Definitions:

- **Detected**: Group B query where Stage 1 returns `answers_question = false`.
- **Missed**: Group B query where Stage 1 returns `answers_question = true`.
- **Repaired**: Group B query where the repaired SQL becomes execution-correct.
- **Over-corrected**: Group A query that was originally execution-correct but was rejected and repaired into an execution-incorrect query.
- **False rejection**: Group A query where Stage 1 returns `answers_question = false`.
- **Abstained**: Stage 1 returns `evidence_match = "unclear"`, or profile status triggers abstention before LLM verification.

Report abstention rate separately. If abstention is too high, the verifier is refusing hard cases rather than solving them. If abstention is never triggered, inspect the Stage 1 prompt and profile-status handling because uncertainty may not be represented correctly.

Also inspect the `dimension_alignment` field. It is useful for diagnosing whether errors are caused by:

- object/entity mismatch;
- measure mismatch such as `COUNT(DISTINCT Transaction_ID)` vs `SUM(Quantity)`;
- computation mismatch such as current-month filter vs monthly grouping;
- overly strict rejection of missing secondary boundaries.
