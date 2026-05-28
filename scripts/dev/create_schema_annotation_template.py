import json
import sqlite3
from pathlib import Path

DB_PATH = Path("data/booksql/accounting.sqlite")
OUTPUT_PATH = Path("data/booksql/schema_annotations_template.json")


DEFAULT_ATTRS = {
    "description": "",
    "statement_family": "none",
    "account_type": "none",
    "measure_type": "none",
    "sign_convention": "none",
    "unit": "none",
    "temporal_grain": "none",
    "entity_scope": "none",
    "annotation_note": ""
}


def get_tables(conn):
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()

    return [row[0] for row in rows]


def get_columns(conn, table):
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()

    columns = []
    for row in rows:
        columns.append(
            {
                "name": row[1],
                "sqlite_type": row[2],
            }
        )

    return columns


def main():
    annotations = {}

    with sqlite3.connect(DB_PATH) as conn:
        for table in get_tables(conn):
            annotations[table] = {}

            for column in get_columns(conn, table):
                attrs = dict(DEFAULT_ATTRS)
                attrs["sqlite_type"] = column["sqlite_type"]
                annotations[table][column["name"]] = attrs

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(annotations, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Saved template to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()