import sqlite3
import yaml
from huggingface_hub import hf_hub_download


def load_finch_schemas():
    schema_path = hf_hub_download(
        repo_id="domyn/FINCH",
        filename="schemas/database_schemas.yaml",
        repo_type="dataset"
    )

    with open(schema_path, "r", encoding="utf-8") as f:
        schemas = yaml.safe_load(f)

    return schemas


def get_finch_db_path(record):
    db_path = hf_hub_download(
        repo_id="domyn/FINCH",
        filename=f"text2sql-db/text2sql/{record['db_name']}/{record['db_id']}.sqlite",
        repo_type="dataset"
    )

    return db_path


def inspect_sqlite_tables(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
    SELECT name
    FROM sqlite_master
    WHERE type = 'table';
    """)

    results = cursor.fetchall()
    conn.close()

    return results


def get_matching_schema(record, schemas):
    db_name = record["db_name"]
    db_id = record["db_id"]

    for schema_item in schemas:
        if schema_item.get("database") == db_name and db_id in schema_item:
            return schema_item[db_id]

    raise ValueError(f"No schema found for db_name={db_name}, db_id={db_id}")


def format_schema_for_prompt(schema):
    table_lines = []
    relationship_lines = []

    for table_name, table_info in schema.items():
        columns = []

        for col in table_info.get("columns_info", []):
            col_name = col["column_name"]
            col_type = col.get("column_type", "UNKNOWN")

            if col.get("primary_key", False):
                columns.append(f"{col_name} {col_type} PRIMARY KEY")
            else:
                columns.append(f"{col_name} {col_type}")

        columns_text = ", ".join(columns)
        table_lines.append(f"{table_name}({columns_text})")

        for fk in table_info.get("foreign_keys", []):
            fk_col = fk["column_name"]
            ref_table = fk["table_name"]

            # Usually the referenced column has the same name.
            # Example: account.district_id references district.district_id
            relationship_lines.append(
                f"{table_name}.{fk_col} references {ref_table}.{fk_col}"
            )

    schema_text = "Tables:\n"
    schema_text += "\n".join(table_lines)

    if relationship_lines:
        schema_text += "\n\nRelationships:\n"
        schema_text += "\n".join(relationship_lines)

    return schema_text


schema_cache = {}

def get_full_schema_cached(record, schemas):
    key = (record["db_name"], record["db_id"])

    if key not in schema_cache:
        matching_schema = get_matching_schema(record, schemas)
        schema_cache[key] = format_schema_for_prompt(matching_schema)

    return schema_cache[key]