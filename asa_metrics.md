# **Accounting-Semantic Accuracy (ASA): Proposed Metric for Financial Text-to-SQL Evaluation**

## **1\. Motivation**

Execution Accuracy, or EX, remains one of the dominant evaluation metrics in Text-to-SQL. It is attractive because it is simple, automatic, and easy to compare across systems. A generated SQL query is counted as correct if it executes successfully and returns the same output as the gold SQL on the benchmark database.

However, this creates a known weakness in Text-to-SQL evaluation. EX does not directly measure semantic correctness. It measures whether two queries produce the same denotation on a particular database instance. As a result, EX can artificially inflate baseline performance when an incorrect query happens to return the same output as the gold query on the fixed evaluation database.

This problem is not unique to accounting. It is a general limitation of execution-based evaluation in Text-to-SQL. A query may be logically wrong but still produce the expected answer because the current database does not contain cases that expose the error. For example, a query may omit a filter, join through the wrong table, aggregate at the wrong level, or select a related but incorrect column. If the database instance is too small, too clean, or lacks adversarial cases, these errors may remain hidden.

Prior Text-to-SQL evaluation work has raised this concern. Test Suite Accuracy was introduced because evaluating a query on a single database instance is not always sufficient to approximate semantic correctness. Instead, it evaluates predicted SQL across a distilled suite of generated databases to better expose semantic differences. More recent evaluation work such as FLEX also argues that EX can produce both false positives and false negatives, making it an incomplete basis for reliable Text-to-SQL assessment.

This general weakness becomes especially important in financial and accounting Text-to-SQL. In accounting, correctness is not only about returning the same number. A query must also respect the accounting meaning of the requested measure, the correct sign convention, the correct aggregation level, the correct reporting period, and the correct data lineage. A query that accidentally returns the same value while selecting the wrong financial object or omitting a period constraint should not be treated as correct.

Therefore, Accounting-Semantic Accuracy, or ASA, is proposed as a stricter companion metric to EX. ASA does not replace EX for comparability with prior work. Instead, it evaluates whether an EX-passing or executable query is also valid under accounting-semantic constraints.

The central motivation is:

EX asks whether the generated SQL returns the same output as the gold SQL on one database.

ASA asks a narrower question “Did the SQL both match the original execution result and avoid deterministic hard financial contradictions?”

## **2\. Why not use existing metrics?**

Existing Text-to-SQL evaluation work has already recognised that EX is incomplete. For example, Test Suite Accuracy was introduced to approximate semantic accuracy by evaluating predicted SQL across additional generated database instances. FLEX similarly argues that EX can produce both false positives and false negatives, and proposes a more flexible expert-style evaluation approach.

These methods address important general limitations of EX, but they are not the same as ASA.

Test Suite Accuracy is a general semantic-equivalence method. It checks whether predicted SQL continues to agree with the gold SQL across additional database states. This is useful for exposing many kinds of hidden SQL errors, but the active ASA metric in this project does not implement a full multi-state test suite.

FLEX is also broader than ASA. It is designed to evaluate SQL correctness more flexibly, including cases where EX may be too strict or too lenient. In contrast, ASA does not attempt to adjudicate gold SQL ambiguity, accept alternative SQLs that fail EX, or perform expert-style semantic judgement.

ASA is narrower and more domain-specific. It focuses on accounting-semantic false positives among EX-passing queries. In particular, ASA checks whether a generated SQL query violates deterministic financial invariants, such as posting-side reversal or other hard accounting contradictions.

Therefore, ASA should be understood as a companion metric to EX, not a replacement for broader semantic-equivalence evaluation. Its purpose is not to solve every weakness of EX, but to make one important class of EX false positives visible in financial Text-to-SQL:

EX-passing SQL that returns the right output but violates deterministic accounting semantics.

## **3\. Formal ASA Definition**

The active ASA metric is defined as:

ASA(x) \= 1\[EX(x) \= 1 and Inv(x) \= 1\]  
where:

- `EX` is the original execution-match result on the BookSQL database.
- `Inv` is the deterministic accounting-invariant validity result from the Financial Contradiction Rate (FCR) checker.

The row-level scoring logic is:  
asa_strict \= 1 if EX \= 1 and Inv \= 1  
asa_strict \= 0 if EX \= 0  
asa_strict \= 0 if EX \= 1 and Inv \= 0  
asa_strict \= None if EX \= 1 and Inv is not evaluable

The lower-bound score is:  
asa_lower_bound \= 1 if EX \= 1 and Inv \= 1  
asa_lower_bound \= 0 otherwise, for rows with execution_match available

This lower-bound version treats invariant-unevaluable EX-passing rows as not validated.

## **3.1 Inv: Accounting Invariant Validity**

Unlike EX, which compares generated SQL output against gold SQL output, `Inv` checks whether the generated SQL itself contains a deterministic accounting contradiction.

Inv \= 1, means no applicable deterministic accounting invariant is violated.

Inv \= 0, means at least one applicable hard accounting contradiction is detected.

Inv \= None, means the invariant evaluator could not make a decision. This usually happens when the query contains unsupported finance-bearing lineage, unsupported finance-bearing expressions, or missing financial annotations.

The invariant layer is deterministic. It is not an LLM-based judgement. It is also intentionally conservative: if the evaluator cannot establish a contradiction from schema annotations, parsed SQL structure, and predefined accounting rules, it does not force a failure.

## **3.2 Financial Contradiction Rate (FCR)**

The FCR checker is the implementation layer used to evaluate `Inv`.

A hard FCR finding means that the generated SQL contains a deterministic contradiction against an accounting rule supported by the schema annotations. In other words, the generated SQL is not merely different from the gold SQL; it is financially invalid under the implemented accounting invariant.

The current FCR taxonomy includes the following contradiction families. The examples below are illustrative and are intended to clarify the meaning of each contradiction type.

### **3.2.1 Posting-side reversal**

A `posting_side_reversal` occurs when the SQL uses the opposite debit-credit posting side from the one required by the accounting intent.

For example, if the question asks:

What was the total expense for office supplies?

A valid query should use the debit-side amount if expenses are represented as debit postings:

| SELECT SUM(Debit)FROM master_txn_tableWHERE Account \= 'Office Supplies'; |
| :------------------------------------------------------------------------ |

A contradictory query would use the opposite posting side:

| SELECT SUM(Credit)FROM master_txn_tableWHERE Account \= 'Office Supplies'; |
| :------------------------------------------------------------------------- |

Even if this query happens to return the same output on the benchmark database, it is accounting-invalid because it selects the wrong posting side for the requested financial concept.

This contradiction is grounded in the accounting distinction between debit and credit posting sides. `Debit` and `Credit` are not interchangeable numeric columns; they encode different accounting directions.

The checker only flags this as a hard contradiction when the generated SQL’s selected financial measure can be linked to annotated debit-credit semantics and the expected posting side can be determined with sufficient confidence.

### **3.2.2 Financial measure substitution**

A financial measure substitution occurs when the SQL replaces the requested financial measure with another finance-bearing measure that is not accounting-equivalent.

For example, if the question asks:

What was the total invoice amount?

A valid query should aggregate the amount field:

| SELECT SUM(Amount)FROM invoices; |
| :------------------------------- |

A contradictory query would use a rate or percentage field instead:

| SELECT SUM(Tax_Rate)FROM invoices; |
| :--------------------------------- |

This is not a valid substitute because a rate is not the same financial object as a monetary amount.

Another example is replacing a balance with a transaction amount:

| SELECT SUM(Transaction_Amount)FROM transactions; |
| :----------------------------------------------- |

when the question asks for an ending balance:

What was the ending account balance?

This contradiction is grounded in the accounting distinction between different financial measure types, such as stock values, flow values, rates, balances, quantities, and counts.

The checker only flags this as a hard contradiction when the generated output expression can be mapped to an incompatible annotated financial measure.

### **3.2.3 Balance-count or status proxy**

A balance-count or status proxy contradiction occurs when the SQL answers a financial amount question using a count, status flag, or non-financial proxy instead of the requested balance or amount.

For example, if the question asks:

What is the total outstanding balance for unpaid invoices?

A valid query should aggregate the balance amount:

| SELECT SUM(Balance)FROM invoicesWHERE Status \= 'Unpaid'; |
| :-------------------------------------------------------- |

A contradictory query would count the number of unpaid invoices:

| SELECT COUNT(\*)FROM invoicesWHERE Status \= 'Unpaid'; |
| :----------------------------------------------------- |

The count may be related to the business process, but it is not the requested financial balance.

Another contradictory query would return the status itself:

| SELECT StatusFROM invoicesWHERE Status \= 'Unpaid'; |
| :-------------------------------------------------- |

This does not answer the amount question.

This contradiction is grounded in the difference between financial magnitude and non-financial proxies. A count or status may describe the records, but it does not represent the requested monetary value.

The checker only treats this as a hard failure when the question or output intent requires a financial measure and the SQL output instead uses a clearly incompatible proxy.

### **3.2.4 Transaction-type substitution**

A transaction-type substitution occurs when the SQL filters for an incompatible transaction type.

For example, if the question asks:

What was the total amount from invoices issued to customers?

A valid query should filter invoice transactions:

| SELECT SUM(Amount)FROM master_txn_tableWHERE Transaction_Type \= 'Invoice'; |
| :-------------------------------------------------------------------------- |

A contradictory query would filter bills instead:

| SELECT SUM(Amount)FROM master_txn_tableWHERE Transaction_Type \= 'Bill'; |
| :----------------------------------------------------------------------- |

Invoices and bills represent different accounting events. An invoice usually records money owed by a customer, while a bill usually records money owed to a vendor.

Another example is answering a deposit question using sales receipts:

| SELECT SUM(Amount)FROM master_txn_tableWHERE Transaction_Type \= 'Sales Receipt'; |
| :-------------------------------------------------------------------------------- |

when the question specifically asks for deposits.

This contradiction is grounded in the accounting meaning of transaction types. Different transaction types encode different business events and may affect different financial flows.

The checker only flags this when both the expected and observed transaction types can be identified and the substitution is predefined as incompatible.

### **3.2.5 Financial object or account-scope mismatch**

A financial object mismatch occurs when the SQL uses a financial object, account category, or account scope that contradicts the requested accounting concept.

For example, if the question asks:

What was the total income for consulting services?

A valid query should use an income or revenue account:

| SELECT SUM(Credit)FROM master_txn_tableWHERE Account_Type \= 'Income'AND Account \= 'Consulting Services'; |
| :--------------------------------------------------------------------------------------------------------- |

A contradictory query would use an expense account instead:

| SELECT SUM(Debit)FROM master_txn_tableWHERE Account_Type \= 'Expense'AND Account \= 'Consulting Services'; |
| :--------------------------------------------------------------------------------------------------------- |

Another example is answering a liabilities question using asset accounts:

| SELECT SUM(Balance)FROM accountsWHERE Account_Type \= 'Asset'; |
| :------------------------------------------------------------- |

when the question asks:

What were the company’s total liabilities?

This contradiction is grounded in account classification and financial statement structure. Income, expense, asset, liability, and equity accounts have different accounting meanings.

The checker only flags this when the relevant account classes or financial objects are supported by schema annotations. If the account meaning cannot be determined reliably, the row is marked not evaluable rather than failed.

### **3.2.6 Extra incompatible financial output**

An extra incompatible financial output occurs when the generated SQL adds an additional finance-bearing output that changes the meaning of the answer.

For example, if the question asks:

What was the total sales income?

A valid query should return the requested measure:

| SELECT SUM(Credit) AS total_sales_incomeFROM master_txn_tableWHERE Account \= 'Sales'; |
| :------------------------------------------------------------------------------------- |

A contradictory query may return an additional incompatible financial measure:

| SELECT SUM(Credit) AS total_sales_income, SUM(Debit) AS total_expense_amountFROM master_txn_tableWHERE Account \= 'Sales'; |
| :------------------------------------------------------------------------------------------------------------------------- |

The second output introduces a debit-side amount that was not requested and may change the meaning of the answer.

Another example is returning both a balance and a count:

| SELECT SUM(Balance) AS outstanding_balance, COUNT(\*) AS invoice_countFROM invoicesWHERE Status \= 'Unpaid'; |
| :----------------------------------------------------------------------------------------------------------- |

when the question only asks for the outstanding balance.

This contradiction is grounded in output intent. The generated SQL should return the requested financial object, not an unrelated or incompatible additional financial measure.

The checker only treats this as a hard failure when the added output is finance-bearing and incompatible with the requested output intent.

## **3.3 How the FCR Contradictions Were Derived**

The FCR contradiction families were not added as ad hoc rules after observing individual model outputs. They were derived using a fixed design process.

First, the project identifies accounting concepts that are explicitly represented in the BookSQL schema or schema annotations. These include finance-bearing columns, debit-credit posting-side columns, account categories, transaction types, balances, quantities, and other annotated financial measures.

Second, the project defines contradiction families only when there is a deterministic accounting distinction. For example, debit and credit have different posting-side meanings; a count is not the same as a monetary amount; a transaction type such as invoice is not the same event as a bill or deposit.

Third, each contradiction must be detectable from the generated SQL structure and schema annotations. The evaluator does not rely on an LLM to decide whether a contradiction exists. It parses the SQL, identifies finance-bearing expressions and filters, maps them to annotated schema concepts, and applies predefined compatibility rules.

Fourth, the checker is conservative. If the required annotation, lineage, or expression support is missing, the row is marked not evaluable rather than failed. This avoids turning uncertain cases into artificial ASA failures.

Finally, the same FCR rules are applied uniformly to baseline SQL and repaired SQL. The rules are not conditioned on the verifier’s predicted error label, and they are not changed per model output. This keeps ASA separate from the verifier and reduces circularity.

In short, the FCR taxonomy is derived from:

BookSQL schema annotations  
\+ deterministic accounting distinctions  
\+ observed BookSQL error categories  
\+ conservative SQL-structure checks

It is not intended to cover every possible accounting error. It only covers contradiction families that can be deterministically identified under the current schema annotations and parser support.
