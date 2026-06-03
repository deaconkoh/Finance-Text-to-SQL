from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_SCHEMA_ANNOTATION_PATH = Path("data/booksql/schema_annotations.json")


def normalise_identifier(value: str | None) -> str:
    if value is None:
        return ""

    return str(value).strip().lower()


def normalise_value(value: Any) -> str:
    if value is None:
        return ""

    return str(value).strip().strip("'").strip('"').lower()


class SchemaAnnotationStore:
    """
    Loads and resolves schema annotations.

    This class should not interpret SQL semantics directly.
    It only resolves table/column references to their annotation records.

    Expected JSON shape:

    {
      "table_name": {
        "column_name": {
          "semantic_role": "...",
          "measure_type": "...",
          "sign_convention": "...",
          "entity_scope": "...",
          "value_concepts": {...}
        }
      }
    }
    """

    def __init__(self, annotations: dict[str, dict[str, dict[str, Any]]]):
        self.annotations = annotations

        self._table_lookup = {
            normalise_identifier(table): table
            for table in annotations.keys()
        }

        self._column_lookup: dict[str, dict[str, str]] = {}

        for table, column_annotations in annotations.items():
            self._column_lookup[table] = {
                normalise_identifier(column): column
                for column in column_annotations.keys()
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

    def get_table_name(self, table: str | None) -> str | None:
        if table is None:
            return None

        return self._table_lookup.get(normalise_identifier(table))

    def get_column_name(self, table: str, column: str | None) -> str | None:
        if column is None:
            return None

        real_table = self.get_table_name(table)

        if real_table is None:
            return None

        return self._column_lookup.get(real_table, {}).get(normalise_identifier(column))

    def get_column_annotation(
        self,
        table: str | None,
        column: str | None,
    ) -> dict[str, Any] | None:
        """
        Resolve a qualified table.column reference.

        Returns the full annotation dictionary, enriched with table/column metadata.
        """
        if table is None or column is None:
            return None

        real_table = self.get_table_name(table)

        if real_table is None:
            return None

        real_column = self.get_column_name(real_table, column)

        if real_column is None:
            return None

        raw_annotation = self.annotations[real_table][real_column]

        return {
            "table": real_table,
            "column": real_column,
            "resolution_status": "qualified",
            "is_ambiguous": False,
            "candidate_count": 1,
            **raw_annotation,
        }

    def find_column_annotations(
        self,
        column: str | None,
        candidate_tables: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Resolve an unqualified column reference.

        If candidate_tables is provided, search only tables used in the SQL.
        Otherwise, search the entire annotated schema.

        Important:
        If multiple matches are found, this returns all of them and marks them
        as ambiguous. It does not silently pick the first match.
        """
        if column is None:
            return []

        matches: list[dict[str, Any]] = []

        tables_to_search = candidate_tables or list(self.annotations.keys())

        for table in tables_to_search:
            real_table = self.get_table_name(table)

            if real_table is None:
                continue

            real_column = self.get_column_name(real_table, column)

            if real_column is None:
                continue

            raw_annotation = self.annotations[real_table][real_column]

            matches.append(
                {
                    "table": real_table,
                    "column": real_column,
                    **raw_annotation,
                }
            )

        is_ambiguous = len(matches) > 1
        resolution_status = "ambiguous_unqualified" if is_ambiguous else "unique_unqualified"

        enriched_matches = []

        for match in matches:
            enriched_matches.append(
                {
                    **match,
                    "resolution_status": resolution_status,
                    "is_ambiguous": is_ambiguous,
                    "candidate_count": len(matches),
                }
            )

        return enriched_matches

    def annotate_column_reference(
        self,
        column: str | None,
        table: str | None = None,
        candidate_tables: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Resolve a SQL column reference.

        - Qualified column: resolve table.column directly.
        - Unqualified column: search candidate tables and preserve ambiguity.
        """
        if column is None:
            return []

        if table:
            annotation = self.get_column_annotation(table=table, column=column)
            return [annotation] if annotation is not None else []

        return self.find_column_annotations(
            column=column,
            candidate_tables=candidate_tables,
        )

    def resolve_value_concept(
        self,
        annotation: dict[str, Any],
        raw_value: Any,
    ) -> str | None:
        """
        Resolve categorical values using the annotation's value_concepts map.

        Example:
        annotation["value_concepts"] = {
            "expense": "expense",
            "other expense": "expense",
            "income": "revenue"
        }

        raw_value = "Expense"
        → "expense"
        """
        value_concepts = annotation.get("value_concepts") or {}

        if not isinstance(value_concepts, dict):
            return None

        normalised_value = normalise_value(raw_value)

        normalised_map = {
            normalise_value(key): value
            for key, value in value_concepts.items()
        }

        return normalised_map.get(normalised_value)