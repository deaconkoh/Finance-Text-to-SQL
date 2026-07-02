"""Sequential intent decomposition for FinVeriSQL.

Stage 1 decomposes a natural-language financial question into a SQL-independent
intent representation.

Supported modes:
    - none:
        Performs no decomposition and returns the raw natural-language question.

    - nl_only:
        Uses only the natural-language question.

    - metadata_guided:
        Uses the natural-language question plus a compact metadata guide derived
        from schema annotations. This mode may use question_aliases because they
        are natural-language interpretation aids, but it must not inspect
        candidate SQL, SQL ASTs, compact semantic profiles, or gold SQL.

Expected usage:
    decomposer = IntentDecomposer(
        llm_call=your_model_call_function,
        intent_mode="metadata_guided",
        schema_store=schema_store,
    )

    intent = decomposer.decompose(question)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal


IntentMode = Literal["none", "nl_only", "metadata_guided"]
SlotName = Literal["entity", "measure", "time", "operation"]
JsonDict = dict[str, Any]
LLMCall = Callable[[str], str]


# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------


def _strip_code_fence(text: str) -> str:
    """Remove a surrounding markdown code fence if the model returned one."""
    text = text.strip()

    fence_match = re.match(
        r"^```(?:json)?\s*(.*?)\s*```$",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if fence_match:
        return fence_match.group(1).strip()

    return text


def _extract_first_json_object(text: str) -> JsonDict:
    """Extract and parse the first JSON object from an LLM response.

    This is intentionally defensive because local models sometimes return
    short preambles or markdown fences even when instructed to return JSON only.
    """
    cleaned = _strip_code_fence(text)

    # Defensive cleaning for models that over-escape quotes in values.
    # e.g., "key": \"value\" -> "key": "value"
    # This is a common failure mode for some models.
    cleaned = re.sub(r'(:\s*)\\"([^"\\]*)\\"', r'\1"\2"', cleaned)


    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("Parsed JSON is not an object")
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model response")

    depth = 0
    in_string = False
    escape = False

    for index in range(start, len(cleaned)):
        char = cleaned[index]

        if escape:
            escape = False
            continue

        if char == "\\":
            escape = True
            continue

        if char == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1

            if depth == 0:
                candidate = cleaned[start : index + 1]
                parsed = json.loads(candidate)
                if not isinstance(parsed, dict):
                    raise ValueError("Parsed JSON is not an object")
                return parsed

    raise ValueError("Could not find a complete JSON object")


def _safe_json_parse(raw_response: str, slot_name: str) -> JsonDict:
    """Parse a slot response. Return a low-confidence object on failure."""
    try:
        return _extract_first_json_object(raw_response)
    except Exception as exc:
        return {
            "_parse_error": str(exc),
            "_raw_response": raw_response,
            "ambiguities": [
                f"{slot_name} slot response could not be parsed as JSON."
            ],
            "confidence": "low",
        }


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value

    if value is None:
        return []

    return [value]


def _normalise_confidence(value: Any) -> str:
    if value in {"high", "medium", "low"}:
        return str(value)

    return "medium"


def normalise_entity_slot(slot: JsonDict) -> JsonDict:
    normalised: JsonDict = {
        "reasoning": "",
        "entity_mentions": [],
        "ambiguities": [],
        "confidence": "medium",
    }
    normalised.update(slot)
    normalised["entity_mentions"] = _as_list(normalised.get("entity_mentions"))
    normalised["ambiguities"] = _as_list(normalised.get("ambiguities"))
    normalised["confidence"] = _normalise_confidence(normalised.get("confidence"))
    return normalised


def normalise_measure_slot(slot: JsonDict) -> JsonDict:
    normalised: JsonDict = {
        "reasoning": "",
        "answer_type": "unknown",
        "target_concept": "",
        "measure_kind": "unknown",
        "possible_posting_sides": ["unknown"],
        "possible_event_types": ["unknown"],
        "non_equivalent_measures": [],
        "ambiguities": [],
        "confidence": "medium",
    }
    normalised.update(slot)
    normalised.pop("preferred_columns_or_vectors", None)
    normalised["possible_posting_sides"] = _as_list(
        normalised.get("possible_posting_sides")
    )
    normalised["possible_event_types"] = _as_list(
        normalised.get("possible_event_types")
    )
    normalised["non_equivalent_measures"] = _as_list(
        normalised.get("non_equivalent_measures")
    )
    normalised["ambiguities"] = _as_list(normalised.get("ambiguities"))
    normalised["confidence"] = _normalise_confidence(normalised.get("confidence"))
    return normalised


def normalise_time_slot(slot: JsonDict) -> JsonDict:
    normalised: JsonDict = {
        "reasoning": "",
        "expression": None,
        "granularity": "none",
        "range_start": None,
        "range_end": None,
        "relative_time": None,
        "date_type": "none",
        "requires_group_by_period": False,
        "requires_latest": False,
        "requires_today": False,
        "ambiguities": [],
        "confidence": "medium",
    }
    normalised.update(slot)
    normalised["requires_group_by_period"] = bool(
        normalised.get("requires_group_by_period")
    )
    normalised["requires_latest"] = bool(normalised.get("requires_latest"))
    normalised["requires_today"] = bool(normalised.get("requires_today"))
    normalised["ambiguities"] = _as_list(normalised.get("ambiguities"))
    normalised["confidence"] = _normalise_confidence(normalised.get("confidence"))
    return normalised


def normalise_operation_slot(slot: JsonDict) -> JsonDict:
    normalised: JsonDict = {
        "reasoning": "",
        "aggregation": "none",
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
        "confidence": "medium",
    }
    normalised.update(slot)
    normalised["group_by"] = _as_list(normalised.get("group_by"))
    normalised["order_by"] = _as_list(normalised.get("order_by"))

    comparison = normalised.get("comparison")
    if not isinstance(comparison, dict):
        comparison = {}

    default_comparison = {
        "required": False,
        "type": "none",
        "left": None,
        "right": None,
        "evidence": "",
    }
    default_comparison.update(comparison)
    default_comparison["required"] = bool(default_comparison.get("required"))
    normalised["comparison"] = default_comparison
    normalised["ambiguities"] = _as_list(normalised.get("ambiguities"))
    normalised["confidence"] = _normalise_confidence(normalised.get("confidence"))
    return normalised


def _normalise_slot(slot_name: SlotName, slot: JsonDict) -> JsonDict:
    if slot_name == "entity":
        return normalise_entity_slot(slot)

    if slot_name == "measure":
        return normalise_measure_slot(slot)

    if slot_name == "time":
        return normalise_time_slot(slot)

    if slot_name == "operation":
        return normalise_operation_slot(slot)

    raise ValueError(f"Unsupported slot_name={slot_name!r}")


def _prune_empty(value: Any) -> Any:
    """Recursively remove empty, null, and 'unknown' fields to reduce verbosity."""
    if isinstance(value, dict):
        cleaned = {k: _prune_empty(v) for k, v in value.items()}
        return {
            k: v for k, v in cleaned.items()
            # Keep booleans (False is valid), but drop explicit empty states
            if v is not None and v != "" and v != [] and v != {} and v != "none" and v != "unknown" and v != ["unknown"]
        }
    if isinstance(value, list):
        cleaned_items = [_prune_empty(item) for item in value]
        return [
            item for item in cleaned_items 
            if item is not None and item != "" and item != [] and item != {} and item != "none" and item != "unknown"
        ]
    return value


def _collect_ambiguities(slots: dict[str, JsonDict]) -> list[Any]:
    ambiguities: list[Any] = []

    for slot in slots.values():
        for amb in _as_list(slot.get("ambiguities")):
            ambiguities.append(amb)

    return ambiguities


def _collect_parse_errors(slots: dict[str, JsonDict]) -> list[Any]:
    parse_errors: list[Any] = []

    for slot in slots.values():
        parse_error = slot.get("_parse_error")

        if parse_error:
            parse_errors.append(parse_error)

    return parse_errors


def _overall_confidence(
    *,
    slot_confidences: dict[str, str],
    parse_errors: list[str],
) -> str:
    if parse_errors or any(value == "low" for value in slot_confidences.values()):
        return "low"

    if any(value == "medium" for value in slot_confidences.values()):
        return "medium"

    return "high"


# ---------------------------------------------------------------------------
# Metadata guide construction
# ---------------------------------------------------------------------------


def _format_aliases(annotation: JsonDict) -> str:
    aliases = annotation.get("question_aliases") or {}

    if not isinstance(aliases, dict) or not aliases:
        return ""

    pairs = [f"{alias} -> {concept}" for alias, concept in aliases.items()]
    return "; ".join(pairs)


def _format_value_concepts(annotation: JsonDict) -> str:
    concepts = annotation.get("value_concepts") or {}

    if not isinstance(concepts, dict) or not concepts:
        return ""

    pairs = [f"{value} -> {concept}" for value, concept in concepts.items()]
    return "; ".join(pairs)


def build_slot_metadata_guide(schema_store: Any | None, slot_name: SlotName) -> str:
    """Build a compact metadata guide for one decomposition slot.

    This injects ONLY the abstract business glossary and interpretation rules
    from the schema annotations. It intentionally hides physical table and 
    column names to prevent architectural anchoring / data leakage.
    """
    if schema_store is None:
        return ""

    guide_data = schema_store.build_intent_metadata_guide()

    lines: list[str] = []
    lines.append("BUSINESS GLOSSARY & INTERPRETATION RULES")
    lines.append("Use these domain rules to interpret the financial intent of the question.")
    lines.append("")

    glossary = guide_data.get("business_glossary", [])
    if glossary:
        lines.append("Business Glossary:")
        for term in glossary:
            lines.append(f"- {term.get('concept')}: {term.get('description')}")
        lines.append("")

    aliases = guide_data.get("question_aliases", {})
    if aliases:
        lines.append("Domain Vocabulary Aliases:")
        for col_ref, alias_map in aliases.items():
            for alias, concept in alias_map.items():
                lines.append(f"- '{alias}' usually refers to '{concept}'")
        lines.append("")

    return "\n".join(lines).strip()


def _metadata_block(metadata_guide: str) -> str:
    if not metadata_guide.strip():
        return ""

    return (
        "Financial interpretation guide:\n"
        "Use this guide only to interpret natural-language business meaning.\n"
        "Do not generate SQL.\n"
        "Do not assume or inspect candidate SQL.\n"
        "Do not validate SQL literals here.\n\n"
        f"{metadata_guide}\n"
    )


# ---------------------------------------------------------------------------
# Slot prompt builders
# ---------------------------------------------------------------------------


def build_entity_slot_prompt(
    question: str,
    metadata_guide: str = "",
) -> str:
    return f"""Extract entity mentions from the question.

{_metadata_block(metadata_guide)}

Focus only on nouns or named mentions that identify who/what the question is about.

Rules:
- Copy entity text exactly as written in the question.
- Do not infer a role unless the wording explicitly states it.
- If a named person or organisation is mentioned without a clear role, classify it as "named_party".
- Focus on named parties, customers, vendors, suppliers, employees, accounts, account types, products/services, transaction types, and payment status.
- Classify customer, vendor, supplier, employee, account, product, service, transaction type, or payment status only when those words or close synonyms are explicit.
- Do not extract financial measures, dates, amounts, or operations here.
- If no entity is mentioned, return an empty list.
- Evidence must be a short quote from the question.

Return valid JSON only. Ensure any double quotes inside strings are escaped (e.g., \\") or use single quotes instead.

JSON shape:
{{
  "entity_mentions": [
    {{
      "text": "",
      "mention_type": "named_party|product_service|account|account_type|transaction_type|payment_status|employee|customer|vendor|unknown",
      "explicit_role": "customer|vendor|product_service|account|account_type|transaction_type|payment_status|employee|unknown",
      "mapped_concept": "",
      "evidence": "",
      "confidence": "high|medium|low"
    }}
  ],
  "ambiguities": [],
  "confidence": "high|medium|low"
}}

Question:
{question}

JSON:
""".strip()


def build_measure_slot_prompt(
    question: str,
    metadata_guide: str = "",
) -> str:
    return f"""Extract the requested business or financial concept from the question.

{_metadata_block(metadata_guide)}

Focus only on what value, concept, or answer the user is asking for.

Rules:
- Identify the natural-language business concept, not database columns.
- Do not decide grouping, ranking, ordering, or limits here.
- Use "amount" for questions asking how much money.
- Use "balance" for outstanding, open, owed, receivable, payable, or remaining amounts.
- Use "count" for how many, number of, count of, or total number of.
- Use "quantity" for units/items sold or bought.
- Use "rate" for unit price, billing rate, percentage rate, or per-unit value.
- Use "date" for questions asking when something happened or is due.
- Use "boolean" for yes/no or existence questions.
- Consult the Business Glossary to accurately map ambiguous phrases (e.g., distinguishing quantity from transaction count).
- For debit/credit, only set possible_posting_sides when the question explicitly mentions debit, credit, posting side, increase/decrease, or accounting direction. Otherwise use ["unknown"].
- For event types, include only events clearly implied by the wording, such as invoice, bill, deposit, sale, purchase, payment received, or payment made.
- If the concept is ambiguous, preserve the ambiguity instead of choosing one interpretation.

Return valid JSON only. Ensure any double quotes inside strings are escaped (e.g., \\") or use single quotes instead.

JSON shape:
{{
  "reasoning": "Step-by-step logic. If a Business Glossary is provided, cite the relevant concept to justify your measure_kind.",
  "answer_type": "date|amount|count|quantity|boolean|list|ratio|unknown",
  "target_concept": "",
  "measure_kind": "monetary|quantity|rate|balance|date|identifier|existence|unknown",
  "possible_posting_sides": ["debit|credit|none|unknown"],
  "possible_event_types": ["sale|purchase|payment_received|payment_made|deposit|invoice|bill|unknown"],
  "non_equivalent_measures": [],
  "ambiguities": [],
  "confidence": "high|medium|low"
}}

Question:
{question}

JSON:
""".strip()


def build_time_slot_prompt(
    question: str,
    metadata_guide: str = "",
) -> str:
    return f"""Extract temporal conditions from the question.

{_metadata_block(metadata_guide)}

Focus only on dates, periods, relative time expressions, and time-window requirements.

Rules:
- Copy the time expression as written when possible.
- Detect exact dates, months, quarters, years, fiscal periods, date ranges, before/after conditions, and relative periods.
- Interpret XTD expressions as "to-date" windows:
  - YTD = year-to-date
  - QTD = quarter-to-date
  - MTD = month-to-date
  - WTD = week-to-date
  - DTD = day-to-date
- Mark requires_latest as true for phrases like latest, most recent, current, last available, newest.
- Mark requires_today as true only when the question depends on today's/current date, such as "as of today", "currently", or "to date".
- Mark requires_group_by_period as true only when the question asks for results by day, month, quarter, year, or over time.
- Do not extract non-temporal ordering such as highest, lowest, top, most, least.
- Use any financial interpretation guide to distinguish transaction/economic event date from system-created date and due date.
- If no time condition is mentioned, set granularity to "none" and expression to null.
- If the time expression is vague, keep the original phrase and lower the confidence.

Return valid JSON only. Ensure any double quotes inside strings are escaped (e.g., \\") or use single quotes instead.

JSON shape:
{{
  "reasoning": "Step-by-step explanation of the temporal bounds.",
  "expression": null,
  "granularity": "day|week|month|quarter|year|range|to_date|none|unknown",
  "range_start": null,
  "range_end": null,
  "relative_time": null,
  "date_type": "transaction_date|due_date|created_date|hire_date|unspecified|none",
  "requires_group_by_period": false,
  "requires_latest": false,
  "requires_today": false,
  "ambiguities": [],
  "confidence": "high|medium|low"
}}

Question:
{question}

JSON:
""".strip()


def build_operation_slot_prompt(
    question: str,
    metadata_guide: str = "",
) -> str:
    return f"""Extract structural operation requirements from the question.

{_metadata_block(metadata_guide)}

Focus only on how the answer should be computed, grouped, compared, ranked, or limited.

Rules:
- Extract aggregation words such as total, sum, average, count, minimum, maximum.
- STRICT RULE: Always check the Business Glossary for the required aggregation. If the glossary states a concept requires "summing", you MUST output "sum" (never "count"), even if the question asks "how many".
- Extract grouping requirements such as by customer, by vendor, by account, by product, by month, or by year.
- Extract ranking requirements such as highest, lowest, top, bottom, most, least, largest, smallest.
- Extract limits such as top 1, top 5, first, last, highest single item.
- Extract comparison requirements such as compare, difference, change, growth, percentage change, ratio, more than, less than, before vs after.
- Do not extract entity names, dates, or financial concepts here unless they are part of grouping/order/comparison.
- If "most", "highest", or "largest" appears, this usually implies descending order and a limit if the question asks for a single best/highest item.
- If "least", "lowest", or "smallest" appears, this usually implies ascending order and a limit if the question asks for a single lowest item.
- If the operation is not explicitly stated, use "none" or null rather than guessing.
- Use any financial interpretation guide to identify when counts may refer to transactions rather than rows or lines.

Return valid JSON only. Ensure any double quotes inside strings are escaped (e.g., \\") or use single quotes instead.

JSON shape:
{{
  "reasoning": "Step-by-step logic. If a Business Glossary is provided, cite the relevant concept to justify your chosen aggregation.",
  "aggregation": "sum|count|avg|max|min|none|unknown",
  "group_by": [],
  "order_by": [
    {{
      "target": "",
      "direction": "asc|desc|unknown",
      "evidence": ""
    }}
  ],
  "limit": null,
  "comparison": {{
    "required": false,
    "type": "difference|percentage_change|ratio|greater_than|less_than|before_after|none|unknown",
    "left": null,
    "right": null,
    "evidence": ""
  }},
  "ambiguities": [],
  "confidence": "high|medium|low"
}}

Question:
{question}

JSON:
""".strip()


# ---------------------------------------------------------------------------
# Main decomposer
# ---------------------------------------------------------------------------


@dataclass
class IntentDecomposer:
    """Sequential Stage 1 intent decomposer.

    Args:
        llm_call:
            Function that takes a prompt string and returns the model response.

        intent_mode:
            "none", "nl_only", or "metadata_guided".

        schema_store:
            Required only for metadata_guided mode. Should be a
            SchemaAnnotationStore instance.
    """

    llm_call: LLMCall
    intent_mode: IntentMode = "nl_only"
    schema_store: Any | None = None

    def __post_init__(self) -> None:
        if self.intent_mode not in {"none", "nl_only", "metadata_guided"}:
            raise ValueError(
                f"Unsupported intent_mode={self.intent_mode!r}. "
                "Expected 'none', 'nl_only', or 'metadata_guided'."
            )

        if self.intent_mode == "metadata_guided" and self.schema_store is None:
            raise ValueError(
                "schema_store is required when intent_mode='metadata_guided'."
            )

    def _guide(self, slot_name: SlotName) -> str:
        if self.intent_mode in {"none", "nl_only"}:
            return ""

        return build_slot_metadata_guide(
            schema_store=self.schema_store,
            slot_name=slot_name,
        )

    def _run_slot(self, slot_name: SlotName, prompt: str) -> JsonDict:
        raw_response = self.llm_call(prompt)
        parsed = _safe_json_parse(raw_response, slot_name=slot_name)

        parsed["_slot_name"] = slot_name
        parsed["_intent_mode"] = self.intent_mode

        return _normalise_slot(slot_name, parsed)

    def decompose(
        self,
        question: str,
        *,
        include_intermediate: bool = True,
    ) -> JsonDict:
        """Run slot decomposition and return deterministic intent JSON."""
        if self.intent_mode == "none":
            return {
                "_stage": "intent_decomposition",
                "_decomposition_method": "none",
                "question": question,
                "raw_question": question,
                "slots": {},
                "ambiguities": [],
                "slot_confidences": {},
                "parse_errors": [],
                "overall_confidence": "medium",
            }

        entity_prompt = build_entity_slot_prompt(
            question=question,
            metadata_guide=self._guide("entity"),
        )
        entity_slot = self._run_slot("entity", entity_prompt)

        measure_prompt = build_measure_slot_prompt(
            question=question,
            metadata_guide=self._guide("measure"),
        )
        measure_slot = self._run_slot("measure", measure_prompt)

        time_prompt = build_time_slot_prompt(
            question=question,
            metadata_guide=self._guide("time"),
        )
        time_slot = self._run_slot("time", time_prompt)

        operation_prompt = build_operation_slot_prompt(
            question=question,
            metadata_guide=self._guide("operation"),
        )
        operation_slot = self._run_slot("operation", operation_prompt)

        slots = {
            "entity": entity_slot,
            "measure": measure_slot,
            "time": time_slot,
            "operation": operation_slot,
        }
        slot_confidences = {
            slot_name: _normalise_confidence(slot.get("confidence"))
            for slot_name, slot in slots.items()
        }
        parse_errors = _collect_parse_errors(slots)

        result = {
            "_stage": "intent_decomposition",
            "_decomposition_method": (
                "question_only"
                if self.intent_mode == "nl_only"
                else "metadata_guided"
            ),
            "question": question,
            "slots": slots,
            "ambiguities": _collect_ambiguities(slots),
            "slot_confidences": slot_confidences,
            "parse_errors": parse_errors,
            "overall_confidence": _overall_confidence(
                slot_confidences=slot_confidences,
                parse_errors=parse_errors,
            ),
        }
        
        return _prune_empty(result)


# ---------------------------------------------------------------------------
# Convenience function for runners
# ---------------------------------------------------------------------------


def decompose_question_intent(
    *,
    question: str,
    llm_call: LLMCall,
    intent_mode: IntentMode = "nl_only",
    schema_store: Any | None = None,
    include_intermediate: bool = True,
) -> JsonDict:
    """Convenience wrapper for scripts/runners."""
    decomposer = IntentDecomposer(
        llm_call=llm_call,
        intent_mode=intent_mode,
        schema_store=schema_store,
    )

    return decomposer.decompose(
        question=question,
        include_intermediate=include_intermediate,
    )
