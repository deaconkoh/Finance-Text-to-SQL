from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import scripts.hardware_ablation_utils as hardware
from src.asa_metrics.asa_metrics import evaluate_asa_rows
from src.repair_learning.generate import generate_repairs_from_file
from src.utils import inference_utils


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_ollama_single_host_default_and_round_robin(monkeypatch) -> None:
    urls: list[str] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return b'{"message":{"content":"ok"},"done_reason":"stop"}'

    def fake_urlopen(request, timeout):
        urls.append(request.full_url)
        return Response()

    monkeypatch.delenv("OLLAMA_HOSTS", raising=False)
    monkeypatch.setattr(inference_utils.urllib.request, "urlopen", fake_urlopen)
    generate = inference_utils.build_ollama_generate_fn("model")
    generate("one")
    assert urls == ["http://localhost:11434/api/chat"]

    urls.clear()
    monkeypatch.setenv("OLLAMA_HOSTS", "http://one:11434,http://two:11434")
    generate = inference_utils.build_ollama_generate_fn("model")
    for prompt in ("one", "two", "three"):
        generate(prompt)
    assert urls == [
        "http://one:11434/api/chat",
        "http://two:11434/api/chat",
        "http://one:11434/api/chat",
    ]


def test_asa_workers_preserve_input_order() -> None:
    rows = [
        {"question_id": str(index), "execution_match": True, "gold_sql": "x", "generated_sql": "x"}
        for index in range(4)
    ]

    def checker(*, generated_sql, **_kwargs):
        time.sleep(0.001)
        return {"primary_status": "NONE", "findings": [], "warnings": []}

    _, diagnostics = evaluate_asa_rows(rows, {}, inv_checker=checker, workers=4)
    assert [row["question_id"] for row in diagnostics] == ["0", "1", "2", "3"]


def test_prepare_cohort_exact_derivation(tmp_path: Path, monkeypatch) -> None:
    baseline = [
        {"question_id": "q0", "evaluation_group": "D_ambiguous", "question": "d"},
        {"question_id": "q1", "evaluation_group": "A_correct_executable", "question": "a"},
        {"question_id": "q2", "evaluation_group": "B_wrong_executable", "question": "b"},
        {"question_id": "q3", "evaluation_group": "C_non_executable", "question": "c"},
    ]
    intents = [
        {
            "question_id": row["question_id"],
            "question": row["question"],
            "intent_mode": "nl_only",
            "status": "success",
            "intent_representation": {"id": row["question_id"]},
        }
        for row in baseline
    ]
    development = [{"question_id": "q0"}, {"question_id": "q1"}]
    write_jsonl(tmp_path / "baseline.jsonl", baseline)
    write_jsonl(tmp_path / "intents.jsonl", intents)
    write_jsonl(tmp_path / "development.jsonl", development)
    monkeypatch.setattr(
        hardware,
        "EXPECTED_COUNTS",
        {"validation": 4, "ambiguous": 1, "non_ambiguous": 3, "development_ids": 2, "eligible": 2},
    )
    args = type(
        "Args",
        (),
        {
            "baseline": str(tmp_path / "baseline.jsonl"),
            "intents": str(tmp_path / "intents.jsonl"),
            "development_ids": str(tmp_path / "development.jsonl"),
            "output_dir": str(tmp_path / "out"),
        },
    )()
    hardware.prepare_cohort(args)
    assert [row["question_id"] for row in hardware.read_jsonl(tmp_path / "out/cohort_baseline.jsonl")] == [
        "q2",
        "q3",
    ]


def test_artifact_validation_rejects_duplicate_and_foreign_ids(tmp_path: Path) -> None:
    cohort = tmp_path / "cohort.jsonl"
    write_jsonl(cohort, [{"question_id": "q1"}, {"question_id": "q2"}])
    duplicate = tmp_path / "duplicate.jsonl"
    write_jsonl(duplicate, [{"question_id": "q1"}, {"question_id": "q1"}])
    with pytest.raises(ValueError, match="duplicate"):
        hardware.validate_rows(duplicate, cohort, complete=False, expects=[])
    foreign = tmp_path / "foreign.jsonl"
    write_jsonl(foreign, [{"question_id": "q3"}])
    with pytest.raises(ValueError, match="foreign"):
        hardware.validate_rows(foreign, cohort, complete=False, expects=[])


def test_learned_generation_resumes_completion_ordered_jsonl(tmp_path: Path) -> None:
    verifier = tmp_path / "verify.jsonl"
    output = tmp_path / "repairs.jsonl"
    summary = tmp_path / "summary.json"
    base = {
        "db_id": "booksql",
        "split": "validation",
        "evaluation_group": "B_wrong_executable",
        "question": "question",
        "gold_sql": "SELECT 1",
        "generated_sql": "SELECT 2",
        "intent_mode": "nl_only",
        "intent_representation": {},
        "execution_profile": "{}",
        "verification": {"answers_question": False, "mismatch_type": "financial_measure_error"},
    }
    write_jsonl(verifier, [{**base, "question_id": "q2"}, {**base, "question_id": "q1"}])
    calls = 0

    def generator(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return '{"repaired_sql":"SELECT 1"}'

    first = generate_repairs_from_file(
        verifier, output, summary, "schema", generator, "model", "sft_llama31_8b"
    )
    assert first["new_rows"] == 2
    second = generate_repairs_from_file(
        verifier, output, summary, "schema", generator, "model", "sft_llama31_8b"
    )
    assert second["new_rows"] == 0
    assert calls == 2
    assert [row["question_id"] for row in hardware.read_jsonl(output)] == ["q2", "q1"]


def test_hardware_runner_contracts() -> None:
    workstation = (PROJECT_ROOT / "run_workstation_internal_ablations_only.sh").read_text()
    a100 = (PROJECT_ROOT / "run_a100_repair_ablation_only.sh").read_text()
    assert 'RUN_ID="eval_publication_20260706_154755"' in workstation
    assert 'FINVERISQL_VERIFY_WORKERS=8' in workstation
    assert "scripts/precompute_finverisql_intents.py" not in workstation
    assert "2_run_ablations.sh" not in workstation
    assert "adir_ws_ollama_0" in workstation and "adir_ws_ollama_1" in workstation
    assert 'RUN_ID="eval_publication_20260706_154755"' in a100
    assert "adir_a100_ollama_3" in a100
    assert "--learning-rate 1e-6" in a100
    assert "--batch-size 8 --mini-batch-size 1 --ppo-epochs 1" in a100
    assert "4_run_repair_ablation.sh" not in a100

    official = (PROJECT_ROOT / "run_workstation_official_test_only.sh").read_text()
    assert 'RUN_ID="eval_publication_20260706_154755"' in official
    assert 'QWEN_MODEL="qwen2.5-coder:7b-instruct"' in official
    assert 'ADIR_MODEL="llama3.1:8b"' in official
    assert "run_workstation_internal_ablations_only.sh" not in official
    assert "--workers \"$WORKERS\"" in official
    normal = official.split('if [[ "${1:-}" == "--setup-ollama" ]]', 1)[1]
    normal = normal.split("fi", 1)[1]
    assert "sudo docker" not in normal
