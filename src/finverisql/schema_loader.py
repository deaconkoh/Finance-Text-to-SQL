from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_SCHEMA_ANNOTATION_PATH = Path("data/booksql/schema_annotations.json")


class SchemaAnnotationStore:
    def __init__(self, annotations: dict[str, dict[str, dict[str, Any]]]):
        self.annotations = annotations
        self._table_lookup = {
            table.lower(): table
            for table in annotations.keys()
        }

    @classmethod
    def from_json(
        cls,
        path: str | Path = DEFAULT_SCHEMA_ANNOTATION_PATH,
    ) -> "SchemaAnnotationStore":
        path = Path(path)

        with path.open("r", encoding="utf-8") as f:
            annotations = json.load(f)

        if not isinstance(annotations, dict):
            raise ValueError(f"Expected schema annotation JSON object at {path}")

        return cls(annotations=annotations)

    def get_table_name(self, table: str) -> str | None:
        return self._table_lookup.get(table.lower())

    def get_column_annotation(
        self,
        table: str,
        column: str,
    ) -> dict[str, Any] | None:
        real_table = self.get_table_name(table)

        if real_table is None:
            return None

        table_annotations = self.annotations.get(real_table, {})

        for real_column, annotation in table_annotations.items():
            if real_column.lower() == column.lower():
                return {
                    "table": real_table,
                    "column": real_column,
                    **annotation,
                }

        return None

    def find_column_annotations(
        self,
        column: str,
        candidate_tables: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []

        tables_to_search = candidate_tables or list(self.annotations.keys())

        for table in tables_to_search:
            real_table = self.get_table_name(table)

            if real_table is None:
                continue

            annotation = self.get_column_annotation(real_table, column)

            if annotation is not None:
                matches.append(annotation)

        return matches

    def annotate_column_reference(
        self,
        column: str,
        table: str | None = None,
        candidate_tables: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if table:
            annotation = self.get_column_annotation(table, column)
            return [annotation] if annotation is not None else []

        return self.find_column_annotations(
            column=column,
            candidate_tables=candidate_tables,
        )