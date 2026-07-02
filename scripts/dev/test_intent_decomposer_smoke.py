#!/usr/bin/env python3
"""Smoke test for Stage 1 intent decomposition without an assembler LLM call."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


from src.finverisql.intent_decomposer import IntentDecomposer


class FakeSchemaStore:
    annotations: dict[str, Any] = {
        "sales": {
            "customer_name": {
                "semantic_role": "entity_identifier",
                "entity_scope": "customer",
                "domain_object": "customer",
                "question_aliases": {"customer": "customer"},
            },
            "sale_amount": {
                "semantic_role": "financial_measure",
                "measure_type": "flow",
                "unit": "money",
                "financial_element": "revenue",
            },
            "sale_date": {
                "semantic_role": "transaction_date",
                "measure_type": "date",
                "time_behavior": "event_date",
            },
        }
    }

    def get_schema_metadata(self) -> dict[str, Any]:
        return {
            "table_metadata": {
                "sales": {
                    "table_grain": "transaction line",
                    "transaction_group_key": "invoice_id",
                    "count_star_warning": "COUNT(*) counts lines.",
                }
            }
        }

    def build_intent_metadata_guide(self) -> dict[str, Any]:
        return {
            "business_glossary": [
                {
                    "concept": "revenue",
                    "description": "Money earned from sales.",
                }
            ],
            "question_aliases": {
                "sales.customer_name": {
                    "customer": "customer",
                }
            },
        }


def main() -> None:
    prompts: list[str] = []
    slot_calls: list[str] = []

    def fake_llm_call(prompt: str) -> str:
        prompts.append(prompt)

        if prompt.startswith("Assemble"):
            raise AssertionError("Assembler prompt should not be called.")

        if prompt.startswith("Extract entity mentions"):
            slot_calls.append("entity")
            return json.dumps(
                {
                    "entity_mentions": [
                        {
                            "text": "Acme",
                            "mention_type": "customer",
                            "explicit_role": "customer",
                            "mapped_concept": "customer",
                            "evidence": "Acme",
                            "confidence": "high",
                        }
                    ],
                    "ambiguities": [],
                    "confidence": "high",
                }
            )

        if prompt.startswith("Extract the requested business"):
            slot_calls.append("measure")
            return json.dumps(
                {
                    "answer_type": "amount",
                    "target_concept": "revenue",
                    "measure_kind": "monetary",
                    "possible_posting_sides": ["credit"],
                    "possible_event_types": ["sale"],
                    "preferred_columns_or_vectors": ["sale_amount"],
                    "non_equivalent_measures": [],
                    "ambiguities": [],
                    "confidence": "high",
                }
            )

        if prompt.startswith("Extract temporal conditions"):
            slot_calls.append("time")
            return json.dumps(
                {
                    "expression": "YTD",
                    "granularity": "to_date",
                    "range_start": None,
                    "range_end": None,
                    "relative_time": "year-to-date",
                    "date_type": "transaction_date",
                    "requires_group_by_period": False,
                    "requires_latest": False,
                    "requires_today": True,
                    "ambiguities": [],
                    "confidence": "high",
                }
            )

        if prompt.startswith("Extract structural operation"):
            slot_calls.append("operation")
            return json.dumps(
                {
                    "aggregation": "sum",
                    "group_by": [],
                    "order_by": [],
                    "limit": None,
                    "comparison": {
                        "required": False,
                        "type": "none",
                        "left": None,
                        "right": None,
                        "evidence": "",
                    },
                    "ambiguities": [],
                    "confidence": "high",
                }
            )

        raise AssertionError(f"Unexpected prompt title: {prompt.splitlines()[0]}")

    decomposer = IntentDecomposer(
        llm_call=fake_llm_call,
        intent_mode="metadata_guided",
        schema_store=FakeSchemaStore(),
    )

    result = decomposer.decompose("What is Acme revenue YTD?")

    assert slot_calls == ["entity", "measure", "time", "operation"], slot_calls
    assert len(prompts) == 4, len(prompts)
    assert all("Assemble" not in prompt for prompt in prompts)
    assert all("In metadata-guided mode" not in prompt for prompt in prompts)
    assert all("Metadata mode" not in prompt for prompt in prompts)
    assert all("preferred_columns_or_vectors" not in prompt for prompt in prompts)

    assert set(result["slots"]) == {"entity", "measure", "time", "operation"}
    assert result["slots"]["entity"]["entity_mentions"]
    assert result["slots"]["measure"]["target_concept"] == "revenue"
    assert "preferred_columns_or_vectors" not in result["slots"]["measure"]
    assert result["overall_confidence"] == "high"
    assert result.get("parse_errors", []) == []

    none_calls: list[str] = []

    def unexpected_llm_call(prompt: str) -> str:
        none_calls.append(prompt)
        raise AssertionError("intent_mode='none' should not call the LLM.")

    none_decomposer = IntentDecomposer(
        llm_call=unexpected_llm_call,
        intent_mode="none",
    )
    none_result = none_decomposer.decompose("What is Acme revenue YTD?")

    assert none_calls == [], none_calls
    assert none_result == {
        "_stage": "intent_decomposition",
        "_decomposition_method": "none",
        "question": "What is Acme revenue YTD?",
        "raw_question": "What is Acme revenue YTD?",
        "slots": {},
        "ambiguities": [],
        "slot_confidences": {},
        "parse_errors": [],
        "overall_confidence": "medium",
    }

    print("intent decomposer smoke test passed")


if __name__ == "__main__":
    main()
