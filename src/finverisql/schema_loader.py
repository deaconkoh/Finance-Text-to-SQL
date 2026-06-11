"""Load and resolve BookSQL schema annotations.

This loader is intentionally conservative. It resolves SQL table/column
references to frozen schema annotations, separates top-level schema metadata
from table annotations, and resolves SQL literals using observed database
literals in `value_concepts` only.

Important design rule:
    `value_concepts` is for SQL/database literals.
    `question_aliases` is for natural-language question interpretation and is
    deliberately not used to validate SQL literals.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_SCHEMA_ANNOTATION_PATH = Path("data/booksql/schema_annotations.json")
SCHEMA_METADATA_KEY = "__schema_metadata__"


def normalise_identifier(value: str | None) -> str:
    """Normalise a table or column identifier for lookup."""
    if value is None:
        return ""

    return str(value).strip().lower()


def strip_literal_quotes(value: Any) -> str:
    """Return a stripped SQL literal string without surrounding quotes."""
    if value is None:
        return ""

    return str(value).strip().strip("'").strip('"')


def normalise_value(value: Any) -> str:
    """Normalise a categorical SQL literal for value-concept lookup."""
    return strip_literal_quotes(value).lower()


def _is_internal_metadata_key(key: str) -> bool:
    return str(key).startswith("__")


class SchemaAnnotationStore:
    """Resolve schema annotations for parsed SQL identifiers.

    The input JSON may contain a top-level `__schema_metadata__` object plus
    table annotation objects. Only real table objects are used for table/column
    lookup. The metadata object is exposed through dedicated accessors.
    """

    def __init__(self, annotations: dict[str, Any]):
        if not isinstance(annotations, dict):
            raise ValueError("Expected schema annotations to be a JSON object")

        self.raw_annotations = annotations
        self.metadata: dict[str, Any] = annotations.get(SCHEMA_METADATA_KEY, {}) or {}

        self.annotations: dict[str, dict[str, dict[str, Any]]] = {
            table: column_annotations
            for table, column_annotations in annotations.items()
            if not _is_internal_metadata_key(table) and isinstance(column_annotations, dict)
        }

        self._table_lookup = {
            normalise_identifier(table): table
            for table in self.annotations.keys()
        }

        self._column_lookup: dict[str, dict[str, str]] = {}

        for table, column_annotations in self.annotations.items():
            self._column_lookup[table] = {
                normalise_identifier(column): column
                for column in column_annotations.keys()
            }

    @classmethod
    def from_json(
        cls,
        path: str | Path = DEFAULT_SCHEMA_ANNOTATION_PATH,
    ) -> "SchemaAnnotationStore":
        """Load schema annotations from a JSON file."""
        path = Path(path)

        with path.open("r", encoding="utf-8") as f:
            annotations = json.load(f)

        if not isinstance(annotations, dict):
            raise ValueError(f"Expected schema annotation JSON object at {path}")

        return cls(annotations=annotations)

    # Metadata accessors
    def get_schema_metadata(self) -> dict[str, Any]:
        """Return the top-level schema metadata block."""
        return self.metadata

    def get_table_metadata(self, table: str | None) -> dict[str, Any]:
        """Return table-level metadata such as table grain, if available."""
        real_table = self.get_table_name(table)

        if real_table is None:
            return {}

        table_metadata = self.metadata.get("table_metadata", {}) or {}
        metadata = table_metadata.get(real_table, {})
        return metadata if isinstance(metadata, dict) else {}

    def get_bookkeeping_rules(self) -> dict[str, Any]:
        """Return global bookkeeping rules from schema metadata."""
        rules = self.metadata.get("bookkeeping_rules", {}) or {}
        return rules if isinstance(rules, dict) else {}

    def get_posting_effect(
        self,
        financial_element: str | None,
        posting_side: str | None,
    ) -> dict[str, Any]:
        """Return debit/credit effect for a financial element, if known.

        Example:
            financial_element='asset', posting_side='credit' returns a rule
            indicating that a credit decreases an asset.
        """
        if not financial_element or not posting_side:
            return {}

        rules = self.get_bookkeeping_rules().get("posting_effect_rules", {}) or {}
        element_rule = rules.get(normalise_value(financial_element), {})

        if not isinstance(element_rule, dict):
            return {}

        side = normalise_value(posting_side)
        effect = element_rule.get(f"{side}_effect")

        if not effect:
            return {}

        return {
            "financial_element": normalise_value(financial_element),
            "posting_side": side,
            "posting_effect": effect,
            "normal_balance": element_rule.get("normal_balance"),
        }

    # Table and column resolution
    def get_table_name(self, table: str | None) -> str | None:
        """Resolve a possibly case-varied table name to the canonical name."""
        if table is None:
            return None

        return self._table_lookup.get(normalise_identifier(table))

    def get_column_name(self, table: str, column: str | None) -> str | None:
        """Resolve a possibly case-varied column name within a table."""
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
        """Resolve a qualified `table.column` reference."""
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
        """Resolve an unqualified column reference.

        If the same column name appears in multiple candidate tables, all
        matches are returned and marked ambiguous. The loader never silently
        chooses the first match.
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

        return [
            {
                **match,
                "resolution_status": resolution_status,
                "is_ambiguous": is_ambiguous,
                "candidate_count": len(matches),
            }
            for match in matches
        ]

    def annotate_column_reference(
        self,
        column: str | None,
        table: str | None = None,
        candidate_tables: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Resolve a SQL column reference to annotation records."""
        if column is None:
            return []

        if table:
            annotation = self.get_column_annotation(table=table, column=column)
            return [annotation] if annotation is not None else []

        return self.find_column_annotations(
            column=column,
            candidate_tables=candidate_tables,
        )

    # Value resolution
    def resolve_value_concept(
        self,
        annotation: dict[str, Any],
        raw_value: Any,
    ) -> str | None:
        """Compatibility wrapper returning only the resolved concept string."""
        result = self.resolve_value_semantics(annotation=annotation, raw_value=raw_value)
        concept = result.get("concept")
        return str(concept) if concept else None

    def resolve_value_semantics(
        self,
        annotation: dict[str, Any],
        raw_value: Any,
    ) -> dict[str, Any]:
        """Resolve a SQL literal using observed value mappings.

        Returns a rich result that distinguishes exact matches, normalised
        matches, missing markers, and unobserved literals.

        This method deliberately ignores `question_aliases` because those are
        natural-language aids, not valid SQL/database literal mappings.
        """
        clean_value = strip_literal_quotes(raw_value)
        normalised_value = normalise_value(raw_value)

        base_result: dict[str, Any] = {
            "raw_value": raw_value,
            "clean_value": clean_value,
            "normalised_value": normalised_value,
            "concept": None,
            "value_status": "no_value_map",
            "exact_literal_match": None,
            "concept_metadata": {},
            "warning": None,
            "question_aliases_used": False,
        }

        value_concepts = annotation.get("value_concepts") or {}
        concept_metadata_map = annotation.get("concept_metadata") or {}

        if not isinstance(value_concepts, dict) or not value_concepts:
            missing_match = self._resolve_missing_value(annotation, raw_value)
            if missing_match:
                return {**base_result, **missing_match}
            return base_result

        # 1. Exact literal match. This is the strongest value-level evidence.
        if clean_value in value_concepts:
            concept = value_concepts[clean_value]
            return self._build_value_match_result(
                base_result=base_result,
                concept=concept,
                value_status="exact_match",
                exact_literal_match=True,
                concept_metadata_map=concept_metadata_map,
            )

        # 2. Normalised match. This is semantically understandable, but weaker
        # because the SQL literal may not match the database's exact casing.
        normalised_map = {
            normalise_value(key): value
            for key, value in value_concepts.items()
        }

        if normalised_value in normalised_map:
            concept = normalised_map[normalised_value]
            result = self._build_value_match_result(
                base_result=base_result,
                concept=concept,
                value_status="normalised_match",
                exact_literal_match=False,
                concept_metadata_map=concept_metadata_map,
            )
            literal_policy = annotation.get("literal_match_policy")
            if literal_policy == "case_sensitive_exact_match_preferred":
                result["warning"] = (
                    f"Literal {clean_value!r} maps after normalisation, but it is "
                    "not an exact observed literal."
                )
            return result

        # 3. Missing marker. For columns like AR_paid/AP_paid, missing values
        # should not be promoted to unpaid unless a rule explicitly says so.
        missing_match = self._resolve_missing_value(annotation, raw_value)
        if missing_match:
            return {**base_result, **missing_match}

        # 4. Unobserved literal. The annotation has a value map, but this SQL
        # literal is not in it.
        if annotation.get("invalid_value_policy") == "unobserved_literal_warning":
            return {
                **base_result,
                "value_status": "unobserved_literal",
                "exact_literal_match": False,
                "warning": (
                    f"Literal {clean_value!r} is not an observed/mapped value "
                    f"for {annotation.get('table')}.{annotation.get('column')}."
                ),
            }

        return {
            **base_result,
            "value_status": "unmapped_literal",
            "exact_literal_match": False,
        }

    def resolve_values_semantics(
        self,
        annotation: dict[str, Any],
        raw_values: list[Any],
    ) -> list[dict[str, Any]]:
        """Resolve a list of SQL literals against one annotation."""
        return [
            self.resolve_value_semantics(annotation=annotation, raw_value=value)
            for value in raw_values
        ]

    def _build_value_match_result(
        self,
        base_result: dict[str, Any],
        concept: Any,
        value_status: str,
        exact_literal_match: bool,
        concept_metadata_map: dict[str, Any],
    ) -> dict[str, Any]:
        concept_key = str(concept)
        concept_metadata = concept_metadata_map.get(concept_key, {})

        return {
            **base_result,
            "concept": concept_key,
            "value_status": value_status,
            "exact_literal_match": exact_literal_match,
            "concept_metadata": concept_metadata if isinstance(concept_metadata, dict) else {},
        }

    def _resolve_missing_value(
        self,
        annotation: dict[str, Any],
        raw_value: Any,
    ) -> dict[str, Any] | None:
        missing_policy = annotation.get("missing_value_policy") or {}

        if not isinstance(missing_policy, dict):
            return None

        clean_value = strip_literal_quotes(raw_value)
        normalised_value = normalise_value(raw_value)
        missing_markers = missing_policy.get("missing_markers", []) or []

        # Treat None as a possible null marker even if the SQL parser gives None.
        if raw_value is None:
            marker_match = "null"
        else:
            normalised_markers = {normalise_value(marker) for marker in missing_markers}
            marker_match = normalised_value if normalised_value in normalised_markers else None

        if marker_match is None:
            return None

        warning = None
        if missing_policy.get("do_not_map_directly_to"):
            warning = (
                f"Missing marker {clean_value!r} means "
                f"{missing_policy.get('meaning', 'missing')}; do not map directly to "
                f"{missing_policy.get('do_not_map_directly_to')!r}."
            )
        elif missing_policy.get("do_not_use_for_numeric_computation"):
            warning = (
                f"Missing marker {clean_value!r} should not be used for numeric computation."
            )

        return {
            "concept": missing_policy.get("meaning", "missing"),
            "value_status": "missing_marker",
            "exact_literal_match": True,
            "concept_metadata": {},
            "warning": warning,
            "missing_value_policy": missing_policy,
        }
