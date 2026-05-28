from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.data_utils import (  # noqa: E402
    BOOKSQL_DATASET_NAME,
    BOOKSQL_DB_ID,
    generate_schema_from_sqlite,
)


BOOKSQL_DIR = PROJECT_ROOT / "data" / "booksql"
NORMALIZED_JSONL = BOOKSQL_DIR / "booksql_normalized.jsonl"
LOCAL_DB_PATH = BOOKSQL_DIR / "accounting.sqlite"
SCHEMA_TXT_PATH = BOOKSQL_DIR / "schema.txt"

HF_DB_FILENAME = "BookSQL/accounting.sqlite"
HF_TRAIN_FILENAME = "BookSQL/train.json"
HF_VAL_FILENAME = "BookSQL/val.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare BookSQL data, database, and schema for baseline runs.",
    )
    parser.add_argument(
        "--dataset-name",
        default=BOOKSQL_DATASET_NAME,
        help="Official Hugging Face BookSQL dataset repository.",
    )
    parser.add_argument(
        "--sanity-limit",
        type=int,
        default=5,
        help="Number of validation SQL queries to execute as a sanity check.",
    )
    return parser.parse_args()


def is_hf_access_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "401",
            "403",
            "unauthorized",
            "forbidden",
            "gated",
            "repository not found",
            "repo not found",
        )
    )


def get_hf_downloader():
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise RuntimeError(
            "Missing optional dependency 'huggingface_hub'. "
            "Install it before running BookSQL setup."
        ) from exc

    return hf_hub_download


def download_hf_file(dataset_name: str, filename: str) -> Path:
    hf_hub_download = get_hf_downloader()

    try:
        return Path(
            hf_hub_download(
                repo_id=dataset_name,
                filename=filename,
                repo_type="dataset",
            )
        )
    except Exception as exc:
        if is_hf_access_error(exc):
            raise RuntimeError(
                f"Could not access {filename} from {dataset_name}. "
                "This looks like a Hugging Face access/authentication issue. "
                "Make sure you have accepted the dataset terms and are authenticated."
            ) from exc

        raise RuntimeError(
            f"Could not download {filename} from {dataset_name}: {exc}"
        ) from exc


def load_json_file(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return [dict(row) for row in data]

    if isinstance(data, dict):
        for key in ("data", "records", "examples", "rows"):
            if isinstance(data.get(key), list):
                return [dict(row) for row in data[key]]

    raise RuntimeError(f"Unsupported BookSQL JSON format: {path}")


def normalize_split_value(value: Any) -> str:
    split = str(value).strip().lower()

    if split in {"val", "dev", "validation"}:
        return "validation"

    if split == "train":
        return "train"

    if split == "test":
        return "test"

    return split


def load_official_booksql(dataset_name: str) -> list[dict[str, Any]]:
    """
    Download and load the official BookSQL train/validation files directly.

    We do not use load_dataset(dataset_name) here because the official test file
    has a different column structure from train/val, which can cause Hugging Face
    dataset generation to fail with a schema mismatch.
    """
    print(f"Downloading official BookSQL data: {HF_TRAIN_FILENAME}")
    train_path = download_hf_file(dataset_name, HF_TRAIN_FILENAME)

    print(f"Downloading official BookSQL data: {HF_VAL_FILENAME}")
    val_path = download_hf_file(dataset_name, HF_VAL_FILENAME)

    train_rows = load_json_file(train_path)
    val_rows = load_json_file(val_path)

    for row in train_rows:
        row["split"] = normalize_split_value(row.get("split", "train"))

    for row in val_rows:
        row["split"] = normalize_split_value(row.get("split", "validation"))

    return train_rows + val_rows


def normalize_booksql_rows(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Convert official BookSQL rows into the project-wide internal format.

    Official BookSQL rows contain:
    - Levels
    - SQL
    - Query
    - split

    The official SQL column is the gold/reference SQL.
    We store it internally as gold_sql so the baseline and evaluation code can
    use a consistent field name across experiments.
    """
    normalized: list[dict[str, Any]] = []
    required_columns = ("Levels", "SQL", "Query", "split")

    for row_index, row in enumerate(raw_rows):
        missing = [column for column in required_columns if column not in row]
        if missing:
            raise RuntimeError(
                f"BookSQL row {row_index} is missing required columns: {missing}. "
                f"Available columns: {sorted(row.keys())}"
            )

        normalized.append(
            {
                "question_id": f"booksql_{row_index:06d}",
                "db_id": BOOKSQL_DB_ID,
                "question": row["Query"],
                "gold_sql": row["SQL"],
                "level": row["Levels"],
                "split": normalize_split_value(row["split"]),
            }
        )

    return normalized


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def download_booksql_database(dataset_name: str, destination: Path) -> Path:
    cached_path = download_hf_file(dataset_name, HF_DB_FILENAME)

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cached_path, destination)

    return destination


def get_validation_rows_for_sql_check(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Return validation rows and confirm their SQL column was normalised correctly.

    BookSQL does not have a separate gold SQL column.
    The internal gold_sql field is created from the official SQL column.
    """
    validation_rows = [
        row
        for row in rows
        if str(row.get("split", "")).strip().lower() == "validation"
    ]

    if not validation_rows:
        split_counts = Counter(row.get("split") for row in rows)
        raise RuntimeError(
            "No validation rows found after split normalisation. "
            f"Observed split counts: {dict(sorted(split_counts.items()))}"
        )

    missing_sql = [
        row["question_id"]
        for row in validation_rows
        if not str(row.get("gold_sql", "")).strip()
    ]

    if missing_sql:
        preview = ", ".join(missing_sql[:10])
        raise RuntimeError(
            f"Validation rows with missing SQL found after normalisation: {preview}. "
            "BookSQL's official SQL column should be present for validation rows."
        )

    return validation_rows

def run_validation_sql_sanity_check(
    validation_rows: list[dict[str, Any]],
    db_path: Path,
    limit: int,
) -> None:
    """
    Execute a few validation SQL queries against accounting.sqlite.

    This confirms that the official BookSQL SQL column is broadly compatible
    with the downloaded BookSQL database.

    This is only a setup sanity check, not an experiment metric.
    Therefore, failures are reported as warnings instead of stopping setup.
    """
    if limit <= 0:
        print("Validation SQL sanity check skipped because sanity-limit <= 0.")
        return

    checked = 0
    failures: list[tuple[str, str, str]] = []

    with sqlite3.connect(db_path) as conn:
        for row in validation_rows:
            if checked >= limit:
                break

            sql = str(row.get("gold_sql", "")).strip()
            if not sql:
                continue

            try:
                conn.execute(sql).fetchmany(1)
            except Exception as exc:
                failures.append((row["question_id"], str(exc), sql))
            finally:
                checked += 1

    passed = checked - len(failures)

    print(
        f"Validation SQL sanity check completed: "
        f"{passed}/{checked} query/queries executed successfully."
    )

    if failures:
        print("\nWarning: Some validation SQL sanity checks failed.")
        print(
            "This may indicate SQL dialect differences or unsupported syntax in "
            "some official BookSQL queries. Setup will continue because this is "
            "only a sanity check, not an experiment metric."
        )

        for question_id, error, sql in failures[:5]:
            print(f"\nFailed query_id: {question_id}")
            print(f"Error: {error}")
            print(f"SQL preview: {sql[:500]}")

def main() -> None:
    args = parse_args()

    print(f"Loading BookSQL from Hugging Face: {args.dataset_name}")
    raw_rows = load_official_booksql(args.dataset_name)

    normalized_rows = normalize_booksql_rows(raw_rows)

    write_jsonl(NORMALIZED_JSONL, normalized_rows)
    split_counts = Counter(row["split"] for row in normalized_rows)

    print(f"Saved normalized dataset: {NORMALIZED_JSONL}")
    print(f"Row counts by split: {dict(sorted(split_counts.items()))}")

    print(f"Downloading official SQLite database: {HF_DB_FILENAME}")
    download_booksql_database(args.dataset_name, LOCAL_DB_PATH)
    print(f"Saved database: {LOCAL_DB_PATH}")

    schema_text = generate_schema_from_sqlite(LOCAL_DB_PATH)
    SCHEMA_TXT_PATH.write_text(schema_text + "\n", encoding="utf-8")
    print(f"Saved schema: {SCHEMA_TXT_PATH}")

    validation_rows = get_validation_rows_for_sql_check(normalized_rows)
    print(f"Validation rows with SQL: {len(validation_rows)}")

    run_validation_sql_sanity_check(
        validation_rows=validation_rows,
        db_path=LOCAL_DB_PATH,
        limit=args.sanity_limit,
    )



if __name__ == "__main__":
    main()