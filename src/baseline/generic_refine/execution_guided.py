"""Generic execution-guided refinement baseline for BookSQL candidate SQL."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


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


REPAIR_MODE = "generic_execution_guided_refine"


def build_execution_feedback(row: dict[str, Any]) -> dict[str, Any]:
    status = row.get("generated_execution_status")
    generated_error = row.get("generated_error")
    error_message = row.get("error_message")
    result = row.get("generated_result")
    ambiguity_flags = row.get("ambiguity_flags")

    feedback: dict[str, Any] = {
        "generated_execution_status": status or "unknown",
        "generated_error": generated_error,
        "error_message": error_message,
        "generated_result": result,
        "ambiguity_flags": ambiguity_flags or [],
    }

    if status == "success" and not generated_error:
        feedback["summary"] = (
            "The candidate SQL executed successfully. Use the result preview and "
            "question to decide whether a generic revision is still warranted."
        )
    elif generated_error or error_message:
        feedback["summary"] = (
            "The candidate SQL failed or produced execution feedback that may "
            "identify syntax, column, join, or runtime issues."
        )
    else:
        feedback["summary"] = "No generated SQL execution feedback was available."

    return feedback


def build_generic_execution_guided_refine_prompt(request: GenericRefineRequest) -> str:
    schema_text = request.schema_text.strip() or "Not provided."
    feedback_json = json.dumps(
        request.execution_feedback or {},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    return f"""
You are repairing a candidate SQLite query using execution feedback.

Inspect the question, database schema, candidate SQL, and generated-SQL
execution feedback. If the feedback reveals a syntax error, invalid column,
invalid join, runtime problem, or other likely SQL issue, revise the SQL. If no
change is warranted, leave it unchanged.

Rules:
- Use only tables and columns shown in the schema.
- Return one SQLite-compatible query.
- Do not use unsupported syntax such as EXTRACT(...), DATE_TRUNC, INTERVAL,
  ILIKE, BOOL_OR, BOOL_AND, or vendor-specific functions.
- Use only the generated-SQL execution feedback provided here.
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

Generated SQL execution feedback:
{feedback_json}

Return only the JSON object.
""".strip()


def parse_args():
    return build_base_parser(
        "Run generic execution-guided refine over evaluated BookSQL baseline rows.",
    ).parse_args()


def main() -> None:
    args = parse_args()
    run_refine_jsonl(
        args=args,
        repair_mode=REPAIR_MODE,
        prompt_builder=build_generic_execution_guided_refine_prompt,
        execution_feedback_builder=build_execution_feedback,
    )


if __name__ == "__main__":
    main()
