# Research Experiments Steps

## FinVeriSQL: finance-aware semantic verification and repair for Text-to-SQL

**Core Research Question:** Can a verifier that understands financial semantics catch and repair executable-but-wrong SQL before results reach users?

**Research Gap:** BookSQL shows that financial Text-to-SQL models make recurring domain-semantic errors, not just generic SQL errors. In its error analysis, models confuse credit, debit, and amount, apply filters to the wrong financial field such as product_service instead of account, and misuse transaction-type or account-type filters such as invoice, expense, income, accounts receivable, and accounts payable. These mistakes are especially problematic in finance because the generated SQL can still execute successfully while returning a financially wrong answer. For example, using amount instead of credit for invoice value, or filtering by product/service instead of account, may produce a plausible numeric result but answer a different financial question.

Recent work has begun to address semantic error detection and correction in Text-to-SQL. Frameworks such as SQLens, SQLFixAgent, and ErrorLLM show that generated SQL can be syntactically valid yet semantically wrong, and that post-generation refinement can improve reliability. However, these methods remain largely domain-general. They verify whether SQL aligns with the natural language question, schema, and execution behaviour, but they do not explicitly check whether the SQL is financially valid.

The gap is not the absence of SQL verification in general. The gap is the absence of a finance-aware SQL verification layer that targets recurring accounting and financial semantic errors such as credit/debit inversion, wrong account class, wrong statement family, wrong denominator, or wrong period basis. FinVeriSQL addresses this by checking generated SQL against explicit financial constraints and repairing queries that are executable but financially incorrect.

**Methodology:** FinVeriSQL is a post-generation verification and repair system for financial Text-to-SQL. Rather than modifying the generation process, it adds a finance-aware verification layer between the SQL generator and the user, inspecting candidate queries for financial semantic validity before results are returned. The current methodology is structured around: a fixed baseline SQL generator, a SQL parser, a schema-grounded semantic mapper, a compact semantic profile, a metadata-aware intent decomposer, an intent/profile verifier with optional targeted probing, and a downstream repairer.

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

### Accounting-Semantic Accuracy (ASA)

Execution accuracy on the original BookSQL database remains necessary, but exact-result matching alone can hide finance-semantic mistakes. A generated query may use `Debit` instead of `Credit`, count transactions instead of summing `Quantity`, swap financial scope, or use a status proxy for a monetary balance and still return the same result on sparse or symmetric data.

The active headline finance-aware metric is **Accounting-Semantic Accuracy (ASA)**:

```text
ASA(x) = 1[EX(x) = 1 and Inv(x) = 1]
```

`EX` is the original execution-match result on the BookSQL database. `Inv` is deterministic invariant validity from the Financial Contradiction Rate (FCR) checker:

| FCR status                     |    Inv | Meaning                                                                                                                                     |
| ------------------------------ | -----: | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `no_financial_contradiction`   |    `1` | No deterministic hard financial contradiction was found.                                                                                    |
| `hard_financial_contradiction` |    `0` | The generated SQL changes financial meaning in a narrow, deterministic way.                                                                 |
| `not_evaluable`                | `None` | The checker cannot safely decide because of parse failure, unsupported finance-bearing lineage/expression, or missing financial annotation. |

ASA therefore measures whether the final SQL both matches the original execution result and avoids deterministic hard financial contradictions. It is not an LLM judge and it does not call the verifier or repairer. Gold SQL is used only for offline evaluation through original execution matching and FCR comparison.

Each row is evaluated as follows:

| Condition                              |     EX |    Inv | `asa_strict` | `asa_lower_bound` | Decision available |
| -------------------------------------- | -----: | -----: | -----------: | ----------------: | ------------------ |
| missing `execution_match`              | `None` | `None` |       `None` |            `None` | no                 |
| `execution_match = False`              |    `0` | `None` |          `0` |               `0` | yes                |
| `execution_match = True`, `Inv = 1`    |    `1` |    `1` |          `1` |               `1` | yes                |
| `execution_match = True`, `Inv = 0`    |    `1` |    `0` |          `0` |               `0` | yes                |
| `execution_match = True`, `Inv = None` |    `1` | `None` |       `None` |               `0` | no                 |

For EX-failing rows, ASA is decisively zero and the invariant checker is not run. For EX-passing rows where FCR is not evaluable, strict ASA is unknown and the lower-bound ASA value is zero.

FCR remains the deterministic diagnostic source behind `Inv`. Current hard finding coverage includes posting-side reversals, incompatible financial filters, balance/count proxy substitutions, rate-as-total-amount substitutions, invoice/bill transaction-type substitutions, balance-stock-to-flow substitutions, and incompatible financial selected outputs. FCR warnings are recorded for analysis but do not make `Inv = 0`; only hard contradictions fail the invariant.

The active ASA implementation lives in:

- `src/asa_metrics/asa_metrics.py`
- `src/asa_metrics/financial_contradiction.py`
- `src/eval/evaluate_asa.py`

Run ASA with:

```bash
python -m src.eval.evaluate_asa \
  --before-jsonl data/outputs/evaluated/qwen_few_shot_validation_evaluated.jsonl \
  --after-jsonl data/outputs/finverisql/dev_diagnostics/exp05_sample_2000/repairs_final_sql_evaluated.jsonl \
  --schema-path data/booksql/schema_annotations.json \
  --output-json data/outputs/metrics_experiments/exp02_asa_fcr_v2/baseline_vs_repairs_final_sql_asa_metrics.json \
  --output-md data/outputs/metrics_experiments/exp02_asa_fcr_v2/baseline_vs_repairs_final_sql_asa_metrics.md \
  --row-output-jsonl data/outputs/metrics_experiments/exp02_asa_fcr_v2/baseline_vs_repairs_final_sql_asa_rows.jsonl
```

The row-level ASA output records `EX`, `Inv`, `asa_strict`, `asa_lower_bound`, decision availability, FCR primary status, hard finding codes, not-evaluable codes, and warning codes. Aggregate reports include execution accuracy, ASA strict accuracy, ASA lower-bound accuracy, invariant evaluability, FPER, FPER lower bound, hard finding counts, and not-evaluable reason counts.

Accounting-adversarial fixture evaluation and Eq_acct are no longer part of the active ASA pipeline. They remain archived as legacy research code under `src/asa_metrics/old/` and should not be presented as the current headline metric.

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

**Note:** Few-shot examples will come from the train split, using a sample of 3-5 diverse examples per query type and logging the selected examples for reproducibility. These generator few-shot examples are separate from the verifier. The current verifier does not use a Stage 3 retrieval pool or D3-specific few-shot selector. Instead, the verifier uses a three-stage process: Stage 1 decomposes the user question into a SQL-independent, metadata-aware intent representation; Stage 2 compares that intent representation against the compact semantic profile, with optional targeted probing for ambiguous checks; and Stage 3 generates a repair hint from the confirmed mismatch evidence.

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

User question + metadata guide
→ metadata-aware intent decomposition

Intent representation + compact semantic profile
→ semantic equivalence verification
→ optional targeted probing for ambiguous checks
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

The verifier now uses a three-stage semantic verification process. The key change is that FinVeriSQL first decomposes the user question into a SQL-independent intent representation before comparing it with the candidate SQL profile. This reduces anchoring bias: the intent decomposer does not see the candidate SQL, so it is less likely to reinterpret the question to match a wrong but plausible SQL query.

The verifier does not regenerate SQL and does not independently predict a full expected query. It checks whether the SQL meaning represented by the compact profile is semantically equivalent to the metadata-aware intent representation across three finance-specific dimensions.

| Dimension              | Intent Representation Asked                          | Compact Profile Checked                                 | Example Mismatch                                                    |
| ---------------------- | ---------------------------------------------------- | ------------------------------------------------------- | ------------------------------------------------------------------- |
| D1 - Financial Object  | What business or financial object is being measured? | `scope`, `absence_signals`, and object-related warnings | Question asks for vendor spend, but profile filters a customer.     |
| D2 - Financial Measure | How is it measured?                                  | `measurement` and measure-related warnings              | Question asks for quantity sold, but profile counts transactions.   |
| D3 - Computation Logic | Over what period, grouping, ranking, or granularity? | `topology`, `absence_signals`, and temporal hints       | Question asks for monthly spend, but profile has no month grouping. |

#### Stage 1 - Metadata-Aware Intent Decomposition

Stage 1 receives only the user question and a compact finance/schema metadata guide. It does not receive the candidate SQL, the SQL AST, or the compact semantic profile.

The goal of Stage 1 is not to generate SQL. It produces a structured representation of what a correct SQL query should semantically satisfy. This intent representation is descriptive and check-oriented.

Stage 1 output includes:

- `question_type`: high-level query type such as quantity sold, transaction count, monetary amount, monthly breakdown, ranking, comparison, payment status, or generic;
- `financial_object`: expected object, entity role, product/service, transaction event, payment status, or account class;
- `financial_measure`: expected measure kind, preferred vectors, non-equivalent measures, and debit/credit direction requirements;
- `computation_logic`: expected aggregation, grouping, time period, temporal grouping, ranking, limit, or comparison logic;
- `required_checks`: concrete semantic checks that Stage 2 must verify;
- `ambiguities` and `confidence`.

Compact Stage 1 intent snippet:

```text
{
  "question_type": "quantity_sold",
  "financial_object": {
    "object_type": "product_service",
    "object_value": "AI Courses",
    "transaction_event": "sale",
    "entity_role": "none"
  },
  "financial_measure": {
    "measure_kind": "quantity",
    "expected_semantics": "quantity sold",
    "preferred_columns_or_vectors": ["Quantity"],
    "non_equivalent_measures": [
      "COUNT(*)",
      "COUNT(Transaction_ID)",
      "COUNT(DISTINCT Transaction_ID)"
    ]
  },
  "computation_logic": {
    "aggregation": "SUM",
    "grouping": [],
    "time_period": "unspecified",
    "requires_temporal_grouping": false
  },
  "required_checks": [
    {
      "check_id": "quantity_not_transaction_count",
      "dimension": "financial_measure",
      "requirement": "Quantity sold is not equivalent to transaction count."
    }
  ],
  "confidence": "high"
}
```

Important BookSQL interpretation rules used by Stage 1:

| Question Pattern                                | Required Meaning                                                       | Non-equivalent Meaning                                                           |
| ----------------------------------------------- | ---------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| "How many [product/service] did we sell?"       | Quantity sold, usually `SUM(Quantity)`                                 | `COUNT(*)`, `COUNT(Transaction_ID)`, or `COUNT(DISTINCT Transaction_ID)`         |
| "How many times did we sell [product/service]?" | Sale-event or transaction count                                        | Quantity sold                                                                    |
| "Monthly spend by vendor"                       | Vendor/payee scope, spend-compatible measure, and month-level grouping | Customer scope, generic `Amount`, or current-month filter without month grouping |
| "Revenue" / "sales amount"                      | Revenue-compatible or credit-side monetary measure                     | Generic `Amount` when direction matters                                          |
| "Expense" / "cost" / "spend"                    | Expense-compatible or debit-side monetary measure                      | Generic `Amount` when direction matters                                          |
| "This fiscal year"                              | Fiscal-year period                                                     | `trailing_1_year` unless explicitly equivalent in the data setup                 |

#### Stage 2 - Intent/Profile Semantic Verification

Stage 2 receives the Stage 1 intent representation and the compact semantic profile of the candidate SQL. It performs a check-level comparison between what the question requires and what the SQL profile computes.

Stage 2 returns:

- `check_results`: pass, fail, or unclear status for each required semantic check;
- `dimension_alignment`: whether D1, D2, and D3 are the same, different, or unclear;
- `evidence_match`: sufficient, insufficient, or unclear;
- `answers_question`: true, false, or null;
- `primary_mismatch_type`: one of `financial_object_error`, `financial_measure_error`, or `computation_logic_error` when rejected;
- `mismatch_detail`, `failed_evidence`, `probe_needed`, and `confidence`.

Compact Stage 2 output snippet:

```text
{
  "check_results": [
    {
      "check_id": "quantity_not_transaction_count",
      "dimension": "financial_measure",
      "expected": "quantity sold using Quantity",
      "observed": "COUNT(DISTINCT Transaction_ID)",
      "alignment": "failed",
      "severity": "answer_changing"
    }
  ],
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
  "probe_needed": false,
  "confidence": "high"
}
```

Stage 2 treats a mismatch as clear when the Stage 1 requirement is medium or high confidence, the compact profile clearly contradicts it, and the failed check is answer-changing. It treats a case as ambiguous when the intent is uncertain, the profile evidence is incomplete, or a field could plausibly support more than one financial interpretation.

#### Optional Stage 2B - Targeted Evidence Probing

If direct comparison is unclear, Stage 2 may trigger targeted probing. Probing is not a second general verification pass. It asks narrow evidence questions over the compact profile and metadata to resolve a specific uncertain check.

Example probes:

```text
{
  "probe_id": "probe_measure_equivalence",
  "question": "Does the profile measurement represent quantity sold or transaction count?",
  "allowed_answers": ["quantity_sold", "transaction_count", "unclear"]
}
```

```text
{
  "probe_id": "probe_period_match",
  "question": "Does the profile temporal filter represent the requested fiscal year?",
  "allowed_answers": ["matches", "different", "unclear"]
}
```

Probing should be used only when the direct comparison is uncertain or low-confidence. It should not run when a mismatch is already clear, such as `COUNT(DISTINCT Transaction_ID)` being used for a question that requires quantity sold.

#### Stage 3 - Repair Hint Generation

Stage 3 runs only when Stage 2 returns a definite rejection with a valid primary mismatch type. Stage 3 receives the failed checks, mismatch type, mismatch detail, and failed evidence, then produces a targeted repair hint.

Stage 3 does not reclassify the error and does not generate corrected SQL.

Example Stage 3 output:

```text
{
  "repair_hint": "Use a quantity-compatible expression such as SUM(Quantity) instead of counting distinct transactions, while preserving the Product_Service filter.",
  "confidence": "high"
}
```

If the profile has parse errors, unsupported lineage, unresolved ambiguity, or insufficient grounding, FinVeriSQL abstains instead of issuing a confident repair instruction.

### Route to repair or abstention

Based on the three-stage verifier output, each candidate SQL is routed to one of three paths.

| Verifier Output                                               | Route   | Action                                                                                                                             |
| ------------------------------------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `answers_question = true`                                     | Accept  | Keep the generated SQL unchanged.                                                                                                  |
| `answers_question = false` with valid `primary_mismatch_type` | Repair  | Send the original question, generated SQL, intent representation, compact profile, failed checks, and repair hint to the repairer. |
| `answers_question = null` or `should_abstain = true`          | Abstain | Do not repair automatically; mark the case for manual review or clarification.                                                     |

The repairer is downstream from the verifier. The verifier identifies what is wrong and produces a repair hint. The repairer attempts to revise the SQL.

Repairer inputs:

```text
{
  "question": "{question}",
  "generated_sql": "{generated_sql}",
  "intent_representation": "{intent_profile}",
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

`scripts/run_finverisql_repair.py` only routes repair candidates and generates repaired SQL. It does not execute repaired SQL, rerun the verifier, or decide whether a repair succeeded. Use `python -m src.eval.evaluate_repair_candidates` to execute repaired SQL, compare against gold, and compute repair success and execution-accuracy contribution.

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

## **Ablations and Evaluation Setup**

The evaluation uses two types of comparisons:

1. **Main system comparison**, which compares FinVeriSQL against simple non-domain-specific baselines.
2. **Internal ablation**, which removes or changes one FinVeriSQL component at a time to measure its contribution.

The main evaluation objective is to determine whether FinVeriSQL improves final SQL correctness while avoiding harmful over-repair.

The four core metrics reported are:

Execution Accuracy (EX)  
Detection F1  
Correction Rate  
Corruption Rate

Together, these metrics separate final task performance from the mechanisms that produce it.

---

### **Main Comparison Table**

The main comparison table is evaluated on the test set.

| System                               | Purpose                                                                                                                                                                                                                                                                                                                                                                       |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Generator only**                   | Baseline Text-to-SQL system. The model generates SQL directly from the question and schema, with no verification or repair. This measures the original execution accuracy.                                                                                                                                                                                                    |
| **Generator \+ generic self-refine** | Generic LLM repair baseline. The generated SQL is passed back to an LLM for reflection or revision using the question and schema, but without FinVeriSQL’s finance-specific verifier, metadata-aware intent representation, compact semantic profile, probing, or accounting-aware error classes. This tests whether generic self-correction alone can improve financial SQL. |
| **Generator \+ FinVeriSQL full**     | Main proposed system. FinVeriSQL verifies candidate SQL using a schema-grounded financial intent/profile comparison, optionally probes ambiguous checks, classifies confirmed mismatches into finance-aware error classes, and generates targeted repair hints.                                                                                                               |

Recommended main comparison table:

| System                                       | EX Accuracy | ASA Metrics | Correction Rate | Corruption Rate | Net Repair Gain |
| -------------------------------------------- | ----------- | ----------- | --------------- | --------------- | --------------- |
| Generator only                               | ...         | ...         | –               | –               | –               |
| Generator \+ generic self-refine             | ...         | ...         | ...             | ...             | ...             |
| Generator \+ generic execution-guided refine | ...         | ...         | ...             | ...             | ...             |
| Generator \+ FinVeriSQL full                 | ...         | ...         | ...             | ...             | ...             |

For the main comparison table:

ΔEX vs Generator \= EX(system) \- EX(generator only)

`Correction Rate`, `Corruption Rate`, and `Net Repair Gain` explain why EX changes.

| System          | Baseline FCR Failures | FCR Caught | FCR Attempted | FCR Fixed | FCR Fixed \+ EX Preserved |
| --------------- | --------------------- | ---------- | ------------- | --------- | ------------------------- |
| FinVeriSQL full | ...                   | ...        | ...           | ...       | ...                       |

These diagnostics explain whether FinVeriSQL is reducing deterministic hard financial contradictions, not just improving general execution accuracy.

---

### **Internal Ablation Table**

The internal ablation table is evaluated on the evaluation/dev set. Each ablation changes one component of FinVeriSQL while keeping the rest of the pipeline as close as possible to the full system.

| System / Ablation                   | Purpose                                                                                                                                                                                                                |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **FinVeriSQL full**                 | Complete proposed system using the selected best configuration. This is the reference point for all internal ablations.                                                                                                |
| **w/o Intent Decomposer**           | Removes explicit decomposition of the natural language question into a structured intent representation. Tests whether structured intent helps the verifier identify meaningful SQL mismatches.                        |
| **w/o Probing / direct only**       | Removes targeted probing and relies only on direct verifier comparison. Tests whether probing helps resolve ambiguous verifier decisions enough to justify its added complexity.                                       |
| **w/o Compact Semantic Profile**    | Removes the compact schema-grounded semantic profile and uses a less semantically grounded SQL representation. Tests whether compact financial profiling improves verifier precision and downstream repair.            |
| **w/o Scope Constraints in Repair** | Keeps dimension-specific repair routing but removes deterministic clause-level scope validation. Tests whether enforcing allowed and disallowed clause changes reduces off-target edits and repair-induced corruption. |
| **w/o re-verification loop**        | Applies a single repair without iterative verifier re-checking. Tests whether post-repair verification is needed to confirm successful fixes, catch persistent mismatches, and avoid accepting unresolved repairs.     |

Recommended internal ablation table:

| System / Ablation               | Detection Precision | Detection Recall | Detection F1 | Correction Rate | Corruption Rate | EX Accuracy | ΔEX vs Full |
| ------------------------------- | ------------------- | ---------------- | ------------ | --------------- | --------------- | ----------- | ----------- |
| FinVeriSQL full                 | ...                 | ...              | ...          | ...             | ...             | ...         | 0.00        |
| w/o Intent Decomposer           | ...                 | ...              | ...          | ...             | ...             | ...         | ...         |
| w/o Probing / direct only       | ...                 | ...              | ...          | ...             | ...             | ...         | ...         |
| w/o Compact Semantic Profile    | ...                 | ...              | ...          | ...             | ...             | ...         | ...         |
| w/o Scope Constraints in Repair | ...                 | ...              | ...          | ...             | ...             | ...         | ...         |
| w/o re-verification loop        | ...                 | ...              | ...          | ...             | ...             | ...         | ...         |

For the internal ablation table:

Detection metrics treat executable-wrong SQL as the positive class:

- **TP**: Group B query where the verifier returns `answers_question = false`.
- **FP**: Group A query where the verifier returns `answers_question = false`.
- **FN**: Group B query where the verifier does not return `answers_question = false`, including `answers_question = true`, `answers_question = null`, or abstention.
- **Detection Precision** = TP / (TP + FP).
- **Detection Recall** = TP / (TP + FN).
- **Detection F1** = 2 _ Precision _ Recall / (Precision + Recall).

Group-specific accept/reject precision, recall, and F1 should be reported only as verifier diagnostics, not as the main internal ablation metrics.

ΔEX vs Full \= EX(ablation) \- EX(FinVeriSQL full)

A negative value means removing the component hurts final EX. A positive value means the ablated system performs better than the full system and should be investigated.

---

### **Isolated Repair Strategy Comparison**

This comparison isolates the repair mechanism while holding verifier outputs fixed. The goal is not to prove that in-context prompting is better than SFT or RL. Instead, it tests whether FinVeriSQL can provide lightweight edit-locality control as a post-generation layer, and whether that control can complement or substitute for learned repair policies under the same confirmed mismatch evidence.

All repair strategies receive the same fixed verifier output: the same rejected rows, mismatch types, failed evidence, repair hints, intent representations, and compact SQL profiles. The verifier is not rerun and the set of repair candidates is held constant. Only the method used to produce the repaired SQL changes.

| Repair Strategy             | Purpose                                                                                                                                                                                                                              |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **FinVeriSQL ICL repair**   | Uses FinVeriSQL's repair model with scope constraints at repair time. This represents a lightweight post-generation repair mechanism with no learned task-specific adapter.                                                          |
| **SFT Llama-3.1-8B repair** | Starts from the same Llama-3.1-8B base model and is supervised using LoRa on train-split repair examples using corrected SQL targets. This tests whether learned repair behavior improves correction while preserving edit locality. |
| **RL Llama-3.1-8B repair**  | Starts from the SFT repairer and is further optimized with rewards aligned to EX correction, ASA, and corruption avoidance. This tests whether explicit optimization improves the repair tradeoff under fixed verifier routing.      |

Recommended isolated repair strategy table:

| Repair Strategy       | Correction Rate | Corruption Rate | ASA |
| --------------------- | --------------- | --------------- | --- |
| Prompted Llama-3.1-8B | ...             | ...             | ... |
| SFT Llama-3.1-8B      | ...             | ...             | ... |
| RL Llama-3.1-8B       | ...             | ...             | ... |

This comparison supports the model-agnostic claim of FinVeriSQL: because verification and repair happen after SQL generation, the same fixed verifier evidence can be paired with different repair producers. If prompted repair is competitive, it suggests FinVeriSQL can offer a low-overhead alternative to learned edit-locality control. If SFT or RL improves the tradeoff, it shows that learned repairers can be plugged into the same post-generation interface without changing the verifier or baseline SQL generator.

---

## Error Analysis

ASA/FCR Group Membership Audit

To validate whether ASA captures errors that EX alone does not reveal, I audited the baseline hard FCR failures by evaluation group.

The evaluation groups are defined as:

Group A = executable and EX-correct
Group B = executable but EX-wrong
Group C = non-executable

All 95 baseline hard FCR failures were found in Group A.

| Group   | Count | Percent |
| ------- | ----- | ------- |
| Group A | 95    | 100.0%  |
| Group B | 0     | 0.0%    |
| Group C | 0     | 0.0%    |

All 95 hard FCR findings were posting_side_reversal.

This means that the hard accounting contradictions identified by FCR were not cases that EX had already rejected. Instead, they were cases where the generated SQL matched the gold execution output on the original database, but violated a deterministic accounting invariant. This supports the motivation for ASA as a stricter companion to EX: ASA exposes accounting-semantic false positives that execution accuracy alone would count as correct.

There will be reports on errors in three layers.

| Layer                         | Purpose                                                                                                                                                                                  |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Generator Distribution        | Report % executable-correct, % executable-wrong, and % non-executable per model. The execution script already classifies outputs into these groups, so aggregate counts and percentages. |
| Error Type Breakdown          | Count and percentage of each manually labelled error type in the executable-wrong group: `financial_object_error`, `financial_measure_error`, and `computation_logic_error`.             |
| FinVeriSQL Behaviour per Type | Report detected, missed, repaired, over-corrected, and abstained cases per error dimension. Cross-reference the labelled error subset against FinVeriSQL output records.                 |

Definitions:

- **Detected**: Group B query where the final verifier decision returns `answers_question = false`.
- **Missed**: Group B query where the final verifier decision returns `answers_question = true`.
- **Repaired**: Group B query where the repaired SQL becomes execution-correct.
- **Over-corrected**: Group A query that was originally execution-correct but was rejected and repaired into an execution-incorrect query.
- **False rejection**: Group A query where the final verifier decision returns `answers_question = false`.
- **Abstained**: The verifier returns `evidence_match = "unclear"`, `answers_question = null`, or profile status triggers abstention before LLM verification.
- **Probe-triggered**: A case where direct intent/profile comparison was insufficient and Stage 2B targeted probing was invoked.

Report abstention rate separately. If abstention is too high, the verifier is refusing hard cases rather than solving them. If abstention is never triggered, inspect the direct-comparison and profile-status handling because uncertainty may not be represented correctly.

Also inspect the `intent_representation`, `check_results`, and `dimension_alignment` fields. They are useful for diagnosing whether errors are caused by:

- incorrect question intent decomposition;
- object/entity mismatch;
- measure mismatch such as `COUNT(DISTINCT Transaction_ID)` vs `SUM(Quantity)`;
- computation mismatch such as current-month filter vs monthly grouping;
- overly strict rejection of missing secondary boundaries;
- probing questions that confirm the wrong interpretation instead of resolving uncertainty.
