from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.utils.resumable_jsonl import (
    append_jsonl_durable,
    exclusive_jsonl_run,
    recover_incomplete_jsonl_tail,
)


def test_recover_incomplete_tail_preserves_prior_rows(tmp_path: Path) -> None:
    output = tmp_path / "results.jsonl"
    append_jsonl_durable(output, {"question_id": "q1"})
    with output.open("ab") as handle:
        handle.write(b'{"question_id":"q2"')

    recover_incomplete_jsonl_tail(output)
    append_jsonl_durable(output, {"question_id": "q2"})

    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert rows == [{"question_id": "q1"}, {"question_id": "q2"}]


def test_valid_unterminated_final_record_is_not_discarded(tmp_path: Path) -> None:
    output = tmp_path / "results.jsonl"
    output.write_text('{"question_id":"q1"}', encoding="utf-8")

    recover_incomplete_jsonl_tail(output)
    append_jsonl_durable(output, {"question_id": "q2"})

    assert [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()] == [
        {"question_id": "q1"},
        {"question_id": "q2"},
    ]


def test_output_lock_rejects_second_runner(tmp_path: Path) -> None:
    output = tmp_path / "results.jsonl"
    with exclusive_jsonl_run((output,)):
        with pytest.raises(RuntimeError, match="output lock"):
            with exclusive_jsonl_run((output,)):
                pass
