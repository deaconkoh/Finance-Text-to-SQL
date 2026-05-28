import argparse
import json
import random
import sys
from pathlib import Path
from pprint import pprint

PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.finverisql.sql_parser import parse_sql
from src.finverisql.schema_loader import SchemaAnnotationStore
from src.finverisql.sql_semantic_mapping import build_sql_financial_semantics


RANDOM_SEED = 42


CONTROLLED_SQLS = [
    {
        "name": "Controlled 1: correct expense object and debit measure",
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
        "name": "Controlled 2: missing account_type filter",
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
    status = row.get("status")
    baseline_status = row.get("baseline_status")

    if status is not None:
        return status == "success"

    if baseline_status is not None:
        return baseline_status == "success"

    return True


def filter_rows(rows: list[dict], evaluation_group: str | None) -> list[dict]:
    filtered = []

    for row in rows:
        if not row.get("generated_sql"):
            continue

        if not row_is_success(row):
            continue

        if evaluation_group is not None:
            if row.get("evaluation_group") != evaluation_group:
                continue

        filtered.append(row)

    return filtered


def compact_parsed_dict(parsed) -> dict:
    parsed_dict = parsed.to_dict()

    return {
        "parse_error": parsed_dict.get("parse_error"),
        "tables": parsed_dict.get("tables"),
        "aliases": parsed_dict.get("aliases"),
        "joins": parsed_dict.get("joins"),
        "aggregations": parsed_dict.get("aggregations"),
        "filters": parsed_dict.get("filters"),
        "group_by": parsed_dict.get("group_by"),
        "order_by": parsed_dict.get("order_by"),
        "limit": parsed_dict.get("limit"),
    }


def compact_column_use(item: dict) -> dict:
    annotations = item.get("annotations") or []

    compact_annotations = []

    for annotation in annotations:
        compact_annotations.append(
            {
                "table": annotation.get("table"),
                "column": annotation.get("column"),
                "semantic_role": annotation.get("semantic_role"),
                "measure_type": annotation.get("measure_type"),
                "sign_convention": annotation.get("sign_convention"),
                "entity_scope": annotation.get("entity_scope"),
                "unit": annotation.get("unit"),
            }
        )

    return {
        "source": item.get("source"),
        "column": item.get("column"),
        "table": item.get("table"),
        "func": item.get("func"),
        "operator": item.get("operator"),
        "values": item.get("values"),
        "expression": item.get("expression"),
        "annotations": compact_annotations,
    }


def compact_semantics_dict(semantics) -> dict:
    data = semantics.to_dict()

    measure_usage = data.get("measure_usage", {})
    logic = data.get("logic", {})

    return {
        "parse_error": data.get("parse_error"),
        "tables": data.get("tables"),
        "joins": data.get("joins"),
        "object_scope": data.get("object_scope"),
        "measure_usage": {
            "aggregated_column_names": measure_usage.get("aggregated_column_names"),
            "aggregation_functions": measure_usage.get("aggregation_functions"),
            "aggregated_columns": [
                compact_column_use(item)
                for item in measure_usage.get("aggregated_columns", [])
            ],
            "selected_columns": [
                compact_column_use(item)
                for item in measure_usage.get("selected_columns", [])
            ],
        },
        "logic": {
            "limit": logic.get("limit"),
            "date_conditions": logic.get("date_conditions"),
            "group_by_columns": [
                compact_column_use(item)
                for item in logic.get("group_by_columns", [])
            ],
            "order_by_columns": [
                compact_column_use(item)
                for item in logic.get("order_by_columns", [])
            ],
        },
    }


def print_case(
    title: str,
    sql: str,
    schema_store: SchemaAnnotationStore,
    question: str | None = None,
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
    print(sql.strip())
    print("-" * 120)

    parsed = parse_sql(sql)

    semantics = build_sql_financial_semantics(
        parsed_sql=parsed,
        schema_store=schema_store,
    )

    print("Compact parsed SQL:")
    pprint(compact_parsed_dict(parsed), width=140)
    print("-" * 120)

    print("Compact SQL financial semantics:")
    pprint(compact_semantics_dict(semantics), width=140)
    print()


def run_controlled(schema_store: SchemaAnnotationStore) -> None:
    print("\n")
    print("#" * 120)
    print("CONTROLLED SQL SEMANTIC MAPPING TESTS")
    print("#" * 120)

    for item in CONTROLLED_SQLS:
        print_case(
            title=item["name"],
            question=item["question"],
            sql=item["sql"],
            schema_store=schema_store,
        )


def run_real(
    input_jsonl: Path,
    schema_store: SchemaAnnotationStore,
    sample_size: int,
    evaluation_group: str | None,
    random_sample: bool,
) -> None:
    rows = filter_rows(load_jsonl(input_jsonl), evaluation_group)

    if not rows:
        raise ValueError(
            "No matching rows found.\n"
            f"input_jsonl={input_jsonl}\n"
            f"evaluation_group={evaluation_group}"
        )

    if random_sample:
        random.seed(RANDOM_SEED)
        sample = random.sample(rows, min(sample_size, len(rows)))
    else:
        sample = rows[:sample_size]

    print("\n")
    print("#" * 120)
    print("REAL SQL SEMANTIC MAPPING TESTS")
    print("#" * 120)
    print(f"Input file      : {input_jsonl}")
    print(f"Matching rows   : {len(rows)}")
    print(f"Sample size     : {len(sample)}")
    print(f"Evaluation group: {evaluation_group or 'ALL'}")
    print(f"Random sample   : {random_sample}")
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

        print_case(
            title=f"Real example {index}: {row.get('question_id')}",
            question=row.get("question"),
            sql=row.get("generated_sql"),
            schema_store=schema_store,
            metadata=metadata,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test SQL semantic mapping from parsed SQL and schema annotations."
    )

    parser.add_argument(
        "--schema-annotations-path",
        default="data/booksql/schema_annotations.json",
        help="Path to schema annotations JSON.",
    )
    parser.add_argument(
        "--input-jsonl",
        default=None,
        help="Optional evaluated/baseline JSONL to test real generated SQL.",
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
        help="Optional evaluation group filter. Only works for evaluated JSONL.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=10,
    )
    parser.add_argument(
        "--first-n",
        action="store_true",
        help="Use first N matching rows instead of random sample.",
    )
    parser.add_argument(
        "--skip-controlled",
        action="store_true",
        help="Skip controlled examples.",
    )
    parser.add_argument(
        "--skip-real",
        action="store_true",
        help="Skip real examples.",
    )

    args = parser.parse_args()

    schema_store = SchemaAnnotationStore.from_json(
        args.schema_annotations_path
    )

    if not args.skip_controlled:
        run_controlled(schema_store)

    if not args.skip_real:
        if args.input_jsonl is None:
            raise ValueError("--input-jsonl is required unless --skip-real is used.")

        run_real(
            input_jsonl=Path(args.input_jsonl),
            schema_store=schema_store,
            sample_size=args.sample_size,
            evaluation_group=args.evaluation_group,
            random_sample=not args.first_n,
        )


if __name__ == "__main__":
    main()