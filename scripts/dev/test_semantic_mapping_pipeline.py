import sys
from pathlib import Path
from pprint import pprint

PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.finverisql.schema_loader import SchemaAnnotationStore
from src.finverisql.sql_parser import parse_sql
from src.finverisql.sql_semantic_mapping import build_sql_financial_semantics


SCHEMA_PATH = "data/booksql/schema_annotations.json"


TEST_SQLS = {
    "payment_transaction_type": """
        SELECT COUNT(*)
        FROM master_txn_table
        WHERE Transaction_TYPE = 'Payment';
    """,

    "expense_account_type": """
        SELECT SUM(Debit)
        FROM master_txn_table m
        JOIN chart_of_accounts c ON m.Account = c.Account_name
        WHERE c.Account_type = 'Expense';
    """,

    "revenue_account_type": """
        SELECT SUM(Credit)
        FROM master_txn_table m
        JOIN chart_of_accounts c ON m.Account = c.Account_name
        WHERE c.Account_type = 'Income';
    """,

    "date_condition": """
        SELECT SUM(Amount)
        FROM master_txn_table
        WHERE Transaction_DATE >= '2024-01-01';
    """,

    "computed_filter": """
        SELECT SUM(Amount)
        FROM master_txn_table
        WHERE Quantity * Rate > 10000;
    """,

    "column_to_column_filter": """
        SELECT COUNT(*)
        FROM master_txn_table
        WHERE Transaction_DATE > CreatedDATE;
    """,

    "ambiguous_unqualified_balance": """
        SELECT Balance
        FROM customers, vendors
        WHERE Balance > 100;
    """,

    "unsupported_cte": """
        WITH cte AS (
            SELECT *
            FROM master_txn_table
        )
        SELECT c.Amount
        FROM cte c;
    """,
}


def compact_semantics(semantics):
    data = semantics.to_dict()

    return {
        "parse_error": data.get("parse_error"),
        "unsupported_lineage": data.get("unsupported_lineage"),
        "tables": data.get("tables"),

        "object_scope": data.get("object_scope"),

        "measure_usage": {
            "aggregation_functions": data["measure_usage"].get("aggregation_functions"),
            "measure_types": data["measure_usage"].get("measure_types"),
            "sign_conventions": data["measure_usage"].get("sign_conventions"),
            "ambiguous_measure_columns": data["measure_usage"].get("ambiguous_measure_columns"),
            "aggregated_columns": [
                {
                    "column": item.get("column"),
                    "table": item.get("table"),
                    "expression": item.get("expression"),
                    "function": item.get("function"),
                    "is_ambiguous": item.get("is_ambiguous"),
                    "annotations": [
                        {
                            "table": ann.get("table"),
                            "column": ann.get("column"),
                            "semantic_role": ann.get("semantic_role"),
                            "measure_type": ann.get("measure_type"),
                            "sign_convention": ann.get("sign_convention"),
                            "entity_scope": ann.get("entity_scope"),
                        }
                        for ann in item.get("annotations", [])
                    ],
                }
                for item in data["measure_usage"].get("aggregated_columns", [])
            ],
        },

        "logic": data.get("logic"),
    }


def main():
    schema_store = SchemaAnnotationStore.from_json(SCHEMA_PATH)

    for name, sql in TEST_SQLS.items():
        print("=" * 120)
        print(f"TEST: {name}")
        print("-" * 120)
        print(sql.strip())

        parsed = parse_sql(sql)
        semantics = build_sql_financial_semantics(parsed, schema_store)

        print("\nParsed SQL:")
        pprint(parsed.to_dict(), width=140)

        print("\nCompact semantics:")
        pprint(compact_semantics(semantics), width=140)

        print()


if __name__ == "__main__":
    main()