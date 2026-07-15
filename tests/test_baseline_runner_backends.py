from __future__ import annotations

import builtins
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.baseline import baseline_runner


def test_qwen_parse_args_defaults_to_mlx_lm(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["baseline_runner", "--model", "qwen"],
    )

    args = baseline_runner.parse_args()

    assert args.backend == "mlx-lm"
    assert args.ollama_model_name == baseline_runner.QWEN_DEFAULT_OLLAMA_MODEL_NAME
    assert args.temperature == 0.0
    assert args.timeout == 300


def test_qwen_default_backend_builds_mlx_runner(monkeypatch) -> None:
    fake_mlx_lm = types.ModuleType("mlx_lm")
    fake_mlx_lm.load = lambda model_name: ("model", "tokenizer")
    monkeypatch.setitem(sys.modules, "mlx_lm", fake_mlx_lm)

    args = SimpleNamespace(
        backend="mlx-lm",
        max_new_tokens=17,
    )

    generator, _generate_fn, metadata = baseline_runner.build_qwen_runner(args)

    assert generator == "qwen"
    assert metadata["model_name"] == baseline_runner.QWEN_MODEL_NAME
    assert metadata["inference_backend"] == "mlx"
    assert metadata["max_new_tokens"] == 17


def test_qwen_ollama_backend_does_not_import_mlx(monkeypatch) -> None:
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "mlx_lm" or name.startswith("mlx_lm."):
            raise AssertionError("Ollama backend should not import mlx_lm")
        return original_import(name, *args, **kwargs)

    captured: dict[str, object] = {}

    def fake_build_ollama_generate_fn(**kwargs):
        captured.update(kwargs)
        return lambda _prompt: "SELECT 1;"

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(
        baseline_runner,
        "build_ollama_generate_fn",
        fake_build_ollama_generate_fn,
    )

    args = SimpleNamespace(
        backend="ollama",
        ollama_model_name="qwen2.5-coder:7b",
        temperature=0.2,
        timeout=45,
        max_new_tokens=99,
    )

    generator, generate_fn, metadata = baseline_runner.build_qwen_runner(args)

    assert generator == "qwen"
    assert generate_fn("prompt") == "SELECT 1;"
    assert captured == {
        "model_name": "qwen2.5-coder:7b",
        "temperature": 0.2,
        "num_predict": 99,
        "timeout": 45,
        "format_json": False,
        "seed": None,
    }
    assert metadata["model_name"] == "qwen2.5-coder:7b"
    assert metadata["inference_backend"] == "ollama"
    assert metadata["max_new_tokens"] == 99
    assert metadata["temperature"] == 0.2
    assert metadata["timeout"] == 45


def test_ollama_qwen_output_still_flows_through_extract_sql(tmp_path) -> None:
    output_path = tmp_path / "baseline.jsonl"
    records = [
        {
            "question_id": "q1",
            "db_id": "booksql",
            "split": "validation",
            "level": "easy",
            "question": "Return one.",
            "schema": "Table t(x)",
            "gold_sql": "SELECT 1;",
        }
    ]
    metadata = {
        "model_name": "qwen2.5-coder:7b",
        "inference_backend": "ollama",
    }

    baseline_runner.run_baseline_inference(
        records=records,
        output_path=output_path,
        generator="qwen",
        generate_fn=lambda _prompt: "Here is the SQL:\n```sql\nSELECT 1;\n```",
        model_metadata=metadata,
        prompt_setting="zero_shot",
    )

    row = json.loads(output_path.read_text(encoding="utf-8").strip())

    assert row["generated_sql"] == "SELECT 1;"
    assert row["raw_output"] == "Here is the SQL:\n```sql\nSELECT 1;\n```"
    assert row["model_metadata"] == metadata
    assert row["status"] == "success"
