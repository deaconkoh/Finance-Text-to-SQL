# Research Experiments Steps

## FinVeriSQL: finance-aware semantic verification and repair for Text-to-SQL

**Core Research Question:** Can a verifier that understands financial semantics catch and repair executable-but-wrong SQL before results reach users?

**Research Gap:** BookSQL shows that financial Text-to-SQL models make recurring domain-semantic errors, not just generic SQL errors. In its error analysis, models confuse credit, debit, and amount, apply filters to the wrong financial field such as product_service instead of account, and misuse transaction-type or account-type filters such as invoice, expense, income, accounts receivable, and accounts payable. These mistakes are especially problematic in finance because the generated SQL can still execute successfully while returning a financially wrong answer. For example, using amount instead of credit for invoice value, or filtering by product/service instead of account, may produce a plausible numeric result but answer a different financial question.

Recent work has begun to address semantic error detection and correction in Text-to-SQL. Frameworks such as SQLens, SQLFixAgent, and ErrorLLM show that generated SQL can be syntactically valid yet semantically wrong, and that post-generation refinement can improve reliability. However, these methods remain largely domain-general. They verify whether SQL aligns with the natural language question, schema, and execution behaviour, but they do not explicitly check whether the SQL is financially valid.

The gap is not the absence of SQL verification in general. The gap is the absence of a finance-aware SQL verification layer that targets recurring accounting and financial semantic errors such as credit/debit inversion, wrong account class, wrong statement family, wrong denominator, or wrong period basis. FinVeriSQL addresses this by checking generated SQL against explicit financial constraints and repairing queries that are executable but financially incorrect.

**Methodology:** FinVeriSQL is a post-generation verification and repair system for financial Text-to-SQL. Rather than modifying the generation process, it adds a finance-aware verification layer between the SQL generator and the user, inspecting candidate queries for financial semantic validity before results are returned. The methodology is structured around four components: a base generator, a semantic layer, a hybrid verifier, and a repair and routing module.

## Experimental Setup and Dataset

## Dataset Selection & Preparation

Experiments are conducted on BookSQL, the primary benchmark for financial and accounting Text-to-SQL, which provides natural language questions, schema definitions, and gold SQL over a realistic bookkeeping database. BookSQL is selected because its published error analysis directly motivates the three constraint dimensions in this work and provides empirical grounding for the claim that financial semantic errors are structurally recurring rather than random.

**Note:** Evaluation on a second financial dataset (e.g. BULL) is treated as a stretch goal. If pursued, it would require separately annotating the new schema using the same seven-attribute semantic layer framework, and reporting results independently. This is not required for the core contribution claim but would strengthen generalisability.

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

**Note:** Few-shot examples will come from the train split, a sample of 3-5 diverse examples per query type (logged for reproducibility). These generator-only few-shot examples are distinct from the D3 few-shot examples used in Stage 3 of the verifier. For D3, a separate set of 30-50 training split examples must be manually labelled using the same error taxonomy (financial_object_error, financial_measure_error, computation_logic_error, etc.) before building FinVeriSQL. These labelled training examples form the retrieval pool for D3 few-shot selection — when D3 detects a computation_logic_error, it retrieves 2-3 training examples labelled computation_logic_error and includes them in the classifier prompt. This step must be completed before Phase 3.

## Build Error Subset & Labels

Using the execution results from the baseline models, partition validation output into 4 sub-groups

| Group                  | Definition                          | Example                      |
| ---------------------- | ----------------------------------- | ---------------------------- |
| A - Correct Executable | Executes & matches gold SQL         | Test over-correction rate    |
| B - Wrong Executable   | Execute but does not match gold SQL | Primary target of FinVeriSQL |
| C - Non- executable    | Throws runtime/syntax error         | Report separately            |
| D - Ambiguous          | Partial Match, null results         | Exclude/inspect manually     |

For Group B errors (Wrong but executable SQL), assign one primary error label and an optional secondary label (if error spans multiple dimension)

| Error Label             | Example                                                                                                                                                                                                                                                                                                                                                                  |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Finance_object_error    | Filters by product_service when question ask for account category                                                                                                                                                                                                                                                                                                        |
| Finance_measure_error   | Uses amount instead of credit for invoice value                                                                                                                                                                                                                                                                                                                          |
| computation_logic_error | Sums all transactions instead of filtering to YTD period.<br><br>For this error, add sub-labels as well<br>"aggregation_error" # Wrong function: AVG used where SUM needed<br>"formula_error" # Wrong numerator or denominator<br>"temporal_scope_error" # Wrong date filter or period boundary<br>"stock_flow_mismatch" # Point-in-time vs period aggregation confusion |
| generic_sql_error       | Wrong join, missing GROUP BY, incorrect subquery                                                                                                                                                                                                                                                                                                                         |
| value_entity_error      | Wrong literal value, mismatched entity name                                                                                                                                                                                                                                                                                                                              |

These error labels are intended to assist our error analysis later in the experiments.

Consider using Cohen’s Kappa to prove that your error categories are clear enough that another person independently arrives at the same labels. (Give 20–30 of your already-labelled examples to another person with only the annotation guide. They label them independently and Kappa measures how much you agreed beyond random chance)

#### Example Label:

```text
{
  "question_id":     "booksql_0142",
  "generator":       "arctic",
  "generated_sql":   "SELECT SUM(amount) FROM transactions ...",
  "gold_sql":        "SELECT SUM(credit) FROM transactions ...",
  "error_label":     "financial_measure_error",
  "error_sublabel":  null,
  "annotation_note": "amount used instead of credit; both execute successfully"
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

### Build Financial Semantic Intermediate Representation (FSIR)

The schema-grounded semantic representation is then converted into a structured Financial Semantic Intermediate Representation (FSIR). The FSIR is a verifier-facing JSON profile that describes what the candidate SQL appears to compute.

This shifts FinVeriSQL away from predicting an expected intent from the question. Instead, the generated SQL is treated as the object being verified. The older natural-language decompiler may still be kept as a debugging artefact, but the verifier-facing profile is the FSIR.

The workflow becomes:

```text
Predicted SQL
→ SQL AST parsing
→ schema-grounded semantic mapping
→ FSIR construction
→ constrained semantic verification against the question
```

rather than:

```text
Question
→ predicted intent
→ expected SQL requirements
→ rule comparison
```

#### Comparison: Old Decompiler vs. FSIR Layer

Our previous verification methodology utilized a Structural Decompiler. While the decompiler was technically an intermediate representation, it merely re-mapped physical SQL clauses into a semi-structured text block using bracketed headers (e.g., [Tables], [Filters], [Select]). This approach left critical business boundaries buried inside text arrays, forcing the downstream verifier LLM to read, parse, and evaluate conversational strings simultaneously.

The FSIR replaces this plain-text configuration with three highly structured, coupled JSON layers that align perfectly with our core research dimensions:

1. Financial Concept Layer (D1 — Object & Grounding Constraints): Instead of capturing loose filter criteria, this layer uses a unified scope_constraints collection. It explicitly categorizes filters by their operational roles (derived_financial_classes: ["revenue"], scope_role: "customer") and documents their location (global_filter vs measurement_condition), mapping boundaries cleanly.

2. Measurement Layer (D2 — Quantitative & Sign Vector Constraints): The FSIR eliminates unlinked text lists and replaces them with dedicated Measurement Objects. Each calculation tracks its own target column, aggregation function, and normal balance state (extracted_vector: "credit", column_normal_balance: "credit_normal"), ensuring multi-metric comparative ledger queries maintain perfect structural alignment.

3. Reporting Topology Layer (D3 — Computational & Temporal Constraints): This layer maps the output structure of the query, explicitly tracking the primary analytical_grain alongside a multi-tiered temporal canonicalization engine. The date resolution block maps physical predicates, handles symbolic anchors, and assigns a clean logical period label (prior_month) to isolate raw database calculations from semantic intent evaluation.

The FSIR is descriptive, not evaluative. It does not decide whether the SQL is correct. It only records the physical SQL computation, schema-grounded scope constraints, measurement components, grouping behaviour, temporal predicates, and extraction limitations.

Example FSIR Meaning:

```text
Candidate SQL:
SELECT SUM(Debit)
FROM master_txn_table
WHERE Account_type = 'Expense';

FSIR semantic meaning:
{
  "financial_concept_layer": {
    "scope_constraints": [
      {
        "scope_role": "account",
        "mapped_column": "Account_type",
        "operator": "=",
        "values": ["Expense"],
        "mapped_concepts": ["expense"],
        "derived_financial_classes": ["expense"],
        "enforcement_location": "global_filter"
      }
    ]
  },
  "measurement_layer": {
    "measurements": [
      {
        "measurement_id": "m1",
        "metric_expression": {
          "expression_type": "single_column_aggregate",
          "raw_expression": "SUM(Debit)",
          "components": [
            {
              "column": "Debit",
              "aggregation_function": "SUM",
              "extracted_vector": "debit",
              "column_normal_balance": "debit_normal",
              "measure_type": "flow",
              "unit": "currency"
            }
          ]
        }
      }
    ]
  },
  "reporting_topology_layer": {
    "analytical_grain": "global_summary",
    "grouping_dimensions": [],
    "temporal_resolution": {
      "source_dialect": "sqlite",
      "parser_scope": "sqlite_date_arithmetic",
      "representation_level": "symbolic_temporal_boundary",
      "normalization_status": "no_temporal_filter"
    }
  }
}
```

The FSIR avoids collapsing financial meaning into scalar fields. For example, account-related concepts are represented as arrays so mixed scopes such as `Income` and `Expense` can be represented without losing information. Measurement fields describe the SQL's extracted vector, such as `debit`, `credit`, `quantity`, `row_count`, or `raw_numeric`, rather than the expected financial measure for the question.

Temporal logic is represented through symbolic reporting-period boundaries. The current physical parser targets SQLite date arithmetic because BookSQL is SQLite-based, but the FSIR temporal representation stores dialect-independent concepts such as anchor, boundary, offsets, period grain, and normalisation status.

FSIR v0 does not fully support HAVING extraction because the current parser does not expose HAVING clauses separately. It can surface suspicious pre-aggregation numeric threshold filters in WHERE, but aggregation-threshold filtering errors involving HAVING are treated as a known D3 limitation until parser support is added.

### Apply Semantic Verification

The verifier compares the FSIR against the original natural language question using a constrained semantic verification task.

The verifier does not regenerate SQL and does not independently predict a full expected query. It only checks whether the SQL meaning represented by the FSIR logically answers the question.

| Dimensions                                 | Definition                                                                                                                                                                                                  |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dimension 1 - Financial Object Constraint  | Checks whether the FSIR operates on the same financial object requested by the question. For example, an SQL meaning that calculates expense does not answer a question asking for revenue.                 |
| Dimension 2 - Financial Measure Constraint | Checks whether the SQL uses the correct physical measure, extracted vector, or unit. For example, using a raw amount column may be semantically different from using a credit or debit vector.              |
| Dimension 3 - Computation Logic Constraint | Checks whether the aggregation, temporal scope, grouping, ranking, or formula logic matches the question. This includes mismatches such as monthly vs YTD filtering, count vs sum, or wrong grouping level. |

The output of this stage is a verification decision, mismatch type, and optional repair hint. If the SQL contains unsupported lineage, unresolved ambiguity, or insufficient semantic grounding, FinVeriSQL abstains instead of issuing a confident error label.

### Route to repair or abstention

Based on the semantic verification output, route each flagged query to one of three paths.

| Condition                                         | Route                                                         |
| ------------------------------------------------- | ------------------------------------------------------------- |
| Single high-confidence semantic mismatch          | Deterministic repair prompt, rewrite only the violated clause |
| Multi-dimension or LLM-classified violation       | LLM repair prompt with full error context                     |
| Conflicting constraints or unresolvable ambiguity | Abstention — return clarification request to user             |

**Note:** Multi-dimension refers to a flagged error of both D1 and D2 at the same time on the same SQL. Abstention occurs when the verifier cannot confidently determine whether the FSIR answers the question, or when the SQL contains unsupported lineage or unresolved ambiguity.

#### Example Repair Prompt - Single Violation:

```text
The SQL below has a financial semantic error. Fix only the identified issue.

Question:
{question}

Schema:
{schema}

Original SQL:
{generated_sql}

Identified financial issue:
{violation_type}: {violation_detail}

Repair instruction:
{repair_hint}

Return only the corrected SQL. No explanation.
```

#### Example Repair Prompt - Multi-dimension Violation:

```text
The SQL below may be financially incorrect. It has been flagged for the following issues.

Question:
{question}

Schema (with financial annotations):
{annotated_schema}

Original SQL:
{generated_sql}

Detected issues:
{violation_report_json}

Revise the SQL so that it correctly answers the financial question.
Pay special attention to: which numeric field to use, which account category applies,
and whether the date filter matches the period asked about.

Return only the corrected SQL.
```

#### Example Abstention Message (Sent to user):

```text
The generated SQL could not be confidently verified against your question.
The query may involve ambiguous financial terms or conflicting constraints.

Could you clarify:
{clarification_question}
```

After repairs, execute the repaired SQL and compare against gold. Store the full before/after record for every repaired query.

```text
{
  "question_id":          "booksql_0142",
  "generator":             "arctic",
  "violation_detected":    true,
  "violation_type":        "financial_measure_error",
  "before_exec_match":     false,
  "repaired_sql":          "SELECT SUM(credit) FROM transactions WHERE ...",
  "after_exec_match":      true,
  "repair_success":        true,
  "route":                  "single_rule_repair"
}
```

## Ablations

There will be 2 main forms of comparison system, Main Comparison Table & Internal Ablation Table.

### Main Comparison Table (Test Set):

| System                                         | Purpose                               |
| ---------------------------------------------- | ------------------------------------- |
| Generator only                                 | Unverified Baseline                   |
| Generator + Generic Self-refine                | Generic reflection baseline           |
| Generator + execution-only repair              | Show execution signal is insufficient |
| Generator + LLM-only verifier (No constraints) | Show LLM alone is not enough          |
| Generator + FinVeriSQL full                    | Main contribution                     |

### Internal Ablation Table (Evaluation Set):

| System                           | Purpose                                                                                      |
| -------------------------------- | -------------------------------------------------------------------------------------------- |
| Full FinVeriSQL                  | Reference system                                                                             |
| Raw-SQL verifier                 | Tests whether direct LLM verification over raw SQL is weaker than verification over FSIR     |
| FSIR without financial grounding | Tests whether structured SQL facts alone are sufficient without financial semantic grounding |
| Remove schema annotations        | Tests whether column-level semantic grounding matters                                        |
| Remove value-level concepts      | Tests whether classifier value mappings improve financial object and event understanding     |
| No abstention mechanism          | Tests whether explicit uncertainty handling improves reliability                             |

### Evaluation Metrics:

| System                                   | Purpose                                                                                                                                            |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Execution Accuracy <br>(Before vs After) | Verifies if the contribution improves financial SQL correctness overall<br><br>E.g. used by SQLFixAgent, SQLens, ErrorLLM                          |
| End-to-end Correction Rate               | Supports the fact that improvement is broad, not just on a handful of detected cases<br><br>E.g. Used by SQLens (as net score: fixed minus broken) |
| Wrong-SQL detection F1                   | Ensure that the verifier is actually identifying eros, not just rewriting randomly<br><br>E.g. Used by SQLens, ErrorLLM                            |
| Harmful over-correction rate             | Ensure that the contribution doesnt not corrupt SQL that was already correct<br><br>E.g. Used by ErrorLLM as corruption rate                       |

## Error Analysis

There will be report on errors in three layers.

| System                        | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Generator Distribution        | % executable-correct, % executable-wrong, % non-executable per model<br><br>The execution script from phase 1 already classifies the output into these 3 groups, just aggregate count and compute percentages<br>                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Error Type Breakdown          | Count and % of each error type in executable-wrong group<br><br>This comes directly from the manually labelled error subset in Phase 2, just aggregate by label.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| FinVeriSQL behaviour per Type | Detected, missed, repaired, over-corrected — per error dimension. Cross-reference the labelled error subset against FinVeriSQL's output records from Phase 3.<br><br>Definitions: Over-corrected = FinVeriSQL modified a SQL that was originally execution-correct (Group A) and turned it into execution-incorrect (before_exec_match: true, after_exec_match: false). Abstained = D3 returned ambiguous: true and no repair was attempted. Report abstention rate separately — if too high, the system is refusing hard cases rather than solving them. Also check after running on validation that ambiguous: true is actually triggered; if never triggered, the D3 prompt ambiguity examples need revision. |
