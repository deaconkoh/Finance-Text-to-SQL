from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.eval.evaluate_asa import (
    filter_joined_primary_metric_rows,
    filter_primary_metric_rows,
)


def test_filter_primary_metric_rows_removes_group_d_and_excluded_rows() -> None:
    by_id = {
        "a": {"question_id": "a", "evaluation_group": "A_correct_executable"},
        "d": {"question_id": "d", "evaluation_group": "D_ambiguous"},
        "x": {"question_id": "x", "excluded_from_primary_metrics": True},
    }

    kept, filtered = filter_primary_metric_rows(by_id, ["a", "d", "x"])

    assert kept == ["a"]
    assert filtered == 2


def test_filter_joined_primary_metric_rows_removes_group_d_from_either_side() -> None:
    before_by_id = {
        "a": {"question_id": "a", "evaluation_group": "A_correct_executable"},
        "b": {"question_id": "b", "evaluation_group": "B_wrong_executable"},
        "c": {"question_id": "c", "evaluation_group": "D_ambiguous"},
    }
    after_by_id = {
        "a": {"question_id": "a", "evaluation_group": "A_correct_executable"},
        "b": {"question_id": "b", "excluded_from_primary_metrics": True},
        "c": {"question_id": "c", "evaluation_group": "A_correct_executable"},
    }

    kept, filtered = filter_joined_primary_metric_rows(
        before_by_id,
        after_by_id,
        ["a", "b", "c"],
    )

    assert kept == ["a"]
    assert filtered == 2

