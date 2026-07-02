from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.finverisql.verifier import (
    build_execution_error_profile,
    build_non_executable_verification_result,
)


def test_group_c_execution_error_profile_and_verification_are_deterministic() -> None:
    profile = build_execution_error_profile(
        generated_sql="SELECT bad_column FROM invoices;",
        execution_error="no such column: bad_column",
        error_source="generated_error",
    )
    parsed_profile = json.loads(profile)

    assert parsed_profile["status"] == "EXECUTION_ERROR"
    assert parsed_profile["profile_type"] == "execution_error"
    assert parsed_profile["execution_error"] == "no such column: bad_column"
    assert parsed_profile["error_source"] == "generated_error"

    verification = build_non_executable_verification_result(
        "no such column: bad_column"
    ).to_dict()

    assert verification["answers_question"] is False
    assert verification["mismatch_type"] == "non_executable_error"
    assert verification["should_abstain"] is False
    assert verification["stage2_evidence_match"] == "insufficient"
    assert verification["stage2_primary_mismatch_type"] == "non_executable_error"
    assert "no such column: bad_column" in verification["stage2_failed_evidence"][0]
