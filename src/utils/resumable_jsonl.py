"""Durable JSONL helpers for single-writer resumable experiment runners."""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence


def recover_incomplete_jsonl_tail(path: str | Path) -> None:
    """Remove only an invalid unterminated final JSONL record, if present."""
    output_path = Path(path)
    if not output_path.exists() or output_path.stat().st_size == 0:
        return

    data = output_path.read_bytes()
    if data.endswith(b"\n"):
        return

    last_newline = data.rfind(b"\n")
    tail = data[last_newline + 1 :]
    try:
        json.loads(tail.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        with output_path.open("r+b") as handle:
            handle.truncate(last_newline + 1)
            handle.flush()
            os.fsync(handle.fileno())
    else:
        # A valid record without a newline is complete, but must be terminated
        # before later appends so the next record cannot be concatenated to it.
        with output_path.open("ab") as handle:
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())


def append_jsonl_durable(path: str | Path, row: dict[str, Any]) -> None:
    """Append one complete JSONL record and persist it before returning."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    with output_path.open("ab") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


@contextmanager
def exclusive_jsonl_run(paths: Sequence[str | Path]) -> Iterator[None]:
    """Prevent concurrent runners from writing the same experiment artifacts."""
    lock_handles = []
    try:
        for path in sorted({str(Path(path)) for path in paths}):
            lock_path = Path(f"{path}.lock")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_path.open("a+", encoding="utf-8")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                handle.close()
                raise RuntimeError(
                    f"Another FinVeriSQL runner already holds the output lock: {lock_path}"
                ) from exc
            lock_handles.append(handle)
        yield
    finally:
        for handle in reversed(lock_handles):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
