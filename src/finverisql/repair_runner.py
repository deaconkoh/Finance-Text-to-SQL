"""Orchestration helpers for additive FinVeriSQL repair experiments."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sqlglot import exp, parse_one

from src.finverisql.compact_semantic_profile import build_verifier_payload
from finverisql.ablation.generic_repair_wo_scope_constraint import (
    GenericSemanticRepairRequest,
    repair_generic_semantic_sql,
)
from src.finverisql.repair import (
    NonExecutableRepairRequest,
    SemanticRepairRequest,
    SemanticRepairResult,
    repair_non_executable_sql,
    repair_semantic_sql,
)
from src.finverisql.schema_loader import SchemaAnnotationStore
from src.finverisql.sql_parser import parse_sql
from src.finverisql.sql_semantic_mapping import build_sql_financial_semantics
from src.finverisql.verifier import VerificationResult, verify_execution_profile


GROUP_B = "B_wrong_executable"
GROUP_C = "C_non_executable"
GROUP_A = "A_correct_executable"
ACTIVE_REPAIR_MODES = (
    "financial_measure_error",
    "financial_object_error",
    "computation_logic_error",
)
MAX_SPECIALIZED_REPAIR_ATTEMPTS = 3
MAX_GENERIC_REPAIR_ATTEMPTS = 3
CLAUSES = ("SELECT", "FROM", "JOIN", "WHERE", "GROUP BY", "HAVING", "ORDER BY", "LIMIT")
GROUPED_OUTPUT_QUESTION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bby\b", "question token: by"),
    (r"\bper\b", "question token: per"),
    (r"\beach\b", "question token: each"),
    (r"\bcompare\b", "question token: compare"),
    (r"\bbreakdown\b", "question token: breakdown"),
    (r"\btrend\b", "question token: trend"),
    (r"\bmonthly\b", "question token: monthly"),
    (r"\bweekly\b", "question token: weekly"),
    (r"\bdaily\b", "question token: daily"),
    (r"\bquarterly\b", "question token: quarterly"),
    (r"\byearly\b", "question token: yearly"),
    (r"\btop\b", "question token: top"),
    (r"\bbiggest\b", "question token: biggest"),
    (r"\blowest\b", "question token: lowest"),
    (r"\bhighest\b", "question token: highest"),
    (r"\bwhich\s+(account|customer|vendor|product)\b", "question phrase: which dimension"),
    (r"\bwhat\s+(account|customer|vendor|product)\b", "question phrase: what dimension"),
)


@dataclass(frozen=True)
class RepairScopePolicy:
    repair_mode: str
    allowed_clause_changes: tuple[str, ...]
    disallowed_clause_changes: tuple[str, ...]


@dataclass(frozen=True)
class ScopeValidationResult:
    status: str
    changed_clauses: tuple[str, ...]
    violated_clauses: tuple[str, ...]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "changed_clauses": list(self.changed_clauses),
            "violated_clauses": list(self.violated_clauses),
            "error": self.error,
        }


@dataclass(frozen=True)
class ScalarGroupByGateResult:
    status: str | None
    requires_grouped_output: bool | None
    grouped_output_evidence: tuple[str, ...]
    error: str | None = None

    def to_attempt_fields(self) -> dict[str, Any]:
        return {
            "scalar_group_by_gate_status": self.status,
            "scalar_group_by_gate_error": self.error,
            "requires_grouped_output": self.requires_grouped_output,
            "grouped_output_evidence": list(self.grouped_output_evidence),
        }


REPAIR_SCOPE_POLICIES: dict[str, RepairScopePolicy] = {
    "financial_measure_error": RepairScopePolicy(
        repair_mode="financial_measure_error",
        allowed_clause_changes=("SELECT",),
        disallowed_clause_changes=(
            "FROM",
            "JOIN",
            "WHERE",
            "GROUP BY",
            "HAVING",
            "ORDER BY",
            "LIMIT",
        ),
    ),
    "financial_object_error": RepairScopePolicy(
        repair_mode="financial_object_error",
        allowed_clause_changes=("WHERE",),
        disallowed_clause_changes=(
            "SELECT",
            "FROM",
            "JOIN",
            "GROUP BY",
            "HAVING",
            "ORDER BY",
            "LIMIT",
        ),
    ),
    "computation_logic_error": RepairScopePolicy(
        repair_mode="computation_logic_error",
        allowed_clause_changes=(
            "SELECT",
            "GROUP BY",
            "HAVING",
            "ORDER BY",
            "LIMIT",
            "WHERE",
        ),
        disallowed_clause_changes=("FROM", "JOIN"),
    ),
}


TEMPORAL_TOKENS = {
    "date",
    "time",
    "year",
    "month",
    "day",
    "week",
    "quarter",
    "fiscal",
    "period",
    "posted",
    "posting",
    "created",
    "updated",
    "transaction_date",
    "invoice_date",
    "due_date",
    "payment_date",
}


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    path = Path(path)

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()

            if not text:
                continue

            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSONL at {path}, line {line_number}: {exc}"
                ) from exc

            if not isinstance(row, dict):
                raise ValueError(
                    f"Expected JSON object at {path}, line {line_number}, "
                    f"got {type(row).__name__}."
                )

            rows.append(row)

    return rows


def append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def stable_sql_hash(sql: Any) -> str:
    return hashlib.sha256(str(sql or "").encode("utf-8")).hexdigest()[:16]


def to_jsonable(obj: Any) -> Any:
    if obj is None:
        return None

    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return to_jsonable(obj.to_dict())

    if isinstance(obj, dict):
        return {str(key): to_jsonable(value) for key, value in obj.items()}

    if isinstance(obj, list):
        return [to_jsonable(value) for value in obj]

    if isinstance(obj, tuple):
        return [to_jsonable(value) for value in obj]

    if isinstance(obj, (str, int, float, bool)):
        return obj

    return str(obj)


def render_json_profile(profile: dict[str, Any]) -> str:
    return json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True)


def build_execution_profile(
    generated_sql: str,
    schema_store: SchemaAnnotationStore,
    profile_mode: str,
) -> str:
    try:
        parsed_sql = parse_sql(generated_sql)
        parsed_dict = to_jsonable(parsed_sql)

        if profile_mode == "ast":
            return render_json_profile(
                {
                    "status": "OK",
                    "profile_type": "parsed_ast",
                    "parsed_sql": parsed_dict,
                }
            )

        semantics = build_sql_financial_semantics(
            parsed_sql=parsed_sql,
            schema_store=schema_store,
        )
        semantic_dict = to_jsonable(semantics)

        if profile_mode == "semantic":
            return render_json_profile(
                {
                    "status": "OK",
                    "profile_type": "semantic_profile",
                    **semantic_dict,
                }
            )

        if profile_mode == "compact":
            compact_payload = build_verifier_payload(semantics)
            return render_json_profile(
                {
                    "status": "OK",
                    "profile_type": "compact_semantic_profile",
                    **compact_payload,
                }
            )

        raise ValueError(f"Unsupported profile_mode: {profile_mode}")

    except Exception as exc:
        return render_json_profile(
            {
                "status": "PARSE_ERROR",
                "profile_type": profile_mode,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "warnings": [
                    "SQL parse/profile pipeline failed before verification: "
                    f"{type(exc).__name__}: {exc}"
                ],
            }
        )


def detect_profile_status(execution_profile: str) -> str | None:
    try:
        parsed = json.loads(execution_profile)
    except Exception:
        return None

    if not isinstance(parsed, dict):
        return None

    extraction = parsed.get("profile_extraction") or {}
    return parsed.get("status") or extraction.get("status")


def route_mismatch_to_repair_mode(
    mismatch_type: str | None,
    attempted_error_classes: set[str] | None = None,
) -> tuple[str | None, str | None]:
    attempted_error_classes = attempted_error_classes or set()
    mismatch = str(mismatch_type or "").strip()

    if mismatch not in ACTIVE_REPAIR_MODES:
        return None, "unsupported_mismatch_type"

    if mismatch in attempted_error_classes:
        return None, "same_error_persisted_after_repair"

    return mismatch, None


def _is_profile_ok(execution_profile: str) -> bool:
    return detect_profile_status(execution_profile) == "OK"


def _normalise_sql_fragment(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalise_sql_fragment(item) for item in value]

    if value is None:
        return None

    text = str(value).strip().rstrip(";")
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def _parse_tree_or_error(sql: str) -> tuple[exp.Expression | None, str | None]:
    if not str(sql or "").strip():
        return None, "repaired SQL is empty"

    parsed = parse_sql(sql)
    if parsed.parse_error:
        return None, parsed.parse_error

    try:
        return parse_one(sql, read="sqlite"), None
    except Exception as exc:
        return None, str(exc)


def _clause_map(tree: exp.Expression) -> dict[str, Any]:
    select_expr = tree.find(exp.Select)
    from_expr = tree.find(exp.From)
    where_expr = tree.find(exp.Where)
    group_expr = tree.find(exp.Group)
    having_expr = tree.find(exp.Having)
    order_expr = tree.find(exp.Order)
    limit_expr = tree.find(exp.Limit)

    return {
        "SELECT": [
            expression.sql(dialect="sqlite")
            for expression in (select_expr.expressions if select_expr else [])
        ],
        "FROM": from_expr.sql(dialect="sqlite") if from_expr else None,
        "JOIN": [
            join_expr.sql(dialect="sqlite")
            for join_expr in tree.find_all(exp.Join)
        ],
        "WHERE": where_expr.this.sql(dialect="sqlite") if where_expr else None,
        "GROUP BY": [
            expression.sql(dialect="sqlite")
            for expression in (group_expr.expressions if group_expr else [])
        ],
        "HAVING": having_expr.this.sql(dialect="sqlite") if having_expr else None,
        "ORDER BY": [
            expression.sql(dialect="sqlite")
            for expression in (order_expr.expressions if order_expr else [])
        ],
        "LIMIT": limit_expr.expression.sql(dialect="sqlite") if limit_expr and limit_expr.expression else None,
    }


def _extract_slot(intent_representation: dict[str, Any], slot_name: str) -> dict[str, Any]:
    direct = intent_representation.get(slot_name)
    if isinstance(direct, dict):
        return direct

    slots = intent_representation.get("slots")
    if isinstance(slots, dict) and isinstance(slots.get(slot_name), dict):
        return slots[slot_name]

    return {}


def detect_grouped_output_requirement(
    question: str,
    intent_representation: dict[str, Any] | None,
) -> tuple[bool, tuple[str, ...]]:
    intent_representation = intent_representation or {}
    evidence: list[str] = []
    lowered_question = str(question or "").lower()

    time_slot = _extract_slot(intent_representation, "time")
    if time_slot.get("requires_group_by_period") is True:
        evidence.append("intent.slots.time.requires_group_by_period")

    operation_slot = _extract_slot(intent_representation, "operation")
    group_by = operation_slot.get("group_by")
    if isinstance(group_by, list) and group_by:
        evidence.append("intent.slots.operation.group_by")

    comparison = operation_slot.get("comparison")
    if isinstance(comparison, dict) and comparison.get("required") is True:
        evidence.append("intent.slots.operation.comparison.required")

    order_by = operation_slot.get("order_by")
    limit = operation_slot.get("limit")
    if isinstance(order_by, list) and order_by:
        evidence.append("intent.slots.operation.order_by")
    if limit not in (None, "", "none", "null"):
        evidence.append("intent.slots.operation.limit")

    for pattern, label in GROUPED_OUTPUT_QUESTION_PATTERNS:
        if re.search(pattern, lowered_question):
            evidence.append(label)

    return bool(evidence), tuple(dict.fromkeys(evidence))


def _group_by_added_or_changed(before_sql: str, after_sql: str) -> bool:
    before_tree, before_error = _parse_tree_or_error(before_sql)
    after_tree, after_error = _parse_tree_or_error(after_sql)

    if before_error:
        raise ValueError(f"Could not parse original SQL: {before_error}")

    if after_error:
        raise ValueError(f"Could not parse repaired SQL: {after_error}")

    assert before_tree is not None
    assert after_tree is not None
    before_group_by = _normalise_sql_fragment(_clause_map(before_tree).get("GROUP BY"))
    after_group_by = _normalise_sql_fragment(_clause_map(after_tree).get("GROUP BY"))

    return bool(after_group_by) and before_group_by != after_group_by


def apply_scalar_group_by_gate(
    before_sql: str,
    after_sql: str,
    repair_mode: str,
    question: str,
    intent_representation: dict[str, Any] | None,
) -> ScalarGroupByGateResult:
    if repair_mode != "computation_logic_error":
        return ScalarGroupByGateResult(
            status=None,
            requires_grouped_output=None,
            grouped_output_evidence=(),
            error=None,
        )

    requires_grouped_output, evidence = detect_grouped_output_requirement(
        question=question,
        intent_representation=intent_representation,
    )

    try:
        group_by_changed = _group_by_added_or_changed(before_sql, after_sql)
    except Exception as exc:
        return ScalarGroupByGateResult(
            status="rejected",
            requires_grouped_output=requires_grouped_output,
            grouped_output_evidence=evidence,
            error=str(exc),
        )

    if not group_by_changed:
        return ScalarGroupByGateResult(
            status="not_applicable",
            requires_grouped_output=requires_grouped_output,
            grouped_output_evidence=evidence,
            error=None,
        )

    if requires_grouped_output:
        return ScalarGroupByGateResult(
            status="accepted",
            requires_grouped_output=True,
            grouped_output_evidence=evidence,
            error=None,
        )

    return ScalarGroupByGateResult(
        status="rejected",
        requires_grouped_output=False,
        grouped_output_evidence=evidence,
        error=(
            "Computation logic repair changed GROUP BY, but the question and "
            "intent do not explicitly require grouped output."
        ),
    )


def compute_changed_clauses(before_sql: str, after_sql: str) -> tuple[str, ...]:
    before_tree, before_error = _parse_tree_or_error(before_sql)
    after_tree, after_error = _parse_tree_or_error(after_sql)

    if before_error:
        raise ValueError(f"Could not parse original SQL: {before_error}")

    if after_error:
        raise ValueError(f"Could not parse repaired SQL: {after_error}")

    assert before_tree is not None
    assert after_tree is not None
    before = _clause_map(before_tree)
    after = _clause_map(after_tree)

    return tuple(
        clause
        for clause in CLAUSES
        if _normalise_sql_fragment(before.get(clause))
        != _normalise_sql_fragment(after.get(clause))
    )


def _predicate_key(expression: str) -> str:
    return str(_normalise_sql_fragment(expression) or "")


def _is_temporal_predicate(expression: str) -> bool:
    lowered = expression.lower()
    tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", lowered))

    if tokens & TEMPORAL_TOKENS:
        return True

    return any(token in lowered for token in TEMPORAL_TOKENS)


def _changed_where_predicates(before_sql: str, after_sql: str) -> list[str]:
    before = parse_sql(before_sql)
    after = parse_sql(after_sql)
    before_filters = {_predicate_key(item.expression): item.expression for item in before.filters}
    after_filters = {_predicate_key(item.expression): item.expression for item in after.filters}
    changed_keys = set(before_filters) ^ set(after_filters)

    return [
        before_filters.get(key) or after_filters[key]
        for key in sorted(changed_keys)
    ]


def _select_aliases(tree: exp.Expression) -> set[str]:
    select_expr = tree.find(exp.Select)
    aliases: set[str] = set()

    if select_expr is None:
        return aliases

    for expression in select_expr.expressions:
        alias = expression.alias_or_name
        if alias:
            aliases.add(alias.lower())

    return aliases


def _order_by_identifiers(tree: exp.Expression) -> set[str]:
    order_expr = tree.find(exp.Order)
    identifiers: set[str] = set()

    if order_expr is None:
        return identifiers

    for expression in order_expr.expressions:
        for column in expression.find_all(exp.Column):
            if column.name:
                identifiers.add(column.name.lower())

    return identifiers


def _removed_order_by_aliases(before_sql: str, after_sql: str) -> set[str]:
    before_tree, before_error = _parse_tree_or_error(before_sql)
    after_tree, after_error = _parse_tree_or_error(after_sql)

    if before_error or after_error or before_tree is None or after_tree is None:
        return set()

    removed_aliases = _select_aliases(before_tree) - _select_aliases(after_tree)
    unchanged_order_identifiers = (
        _order_by_identifiers(before_tree) & _order_by_identifiers(after_tree)
    )
    return removed_aliases & unchanged_order_identifiers


def validate_repair_scope(
    before_sql: str,
    after_sql: str,
    repair_mode: str,
) -> ScopeValidationResult:
    policy = REPAIR_SCOPE_POLICIES.get(repair_mode)

    if policy is None:
        return ScopeValidationResult(
            status="rejected",
            changed_clauses=(),
            violated_clauses=(),
            error=f"Unsupported repair mode: {repair_mode}",
        )

    if not str(after_sql or "").strip():
        return ScopeValidationResult(
            status="rejected",
            changed_clauses=(),
            violated_clauses=(),
            error="repaired SQL is empty",
        )

    try:
        changed_clauses = compute_changed_clauses(before_sql, after_sql)
    except Exception as exc:
        return ScopeValidationResult(
            status="rejected",
            changed_clauses=(),
            violated_clauses=(),
            error=str(exc),
        )

    violated = tuple(
        clause
        for clause in changed_clauses
        if clause in set(policy.disallowed_clause_changes)
    )

    if violated:
        return ScopeValidationResult(
            status="rejected",
            changed_clauses=changed_clauses,
            violated_clauses=violated,
            error=f"Disallowed clause changes: {', '.join(violated)}",
        )

    if repair_mode == "computation_logic_error" and "WHERE" in changed_clauses:
        changed_predicates = _changed_where_predicates(before_sql, after_sql)
        non_temporal = [
            predicate
            for predicate in changed_predicates
            if not _is_temporal_predicate(predicate)
        ]

        if non_temporal:
            return ScopeValidationResult(
                status="rejected",
                changed_clauses=changed_clauses,
                violated_clauses=("WHERE",),
                error=(
                    "Computation logic WHERE changes must be temporal/date-related; "
                    f"non-temporal predicates: {non_temporal}"
                ),
            )

    if "SELECT" in changed_clauses and "ORDER BY" not in changed_clauses:
        removed_aliases = _removed_order_by_aliases(before_sql, after_sql)

        if removed_aliases:
            return ScopeValidationResult(
                status="rejected",
                changed_clauses=changed_clauses,
                violated_clauses=("SELECT",),
                error=(
                    "SELECT change removed aliases still referenced by ORDER BY: "
                    f"{sorted(removed_aliases)}"
                ),
            )

    return ScopeValidationResult(
        status="accepted",
        changed_clauses=changed_clauses,
        violated_clauses=(),
        error=None,
    )


def classify_candidate_row(row: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    group = row.get("evaluation_group")

    if group in {GROUP_A, GROUP_B}:
        return _classify_semantic_candidate(row)

    if group == GROUP_C:
        return _classify_group_c_candidate(row)

    return False, None, "unsupported_evaluation_group"


def _classify_semantic_candidate(row: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    if row.get("status") not in {None, "success"}:
        return False, None, "upstream_verification_failed"

    verification = row.get("verification")

    if not isinstance(verification, dict):
        return False, None, "missing_verification"

    if verification.get("answers_question") is not False:
        return False, None, "verification_not_rejected"

    if verification.get("should_abstain") is True:
        return False, None, "verification_abstained"

    if not verification.get("mismatch_type"):
        return False, None, "missing_mismatch_type"

    failed_evidence = verification.get("stage2_failed_evidence")
    if not isinstance(failed_evidence, list) or not failed_evidence:
        return False, None, "missing_failed_evidence"

    if not str(verification.get("repair_hint") or "").strip():
        return False, None, "missing_repair_hint"

    if not isinstance(row.get("intent_representation"), dict):
        return False, None, "missing_intent_representation"

    if not row.get("execution_profile"):
        return False, None, "missing_execution_profile"

    return True, "semantic", None


def _classify_group_c_candidate(row: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    generated_sql = str(row.get("generated_sql") or "").strip()
    question = str(row.get("question") or "").strip()

    if not generated_sql:
        return False, None, "missing_generated_sql"

    if not question:
        return False, None, "missing_question"

    return True, "non_executable", None


def build_semantic_repair_request(
    row: dict[str, Any],
    schema_text: str | None = None,
) -> SemanticRepairRequest:
    verification = row.get("verification") or {}

    return SemanticRepairRequest(
        question_id=str(row.get("question_id") or row.get("id") or ""),
        question=str(row.get("question") or ""),
        generated_sql=str(row.get("generated_sql") or ""),
        intent_representation=row.get("intent_representation") or {},
        execution_profile=row.get("execution_profile") or {},
        primary_mismatch_type=str(verification.get("mismatch_type") or ""),
        mismatch_detail=verification.get("mismatch_detail"),
        failed_evidence=verification.get("stage2_failed_evidence") or [],
        repair_hint=verification.get("repair_hint"),
        diagnostic_dimensions=verification.get("stage2_diagnostic_dimensions"),
        confidence=verification.get("confidence"),
        schema_text=schema_text,
    )


def _build_chain_repair_request(
    row: dict[str, Any],
    verification: dict[str, Any],
    current_sql: str,
    original_sql: str,
    execution_profile: str,
    repair_mode: str,
    policy: RepairScopePolicy,
    schema_text: str | None,
) -> SemanticRepairRequest:
    return SemanticRepairRequest(
        question_id=str(row.get("question_id") or row.get("id") or ""),
        question=str(row.get("question") or ""),
        generated_sql=original_sql,
        current_sql=current_sql,
        original_sql=original_sql,
        repair_mode=repair_mode,
        allowed_clause_changes=list(policy.allowed_clause_changes),
        disallowed_clause_changes=list(policy.disallowed_clause_changes),
        intent_representation=row.get("intent_representation") or {},
        execution_profile=execution_profile,
        primary_mismatch_type=str(verification.get("mismatch_type") or ""),
        mismatch_detail=verification.get("mismatch_detail"),
        failed_evidence=verification.get("stage2_failed_evidence") or [],
        repair_hint=verification.get("repair_hint"),
        diagnostic_dimensions=verification.get("stage2_diagnostic_dimensions"),
        confidence=verification.get("confidence"),
        schema_text=schema_text,
    )


def _build_generic_chain_repair_request(
    row: dict[str, Any],
    verification: dict[str, Any],
    current_sql: str,
    original_sql: str,
    execution_profile: str,
    schema_text: str | None,
) -> GenericSemanticRepairRequest:
    return GenericSemanticRepairRequest(
        question_id=str(row.get("question_id") or row.get("id") or ""),
        question=str(row.get("question") or ""),
        original_sql=original_sql,
        current_sql=current_sql,
        intent_representation=row.get("intent_representation") or {},
        execution_profile=execution_profile,
        mismatch_type=verification.get("mismatch_type"),
        mismatch_detail=verification.get("mismatch_detail"),
        failed_evidence=verification.get("stage2_failed_evidence") or [],
        repair_hint=verification.get("repair_hint"),
        diagnostic_dimensions=verification.get("stage2_diagnostic_dimensions"),
        confidence=verification.get("confidence"),
        schema_text=schema_text,
    )


def _verification_to_dict(verification: VerificationResult | dict[str, Any]) -> dict[str, Any]:
    if hasattr(verification, "to_dict") and callable(verification.to_dict):
        return to_jsonable(verification.to_dict())

    return to_jsonable(verification)


def _is_reverification_failed_or_abstained(verification: dict[str, Any]) -> bool:
    if verification.get("should_abstain") is True:
        return True

    if verification.get("error"):
        return True

    return verification.get("answers_question") is None


def run_specialized_semantic_repair_chain(
    row: dict[str, Any],
    schema_text: str | None,
    schema_store: SchemaAnnotationStore,
    repair_generate_fn: Callable[[str], str],
    verifier_generate_fn: Callable[[str], str],
    profile_mode: str = "compact",
    probing_mode: str = "probe",
    max_probes: int = 7,
) -> dict[str, Any]:
    original_sql = str(row.get("generated_sql") or "")
    current_sql = original_sql
    original_verification = row.get("verification") if isinstance(row.get("verification"), dict) else {}
    current_verification = dict(original_verification)
    current_execution_profile = row.get("execution_profile") or build_execution_profile(
        generated_sql=current_sql,
        schema_store=schema_store,
        profile_mode=profile_mode,
    )
    initial_mismatch_type = current_verification.get("mismatch_type")
    attempted_error_classes: set[str] = set()
    repair_attempt_sequence: list[dict[str, Any]] = []
    previous_sql_versions = [original_sql]
    final_repaired_sql: str | None = None
    final_sql_source = "original_generated_sql"
    stop_reason = "max_attempts_reached"
    final_verification = dict(current_verification)

    while len(attempted_error_classes) < MAX_SPECIALIZED_REPAIR_ATTEMPTS:
        repair_mode, route_stop_reason = route_mismatch_to_repair_mode(
            current_verification.get("mismatch_type"),
            attempted_error_classes,
        )

        if repair_mode is None:
            stop_reason = route_stop_reason or "unsupported_mismatch_type"
            break

        if not str(current_verification.get("repair_hint") or "").strip():
            stop_reason = "repair_not_attempted_missing_hint"
            break

        policy = REPAIR_SCOPE_POLICIES[repair_mode]
        repair_request = _build_chain_repair_request(
            row=row,
            verification=current_verification,
            current_sql=current_sql,
            original_sql=original_sql,
            execution_profile=current_execution_profile,
            repair_mode=repair_mode,
            policy=policy,
            schema_text=schema_text,
        )
        repair_result = repair_semantic_sql(repair_request, repair_generate_fn)
        attempted_error_classes.add(repair_mode)
        attempt: dict[str, Any] = {
            "attempt_index": len(repair_attempt_sequence) + 1,
            "repair_mode": repair_mode,
            "current_sql_before_attempt": current_sql,
            "repair_request": repair_request.to_dict(),
            "repair_result": repair_result.to_dict(),
            "repaired_sql_after_attempt": repair_result.repaired_sql,
            "allowed_clause_changes": list(policy.allowed_clause_changes),
            "disallowed_clause_changes": list(policy.disallowed_clause_changes),
            "verifier_evidence_used": {
                "mismatch_type": current_verification.get("mismatch_type"),
                "mismatch_detail": current_verification.get("mismatch_detail"),
                "repair_hint": current_verification.get("repair_hint"),
                "stage2_failed_evidence": current_verification.get("stage2_failed_evidence"),
            },
        }

        if repair_result.status != "success" or not repair_result.repaired_sql:
            attempt.update(
                {
                    "scope_check_status": None,
                    "scope_check_error": None,
                    "clause_change_summary": None,
                    "routing_decision": "repair_failed_to_generate_sql",
                }
            )
            repair_attempt_sequence.append(attempt)
            stop_reason = "repair_failed_to_generate_sql"
            break

        scope_check = validate_repair_scope(
            before_sql=current_sql,
            after_sql=repair_result.repaired_sql,
            repair_mode=repair_mode,
        )
        attempt.update(
            {
                "scope_check": scope_check.to_dict(),
                "scope_check_status": scope_check.status,
                "scope_check_error": scope_check.error,
                "clause_change_summary": list(scope_check.changed_clauses),
                "scalar_group_by_gate_status": None,
                "scalar_group_by_gate_error": None,
                "requires_grouped_output": None,
                "grouped_output_evidence": [],
            }
        )

        if scope_check.status != "accepted":
            attempt["routing_decision"] = "repair_rejected_by_scope_check"
            repair_attempt_sequence.append(attempt)
            stop_reason = "repair_rejected_by_scope_check"
            break

        scalar_group_by_gate = apply_scalar_group_by_gate(
            before_sql=current_sql,
            after_sql=repair_result.repaired_sql,
            repair_mode=repair_mode,
            question=str(row.get("question") or ""),
            intent_representation=row.get("intent_representation") or {},
        )
        attempt.update(scalar_group_by_gate.to_attempt_fields())

        if scalar_group_by_gate.status == "rejected":
            attempt["routing_decision"] = "repair_rejected_by_scalar_group_by_gate"
            repair_attempt_sequence.append(attempt)
            stop_reason = "repair_rejected_by_scalar_group_by_gate"
            break

        current_sql = repair_result.repaired_sql
        previous_sql_versions.append(current_sql)
        current_execution_profile = build_execution_profile(
            generated_sql=current_sql,
            schema_store=schema_store,
            profile_mode=profile_mode,
        )
        reverified = verify_execution_profile(
            question=str(row.get("question") or ""),
            execution_profile=current_execution_profile,
            llm_generate_fn=verifier_generate_fn,
            intent_representation=row.get("intent_representation") or {},
            probing_mode=probing_mode,
            max_probes=max_probes,
            profile_mode=profile_mode,
        )
        final_verification = _verification_to_dict(reverified)
        current_verification = dict(final_verification)
        attempt.update(
            {
                "reverification_mismatch_type": current_verification.get("mismatch_type"),
                "reverification_answers_question": current_verification.get("answers_question"),
                "reverification_results": current_verification,
                "repaired_execution_profile": current_execution_profile,
            }
        )
        repair_attempt_sequence.append(attempt)

        if _is_reverification_failed_or_abstained(current_verification):
            stop_reason = "reverification_failed_or_abstained"
            break

        if current_verification.get("answers_question") is True:
            final_repaired_sql = current_sql
            final_sql_source = "specialized_chain_repair"
            stop_reason = "verifier_accepts_after_repair"
            break

        next_mismatch_type = current_verification.get("mismatch_type")

        if len(attempted_error_classes) >= MAX_SPECIALIZED_REPAIR_ATTEMPTS:
            stop_reason = "all_error_classes_attempted"
            break

        if next_mismatch_type in attempted_error_classes:
            stop_reason = "same_error_persisted_after_repair"
            break

        next_mode, next_stop_reason = route_mismatch_to_repair_mode(
            next_mismatch_type,
            attempted_error_classes,
        )

        if next_mode is None:
            stop_reason = next_stop_reason or "unsupported_mismatch_type"
            break

        attempt["routing_decision"] = "new_error_detected_and_routed"

    else:
        stop_reason = "max_attempts_reached"

    last_attempt = repair_attempt_sequence[-1] if repair_attempt_sequence else {}

    return {
        "initial_mismatch_type": initial_mismatch_type,
        "repair_attempt_sequence": repair_attempt_sequence,
        "attempted_error_classes": list(attempted_error_classes),
        "repair_mode_used": last_attempt.get("repair_mode"),
        "current_sql_before_each_attempt": [
            attempt.get("current_sql_before_attempt")
            for attempt in repair_attempt_sequence
        ],
        "repaired_sql_after_each_attempt": [
            attempt.get("repaired_sql_after_attempt")
            for attempt in repair_attempt_sequence
        ],
        "reverification_mismatch_type": last_attempt.get("reverification_mismatch_type"),
        "reverification_answers_question": last_attempt.get("reverification_answers_question"),
        "reverification_results": last_attempt.get("reverification_results"),
        "stop_reason": stop_reason,
        "final_sql_source": final_sql_source,
        "num_repair_attempts": len(repair_attempt_sequence),
        "clause_change_summary": last_attempt.get("clause_change_summary"),
        "scope_check_status": last_attempt.get("scope_check_status"),
        "scope_check_error": last_attempt.get("scope_check_error"),
        "allowed_clause_changes": last_attempt.get("allowed_clause_changes"),
        "disallowed_clause_changes": last_attempt.get("disallowed_clause_changes"),
        "original_generated_sql": original_sql,
        "previous_sql_versions": previous_sql_versions,
        "final_repaired_sql": final_repaired_sql,
        "final_verification": final_verification,
        "final_execution_profile": current_execution_profile,
    }


def run_specialized_first_repair_no_reverification(
    row: dict[str, Any],
    schema_text: str | None,
    schema_store: SchemaAnnotationStore,
    repair_generate_fn: Callable[[str], str],
    profile_mode: str = "compact",
) -> dict[str, Any]:
    original_sql = str(row.get("generated_sql") or "")
    current_verification = (
        dict(row.get("verification"))
        if isinstance(row.get("verification"), dict)
        else {}
    )
    current_execution_profile = row.get("execution_profile") or build_execution_profile(
        generated_sql=original_sql,
        schema_store=schema_store,
        profile_mode=profile_mode,
    )
    initial_mismatch_type = current_verification.get("mismatch_type")
    repair_attempt_sequence: list[dict[str, Any]] = []
    attempted_error_classes: list[str] = []
    previous_sql_versions = [original_sql]
    final_repaired_sql: str | None = None
    final_sql_source = "original_generated_sql"
    stop_reason = "repair_not_attempted"

    repair_mode, route_stop_reason = route_mismatch_to_repair_mode(
        current_verification.get("mismatch_type"),
        attempted_error_classes=set(),
    )

    if repair_mode is None:
        stop_reason = route_stop_reason or "unsupported_mismatch_type"
    elif not str(current_verification.get("repair_hint") or "").strip():
        stop_reason = "repair_not_attempted_missing_hint"
    else:
        policy = REPAIR_SCOPE_POLICIES[repair_mode]
        repair_request = _build_chain_repair_request(
            row=row,
            verification=current_verification,
            current_sql=original_sql,
            original_sql=original_sql,
            execution_profile=current_execution_profile,
            repair_mode=repair_mode,
            policy=policy,
            schema_text=schema_text,
        )
        repair_result = repair_semantic_sql(repair_request, repair_generate_fn)
        attempted_error_classes.append(repair_mode)
        attempt: dict[str, Any] = {
            "attempt_index": 1,
            "repair_mode": repair_mode,
            "current_sql_before_attempt": original_sql,
            "repair_request": repair_request.to_dict(),
            "repair_result": repair_result.to_dict(),
            "repaired_sql_after_attempt": repair_result.repaired_sql,
            "allowed_clause_changes": list(policy.allowed_clause_changes),
            "disallowed_clause_changes": list(policy.disallowed_clause_changes),
            "verifier_evidence_used": {
                "mismatch_type": current_verification.get("mismatch_type"),
                "mismatch_detail": current_verification.get("mismatch_detail"),
                "repair_hint": current_verification.get("repair_hint"),
                "stage2_failed_evidence": current_verification.get("stage2_failed_evidence"),
            },
            "reverification_mismatch_type": None,
            "reverification_answers_question": None,
            "reverification_results": None,
        }

        if repair_result.status != "success" or not repair_result.repaired_sql:
            attempt.update(
                {
                    "scope_check_status": None,
                    "scope_check_error": None,
                    "clause_change_summary": None,
                    "routing_decision": "repair_failed_to_generate_sql",
                    "scalar_group_by_gate_status": None,
                    "scalar_group_by_gate_error": None,
                    "requires_grouped_output": None,
                    "grouped_output_evidence": [],
                }
            )
            stop_reason = "repair_failed_to_generate_sql"
            repair_attempt_sequence.append(attempt)
        else:
            scope_check = validate_repair_scope(
                before_sql=original_sql,
                after_sql=repair_result.repaired_sql,
                repair_mode=repair_mode,
            )
            attempt.update(
                {
                    "scope_check": scope_check.to_dict(),
                    "scope_check_status": scope_check.status,
                    "scope_check_error": scope_check.error,
                    "clause_change_summary": list(scope_check.changed_clauses),
                    "scalar_group_by_gate_status": None,
                    "scalar_group_by_gate_error": None,
                    "requires_grouped_output": None,
                    "grouped_output_evidence": [],
                }
            )

            if scope_check.status != "accepted":
                attempt["routing_decision"] = "repair_rejected_by_scope_check"
                stop_reason = "repair_rejected_by_scope_check"
                repair_attempt_sequence.append(attempt)
            else:
                scalar_group_by_gate = apply_scalar_group_by_gate(
                    before_sql=original_sql,
                    after_sql=repair_result.repaired_sql,
                    repair_mode=repair_mode,
                    question=str(row.get("question") or ""),
                    intent_representation=row.get("intent_representation") or {},
                )
                attempt.update(scalar_group_by_gate.to_attempt_fields())

                if scalar_group_by_gate.status == "rejected":
                    attempt["routing_decision"] = "repair_rejected_by_scalar_group_by_gate"
                    stop_reason = "repair_rejected_by_scalar_group_by_gate"
                    repair_attempt_sequence.append(attempt)
                else:
                    final_repaired_sql = repair_result.repaired_sql
                    final_sql_source = "specialized_first_repair_no_reverification"
                    stop_reason = "first_repair_accepted_without_reverification"
                    previous_sql_versions.append(final_repaired_sql)
                    attempt["routing_decision"] = (
                        "first_repair_accepted_without_reverification"
                    )
                    repair_attempt_sequence.append(attempt)

    last_attempt = repair_attempt_sequence[-1] if repair_attempt_sequence else {}
    final_execution_profile = (
        build_execution_profile(
            generated_sql=final_repaired_sql,
            schema_store=schema_store,
            profile_mode=profile_mode,
        )
        if final_repaired_sql
        else current_execution_profile
    )

    return {
        "initial_mismatch_type": initial_mismatch_type,
        "repair_attempt_sequence": repair_attempt_sequence,
        "attempted_error_classes": attempted_error_classes,
        "repair_mode_used": last_attempt.get("repair_mode"),
        "current_sql_before_each_attempt": [
            attempt.get("current_sql_before_attempt")
            for attempt in repair_attempt_sequence
        ],
        "repaired_sql_after_each_attempt": [
            attempt.get("repaired_sql_after_attempt")
            for attempt in repair_attempt_sequence
        ],
        "reverification_mismatch_type": None,
        "reverification_answers_question": None,
        "reverification_results": None,
        "stop_reason": stop_reason,
        "final_sql_source": final_sql_source,
        "num_repair_attempts": len(repair_attempt_sequence),
        "clause_change_summary": last_attempt.get("clause_change_summary"),
        "scope_check_status": last_attempt.get("scope_check_status"),
        "scope_check_error": last_attempt.get("scope_check_error"),
        "allowed_clause_changes": last_attempt.get("allowed_clause_changes"),
        "disallowed_clause_changes": last_attempt.get("disallowed_clause_changes"),
        "original_generated_sql": original_sql,
        "previous_sql_versions": previous_sql_versions,
        "final_repaired_sql": final_repaired_sql,
        "final_verification": current_verification,
        "final_execution_profile": final_execution_profile,
    }


def run_generic_semantic_repair_chain(
    row: dict[str, Any],
    schema_text: str | None,
    schema_store: SchemaAnnotationStore,
    repair_generate_fn: Callable[[str], str],
    verifier_generate_fn: Callable[[str], str],
    profile_mode: str = "compact",
    probing_mode: str = "probe",
    max_probes: int = 7,
) -> dict[str, Any]:
    original_sql = str(row.get("generated_sql") or "")
    current_sql = original_sql
    original_verification = row.get("verification") if isinstance(row.get("verification"), dict) else {}
    current_verification = dict(original_verification)
    current_execution_profile = row.get("execution_profile") or build_execution_profile(
        generated_sql=current_sql,
        schema_store=schema_store,
        profile_mode=profile_mode,
    )
    initial_mismatch_type = current_verification.get("mismatch_type")
    attempted_mismatch_types: list[str] = []
    repair_attempt_sequence: list[dict[str, Any]] = []
    previous_sql_versions = [original_sql]
    final_repaired_sql: str | None = None
    final_sql_source = "original_generated_sql"
    stop_reason = "max_attempts_reached"
    final_verification = dict(current_verification)

    while len(repair_attempt_sequence) < MAX_GENERIC_REPAIR_ATTEMPTS:
        current_mismatch_type = str(current_verification.get("mismatch_type") or "").strip()

        if not current_mismatch_type:
            stop_reason = "missing_mismatch_type"
            break

        if current_mismatch_type in attempted_mismatch_types:
            stop_reason = "same_error_persisted_after_repair"
            break

        if not str(current_verification.get("repair_hint") or "").strip():
            stop_reason = "repair_not_attempted_missing_hint"
            break

        repair_request = _build_generic_chain_repair_request(
            row=row,
            verification=current_verification,
            current_sql=current_sql,
            original_sql=original_sql,
            execution_profile=current_execution_profile,
            schema_text=schema_text,
        )
        repair_result = repair_generic_semantic_sql(repair_request, repair_generate_fn)
        attempted_mismatch_types.append(current_mismatch_type)
        attempt: dict[str, Any] = {
            "attempt_index": len(repair_attempt_sequence) + 1,
            "repair_mode": "generic_semantic_repair",
            "mismatch_type_repaired": current_mismatch_type,
            "current_sql_before_attempt": current_sql,
            "repair_request": repair_request.to_dict(),
            "repair_result": repair_result.to_dict(),
            "repaired_sql_after_attempt": repair_result.repaired_sql,
            "allowed_clause_changes": None,
            "disallowed_clause_changes": None,
            "scope_check": None,
            "scope_check_status": None,
            "scope_check_error": None,
            "clause_change_summary": None,
            "scalar_group_by_gate_status": None,
            "scalar_group_by_gate_error": None,
            "requires_grouped_output": None,
            "grouped_output_evidence": [],
            "verifier_evidence_used": {
                "mismatch_type": current_verification.get("mismatch_type"),
                "mismatch_detail": current_verification.get("mismatch_detail"),
                "repair_hint": current_verification.get("repair_hint"),
                "stage2_failed_evidence": current_verification.get("stage2_failed_evidence"),
            },
        }

        if repair_result.status != "success" or not repair_result.repaired_sql:
            attempt["routing_decision"] = "repair_failed_to_generate_sql"
            repair_attempt_sequence.append(attempt)
            stop_reason = "repair_failed_to_generate_sql"
            break

        current_sql = repair_result.repaired_sql
        previous_sql_versions.append(current_sql)
        current_execution_profile = build_execution_profile(
            generated_sql=current_sql,
            schema_store=schema_store,
            profile_mode=profile_mode,
        )
        reverified = verify_execution_profile(
            question=str(row.get("question") or ""),
            execution_profile=current_execution_profile,
            llm_generate_fn=verifier_generate_fn,
            intent_representation=row.get("intent_representation") or {},
            probing_mode=probing_mode,
            max_probes=max_probes,
            profile_mode=profile_mode,
        )
        final_verification = _verification_to_dict(reverified)
        current_verification = dict(final_verification)
        attempt.update(
            {
                "reverification_mismatch_type": current_verification.get("mismatch_type"),
                "reverification_answers_question": current_verification.get("answers_question"),
                "reverification_results": current_verification,
                "repaired_execution_profile": current_execution_profile,
            }
        )
        repair_attempt_sequence.append(attempt)

        if _is_reverification_failed_or_abstained(current_verification):
            stop_reason = "reverification_failed_or_abstained"
            break

        if current_verification.get("answers_question") is True:
            final_repaired_sql = current_sql
            final_sql_source = "generic_chain_repair"
            stop_reason = "verifier_accepts_after_repair"
            break

        next_mismatch_type = str(current_verification.get("mismatch_type") or "").strip()

        if len(repair_attempt_sequence) >= MAX_GENERIC_REPAIR_ATTEMPTS:
            stop_reason = "all_mismatch_types_attempted"
            break

        if not next_mismatch_type:
            stop_reason = "missing_mismatch_type_after_repair"
            break

        if next_mismatch_type in attempted_mismatch_types:
            stop_reason = "same_error_persisted_after_repair"
            break

        attempt["routing_decision"] = "new_error_detected_and_routed"

    else:
        stop_reason = "max_attempts_reached"

    last_attempt = repair_attempt_sequence[-1] if repair_attempt_sequence else {}

    return {
        "initial_mismatch_type": initial_mismatch_type,
        "repair_attempt_sequence": repair_attempt_sequence,
        "attempted_error_classes": attempted_mismatch_types,
        "attempted_mismatch_types": attempted_mismatch_types,
        "repair_mode_used": last_attempt.get("repair_mode"),
        "current_sql_before_each_attempt": [
            attempt.get("current_sql_before_attempt")
            for attempt in repair_attempt_sequence
        ],
        "repaired_sql_after_each_attempt": [
            attempt.get("repaired_sql_after_attempt")
            for attempt in repair_attempt_sequence
        ],
        "reverification_mismatch_type": last_attempt.get("reverification_mismatch_type"),
        "reverification_answers_question": last_attempt.get("reverification_answers_question"),
        "reverification_results": last_attempt.get("reverification_results"),
        "stop_reason": stop_reason,
        "final_sql_source": final_sql_source,
        "num_repair_attempts": len(repair_attempt_sequence),
        "clause_change_summary": last_attempt.get("clause_change_summary"),
        "scope_check_status": last_attempt.get("scope_check_status"),
        "scope_check_error": last_attempt.get("scope_check_error"),
        "allowed_clause_changes": last_attempt.get("allowed_clause_changes"),
        "disallowed_clause_changes": last_attempt.get("disallowed_clause_changes"),
        "original_generated_sql": original_sql,
        "previous_sql_versions": previous_sql_versions,
        "final_repaired_sql": final_repaired_sql,
        "final_verification": final_verification,
        "final_execution_profile": current_execution_profile,
    }


def run_non_executable_then_semantic_repair_chain(
    row: dict[str, Any],
    schema_text: str,
    schema_store: SchemaAnnotationStore,
    repair_generate_fn: Callable[[str], str],
    verifier_generate_fn: Callable[[str], str],
    intent_representation: dict[str, Any] | None = None,
    profile_mode: str = "compact",
    probing_mode: str = "probe",
    max_probes: int = 7,
    semantic_followup_framework: str = "specialized_chain",
    accept_execution_repair_without_reverification: bool = False,
) -> dict[str, Any]:
    original_sql = str(row.get("generated_sql") or "")
    intent = intent_representation if isinstance(intent_representation, dict) else (
        row.get("intent_representation") if isinstance(row.get("intent_representation"), dict) else {}
    )
    execution_repair_request = build_non_executable_repair_request(
        row=row,
        schema_text=schema_text,
        intent_representation=None,
    )
    execution_repair_result = repair_non_executable_sql(
        execution_repair_request,
        repair_generate_fn,
    )
    base_result: dict[str, Any] = {
        "initial_repair_mode": "non_executable",
        "non_executable_repair_request": execution_repair_request.to_dict(),
        "non_executable_repair_result": execution_repair_result.to_dict(),
        "execution_repaired_sql": None,
        "execution_repair_fallback_available": False,
        "execution_repair_fallback_profile": None,
        "post_execution_repair_profile": None,
        "post_execution_reverification": None,
        "semantic_followup_result": None,
        "semantic_followup_attempt_sequence": [],
        "initial_mismatch_type": "non_executable_error",
        "repair_attempt_sequence": [
            {
                "attempt_index": 1,
                "repair_mode": "non_executable",
                "current_sql_before_attempt": original_sql,
                "repair_request": execution_repair_request.to_dict(),
                "repair_result": execution_repair_result.to_dict(),
                "repaired_sql_after_attempt": execution_repair_result.repaired_sql,
                "routing_decision": None,
            }
        ],
        "attempted_error_classes": ["non_executable_error"],
        "repair_mode_used": "non_executable",
        "current_sql_before_each_attempt": [original_sql],
        "repaired_sql_after_each_attempt": [execution_repair_result.repaired_sql],
        "reverification_mismatch_type": None,
        "reverification_answers_question": None,
        "reverification_results": None,
        "stop_reason": "non_executable_repair_failed",
        "final_sql_source": "original_generated_sql",
        "num_repair_attempts": 1,
        "clause_change_summary": None,
        "scope_check_status": None,
        "scope_check_error": None,
        "allowed_clause_changes": None,
        "disallowed_clause_changes": None,
        "original_generated_sql": original_sql,
        "previous_sql_versions": [original_sql],
        "final_repaired_sql": None,
        "final_verification": row.get("verification") if isinstance(row.get("verification"), dict) else {},
        "final_execution_profile": row.get("execution_profile"),
    }

    if execution_repair_result.status != "success" or not execution_repair_result.repaired_sql:
        base_result["repair_attempt_sequence"][0]["routing_decision"] = "non_executable_repair_failed"
        return base_result

    execution_repaired_sql = execution_repair_result.repaired_sql
    base_result["execution_repaired_sql"] = execution_repaired_sql
    base_result["previous_sql_versions"].append(execution_repaired_sql)
    post_execution_profile = build_execution_profile(
        generated_sql=execution_repaired_sql,
        schema_store=schema_store,
        profile_mode=profile_mode,
    )
    base_result["post_execution_repair_profile"] = post_execution_profile
    base_result["final_execution_profile"] = post_execution_profile

    if not _is_profile_ok(post_execution_profile):
        base_result["stop_reason"] = "non_executable_repair_still_non_executable"
        base_result["repair_attempt_sequence"][0]["routing_decision"] = (
            "non_executable_repair_still_non_executable"
        )
        return base_result

    base_result["execution_repair_fallback_available"] = True
    base_result["execution_repair_fallback_profile"] = post_execution_profile

    if accept_execution_repair_without_reverification:
        base_result["stop_reason"] = "execution_repair_accepted_without_reverification"
        base_result["final_sql_source"] = "non_executable_repair_no_reverification"
        base_result["final_repaired_sql"] = execution_repaired_sql
        base_result["final_execution_profile"] = post_execution_profile
        base_result["repair_attempt_sequence"][0]["routing_decision"] = (
            "execution_repair_accepted_without_reverification"
        )
        return base_result

    def use_execution_repair_fallback(stop_reason: str) -> dict[str, Any]:
        base_result["stop_reason"] = stop_reason
        base_result["final_sql_source"] = "non_executable_repair_fallback"
        base_result["final_repaired_sql"] = execution_repaired_sql
        base_result["final_execution_profile"] = post_execution_profile
        base_result["final_verification"] = post_execution_verification
        return base_result

    reverified = verify_execution_profile(
        question=str(row.get("question") or ""),
        execution_profile=post_execution_profile,
        llm_generate_fn=verifier_generate_fn,
        intent_representation=intent,
        probing_mode=probing_mode,
        max_probes=max_probes,
        profile_mode=profile_mode,
    )
    post_execution_verification = _verification_to_dict(reverified)
    base_result["post_execution_reverification"] = post_execution_verification
    base_result["reverification_results"] = post_execution_verification
    base_result["reverification_mismatch_type"] = post_execution_verification.get("mismatch_type")
    base_result["reverification_answers_question"] = post_execution_verification.get("answers_question")
    base_result["final_verification"] = post_execution_verification

    if _is_reverification_failed_or_abstained(post_execution_verification):
        base_result["repair_attempt_sequence"][0]["routing_decision"] = (
            "post_execution_reverification_failed_or_abstained"
        )
        return use_execution_repair_fallback(
            "post_execution_reverification_failed_or_abstained_using_execution_repair_fallback"
        )

    if post_execution_verification.get("answers_question") is True:
        base_result["stop_reason"] = "verifier_accepts_after_execution_repair"
        base_result["final_sql_source"] = "non_executable_repair"
        base_result["final_repaired_sql"] = execution_repaired_sql
        base_result["repair_attempt_sequence"][0]["routing_decision"] = (
            "verifier_accepts_after_execution_repair"
        )
        return base_result

    if semantic_followup_framework == "specialized_chain":
        next_mode, route_stop_reason = route_mismatch_to_repair_mode(
            post_execution_verification.get("mismatch_type"),
            attempted_error_classes=set(),
        )
        if next_mode is None:
            semantic_stop_reason = route_stop_reason or "unsupported_mismatch_type_after_execution_repair"
            base_result["repair_attempt_sequence"][0]["routing_decision"] = semantic_stop_reason
            return use_execution_repair_fallback(
                f"{semantic_stop_reason}_using_execution_repair_fallback"
            )
    elif semantic_followup_framework == "generic_chain":
        if not str(post_execution_verification.get("mismatch_type") or "").strip():
            semantic_stop_reason = "missing_mismatch_type_after_execution_repair"
            base_result["repair_attempt_sequence"][0]["routing_decision"] = semantic_stop_reason
            return use_execution_repair_fallback(
                f"{semantic_stop_reason}_using_execution_repair_fallback"
            )
        if not str(post_execution_verification.get("repair_hint") or "").strip():
            semantic_stop_reason = "missing_repair_hint_after_execution_repair"
            base_result["repair_attempt_sequence"][0]["routing_decision"] = semantic_stop_reason
            return use_execution_repair_fallback(
                f"{semantic_stop_reason}_using_execution_repair_fallback"
            )
    else:
        semantic_stop_reason = f"unsupported_semantic_followup_framework:{semantic_followup_framework}"
        base_result["repair_attempt_sequence"][0]["routing_decision"] = semantic_stop_reason
        return use_execution_repair_fallback(
            f"{semantic_stop_reason}_using_execution_repair_fallback"
        )

    followup_row = {
        **row,
        "generated_sql": execution_repaired_sql,
        "intent_representation": intent,
        "execution_profile": post_execution_profile,
        "verification": post_execution_verification,
        "evaluation_group": GROUP_B,
    }
    if semantic_followup_framework == "generic_chain":
        followup_result = run_generic_semantic_repair_chain(
            row=followup_row,
            schema_text=schema_text,
            schema_store=schema_store,
            repair_generate_fn=repair_generate_fn,
            verifier_generate_fn=verifier_generate_fn,
            profile_mode=profile_mode,
            probing_mode=probing_mode,
            max_probes=max_probes,
        )
        semantic_followup_routing_decision = "post_execution_repair_routed_to_generic_chain"
    else:
        followup_result = run_specialized_semantic_repair_chain(
            row=followup_row,
            schema_text=schema_text,
            schema_store=schema_store,
            repair_generate_fn=repair_generate_fn,
            verifier_generate_fn=verifier_generate_fn,
            profile_mode=profile_mode,
            probing_mode=probing_mode,
            max_probes=max_probes,
        )
        semantic_followup_routing_decision = "post_execution_repair_routed_to_semantic_chain"
    base_result["semantic_followup_result"] = followup_result
    base_result["semantic_followup_attempt_sequence"] = (
        followup_result.get("repair_attempt_sequence") or []
    )
    base_result["repair_attempt_sequence"][0]["routing_decision"] = semantic_followup_routing_decision
    base_result["repair_attempt_sequence"].extend(base_result["semantic_followup_attempt_sequence"])
    base_result["attempted_error_classes"].extend(
        followup_result.get("attempted_error_classes") or []
    )
    base_result["repair_mode_used"] = followup_result.get("repair_mode_used")
    base_result["current_sql_before_each_attempt"].extend(
        followup_result.get("current_sql_before_each_attempt") or []
    )
    base_result["repaired_sql_after_each_attempt"].extend(
        followup_result.get("repaired_sql_after_each_attempt") or []
    )
    base_result["reverification_mismatch_type"] = followup_result.get("reverification_mismatch_type")
    base_result["reverification_answers_question"] = followup_result.get("reverification_answers_question")
    base_result["reverification_results"] = followup_result.get("reverification_results")
    base_result["num_repair_attempts"] = len(base_result["repair_attempt_sequence"])
    base_result["clause_change_summary"] = followup_result.get("clause_change_summary")
    base_result["scope_check_status"] = followup_result.get("scope_check_status")
    base_result["scope_check_error"] = followup_result.get("scope_check_error")
    base_result["allowed_clause_changes"] = followup_result.get("allowed_clause_changes")
    base_result["disallowed_clause_changes"] = followup_result.get("disallowed_clause_changes")
    base_result["previous_sql_versions"].extend(
        (followup_result.get("previous_sql_versions") or [])[1:]
    )
    if followup_result.get("final_repaired_sql"):
        base_result["stop_reason"] = "semantic_followup_accepts_after_repair"
        base_result["final_sql_source"] = followup_result.get("final_sql_source")
        base_result["final_repaired_sql"] = followup_result.get("final_repaired_sql")
        base_result["final_verification"] = followup_result.get("final_verification")
        base_result["final_execution_profile"] = followup_result.get("final_execution_profile")
    else:
        semantic_stop_reason = str(followup_result.get("stop_reason") or "semantic_followup_failed")
        use_execution_repair_fallback(
            f"semantic_followup_failed_using_execution_repair_fallback:{semantic_stop_reason}"
        )
    return base_result


def build_non_executable_repair_request(
    row: dict[str, Any],
    schema_text: str,
    intent_representation: dict[str, Any] | None,
) -> NonExecutableRepairRequest:
    return NonExecutableRepairRequest(
        question_id=str(row.get("question_id") or row.get("id") or ""),
        question=str(row.get("question") or ""),
        generated_sql=str(row.get("generated_sql") or ""),
        execution_error=_extract_execution_error(row),
        schema_text=schema_text,
        intent_representation=intent_representation,
    )


def _extract_execution_error(row: dict[str, Any]) -> str | None:
    for key in ("generated_error", "error_message", "error", "route_reason"):
        value = row.get(key)
        if value:
            return str(value)

    execution_profile = row.get("execution_profile") or row.get("original_execution_profile")
    if isinstance(execution_profile, str):
        try:
            parsed = json.loads(execution_profile)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            for key in ("execution_error", "error"):
                value = parsed.get(key)
                if value:
                    return str(value)

    verification = row.get("verification") or row.get("original_verification")
    if isinstance(verification, dict):
        evidence = verification.get("stage2_failed_evidence")
        if isinstance(evidence, list):
            for item in evidence:
                if item:
                    return str(item)

    return None


def stable_context_hash(*values: Any) -> str:
    payload = json.dumps(
        [to_jsonable(value) for value in values],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_completed_keys(output_path: str | Path) -> set[tuple[str, str, str, str, str, str]]:
    path = Path(output_path)

    if not path.exists():
        return set()

    completed: set[tuple[str, str, str, str, str, str]] = set()

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except Exception:
                continue

            completed.add(
                (
                    str(row.get("question_id") or row.get("id") or ""),
                    str(row.get("original_generated_sql_hash") or ""),
                    str(row.get("repair_mode") or ""),
                    str(row.get("repair_model") or ""),
                    str(row.get("intent_mode") or ""),
                    str(row.get("repair_context_hash") or ""),
                )
            )

    return completed


def get_repair_run_key(
    row: dict[str, Any],
    repair_mode: str,
    repair_model: str,
    intent_mode: str,
    repair_context_hash: str,
) -> tuple[str, str, str, str, str, str]:
    return (
        str(row.get("question_id") or row.get("id") or ""),
        stable_sql_hash(row.get("generated_sql")),
        repair_mode,
        repair_model,
        intent_mode,
        repair_context_hash,
    )


def build_attempt_output_row(
    source_row: dict[str, Any],
    repair_request: (
        GenericSemanticRepairRequest
        | SemanticRepairRequest
        | NonExecutableRepairRequest
        | None
    ),
    repair_result: SemanticRepairResult | None,
    intent_representation_used: dict[str, Any] | None,
    repair_mode: str | None,
    status: str,
    skip_reason: str | None,
    repair_model: str,
    intent_mode: str,
    repair_context_hash: str,
) -> dict[str, Any]:
    repaired_sql = repair_result.repaired_sql if repair_result else None

    return {
        "question_id": source_row.get("question_id") or source_row.get("id"),
        "db_id": source_row.get("db_id"),
        "split": source_row.get("split"),
        "level": source_row.get("level"),
        "generator": source_row.get("generator") or source_row.get("model") or source_row.get("model_key"),
        "prompt_setting": source_row.get("prompt_setting"),
        "evaluation_group": source_row.get("evaluation_group"),
        "question": source_row.get("question"),
        "gold_sql": source_row.get("gold_sql"),
        "original_generated_sql": source_row.get("generated_sql"),
        "original_generated_sql_hash": stable_sql_hash(source_row.get("generated_sql")),
        "original_execution_profile": source_row.get("execution_profile"),
        "original_verification": source_row.get("verification"),
        "intent_representation_used": intent_representation_used,
        "repair_mode": repair_mode,
        "repair_request": repair_request.to_dict() if repair_request else None,
        "repair_result": repair_result.to_dict() if repair_result else None,
        "repair_status": repair_result.status if repair_result else status,
        "repair_error": repair_result.error if repair_result else None,
        "repair_model": repair_model,
        "intent_mode": intent_mode,
        "repair_context_hash": repair_context_hash,
        "repaired_sql": repaired_sql,
        "repaired_sql_hash": stable_sql_hash(repaired_sql),
        "status": status,
        "skip_reason": skip_reason,
    }
