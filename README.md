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

| Model | Purpose |
| --- | --- |
| Arctic-Text2SQL-R1-7B | SQL-specific Text-to-SQL baseline |
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

| Group | Definition | Example |
| --- | --- | --- |
| A - Correct Executable | Executes & matches gold SQL | Test over-correction rate |
| B - Wrong Executable | Execute but does not match gold SQL | Primary target of FinVeriSQL |
| C - Non- executable | Throws runtime/syntax error | Report separately |
| D - Ambiguous | Partial Match, null results | Exclude/inspect manually |

For Group B errors (Wrong but executable SQL), assign one primary error label and an optional secondary label (if error spans multiple dimension)

| Error Label | Example |
| --- | --- |
| Finance_object_error | Filters by product_service when question ask for account category |
| Finance_measure_error | Uses amount instead of credit for invoice value |
| computation_logic_error | Sums all transactions instead of filtering to YTD period.<br><br>For this error, add sub-labels as well<br>"aggregation_error"        # Wrong function: AVG used where SUM needed<br>"formula_error"               # Wrong numerator or denominator<br>"temporal_scope_error" # Wrong date filter or period boundary<br>"stock_flow_mismatch"  # Point-in-time vs period aggregation confusion |
| generic_sql_error | Wrong join, missing GROUP BY, incorrect subquery |
| value_entity_error | Wrong literal value, mismatched entity name |

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

Generic SQL verifiers treat all columns as structurally equivalent, they cannot distinguish credit from amount because both are just numeric columns. To make FinVeriSQL financially aware, we attach a fixed set of semantic attributes to each schema column derived from IFRS and double-entry bookkeeping standards. This annotation file is written once, frozen before any test evaluation, and consulted by the rule-based verifier at runtime during D1 and D2 checks.

It is not data leakage as it encodes domain knowledge about what columns mean but not anything about the questions or SQL being evaluated.

| Attributes | Example |
| --- | --- |
| Statement_family | Which financial statement this belong to <br><br>Example:<br>Income_statement, balance_sheet, cash_flow_statement, none |
| account_type | IFRS Conceptual Framework 4.1 - five defined elements of financial statement<br><br>Example: <br>Asset, Liability, Equity, Income, Expense, Classifier, None |
| measure_type | Whether the column represents a flow, a stock or is not numeric<br><br>Example:<br>Flow (Measured over a period of time), Stock (Measure at a point in time i,e, inventory), Categorical (Labels) |
| sign_convention | Double Entry bookkeeping convention<br><br>Example:<br>Credit_normal, debit_normal, none |
| unit | What kind of number this column stores<br><br>Example:<br>Monetary (currency amount), ratio, count, none |
| temporal_grain | What time granularity this column represent or is valid for<br><br>Example:<br>Transaction_level, Period_level, point_in_time, date_field, none |
| entity_scope | What business entity this column is associated with<br><br>Example:<br>Account, transaction, customer, vendor, product_service, none |

## Building FinVeriSQL 😀

### Extract Financial Intent from Question

Map the natural language question to a structured financial intent object. The rule checks in Stage 2 need to know what the question is actually asking for before they can check the SQL against it.

#### Example Prompt:

```text
Return a JSON object with exactly these fields.
Use only the allowed values listed for each field.

financial_object:
  Allowed: invoice, revenue, expense, receivable,
           payable, cash_balance, profit, equity, other

financial_measure:
  Allowed: credit, debit, amount, balance, ratio, count, none

aggregation:
  Allowed: sum, average, count, none

temporal_scope:
  Allowed: daily, monthly, quarterly, yearly,
           ytd, point_in_time, none

stock_or_flow:
  Allowed: flow, stock, none

Return only valid JSON. No explanation.
```

### Parse SQL Abstract Syntax Tree (AST) with sqlglot

The AST parser is used to parse the candidate SQL into a structured representation to extract the components needed for constraint checking.

#### Example Output:

```text
{
  "selected_columns": ["amount"],
  "aggregations":     [{"func": "SUM", "col": "amount"}],
  "tables":           ["transactions"],
  "filters":          [{"col": "transaction_date", "op": "BETWEEN"}],
  "date_conditions":  ["transaction_date BETWEEN '2024-04-01' AND '2024-04-30'"]
}
```

### Apply three-dimension constraint checks

Check the parsed SQL AST against the financial intent and semantic layer. D1 and D2 are rule-based. D3 uses an LLM and only runs if D1 and D2 both pass.

| Dimensions | Definition |
| --- | --- |
| Dimension 1 - Financial Object Constraint | Checks whether the SQL operates on the correct financial object. Cross-references financial_object from intent with account_type and entity_scope in the semantic layer.<br><br>Violation triggered when: the account_type or entity_scope of columns in the AST does not match what the financial object requires. <br><br>Example: intent expects income-class columns but SQL filters on a product_service-scoped column.<br> |
| Dimension 2 - Financial Measure Constraints | Checks whether the SQL reads the correct numeric field.<br>Sign convention and measure type in the semantic layer determine <br>which fields are valid.<br><br>Violation triggered when: the aggregated column has a <br>sign_convention or measure_type that does not match the expected <br>financial measure. <br><br>Example: intent expects credit_normal but SQL <br>aggregates a column with sign_convention: none.<br> |
| Dimension 3 - Computation Logic Contrain | Uses an LLM classifier with few-shot examples retrieved by error type from the labelled training subset. Checks aggregation function correctness and temporal scope correctness. <br><br>The repair_hint field from D3 output is passed directly into the repair prompt in the next stage.<br> |

### D1 and D2 Rule Mapping Tables

The D1 and D2 rule checkers require explicit lookup tables mapping the intent vocabulary (Stage 1 output) to expected semantic layer attributes. These tables ARE the rule logic. They are fixed, derived from IFRS and double-entry bookkeeping, and not sourced from BookSQL data.

#### D1 — financial_object to expected account_type and entity_scope:

| financial_object | expected account_type | expected entity_scope |
| --- | --- | --- |
| invoice | income | transaction |
| revenue | income | account |
| expense | expense | account |
| receivable | asset | account |
| payable | liability | account |
| cash_balance | asset | account |

#### D2 — financial_measure to expected sign_convention and measure_type:

| financial_measure | expected sign_convention | expected measure_type |
| --- | --- | --- |
| credit | credit_normal | flow |
| debit | debit_normal | flow |
| balance | none | stock |
| amount | none — flag as ambiguous | flow |

**Intent Extraction Validation:** Before running the full pipeline, spot-check intent extraction outputs on 20-30 training examples manually. Confirm the JSON output matches what a human financial analyst would say the question asks for. If agreement is below 80%, revise the intent prompt before proceeding to Stage 2. This ensures D1 and D2 are not checking against a broken intent object.

#### Dimension 3 Prompt Example:

```text
You are a financial SQL auditor. Determine if the SQL below has 
a computation logic error given the financial question.

Check two things:
1. Aggregation: Is the aggregation function (SUM, AVG, COUNT) 
   correct for the question?
2. Temporal scope: Does the date filter correctly match the period 
   the question asks about (monthly, YTD, quarterly, point-in-time)?

If you cannot confidently determine whether a violation exists 
because the question has more than one valid financial 
interpretation, set ambiguous to true and write a 
clarification_question asking the user to specify which 
interpretation they intended. Set all other fields to null.

Return a JSON object with exactly these fields:
{
  "ambiguous": true | false,
  "clarification_question": "question to ask user if ambiguous, else null",
  "aggregation_violation": true | false | null,
  "aggregation_detail": "explanation if true, else null",
  "temporal_violation": true | false | null,
  "temporal_detail": "explanation if true, else null",
  "repair_hint": "brief fix instruction if any violation, else null"
}

Here are examples of computation logic errors:

Example 1:
Question: "What is the YTD revenue as of April?"
SQL: SELECT SUM(credit) FROM transactions WHERE MONTH(date) = 4
Error: temporal_violation — only filters April, not cumulative 
       from year start
Fix: WHERE date BETWEEN '2024-01-01' AND '2024-04-30'

Example 2:
Question: "What is the average invoice value?"
SQL: SELECT SUM(credit) FROM transactions WHERE type = 'invoice'
Error: aggregation_violation — SUM used instead of AVG
Fix: SELECT AVG(credit) ...

Example 3:
Question: "What is the account balance for April?"
SQL: SELECT SUM(credit) FROM transactions WHERE MONTH(date) = 4
Ambiguous: question could mean closing balance as of April 30 
           (point-in-time stock) or total flow through April 
           (period flow) — cannot determine without clarification

Now evaluate:
Financial intent: {intent_json}
Schema context: {relevant_schema_columns}
Candidate SQL: {generated_sql}
Question: {question}
```

### Route to repair or abstention

Based on constraint check outputs, route each flagged query to one of three paths.

| Condition | Route |
| --- | --- |
| Single Dimension <br>(Rule identified violation) | Deterministic repair prompt, rewrite only the violated clause |
| Multi-dimension or LLM-classified violation | LLM repair prompt with full error context |
| Conflicting constraints or unresolvable ambiguity | Abstention — return clarification request to user |

**Note:** Multi-dimension refers to a flagged error of both D1 and D2 at the same time on the same SQL. Abstention occurs when D3 runs but cannot confidently determine what’s wrong

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

| System | Purpose |
| --- | --- |
| Generator only | Unverified Baseline |
| Generator + Generic Self-refine | Generic reflection baseline |
| Generator + execution-only repair | Show execution signal is insufficient |
| Generator + LLM-only verifier (No constraints) | Show LLM alone is not enough |
| Generator + FinVeriSQL full | Main contribution |

### Internal Ablation Table (Evaluation Set):

| System | Purpose |
| --- | --- |
| Full FinVeriSQL | Reference Point |
| Remove D1 | Contribution of financial objects |
| Remove D2 | Contribution of measure checks |
| Remove D3 | Contribution of computation logic checks |
| Rules only (D1 + D2), no LLM | Hybrid vs Rules alone |
| LLM only, no rules | Hybrid vs LLM alone |

### Evaluation Metrics:

| System | Purpose |
| --- | --- |
| Execution Accuracy <br>(Before vs After) | Verifies if the contribution improves financial SQL correctness overall<br><br>E.g. used by SQLFixAgent, SQLens, ErrorLLM |
| End-to-end Correction Rate | Supports the fact that improvement is broad, not just on a handful of detected cases<br><br>E.g. Used by SQLens (as net score: fixed minus broken) |
| Wrong-SQL detection F1 | Ensure that the verifier is actually identifying eros, not just rewriting randomly<br><br>E.g. Used by SQLens, ErrorLLM |
| Harmful over-correction rate | Ensure that the contribution doesnt not corrupt SQL that was already correct<br><br>E.g. Used by ErrorLLM as corruption rate |

## Error Analysis

There will be report on errors in three layers.

| System | Purpose |
| --- | --- |
| Generator Distribution | % executable-correct, % executable-wrong, % non-executable per model<br><br>The execution script from phase 1 already classifies the output into these 3 groups, just aggregate count and compute percentages<br> |
| Error Type Breakdown | Count and % of each error type in executable-wrong group<br><br>This comes directly from the manually labelled error subset in Phase 2, just aggregate by label. |
| FinVeriSQL behaviour per Type | Detected, missed, repaired, over-corrected — per error dimension. Cross-reference the labelled error subset against FinVeriSQL's output records from Phase 3.<br><br>Definitions: Over-corrected = FinVeriSQL modified a SQL that was originally execution-correct (Group A) and turned it into execution-incorrect (before_exec_match: true, after_exec_match: false). Abstained = D3 returned ambiguous: true and no repair was attempted. Report abstention rate separately — if too high, the system is refusing hard cases rather than solving them. Also check after running on validation that ambiguous: true is actually triggered; if never triggered, the D3 prompt ambiguity examples need revision. |
