from __future__ import annotations

import json
from pathlib import Path
import sys
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from src.finverisql.schema_loader import SchemaAnnotationStore
from src.finverisql.sql_parser import parse_sql
from src.finverisql.sql_semantic_mapping import build_sql_financial_semantics
from finverisql.old.fsir_builder import build_fsir


SCHEMA_PATH = PROJECT_ROOT / "data/booksql/schema_annotations.json"


TEST_CASES = {
    "account_income_scope_join": """
        SELECT SUM(m.Credit)
        FROM master_txn_table AS m
        JOIN chart_of_accounts AS coa
          ON m.Account = coa.Account_name
        WHERE coa.Account_type IN ('Income', 'Other Income');
    """,

    "payment_event_scope": """
        SELECT COUNT(*)
        FROM master_txn_table
        WHERE Transaction_TYPE = 'Payment';
    """,

    "payment_status_scope": """
        SELECT COUNT(*)
        FROM master_txn_table
        WHERE AR_paid = 'Yes';
    """,

    "payment_method_scope": """
        SELECT SUM(Amount)
        FROM master_txn_table
        WHERE payment_method = '[payment_method]';
    """,

    "sold_product_scope": """
        SELECT MAX(Transaction_DATE)
        FROM master_txn_table
        WHERE Product_Service = 'Photography'
          AND Credit > 0;
    """,

    "quantity_sold": """
        SELECT SUM(Quantity)
        FROM master_txn_table
        WHERE Product_Service = 'Paper';
    """,

    "row_count_sold": """
        SELECT COUNT(transaction_id)
        FROM master_txn_table
        WHERE Product_Service = 'Paper';
    """,
}


def compact_fsir_view(fsir: dict) -> dict:
    concept_layer = fsir.get("financial_concept_layer", {})
    measurement_layer = fsir.get("measurement_layer", {})
    topology = fsir.get("reporting_topology_layer", {})

    measurements = measurement_layer.get("measurements", [])
    components = []

    for measurement in measurements:
        metric_expression = measurement.get("metric_expression", {})
        for component in metric_expression.get("components", []):
            components.append(
                {
                    "column": component.get("column"),
                    "aggregation_function": component.get("aggregation_function"),
                    "extracted_vector": component.get("extracted_vector"),
                    "measure_family": component.get("measure_family"),
                    "measure_type": component.get("measure_type"),
                    "unit": component.get("unit"),
                    "column_normal_balance": component.get("column_normal_balance"),
                    "mapping_status": component.get("mapping_status"),
                }
            )

    return {
        "status": fsir.get("status"),
        "profile_extraction": fsir.get("profile_extraction"),
        "scope_constraints": concept_layer.get("scope_constraints", []),
        "scope_coverage": concept_layer.get("scope_coverage"),
        "measurement_components": components,
        "analytical_grain": topology.get("analytical_grain"),
        "grouping_dimensions": topology.get("grouping_dimensions"),
        "temporal_resolution": topology.get("temporal_resolution"),
        "filter_topology": topology.get("filter_topology"),
    }


def main() -> None:
    if not Path(SCHEMA_PATH).exists():
        raise FileNotFoundError(
            f"Could not find schema annotations at {SCHEMA_PATH}. "
            "Update SCHEMA_PATH in this script."
        )

    schema_store = SchemaAnnotationStore.from_json(SCHEMA_PATH)

    for name, sql in TEST_CASES.items():
        print("\n" + "=" * 100)
        print(f"TEST CASE: {name}")
        print("=" * 100)

        parsed = parse_sql(sql)
        semantics = build_sql_financial_semantics(parsed, schema_store)
        fsir = build_fsir(semantics)

        print(json.dumps(compact_fsir_view(fsir), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()