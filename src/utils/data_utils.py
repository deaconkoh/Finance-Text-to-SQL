"""
BookSQL-only dataset utilities for baseline Text-to-SQL experiments.

Expected setup workflow:
    python scripts/setup_booksql.py

That script prepares:
    data/booksql/booksql_normalized.jsonl
    data/booksql/accounting.sqlite
    data/booksql/schema.txt

This module does not download BookSQL from Hugging Face.
It only loads the prepared local files for baseline generation and evaluation.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]

BOOKSQL_DATASET_NAME = "Exploration-Lab/BookSQL"
BOOKSQL_DB_ID = "booksql"

BOOKSQL_DIR = PROJECT_ROOT / "data" / "booksql"
BOOKSQL_DATA_PATH = BOOKSQL_DIR / "booksql_normalized.jsonl"
BOOKSQL_DB_PATH = BOOKSQL_DIR / "accounting.sqlite"
BOOKSQL_SCHEMA_PATH = BOOKSQL_DIR / "schema.txt"

BOOKSQL_NORMALIZED_COLUMNS = (
    "question_id",
    "db_id",
    "question",
    "gold_sql",
    "level",
    "split",
)


class BookSQLConfigError(RuntimeError):
    """Raised when prepared BookSQL files are missing or malformed."""


def normalize_split(value: Any) -> str:
    """Normalize BookSQL split aliases.

    Args:
        value: Raw split value from a prepared BookSQL row or CLI argument.

    Returns:
        Canonical split name. `val`, `dev`, and `validation` all map to
        `validation`; other values are lowercased and stripped.
    """
    split = str(value).strip().lower()

    if split in {"val", "dev", "validation"}:
        return "validation"

    if split == "train":
        return "train"

    if split == "test":
        return "test"

    return split


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file into a list of dictionaries.

    Args:
        path: JSONL file path.

    Returns:
        List of parsed JSON object rows.

    Raises:
        BookSQLConfigError: If any non-empty line is invalid JSON or not a JSON
            object.
    """
    rows: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()

            if not line:
                continue

            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BookSQLConfigError(
                    f"Invalid JSONL at {path}:{line_number}: {exc}"
                ) from exc

            if not isinstance(row, dict):
                raise BookSQLConfigError(
                    f"Expected JSON object at {path}:{line_number}, "
                    f"got {type(row).__name__}."
                )

            rows.append(row)

    return rows


def validate_normalized_row(row: dict[str, Any], row_index: int) -> None:
    """Validate required fields in one prepared BookSQL row.

    Args:
        row: Parsed JSON object from `booksql_normalized.jsonl`.
        row_index: Zero-based row index used in error messages.

    Raises:
        BookSQLConfigError: If required normalized fields are missing.
    """
    missing = [column for column in BOOKSQL_NORMALIZED_COLUMNS if column not in row]

    if missing:
        raise BookSQLConfigError(
            f"BookSQL row {row_index} is missing required normalized fields: "
            f"{missing}. Available fields: {sorted(row.keys())}"
        )


def validate_inference_row(
    row: dict[str, Any],
    row_index: int,
    allow_missing_gold_sql: bool,
) -> None:
    """Validate a prepared row for inference, optionally without a gold SQL."""
    if allow_missing_gold_sql:
        required = tuple(column for column in BOOKSQL_NORMALIZED_COLUMNS if column != "gold_sql")
        missing = [column for column in required if column not in row]
        if missing:
            raise BookSQLConfigError(
                f"BookSQL row {row_index} is missing required inference fields: "
                f"{missing}. Available fields: {sorted(row.keys())}"
            )
        return

    validate_normalized_row(row, row_index)


def load_booksql_schema(schema_path: str | Path | None = None) -> str:
    """Load the prompt-ready BookSQL schema text.

    Args:
        schema_path: Optional explicit schema path. Defaults to
            `data/booksql/schema.txt`.

    Returns:
        Non-empty schema string.

    Raises:
        BookSQLConfigError: If the schema file is missing or empty.
    """
    path = Path(schema_path) if schema_path is not None else BOOKSQL_SCHEMA_PATH

    if not path.exists():
        raise BookSQLConfigError(
            f"BookSQL schema file not found: {path}\n"
            "Run `python scripts/setup_booksql.py` first."
        )

    schema = path.read_text(encoding="utf-8").strip()

    if not schema:
        raise BookSQLConfigError(f"BookSQL schema file is empty: {path}")

    return schema


def resolve_booksql_db_path(db_path: str | Path | None = None) -> Path:
    """Resolve the BookSQL SQLite database path.

    Args:
        db_path: Optional explicit database path.

    Returns:
        Existing SQLite database path.

    Raises:
        BookSQLConfigError: If the resolved database file is missing.

    Note:
        `BOOKSQL_DB_PATH` environment variable is respected when `db_path` is
        not provided.
    """
    if db_path is not None:
        path = Path(db_path)
    elif os.getenv("BOOKSQL_DB_PATH"):
        path = Path(os.environ["BOOKSQL_DB_PATH"])
    else:
        path = BOOKSQL_DB_PATH

    if not path.exists():
        raise BookSQLConfigError(
            f"BookSQL SQLite database not found: {path}\n"
            "Run `python scripts/setup_booksql.py` first."
        )

    return path


def get_booksql_db_path(db_path: str | Path | None = None) -> str:
    """Return the resolved BookSQL SQLite database path as a string.

    Args:
        db_path: Optional explicit database path.

    Returns:
        String path suitable for SQLite connection helpers.
    """
    return str(resolve_booksql_db_path(db_path))


def generate_schema_from_sqlite(db_path: str | Path) -> str:
    """Generate a prompt-ready schema string from the BookSQL SQLite database.

    Args:
        db_path: SQLite database path.

    Returns:
        Serialized schema string with table columns, primary keys, and foreign
        keys.

    Raises:
        BookSQLConfigError: If the database is missing or contains no user
            tables.

    Note:
        Used by `scripts/setup_booksql.py` to create `data/booksql/schema.txt`.
    """
    path = Path(db_path)

    if not path.exists():
        raise BookSQLConfigError(f"SQLite database not found: {path}")

    with sqlite3.connect(path) as conn:
        table_rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()

        table_names = [row[0] for row in table_rows]

        if not table_names:
            raise BookSQLConfigError(f"No user tables found in SQLite DB: {path}")

        lines = ["Database schema:"]
        foreign_key_lines: list[str] = []

        for table_name in table_names:
            lines.append("")
            lines.append(f"Table: {table_name}")
            lines.append("Columns:")

            columns = conn.execute(
                f'PRAGMA table_info("{table_name}")'
            ).fetchall()

            for _, column_name, column_type, _not_null, _default, pk in columns:
                suffix = ", primary key" if pk else ""
                column_type = column_type or "UNKNOWN"
                lines.append(f"  - {column_name}: {column_type}{suffix}")

            foreign_keys = conn.execute(
                f'PRAGMA foreign_key_list("{table_name}")'
            ).fetchall()

            for fk in foreign_keys:
                _id, _seq, ref_table, from_col, to_col, *_rest = fk
                foreign_key_lines.append(
                    f"  {table_name}.{from_col} -> {ref_table}.{to_col}"
                )

        if foreign_key_lines:
            lines.append("")
            lines.append("Foreign keys:")
            lines.extend(foreign_key_lines)

    return "\n".join(lines)


def load_booksql_records(
    split: str | None = None,
    data_path: str | Path | None = None,
    schema_path: str | Path | None = None,
    db_path: str | Path | None = None,
    dataset_name: str = BOOKSQL_DATASET_NAME,
    allow_missing_gold_sql: bool = False,
) -> list[dict[str, Any]]:
    """Load prepared BookSQL records and attach the schema for prompting.

    Args:
        split: Optional split filter. `val`, `dev`, and `validation` normalize
            to `validation`.
        data_path: Optional prepared JSONL path. Defaults to
            `data/booksql/booksql_normalized.jsonl`.
        schema_path: Optional schema text path.
        db_path: Compatibility argument; validation is handled separately by
            evaluation utilities.
        dataset_name: Compatibility argument retained for callers that still
            pass a dataset name.

    Returns:
        List of records with `schema` attached for prompting.

    Raises:
        BookSQLConfigError: If the prepared dataset is missing, malformed, or no
            records match the requested split.

    Note:
        `dataset_name` and `db_path` are intentionally unused compatibility
        parameters. `scripts/setup_booksql.py` handles download and preparation.
    """
    _ = dataset_name
    _ = db_path

    path = Path(data_path) if data_path is not None else BOOKSQL_DATA_PATH

    if not path.exists():
        raise BookSQLConfigError(
            f"Prepared BookSQL dataset not found: {path}\n"
            "Run `python scripts/setup_booksql.py` first."
        )

    schema = load_booksql_schema(schema_path)
    rows = read_jsonl(path)

    requested_split = normalize_split(split) if split else None
    records: list[dict[str, Any]] = []

    for row_index, row in enumerate(rows):
        validate_inference_row(row, row_index, allow_missing_gold_sql)

        row_split = normalize_split(row["split"])

        if requested_split and row_split != requested_split:
            continue

        records.append(
            {
                "question_id": row["question_id"],
                "db_id": row["db_id"],
                "question": row["question"],
                "gold_sql": row.get("gold_sql"),
                "schema": schema,
                "level": row["level"],
                "split": row_split,
            }
        )

    if requested_split and not records:
        raise BookSQLConfigError(
            f"No BookSQL records found for split '{requested_split}' in {path}."
        )

    return records
