import argparse
import json
import sys
from pathlib import Path
from pprint import pprint
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.finverisql.schema_loader import SchemaAnnotationStore
from src.finverisql.sql_parser import parse_sql
from src.finverisql.sql_semantic_mapping import build_sql_financial_semantics
from src.finverisql.sql_decompiler import decompile_semantics


DEFAULT_SCHEMA_PATH = "data/booksql/schema_annotations.json"


CONTROLLED_SQLS = {
    "payment_transaction_type": {
        "question": "How many payments did we receive?",
        "sql": """
            SELECT COUNT(*)
            FROM master_txn_table
            WHERE Transaction_TYPE = 'Payment';
        """,
    },
    "expense_sum": {
        "question": "What is our total expense?",
        "sql": """
            SELECT SUM(m.Debit)
            FROM master_txn_table m
            JOIN chart_of_accounts c
            ON m.businessID = c.businessID
            AND m.Account = c.Account_name
            WHERE c.Account_type = 'expenses';
        """,
    },
    "revenue_sum": {
        "question": "What is our total revenue?",
        "sql": """
            SELECT SUM(m.Credit)
            FROM master_txn_table m
            JOIN chart_of_accounts c
            ON m.businessID = c.businessID
            AND m.Account = c.Account_name
            WHERE c.Account_type = 'income';
        """,
    },
    "date_filter": {
        "question": "What is our total amount this year?",
        "sql": """
            SELECT SUM(Amount)
            FROM master_txn_table
            WHERE Transaction_DATE >= '2024-01-01';
        """,
    },
    "computed_filter": {
        "question": "What is the total amount for high-value line items?",
        "sql": """
            SELECT SUM(Amount)
            FROM master_txn_table
            WHERE Quantity * Rate > 10000;
        """,
    },
    "column_to_column_filter": {
        "question": "How many transactions were created before the transaction date?",
        "sql": """
            SELECT COUNT(*)
            FROM master_txn_table
            WHERE Transaction_DATE > CreatedDATE;
        """,
    },
    "group_order_limit": {
        "question": "Which account had the highest expense?",
        "sql": """
            SELECT c.Account_name, SUM(m.Debit)
            FROM master_txn_table m
            JOIN chart_of_accounts c
            ON m.businessID = c.businessID
            AND m.Account = c.Account_name
            WHERE c.Account_type = 'expenses'
            GROUP BY c.Account_name
            ORDER BY SUM(m.Debit) DESC
            LIMIT 1;
        """,
    },
    "ambiguous_balance": {
        "question": "What is the balance?",
        "sql": """
            SELECT Balance
            FROM customers, vendors
            WHERE Balance > 100;
        """,
    },
    "unsupported_cte": {
        "question": "What is the total amount?",
        "sql": """
            WITH cte AS (
                SELECT *
                FROM master_txn_table
            )
            SELECT c.Amount
            FROM cte c;
        """,
    },
}


def load_jsonl(path: str | Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows = []
    path = Path(path)

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue

            rows.append(json.loads(line))

            if limit is not None and len(rows) >= limit:
                break

    return rows


def load_jsonl_filtered(
    path: str | Path,
    limit: int | None = None,
    evaluation_group: str | None = None,
) -> list[dict[str, Any]]:
    rows = []
    path = Path(path)

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue

            row = json.loads(line)

            if evaluation_group is not None:
                group = row.get("evaluation_group") or row.get("group")
                if group != evaluation_group:
                    continue

            rows.append(row)

            if limit is not None and len(rows) >= limit:
                break

    return rows


def extract_question(row: dict[str, Any]) -> str:
    return (
        row.get("question")
        or row.get("query")
        or row.get("natural_language_question")
        or ""
    )


def extract_sql(row: dict[str, Any]) -> str:
    return (
        row.get("generated_sql")
        or row.get("predicted_sql")
        or row.get("sql")
        or row.get("prediction")
        or ""
    )


def run_single_case(
    name: str,
    question: str,
    sql: str,
    schema_store: SchemaAnnotationStore,
    show_semantics: bool = False,
) -> None:
    print("=" * 120)
    print(f"TEST: {name}")
    print("-" * 120)

    if question:
        print("QUESTION:")
        print(question.strip())
        print()

    print("SQL:")
    print(sql.strip())
    print()

    parsed = parse_sql(sql)

    semantics = build_sql_financial_semantics(
        parsed_sql=parsed,
        schema_store=schema_store,
    )

    profile = decompile_semantics(semantics)

    print("DECOMPILED EXECUTION PROFILE:")
    print(profile)

    if show_semantics:
        print("\nRAW PARSED SQL:")
        pprint(parsed.to_dict(), width=140)

        print("\nRAW SQL FINANCIAL SEMANTICS:")
        pprint(semantics.to_dict(), width=140)

    print()


def run_controlled_tests(
    schema_store: SchemaAnnotationStore,
    show_semantics: bool = False,
) -> None:
    print("\n" + "#" * 120)
    print("CONTROLLED DECOMPILER TESTS")
    print("#" * 120)

    for name, item in CONTROLLED_SQLS.items():
        run_single_case(
            name=name,
            question=item["question"],
            sql=item["sql"],
            schema_store=schema_store,
            show_semantics=show_semantics,
        )


def run_real_output_tests(
    output_path: str | Path,
    schema_store: SchemaAnnotationStore,
    limit: int,
    evaluation_group: str | None,
    show_semantics: bool = False,
) -> None:
    print("\n" + "#" * 120)
    print("REAL BASELINE OUTPUT DECOMPILER TESTS")
    print("#" * 120)
    print(f"Input file: {output_path}")
    print(f"Limit: {limit}")
    print(f"Evaluation group filter: {evaluation_group or 'none'}")
    print()

    rows = load_jsonl_filtered(
        path=output_path,
        limit=limit,
        evaluation_group=evaluation_group,
    )

    if not rows:
        print("No rows found. Check the file path, field names, or evaluation_group filter.")
        return

    for idx, row in enumerate(rows, start=1):
        question = extract_question(row)
        sql = extract_sql(row)

        row_name = (
            row.get("question_id")
            or row.get("id")
            or row.get("qid")
            or f"row_{idx}"
        )

        if not sql:
            print("=" * 120)
            print(f"REAL TEST: {row_name}")
            print("No generated SQL found in this row. Available keys:")
            print(sorted(row.keys()))
            print()
            continue

        run_single_case(
            name=f"real_{idx}_{row_name}",
            question=question,
            sql=sql,
            schema_store=schema_store,
            show_semantics=show_semantics,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test FinVeriSQL parser → semantic mapping → decompiler pipeline."
    )

    parser.add_argument(
        "--schema-path",
        default=DEFAULT_SCHEMA_PATH,
        help="Path to schema annotations JSON.",
    )

    parser.add_argument(
        "--real-output",
        default=None,
        help="Optional path to baseline output JSONL, e.g. qwen zero/few-shot output.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of real output rows to test.",
    )

    parser.add_argument(
        "--evaluation-group",
        default=None,
        help=(
            "Optional filter for evaluated JSONL rows, e.g. B_wrong_executable. "
            "Leave empty for raw baseline output files."
        ),
    )

    parser.add_argument(
        "--show-semantics",
        action="store_true",
        help="Print raw ParsedSQL and SQLFinancialSemantics objects.",
    )

    parser.add_argument(
        "--skip-controlled",
        action="store_true",
        help="Skip controlled SQL tests.",
    )

    args = parser.parse_args()

    schema_store = SchemaAnnotationStore.from_json(args.schema_path)

    if not args.skip_controlled:
        run_controlled_tests(
            schema_store=schema_store,
            show_semantics=args.show_semantics,
        )

    if args.real_output:
        run_real_output_tests(
            output_path=args.real_output,
            schema_store=schema_store,
            limit=args.limit,
            evaluation_group=args.evaluation_group,
            show_semantics=args.show_semantics,
        )


if __name__ == "__main__":
    main()