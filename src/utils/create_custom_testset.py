"""
create_custom_testset.py

Builds a custom, stratified FINCH candidate set for manual knowledge-intensity labelling.

This script does NOT use an LLM to label examples.

Steps:
  1. Load FINCH records from train/val/dev only.
  2. Attach formatted schema_text.
  3. Stratified sample across sources:
     book_sql 40%, bull 40%, bird 10%, spider 10%.
  4. Within each source, preserve difficulty distribution where possible.
  5. Save candidates to CSV for manual labelling in Google Sheets.

Usage:
  python create_custom_testset.py \
      --target_size 556 \
      --output_csv ../data/subsets/financial_ki_candidates_556.csv
"""

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path
from datasets import load_dataset
from tqdm import tqdm

from data_utils import (
    DEFAULT_ALLOWED_PARTITIONS,
    get_full_schema_cached,
    load_finch_records,
    load_finch_schemas,
)


SOURCE_RATIOS: dict[str, float] = {
    "book_sql": 0.40,
    "bull": 0.40,
    "bird": 0.10,
    "spider": 0.10,
}

DIFFICULTY_LEVELS: list[str] = ["easy", "medium", "hard"]

RANDOM_SEED: int = 42


def stratified_sample(records: list, target_size: int, rng: random.Random) -> list:
    """
    Two-level stratified sample:
      Level 1: db_name at SOURCE_RATIOS.
      Level 2: difficulty proportional to actual distribution within each source.
    """
    buckets: dict = defaultdict(list)

    for r in records:
        difficulty = r.get("difficulty", "unknown").lower()
        buckets[(r["db_name"], difficulty)].append(r)

    source_totals: dict = defaultdict(int)

    for (source, _), items in buckets.items():
        source_totals[source] += len(items)

    sampled = []

    for source, ratio in SOURCE_RATIOS.items():
        n_source = round(target_size * ratio)
        total_available = source_totals.get(source, 0)

        if total_available == 0:
            print(f"  [warn] No records found for db_name='{source}'. Skipping.")
            continue

        n_source = min(n_source, total_available)

        source_buckets = {
            diff: buckets[(source, diff)]
            for diff in DIFFICULTY_LEVELS
            if (source, diff) in buckets
        }

        source_total = sum(len(v) for v in source_buckets.values())

        source_sampled = []

        for diff, items in source_buckets.items():
            n_diff = round(n_source * len(items) / source_total)
            n_diff = min(n_diff, len(items))
            source_sampled.extend(rng.sample(items, n_diff))

        if len(source_sampled) < n_source:
            remaining = [
                r for r in records
                if r["db_name"] == source and r not in source_sampled
            ]

            n_extra = min(n_source - len(source_sampled), len(remaining))
            source_sampled.extend(rng.sample(remaining, n_extra))

        elif len(source_sampled) > n_source:
            rng.shuffle(source_sampled)
            source_sampled = source_sampled[:n_source]

        print(f"  Sampled {len(source_sampled):>4d} from db_name='{source}'")
        sampled.extend(source_sampled)

    rng.shuffle(sampled)
    return sampled


def make_schema_preview(schema_text: str, max_chars: int = 1200) -> str:
    """
    Shorter schema preview for easier viewing in Google Sheets.
    Full schema_text is still saved separately.
    """
    if len(schema_text) <= max_chars:
        return schema_text

    return schema_text[:max_chars] + "\n... [truncated]"


def prepare_candidate_record(record: dict, index: int) -> dict:
    """
    Prepare a row for manual labelling.
    """
    gold_sql = record.get("SQL") or record.get("gold_sql", "")

    return {
        "candidate_id": index,
        "question_id": record["question_id"],
        "db_name": record["db_name"],
        "db_id": record["db_id"],
        "partition": record["partition"],
        "difficulty": record.get("difficulty", ""),
        "question": record["question"],
        "gold_sql": gold_sql,
        "schema_preview": make_schema_preview(record["schema_text"]),
        "schema_text": record["schema_text"],

        # Fill these manually in Google Sheets
        "knowledge_intensity": "",
        "ki_reason": "",
        "include_in_final_eval": "",
        "manual_notes": "",
    }


def save_csv(rows: list[dict], output_path: str):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        raise ValueError("No rows to save.")

    fieldnames = list(rows[0].keys())

    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved CSV → {output_path}")


def print_distribution(rows: list[dict]):
    print("\n── Source distribution ──")
    for source in SOURCE_RATIOS:
        count = sum(1 for r in rows if r["db_name"] == source)
        pct = count / len(rows) * 100 if rows else 0
        print(f"  {source}: {count} ({pct:.1f}%)")

    print("\n── Difficulty distribution ──")
    for diff in DIFFICULTY_LEVELS:
        count = sum(1 for r in rows if r.get("difficulty", "").lower() == diff)
        pct = count / len(rows) * 100 if rows else 0
        print(f"  {diff}: {count} ({pct:.1f}%)")


def main(args):
    rng = random.Random(RANDOM_SEED)

    print("Loading FINCH records...")
    dataset = load_dataset("domyn/FINCH")

    records = [
        dict(r)
        for r in dataset["train"]
        if r["partition"] in DEFAULT_ALLOWED_PARTITIONS
    ]

    print(f"Loaded {len(records)} records from partitions={DEFAULT_ALLOWED_PARTITIONS}")

    print("Loading FINCH schemas...")
    schemas = load_finch_schemas()

    print("Attaching schema_text...")
    enriched = []

    for r in tqdm(records, desc="Adding schema_text"):
        try:
            r = dict(r)
            r["schema_text"] = get_full_schema_cached(r, schemas)
            enriched.append(r)
        except Exception as e:
            print(
                f"  [warn] Skipping question_id={r.get('question_id')} "
                f"db_name={r.get('db_name')} db_id={r.get('db_id')}: {e}"
            )

    print(f"Records with valid schemas: {len(enriched)}")

    print(f"\nStratified sampling target_size={args.target_size}...")
    sampled = stratified_sample(enriched, args.target_size, rng)
    print(f"Final sample size: {len(sampled)}")

    candidate_rows = [
        prepare_candidate_record(record, index=i + 1)
        for i, record in enumerate(sampled)
    ]

    print_distribution(candidate_rows)

    save_csv(candidate_rows, args.output_csv)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build custom FINCH candidate set for manual KI labelling."
    )

    parser.add_argument(
        "--target_size",
        type=int,
        default=556,
        help="Target number of examples in the candidate set.",
    )

    parser.add_argument(
        "--output_csv",
        type=str,
        default="../../data/subsets/financial_ki_candidates_556.csv",
        help="Output CSV path for Google Sheets.",
    )

    args = parser.parse_args()
    main(args)