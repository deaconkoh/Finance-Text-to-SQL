#!/usr/bin/env python3
"""Strict artifact and Ollama helpers for hardware-specific ablation runners."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any


EXPECTED_COUNTS = {
    "validation": 7605,
    "ambiguous": 1002,
    "non_ambiguous": 6603,
    "development_ids": 2000,
    "eligible": 4874,
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected JSON object")
            rows.append(row)
    return rows


def row_id(row: dict[str, Any], *, label: str) -> str:
    value = row.get("question_id") or row.get("id")
    if value in (None, ""):
        raise ValueError(f"{label} row is missing question_id")
    return str(value)


def index_unique(rows: list[dict[str, Any]], *, label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        question_id = row_id(row, label=label)
        if question_id in indexed:
            raise ValueError(f"{label} has duplicate question_id: {question_id}")
        indexed[question_id] = row
    return indexed


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def prepare_cohort(args: argparse.Namespace) -> None:
    baseline_path = Path(args.baseline)
    intent_path = Path(args.intents)
    development_path = Path(args.development_ids)
    baseline = read_jsonl(baseline_path)
    intents = read_jsonl(intent_path)
    development = read_jsonl(development_path)
    baseline_by_id = index_unique(baseline, label="baseline")
    intent_by_id = index_unique(intents, label="intent cache")
    development_by_id = index_unique(development, label="development IDs")

    if len(baseline) != EXPECTED_COUNTS["validation"]:
        raise ValueError(f"Expected 7,605 validation rows, found {len(baseline)}")
    if len(intents) != EXPECTED_COUNTS["validation"]:
        raise ValueError(f"Expected 7,605 intent rows, found {len(intents)}")
    if set(intent_by_id) != set(baseline_by_id):
        raise ValueError("Intent cache question-ID set does not match the validation population")
    if len(development_by_id) != EXPECTED_COUNTS["development_ids"]:
        raise ValueError(f"Expected 2,000 development IDs, found {len(development_by_id)}")
    if not set(development_by_id) <= set(baseline_by_id):
        raise ValueError("Development IDs contain IDs outside the validation population")

    for question_id, intent in intent_by_id.items():
        baseline_row = baseline_by_id[question_id]
        if intent.get("intent_mode") != "nl_only":
            raise ValueError(f"Intent row {question_id} is not nl_only")
        if intent.get("status") not in (None, "success"):
            raise ValueError(f"Intent row {question_id} is not successful")
        if not isinstance(intent.get("intent_representation"), dict):
            raise ValueError(f"Intent row {question_id} has no intent representation")
        for key in ("question", "generated_sql"):
            if key in intent and key in baseline_row and intent[key] != baseline_row[key]:
                raise ValueError(f"Intent row {question_id} has incompatible {key}")

    non_ambiguous = [
        row
        for row in baseline
        if row.get("evaluation_group") != "D_ambiguous"
        and row.get("excluded_from_primary_metrics") is not True
    ]
    ambiguous_count = len(baseline) - len(non_ambiguous)
    eligible = [
        row for row in non_ambiguous if row_id(row, label="baseline") not in development_by_id
    ]
    actual = {
        "validation": len(baseline),
        "ambiguous": ambiguous_count,
        "non_ambiguous": len(non_ambiguous),
        "development_ids": len(development_by_id),
        "eligible": len(eligible),
    }
    if actual != EXPECTED_COUNTS:
        raise ValueError(f"Unexpected publication cohort counts: {actual}; expected {EXPECTED_COUNTS}")

    output_dir = Path(args.output_dir)
    cohort_path = output_dir / "cohort_baseline.jsonl"
    intent_output = output_dir / "cohort_intents_nl_only.jsonl"
    eligible_ids = [row_id(row, label="eligible baseline") for row in eligible]
    write_jsonl(cohort_path, eligible)
    write_jsonl(intent_output, [intent_by_id[question_id] for question_id in eligible_ids])
    manifest = {
        "counts": actual,
        "derivation": "7605 validation - 1002 D_ambiguous - frozen development IDs = 4874",
        "baseline_source": str(baseline_path),
        "baseline_sha256": sha256(baseline_path),
        "intent_source": str(intent_path),
        "intent_sha256": sha256(intent_path),
        "development_ids_source": str(development_path),
        "development_ids_sha256": sha256(development_path),
        "cohort_sha256": sha256(cohort_path),
        "question_ids_sha256": hashlib.sha256(
            "\n".join(eligible_ids).encode("utf-8")
        ).hexdigest(),
    }
    (output_dir / "cohort_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print("Cohort derivation: 7,605 -> 6,603 -> 4,874")


def prepare_primary(args: argparse.Namespace) -> None:
    rows = read_jsonl(Path(args.input))
    index_unique(rows, label=args.input)
    primary = [
        row
        for row in rows
        if row.get("evaluation_group") != "D_ambiguous"
        and row.get("excluded_from_primary_metrics") is not True
    ]
    write_jsonl(Path(args.output), primary)
    print(f"Wrote {len(primary)}/{len(rows)} primary-metric rows to {args.output}")


def expected_value(text: str) -> tuple[str, Any]:
    key, separator, value = text.partition("=")
    if not separator or not key:
        raise ValueError(f"Invalid --expect value: {text!r}; use KEY=JSON")
    return key, json.loads(value)


def validate_rows(
    path: Path,
    cohort_path: Path,
    *,
    complete: bool,
    expects: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    cohort_rows = read_jsonl(cohort_path)
    cohort_by_id = index_unique(cohort_rows, label="cohort")
    rows = read_jsonl(path)
    by_id = index_unique(rows, label=str(path))
    foreign = set(by_id) - set(cohort_by_id)
    if foreign:
        raise ValueError(f"{path} has foreign question IDs: {sorted(foreign)[:5]}")
    if complete and set(by_id) != set(cohort_by_id):
        missing = set(cohort_by_id) - set(by_id)
        raise ValueError(f"{path} is incomplete; missing {len(missing)} question IDs")
    parsed_expects = [expected_value(item) for item in expects]
    for question_id, row in by_id.items():
        source = cohort_by_id[question_id]
        for key in ("question", "generated_sql", "gold_sql", "evaluation_group"):
            if key in row and key in source and row[key] != source[key]:
                raise ValueError(f"{path}: context mismatch for {question_id} field {key}")
        if (
            "original_generated_sql" in row
            and row["original_generated_sql"] != source.get("generated_sql")
        ):
            raise ValueError(
                f"{path}: context mismatch for {question_id} field original_generated_sql"
            )
        for key, value in parsed_expects:
            if row.get(key) != value:
                raise ValueError(
                    f"{path}: incompatible {key} for {question_id}: {row.get(key)!r} != {value!r}"
                )
    return rows, list(cohort_by_id)


def validate_artifact(args: argparse.Namespace) -> None:
    rows, cohort_ids = validate_rows(
        Path(args.path),
        Path(args.cohort),
        complete=args.complete,
        expects=args.expect,
    )
    print(f"Validated {len(rows)}/{len(cohort_ids)} rows: {args.path}")


def import_artifact(args: argparse.Namespace) -> None:
    destination = Path(args.destination)
    existing: list[dict[str, Any]] = []
    if destination.exists():
        existing, _ = validate_rows(
            destination, Path(args.cohort), complete=False, expects=args.expect
        )
    existing_ids = {row_id(row, label="destination") for row in existing}
    additions: list[dict[str, Any]] = []
    cohort_rows = read_jsonl(Path(args.cohort))
    cohort_by_id = index_unique(cohort_rows, label="cohort")
    parsed_expects = [expected_value(item) for item in args.expect]
    for source_text in args.source:
        source = Path(source_text)
        if not source.exists():
            continue
        source_rows = read_jsonl(source)
        index_unique(source_rows, label=str(source))
        for row in source_rows:
            question_id = row_id(row, label=str(source))
            cohort_row = cohort_by_id.get(question_id)
            if cohort_row is None:
                continue
            for key in ("question", "generated_sql", "gold_sql", "evaluation_group"):
                if key in row and key in cohort_row and row[key] != cohort_row[key]:
                    raise ValueError(
                        f"{source}: context mismatch for {question_id} field {key}"
                    )
            if (
                "original_generated_sql" in row
                and row["original_generated_sql"] != cohort_row.get("generated_sql")
            ):
                raise ValueError(
                    f"{source}: context mismatch for {question_id} "
                    "field original_generated_sql"
                )
            for key, value in parsed_expects:
                if row.get(key) != value:
                    raise ValueError(
                        f"{source}: incompatible {key} for {question_id}: "
                        f"{row.get(key)!r} != {value!r}"
                    )
            if question_id not in existing_ids:
                additions.append(row)
                existing_ids.add(question_id)
    if additions:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("a", encoding="utf-8") as handle:
            for row in additions:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
    print(f"Imported {len(additions)} rows into {destination}")


def request_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method=method
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def endpoint_state(host: str, model: str) -> dict[str, str]:
    host = host.rstrip("/")
    version_payload = request_json(f"{host}/api/version")
    tags = request_json(f"{host}/api/tags")
    matches = [
        item
        for item in tags.get("models", [])
        if item.get("name") == model or item.get("model") == model
    ]
    if len(matches) != 1 or not matches[0].get("digest"):
        raise ValueError(f"{host} does not expose model {model!r} with a digest")
    if not version_payload.get("version"):
        raise ValueError(f"{host} did not report an Ollama API version")
    return {
        "host": host,
        "version": str(version_payload.get("version") or ""),
        "model": model,
        "model_digest": str(matches[0]["digest"]),
    }


def ollama_health(args: argparse.Namespace) -> None:
    hosts = [item.strip() for item in args.hosts.split(",") if item.strip()]
    states = [endpoint_state(host, args.model) for host in hosts]
    versions = {state["version"] for state in states}
    digests = {state["model_digest"] for state in states}
    if len(versions) != 1 or len(digests) != 1:
        raise ValueError(f"Ollama endpoints disagree: {states}")
    manifest_path = Path(args.manifest)
    if args.record:
        payload = {
            "image_id": args.image_id,
            "version": states[0]["version"],
            "model": args.model,
            "model_digest": states[0]["model_digest"],
            "hosts": hosts,
        }
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    else:
        expected = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key in ("version", "model", "model_digest", "hosts"):
            actual = (
                hosts
                if key == "hosts"
                else states[0]["model_digest"]
                if key == "model_digest"
                else states[0]["version"]
                if key == "version"
                else args.model
            )
            if expected.get(key) != actual:
                raise ValueError(f"Ollama manifest mismatch for {key}: {actual!r}")
    print(
        f"Healthy Ollama endpoints: {len(states)}; "
        f"version={states[0]['version']}; digest={states[0]['model_digest']}"
    )


def unload(args: argparse.Namespace) -> None:
    for host in [item.strip() for item in args.hosts.split(",") if item.strip()]:
        request_json(
            f"{host.rstrip('/')}/api/generate",
            method="POST",
            payload={"model": args.model, "keep_alive": 0},
        )
    print(f"Unloaded {args.model} from {args.hosts}")


def fingerprint_payload(inputs: list[str], config: str) -> dict[str, Any]:
    parsed_config = json.loads(config)
    if not isinstance(parsed_config, dict):
        raise ValueError("--config must be a JSON object")
    return {
        "inputs": [
            {"path": str(Path(item)), "sha256": sha256(Path(item))}
            for item in inputs
        ],
        "config": parsed_config,
    }


def fingerprint(args: argparse.Namespace) -> None:
    payload = fingerprint_payload(args.input, args.config)
    manifest = Path(args.manifest)
    if args.check:
        existing = json.loads(manifest.read_text(encoding="utf-8"))
        if existing != payload:
            raise ValueError(f"Artifact manifest does not match: {manifest}")
        print(f"Artifact manifest matches: {manifest}")
        return
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote artifact manifest: {manifest}")


def assert_denominator(args: argparse.Namespace) -> None:
    for item in args.metrics:
        path = Path(item)
        payload = json.loads(path.read_text(encoding="utf-8"))
        candidates = [
            payload.get("metric_total_examples"),
            payload.get("joined_question_ids"),
        ]
        totals = [
            row.get("total_rows")
            for row in payload.get("sets", [])
            if isinstance(row, dict)
        ]
        candidates.extend(totals)
        present = [value for value in candidates if isinstance(value, int)]
        if not present or any(value != args.expected for value in present):
            raise ValueError(
                f"{path} does not use the required denominator {args.expected}: {present}"
            )
    print(f"Validated denominator {args.expected} in {len(args.metrics)} metric files")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    cohort = commands.add_parser("prepare-cohort")
    cohort.add_argument("--baseline", required=True)
    cohort.add_argument("--intents", required=True)
    cohort.add_argument("--development-ids", required=True)
    cohort.add_argument("--output-dir", required=True)
    cohort.set_defaults(func=prepare_cohort)

    primary = commands.add_parser("prepare-primary")
    primary.add_argument("--input", required=True)
    primary.add_argument("--output", required=True)
    primary.set_defaults(func=prepare_primary)

    validate = commands.add_parser("validate-artifact")
    validate.add_argument("--path", required=True)
    validate.add_argument("--cohort", required=True)
    validate.add_argument("--complete", action="store_true")
    validate.add_argument("--expect", action="append", default=[])
    validate.set_defaults(func=validate_artifact)

    importer = commands.add_parser("import-artifact")
    importer.add_argument("--source", action="append", default=[])
    importer.add_argument("--destination", required=True)
    importer.add_argument("--cohort", required=True)
    importer.add_argument("--expect", action="append", default=[])
    importer.set_defaults(func=import_artifact)

    health = commands.add_parser("ollama-health")
    health.add_argument("--hosts", required=True)
    health.add_argument("--model", required=True)
    health.add_argument("--manifest", required=True)
    health.add_argument("--record", action="store_true")
    health.add_argument("--image-id", default=None)
    health.set_defaults(func=ollama_health)

    unload_parser = commands.add_parser("unload-ollama")
    unload_parser.add_argument("--hosts", required=True)
    unload_parser.add_argument("--model", required=True)
    unload_parser.set_defaults(func=unload)

    fingerprint_parser = commands.add_parser("fingerprint")
    fingerprint_parser.add_argument("--input", action="append", required=True)
    fingerprint_parser.add_argument("--config", required=True)
    fingerprint_parser.add_argument("--manifest", required=True)
    fingerprint_parser.add_argument("--check", action="store_true")
    fingerprint_parser.set_defaults(func=fingerprint)

    denominator = commands.add_parser("assert-denominator")
    denominator.add_argument("--metrics", action="append", required=True)
    denominator.add_argument("--expected", type=int, required=True)
    denominator.set_defaults(func=assert_denominator)
    return root


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
