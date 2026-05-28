import argparse
import json
import random
import sys
from pathlib import Path
from pprint import pprint

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.finverisql.sql_parser import parse_sql

RANDOM_SEED = 42


CONTROLLED_SQLS = [
    {
        "name": "Controlled 1: expense query missing account_type filter",
        "question": "What account had our biggest expense this fiscal year to date?",
        "sql": """
        SELECT account, SUM(debit)
        FROM master_txn_table
        WHERE transaction_date BETWEEN date('now', 'start of year') AND date('now')
        GROUP BY account
        ORDER BY SUM(debit) DESC
        LIMIT 1;
        """,
    },
    {
        "name": "Controlled 2: expense query with account_type filter and aliases",
        "question": "What account had our biggest expense this fiscal year to date?",
        "sql": """
        SELECT T1.account, SUM(T1.debit)
        FROM master_txn_table AS T1
        JOIN chart_of_accounts AS T2
        ON T1.account = T2.account_name
        WHERE T2.account_type IN ('Expense', 'Other Expense')
        AND T1.transaction_date BETWEEN date(current_date, '-3 months', 'start of year', '+3 months')
        AND date(current_date, '-3 months', 'start of year', '+1 year', '+3 months', '-1 day')
        GROUP BY T1.account
        ORDER BY SUM(T1.debit) DESC
        LIMIT 1;
        """,
    },
    {
        "name": "Controlled 3: wrong measure amount instead of debit",
        "question": "What account had our biggest expense this fiscal year to date?",
        "sql": """
        SELECT T1.account, SUM(T1.amount)
        FROM master_txn_table AS T1
        JOIN chart_of_accounts AS T2
        ON T1.account = T2.account_name
        WHERE T2.account_type IN ('Expense', 'Other Expense')
        GROUP BY T1.account
        ORDER BY SUM(T1.amount) DESC
        LIMIT 1;
        """,
    },
    {
        "name": "Controlled 4: payment existence query",
        "question": "Did we receive payment from Cameron Abbott partnership This year?",
        "sql": """
        SELECT EXISTS (
            SELECT 1
            FROM master_txn_table
            JOIN customers ON master_txn_table.Customers = customers.customer_name
            WHERE customers.customer_name = 'Cameron Abbott Partnership'
              AND master_txn_table.Transaction_DATE >= DATE('now','-1 year')
              AND master_txn_table.AR_paid = 'Yes'
        );
        """,
    },
    {
        "name": "Controlled 5: count checks query",
        "question": "How many checks did we receive yesterday?",
        "sql": """
        SELECT COUNT(DISTINCT transaction_id)
        FROM master_txn_table
        WHERE transaction_type = 'check'
        AND transaction_date BETWEEN date(current_date, '-1 day') AND date(current_date, '-1 day');
        """,
    },
]


def load_jsonl(path: Path) -> list[dict]:
    rows = []

    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))

    return rows


def row_is_success(row: dict) -> bool:
    """
    Supports both baseline JSONL and evaluated JSONL formats.

    Baseline output usually has:
      status = success

    Evaluated output usually has:
      baseline_status = success
    """
    status = row.get("status")
    baseline_status = row.get("baseline_status")

    if status is not None:
        return status == "success"

    if baseline_status is not None:
        return baseline_status == "success"

    return True


def filter_rows(
    rows: list[dict],
    evaluation_group: str | None,
    include_both_empty: bool,
) -> list[dict]:
    filtered = []

    for row in rows:
        generated_sql = row.get("generated_sql")

        if not generated_sql:
            continue

        if not row_is_success(row):
            continue

        if evaluation_group is not None:
            if row.get("evaluation_group") != evaluation_group:
                continue

        if not include_both_empty:
            ambiguity_flags = row.get("ambiguity_flags") or []
            if "both_results_empty" in ambiguity_flags:
                continue

        filtered.append(row)

    return filtered


def print_parsed_case(
    title: str,
    question: str | None,
    sql: str,
    metadata: dict | None = None,
) -> None:
    print("=" * 120)
    print(title)
    print("=" * 120)

    if metadata:
        print("Metadata:")
        pprint(metadata, width=140)
        print("-" * 120)

    if question:
        print("Question:")
        print(question)
        print("-" * 120)

    print("SQL:")
    print(sql)
    print("-" * 120)

    parsed = parse_sql(sql)

    print("Parsed output:")
    pprint(parsed.to_dict(), width=140)
    print()


def run_controlled_examples() -> None:
    print("\n")
    print("#" * 120)
    print("CONTROLLED SQL PARSER TESTS")
    print("#" * 120)

    for item in CONTROLLED_SQLS:
        print_parsed_case(
            title=item["name"],
            question=item["question"],
            sql=item["sql"],
        )


def run_real_examples(
    input_jsonl: Path,
    sample_size: int,
    evaluation_group: str | None,
    include_both_empty: bool,
    random_sample: bool,
) -> None:
    rows = load_jsonl(input_jsonl)

    rows = filter_rows(
        rows=rows,
        evaluation_group=evaluation_group,
        include_both_empty=include_both_empty,
    )

    if not rows:
        raise ValueError(
            "No matching rows found.\n"
            f"input_jsonl={input_jsonl}\n"
            f"evaluation_group={evaluation_group}\n"
            f"include_both_empty={include_both_empty}"
        )

    if random_sample:
        random.seed(RANDOM_SEED)
        sample = random.sample(rows, min(sample_size, len(rows)))
    else:
        sample = rows[:sample_size]

    print("\n")
    print("#" * 120)
    print("REAL BASELINE SQL PARSER TESTS")
    print("#" * 120)
    print(f"Input file        : {input_jsonl}")
    print(f"Matching rows     : {len(rows)}")
    print(f"Sample size       : {len(sample)}")
    print(f"Evaluation group  : {evaluation_group or 'ALL'}")
    print(f"Random sample     : {random_sample}")
    print(f"Include both-empty: {include_both_empty}")
    print("#" * 120)

    for index, row in enumerate(sample, start=1):
        metadata = {
            "sample_index": index,
            "question_id": row.get("question_id"),
            "generator": row.get("generator"),
            "prompt_setting": row.get("prompt_setting"),
            "level": row.get("level"),
            "evaluation_group": row.get("evaluation_group"),
            "execution_match": row.get("execution_match"),
            "ambiguity_flags": row.get("ambiguity_flags"),
            "generated_execution_status": row.get("generated_execution_status"),
            "gold_execution_status": row.get("gold_execution_status"),
        }

        print_parsed_case(
            title=f"Real example {index}: {row.get('question_id')}",
            question=row.get("question"),
            sql=row.get("generated_sql"),
            metadata=metadata,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke test FinVeriSQL SQL parser on controlled and real baseline SQLs."
    )

    parser.add_argument(
        "--input-jsonl",
        default=None,
        help="Optional baseline/evaluated JSONL file to sample generated SQL from.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=20,
        help="Number of real examples to sample.",
    )
    parser.add_argument(
        "--evaluation-group",
        default=None,
        choices=[
            "A_correct_executable",
            "B_wrong_executable",
            "C_non_executable",
            "D_ambiguous",
        ],
        help="Optional evaluation_group filter. Only works for evaluated JSONL.",
    )
    parser.add_argument(
        "--include-both-empty",
        action="store_true",
        help="Include rows with ambiguity_flags containing both_results_empty.",
    )
    parser.add_argument(
        "--first-n",
        action="store_true",
        help="Use first N matching rows instead of random sampling.",
    )
    parser.add_argument(
        "--skip-controlled",
        action="store_true",
        help="Skip controlled parser tests.",
    )
    parser.add_argument(
        "--skip-real",
        action="store_true",
        help="Skip real baseline parser tests.",
    )

    args = parser.parse_args()

    if not args.skip_controlled:
        run_controlled_examples()

    if not args.skip_real:
        if args.input_jsonl is None:
            raise ValueError("--input-jsonl is required unless --skip-real is used.")

        run_real_examples(
            input_jsonl=Path(args.input_jsonl),
            sample_size=args.sample_size,
            evaluation_group=args.evaluation_group,
            include_both_empty=args.include_both_empty,
            random_sample=not args.first_n,
        )


if __name__ == "__main__":
    main()