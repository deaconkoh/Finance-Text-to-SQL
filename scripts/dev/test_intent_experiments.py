import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

for import_root in (PROJECT_ROOT, SRC_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from src.finverisql.schema_loader import SchemaAnnotationStore
from src.finverisql.intent_decomposer import IntentDecomposer
from src.utils.inference_utils import build_verifier_generate_fn

def main() -> None:
    parser = argparse.ArgumentParser(description="Test and compare intent decomposition modes.")
    parser.add_argument(
        "-q", "--question",
        default="How many AI courses did we sell this fiscal year?",
        help="The financial question to test."
    )
    parser.add_argument(
        "--model-name",
        default="mlx-community/gemma-4-e4b-it-4bit",
        help="Model name for the LLM backend."
    )
    parser.add_argument(
        "--backend",
        default="mlx-vlm",
        help="LLM backend to use (e.g., mlx-vlm, ollama)."
    )
    args = parser.parse_args()

    # 1. Load the schema annotations
    print("Loading schema annotations...")
    store = SchemaAnnotationStore.from_json("data/booksql/schema_annotations.json")

    # 2. Setup your local LLM call.
    print("Loading LLM backend...")
    try:
        llm_call = build_verifier_generate_fn(
            model_name=args.model_name,
            backend=args.backend,
            num_predict=1024,
        )
    except Exception as e:
        print(f"Failed to load LLM. Check your backend/model config. Error: {e}")
        return

    # 3. Initialize both decomposers
    nl_decomposer = IntentDecomposer(
        llm_call=llm_call,
        intent_mode="nl_only",
    )
    
    meta_decomposer = IntentDecomposer(
        llm_call=llm_call,
        intent_mode="metadata_guided",
        schema_store=store,
    )

    # 4. Test a tricky financial question
    question = args.question
    print(f"\nTarget Question: '{question}'\n")
    print("=" * 60)

    print("Running Experiment 1: Natural Language Only...")
    nl_result = nl_decomposer.decompose(question)
    print(json.dumps(nl_result, indent=2))
    
    print("\n" + "=" * 60)
    print("Running Experiment 2: Metadata Guided...")
    meta_result = meta_decomposer.decompose(question)
    print(json.dumps(meta_result, indent=2))

if __name__ == "__main__":
    main()

"""
============================================================
Running Experiment 1: Natural Language Only...
{
  "_stage": "intent_decomposition",
  "_decomposition_method": "question_only",
  "question": "How many AI courses did we sell?",
  "slots": {
    "entity": {
      "entity_mentions": [
        {
          "text": "AI courses",
          "mention_type": "product_service",
          "explicit_role": "product_service",
          "mapped_concept": "AI courses",
          "evidence": "AI courses",
          "confidence": "high"
        }
      ],
      "ambiguities": [
        {
          "type": "None"
        }
      ],
      "confidence": "high",
      "_slot_name": "entity",
      "_intent_mode": "nl_only"
    },
    "measure": {
      "reasoning": "The user is asking for the total number of AI courses that were sold. This maps directly to a count of items sold.",
      "answer_type": "count",
      "target_concept": "AI courses sold",
      "measure_kind": "quantity",
      "possible_event_types": [
        "sale"
      ],
      "confidence": "high",
      "_slot_name": "measure",
      "_intent_mode": "nl_only"
    },
    "time": {
      "reasoning": "The question asks for a total count ('How many') of AI courses sold but provides no specific timeframe (dates, periods, or relative times). Therefore, no temporal conditions can be extracted.",
      "date_type": "unspecified",
      "requires_group_by_period": false,
      "requires_latest": false,
      "requires_today": false,
      "confidence": "high",
      "_slot_name": "time",
      "_intent_mode": "nl_only"
    },
    "operation": {
      "reasoning": "The question asks 'How many AI courses did we sell?'. This requires counting the number of courses sold.",
      "aggregation": "count",
      "comparison": {
        "required": false
      },
      "confidence": "high",
      "_slot_name": "operation",
      "_intent_mode": "nl_only"
    }
  },
  "ambiguities": [
    {
      "type": "None"
    }
  ],
  "slot_confidences": {
    "entity": "high",
    "measure": "high",
    "time": "high",
    "operation": "high"
  },
  "overall_confidence": "high"
}

============================================================
Running Experiment 2: Metadata Guided...
{
  "_stage": "intent_decomposition",
  "_decomposition_method": "metadata_guided",
  "question": "How many AI courses did we sell?",
  "slots": {
    "entity": {
      "entity_mentions": [
        {
          "text": "AI courses",
          "mention_type": "product_service",
          "explicit_role": "product_service",
          "mapped_concept": "product_service",
          "evidence": "AI courses",
          "confidence": "high"
        }
      ],
      "ambiguities": [
        {
          "text": "sell",
          "description": "The verb 'sell' implies a transaction, but no specific transaction type entity is mentioned beyond the product itself."
        }
      ],
      "confidence": "high",
      "_slot_name": "entity",
      "_intent_mode": "metadata_guided"
    },
    "measure": {
      "reasoning": "The user is asking for the number of AI courses sold. 'How many' implies a count, and 'sold' relates to the quantity of items moved in a sale.",
      "answer_type": "count",
      "target_concept": "Quantity Sold",
      "measure_kind": "quantity",
      "possible_event_types": [
        "sale"
      ],
      "confidence": "high",
      "_slot_name": "measure",
      "_intent_mode": "metadata_guided"
    },
    "time": {
      "reasoning": "The question asks for the total number of AI courses sold but provides no specific time frame. Therefore, no temporal conditions can be extracted.",
      "date_type": "unspecified",
      "requires_group_by_period": false,
      "requires_latest": false,
      "requires_today": false,
      "confidence": "high",
      "_slot_name": "time",
      "_intent_mode": "metadata_guided"
    },
    "operation": {
      "reasoning": "The question asks 'How many AI courses did we sell?'. Based on the Business Glossary, 'Quantity Sold' applies when asked 'how many [items]' were sold, and it requires aggregating (summing) the quantity. Therefore, the required operation is a sum of the quantity sold.",
      "aggregation": "sum",
      "comparison": {
        "required": false
      },
      "ambiguities": [
        "The specific item being counted ('AI courses') is not defined in the glossary, but the aggregation type (sum) is determined by the phrasing 'how many [items] sold'."
      ],
      "confidence": "high",
      "_slot_name": "operation",
      "_intent_mode": "metadata_guided"
    }
  },
  "ambiguities": [
    {
      "text": "sell",
      "description": "The verb 'sell' implies a transaction, but no specific transaction type entity is mentioned beyond the product itself."
    },
    "The specific item being counted ('AI courses') is not defined in the glossary, but the aggregation type (sum) is determined by the phrasing 'how many [items] sold'."
  ],
  "slot_confidences": {
    "entity": "high",
    "measure": "high",
    "time": "high",
    "operation": "high"
  },
  "overall_confidence": "high"
}
"""