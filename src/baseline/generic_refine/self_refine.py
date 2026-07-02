"""Generic self-refine baseline for BookSQL candidate SQL."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = Path(__file__).resolve().parents[2]

for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

try:
    from src.baseline.generic_refine.common import (
        GenericRefineRequest,
        build_base_parser,
        run_refine_jsonl,
    )
except ModuleNotFoundError:
    from baseline.generic_refine.common import (
        GenericRefineRequest,
        build_base_parser,
        run_refine_jsonl,
    )


REPAIR_MODE = "generic_self_refine"


def build_generic_self_refine_prompt(request: GenericRefineRequest) -> str:
    schema_text = request.schema_text.strip() or "Not provided."

    return f"""
You are reviewing a candidate SQLite query for a text-to-SQL task.

Inspect the question, database schema, and candidate SQL. If the SQL appears
incorrect or non-executable, revise it. If it already appears correct, leave it
unchanged.

Rules:
- Use only tables and columns shown in the schema.
- Return one SQLite-compatible query.
- Do not use unsupported syntax such as EXTRACT(...), DATE_TRUNC, INTERVAL,
  ILIKE, BOOL_OR, BOOL_AND, or vendor-specific functions.
- Do not mention alternate candidates.
- Use only the fields shown in this prompt.

Return only valid JSON with exactly these fields:
{{
  "changed": true,
  "revised_sql": "<single SQL query>",
  "edit_summary": "<short summary, or null>",
  "confidence": "high | medium | low"
}}

If no change is needed, set "changed" to false and return the original SQL in
"revised_sql". Do not use Markdown fences.

Question ID:
{request.question_id}

Question:
{request.question}

Schema:
{schema_text}

Candidate SQL:
{request.candidate_sql}

Return only the JSON object.
""".strip()


def no_execution_feedback(_row: dict[str, object]) -> None:
    return None


def parse_args():
    return build_base_parser(
        "Run generic self-refine over evaluated BookSQL baseline rows.",
    ).parse_args()


def main() -> None:
    args = parse_args()
    run_refine_jsonl(
        args=args,
        repair_mode=REPAIR_MODE,
        prompt_builder=build_generic_self_refine_prompt,
        execution_feedback_builder=no_execution_feedback,
    )


if __name__ == "__main__":
    main()
