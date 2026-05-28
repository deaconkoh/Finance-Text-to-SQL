"""
Keyword-based intent extraction is only for testing, and will be replaced by an LLM-based extractor later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ALLOWED_FINANCIAL_OBJECTS = {
    "invoice",
    "revenue",
    "expense",
    "receivable",
    "payable",
    "cash_balance",
    "profit",
    "equity",
    "other",
}

ALLOWED_FINANCIAL_MEASURES = {
    "credit",
    "debit",
    "amount",
    "balance",
    "ratio",
    "count",
    "none",
}

ALLOWED_AGGREGATIONS = {
    "sum",
    "average",
    "count",
    "none",
}

ALLOWED_TEMPORAL_SCOPES = {
    "daily",
    "monthly",
    "quarterly",
    "yearly",
    "ytd",
    "point_in_time",
    "none",
}

ALLOWED_STOCK_OR_FLOW = {
    "flow",
    "stock",
    "none",
}


@dataclass(frozen=True)
class FinancialIntent:
    financial_object: str
    financial_measure: str
    aggregation: str
    temporal_scope: str
    stock_or_flow: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FinancialIntent":
        intent = cls(
            financial_object=str(data.get("financial_object", "other")).lower(),
            financial_measure=str(data.get("financial_measure", "none")).lower(),
            aggregation=str(data.get("aggregation", "none")).lower(),
            temporal_scope=str(data.get("temporal_scope", "none")).lower(),
            stock_or_flow=str(data.get("stock_or_flow", "none")).lower(),
        )

        intent.validate()
        return intent

    def validate(self) -> None:
        checks = [
            ("financial_object", self.financial_object, ALLOWED_FINANCIAL_OBJECTS),
            ("financial_measure", self.financial_measure, ALLOWED_FINANCIAL_MEASURES),
            ("aggregation", self.aggregation, ALLOWED_AGGREGATIONS),
            ("temporal_scope", self.temporal_scope, ALLOWED_TEMPORAL_SCOPES),
            ("stock_or_flow", self.stock_or_flow, ALLOWED_STOCK_OR_FLOW),
        ]

        for field_name, value, allowed_values in checks:
            if value not in allowed_values:
                raise ValueError(
                    f"Invalid {field_name}: {value}. "
                    f"Allowed values: {sorted(allowed_values)}"
                )

    def to_dict(self) -> dict[str, str]:
        return {
            "financial_object": self.financial_object,
            "financial_measure": self.financial_measure,
            "aggregation": self.aggregation,
            "temporal_scope": self.temporal_scope,
            "stock_or_flow": self.stock_or_flow,
        }


def build_intent_extraction_prompt(question: str) -> str:
    return f"""Return a JSON object with exactly these fields.
Use only the allowed values listed for each field.

financial_object:
  Allowed: invoice, revenue, expense, receivable,
           payable, cash_balance, profit, equity, other

financial_measure:
  Allowed: credit, debit, amount, balance, ratio, count, none

aggregation:
  Allowed: sum, average, count, none

temporal_scope:
  Allowed: daily, monthly, quarterly, yearly,
           ytd, point_in_time, none

stock_or_flow:
  Allowed: flow, stock, none

Question:
{question}

Return only valid JSON. No explanation.
"""


def heuristic_intent_for_smoke_test(question: str) -> FinancialIntent:
    """
    Temporary smoke-test intent extractor.

    Replace this with an LLM-based extractor later.
    This is only to test the FinVeriSQL D1/D2 code path before labels exist.
    """
    q = question.lower()

    financial_object = "other"
    financial_measure = "none"
    aggregation = "none"
    temporal_scope = "none"
    stock_or_flow = "none"

    if "invoice" in q:
        financial_object = "invoice"
        financial_measure = "credit"
        stock_or_flow = "flow"
    elif "revenue" in q or "income" in q:
        financial_object = "revenue"
        financial_measure = "credit"
        stock_or_flow = "flow"
    elif "expense" in q or "cost" in q:
        financial_object = "expense"
        financial_measure = "debit"
        stock_or_flow = "flow"
    elif "receivable" in q or "customer balance" in q:
        financial_object = "receivable"
        financial_measure = "balance"
        stock_or_flow = "stock"
    elif "payable" in q or "vendor balance" in q:
        financial_object = "payable"
        financial_measure = "balance"
        stock_or_flow = "stock"
    elif "cash" in q and "balance" in q:
        financial_object = "cash_balance"
        financial_measure = "balance"
        stock_or_flow = "stock"

    if "average" in q or "avg" in q:
        aggregation = "average"
    elif "count" in q or "number of" in q or "how many" in q:
        aggregation = "count"
        financial_measure = "count"
    elif "total" in q or "sum" in q or "revenue" in q or "expense" in q:
        aggregation = "sum"

    if "ytd" in q or "year to date" in q:
        temporal_scope = "ytd"
    elif "monthly" in q or "month" in q:
        temporal_scope = "monthly"
    elif "quarter" in q:
        temporal_scope = "quarterly"
    elif "year" in q or "annual" in q:
        temporal_scope = "yearly"
    elif "as of" in q or "balance" in q:
        temporal_scope = "point_in_time"

    return FinancialIntent(
        financial_object=financial_object,
        financial_measure=financial_measure,
        aggregation=aggregation,
        temporal_scope=temporal_scope,
        stock_or_flow=stock_or_flow,
    )