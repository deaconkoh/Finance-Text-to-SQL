"""
data_utils.py

Shared utilities for loading and working with the FINCH dataset.
Imported by create_custom_testset.py, baseline_eval.py, retrieval.py, etc.

Provides:
  - load_finch_schemas()         Load the full schema YAML from HuggingFace.
  - load_finch_records()         Load all FINCH records, optionally filtered by partition.
  - load_baseline_eval_records() Load custom CSV or FINCH partition baseline data.
  - get_finch_db_path()          Download and return the SQLite path for a record.
  - get_matching_schema()        Find the raw schema dict for a given record.
  - format_schema_for_prompt()   Render a schema dict as a prompt-ready string.
  - get_full_schema_cached()     Cached convenience wrapper for the above two.
"""

import csv
from typing import Optional

import yaml
from datasets import load_dataset
from huggingface_hub import hf_hub_download

DEFAULT_ALLOWED_PARTITIONS: frozenset[str] = frozenset({"train", "val", "dev"})

# FINCH dataset loading
def load_finch_schemas() -> list:
    """
    Download and parse the FINCH schema YAML from HuggingFace.
    Returns a list of schema dicts (one per database).
    Result is safe to cache — call once and pass around.
    """
    schema_path = hf_hub_download(
        repo_id="domyn/FINCH",
        filename="schemas/database_schemas.yaml",
        repo_type="dataset",
    )
    with open(schema_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_finch_records(
    allowed_partitions: frozenset[str] = DEFAULT_ALLOWED_PARTITIONS,
) -> list[dict]:
    """
    Load all FINCH records from HuggingFace and return those whose
    `partition` field is in `allowed_partitions`.

    Pass allowed_partitions=None to return every record including test.
    """
    dataset = load_dataset("domyn/FINCH", trust_remote_code=True)

    records = []
    for split_name, split_data in dataset.items():
        for record in split_data:
            if allowed_partitions is None:
                records.append(dict(record))
            else:
                partition = record.get("partition", split_name).lower()
                if partition in allowed_partitions:
                    records.append(dict(record))

    return records


def load_custom_subset_records(csv_path: str) -> list[dict]:
    """
    Load a local custom evaluation subset CSV.

    CSV columns are preserved as-is, including knowledge_intensity when present.
    """
    with open(csv_path, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_baseline_eval_records(
    custom_subset_csv: Optional[str] = None,
    partition: str = "test",
) -> list[dict]:
    """
    Load baseline evaluation records from either a custom CSV or FINCH.

    If custom_subset_csv is provided, that CSV is the evaluation set. Otherwise,
    FINCH is loaded and filtered by its internal partition column.
    """
    if custom_subset_csv:
        records = load_custom_subset_records(custom_subset_csv)
    else:
        dataset = load_dataset("domyn/FINCH")
        records = dataset["train"].filter(lambda x: x["partition"] == partition)

    return sorted(
        list(records),
        key=lambda x: (x["db_name"], x["db_id"]),
    )


def get_finch_db_path(record: dict) -> str:
    db_name = record["db_name"]
    db_id = record["db_id"]

    if db_name == "book_sql":
        filename = f"text2sql-db/text2sql/book_sql/accounting.sqlite"
    elif db_name in ("bull", "spider"):
        # subfolder per db_id, need to find the sqlite inside
        filename = f"text2sql-db/text2sql/{db_name}/{db_id}/{db_id}.sqlite"
    else:
        # bird: flat structure
        filename = f"text2sql-db/text2sql/{db_name}/{db_id}.sqlite"

    return hf_hub_download(
        repo_id="domyn/FINCH",
        filename=filename,
        repo_type="dataset",
    )


# Schema formatting
def get_matching_schema(record: dict, schemas: list) -> Optional[dict]:
    """
    Find and return the raw schema dict for a given record's (db_name, db_id).
    Returns None if no matching schema is found.

    Each schema item has a "database" key plus one content key (usually db_id,
    but occasionally differs e.g. book_sql stores its schema under "accounting").
    We match on db_name first, then return whichever key isn't "database".
    """
    db_name = record["db_name"]
    db_id = record["db_id"]
    for schema_item in schemas:
        if schema_item.get("database") != db_name:
            continue
        # Try db_id first (the common case)
        if db_id in schema_item:
            return schema_item[db_id]
        # Fallback: return the first key that isn't "database"
        content_keys = [k for k in schema_item if k != "database"]
        if content_keys:
            return schema_item[content_keys[0]]
    return None


def format_schema_for_prompt(schema: dict) -> str:
    """
    Render a raw schema dict as a compact, prompt-ready string.

    Output format:
        Tables:
        table_name(col1 TYPE PRIMARY KEY, col2 TYPE, ...)

        Relationships:
        table_a.fk_col references table_b.fk_col
    """
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
        table_lines.append(f"{table_name}({', '.join(columns)})")

        for fk in table_info.get("foreign_keys", []):
            fk_col = fk["column_name"]
            ref_table = fk["table_name"]
            relationship_lines.append(
                f"{table_name}.{fk_col} references {ref_table}.{fk_col}"
            )

    schema_text = "Tables:\n" + "\n".join(table_lines)
    if relationship_lines:
        schema_text += "\n\nRelationships:\n" + "\n".join(relationship_lines)
    return schema_text


# Module-level cache: (db_name, db_id) -> formatted schema string
_schema_cache: dict[tuple[str, str], str] = {}


def get_full_schema_cached(record: dict, schemas: list) -> Optional[str]:
    """
    Cached convenience wrapper: looks up and formats the schema for a record.
    Returns None if no schema is found for the record's (db_name, db_id).
    Subsequent calls for the same (db_name, db_id) hit the in-memory cache.
    """
    key = (record["db_name"], record["db_id"])
    if key not in _schema_cache:
        matching = get_matching_schema(record, schemas)
        if matching is None:
            return None
        _schema_cache[key] = format_schema_for_prompt(matching)
    return _schema_cache[key]
