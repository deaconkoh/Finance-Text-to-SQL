from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.eval.evaluate_verifier_diagnostics import DiagnosticRow, metric_table, mode_stats


def diagnostic_row(group: str, answers_question: bool | None) -> DiagnosticRow:
    return DiagnosticRow(
        path=Path("verified_group_sample_probe.jsonl"),
        mode="probe",
        group=group,
        record={
            "verification": {
                "answers_question": answers_question,
                "should_abstain": answers_question is None,
            }
        },
    )


def test_mode_stats_computes_global_detection_counts() -> None:
    rows = [
        diagnostic_row("A_correct_executable", True),
        diagnostic_row("A_correct_executable", False),
        diagnostic_row("B_wrong_executable", False),
        diagnostic_row("B_wrong_executable", True),
        diagnostic_row("B_wrong_executable", None),
    ]

    stats = mode_stats(rows)

    assert stats["detection_tp"] == 1
    assert stats["detection_fp"] == 1
    assert stats["detection_fn"] == 2
    assert stats["detection_tn"] == 1
    assert stats["detection_precision"] == pytest.approx(0.5)
    assert stats["detection_recall"] == pytest.approx(1 / 3)
    assert stats["detection_f1"] == pytest.approx(0.4)


def test_metric_table_uses_detection_metrics_and_keeps_group_debug_metrics() -> None:
    rows = [
        diagnostic_row("A_correct_executable", True),
        diagnostic_row("A_correct_executable", False),
        diagnostic_row("B_wrong_executable", False),
        diagnostic_row("B_wrong_executable", True),
        diagnostic_row("B_wrong_executable", None),
    ]

    table = metric_table(rows)

    assert "Detection Precision" in table
    assert "Detection Recall" in table
    assert "Detection F1" in table
    assert "Macro F1" not in table
    assert "Accept Precision" in table
    assert "Reject Precision" in table
    assert "1/2 (50.0%) | 1/3 (33.3%) | 40.0%" in table
