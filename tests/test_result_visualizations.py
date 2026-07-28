from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.dev.build_result_visualizations import (
    build_correction_corruption_tradeoff,
    build_edit_scope_heatmap,
    build_error_resolution_sankey,
    render_correction_corruption_tradeoff,
    render_edit_scope_heatmap,
    render_error_resolution_sankey,
)


def repair_attempt(
    dimension: str,
    status: str,
    changed_clauses: list[str],
) -> dict[str, object]:
    return {
        "repair_mode": dimension,
        "scope_check_status": status,
        "clause_change_summary": changed_clauses,
    }


def verifier_row(
    question_id: str,
    answers_question: bool | None,
    mismatch_type: str | None,
) -> dict[str, object]:
    return {
        "question_id": question_id,
        "original_verification": {
            "stage2_answers_question": answers_question,
            "stage2_primary_mismatch_type": mismatch_type,
        },
    }


def evaluated_row(question_id: str, execution_match: bool) -> dict[str, object]:
    return {
        "question_id": question_id,
        "execution_match": execution_match,
    }


class ResultVisualizationTests(unittest.TestCase):
    def test_correction_corruption_tradeoff_uses_only_three_systems(self) -> None:
        rows = [
            {
                "key": "generator_only",
                "label": "Generator only",
                "correction_count": None,
                "correction_total": None,
                "corruption_count": None,
                "corruption_total": None,
                "net_repair_gain_count": None,
                "net_repair_gain_total": None,
            },
            {
                "key": "generic_self_refine",
                "correction_count": 3,
                "correction_total": 100,
                "corruption_count": 9,
                "corruption_total": 100,
                "net_repair_gain_count": -6,
                "net_repair_gain_total": 100,
            },
            {
                "key": "generic_execution_guided_refine",
                "correction_count": 4,
                "correction_total": 100,
                "corruption_count": 2,
                "corruption_total": 100,
                "net_repair_gain_count": 2,
                "net_repair_gain_total": 100,
            },
            {
                "key": "finverisql_full",
                "correction_count": 3,
                "correction_total": 100,
                "corruption_count": 0,
                "corruption_total": 100,
                "net_repair_gain_count": 3,
                "net_repair_gain_total": 100,
            },
        ]

        data = build_correction_corruption_tradeoff(rows)

        self.assertEqual(data["denominator"], 100)
        self.assertEqual(
            [row["key"] for row in data["systems"]],
            [
                "generic_self_refine",
                "generic_execution_guided_refine",
                "finverisql_full",
            ],
        )
        self.assertEqual(data["systems"][-1]["corruption_rate"], 0.0)
        for row in data["systems"]:
            self.assertEqual(
                row["correction_count"] - row["corruption_count"],
                row["net_repair_gain_count"],
            )

    def test_correction_corruption_tradeoff_renders_zero_corruption(self) -> None:
        data = build_correction_corruption_tradeoff(
            [
                {
                    "key": "generic_self_refine",
                    "correction_count": 1,
                    "correction_total": 100,
                    "corruption_count": 3,
                    "corruption_total": 100,
                    "net_repair_gain_count": -2,
                    "net_repair_gain_total": 100,
                },
                {
                    "key": "generic_execution_guided_refine",
                    "correction_count": 4,
                    "correction_total": 100,
                    "corruption_count": 2,
                    "corruption_total": 100,
                    "net_repair_gain_count": 2,
                    "net_repair_gain_total": 100,
                },
                {
                    "key": "finverisql_full",
                    "correction_count": 3,
                    "correction_total": 100,
                    "corruption_count": 0,
                    "corruption_total": 100,
                    "net_repair_gain_count": 3,
                    "net_repair_gain_total": 100,
                },
            ]
        )

        with tempfile.TemporaryDirectory() as temporary_dir:
            output_path = Path(temporary_dir) / "tradeoff.svg"
            render_correction_corruption_tradeoff(data, output_path, 1200)
            root = ET.parse(output_path).getroot()
            text = output_path.read_text(encoding="utf-8")
            self.assertNotIn("Correction-Corruption Trade-off", text)
            self.assertIn("Generic self-refine (−2.00 pp)", text)
            self.assertIn("Execution-guided (+2.00 pp)", text)
            self.assertIn("ADiR (+3.00 pp)", text)
            self.assertIn("Correction = Corruption", text)
            self.assertIn("Correction rate (%)", text)
            self.assertIn("Corruption rate (%)", text)
            self.assertIn(">4.0%</text>", text)
            self.assertNotIn(">5.0%</text>", text)
            self.assertIn(
                'style="font-size: 23px; fill: #000000; font-weight: 400;"',
                text,
            )
            self.assertIn('style="font-size: 27px;"', text)
            self.assertIn('style="font-size: 25px;', text)
            labels = {
                "".join(element.itertext()): element.attrib
                for element in root.iter()
                if element.tag.endswith("text")
            }
            self.assertNotIn("font-weight", labels["Generic self-refine (−2.00 pp)"])
            self.assertNotIn("font-weight", labels["Execution-guided (+2.00 pp)"])
            self.assertEqual(labels["ADiR (+3.00 pp)"]["font-weight"], "700")
            self.assertEqual(
                labels["Generic self-refine (−2.00 pp)"]["text-anchor"],
                "end",
            )
            self.assertNotIn('class="title"', text)
            self.assertNotIn("<polygon", text)
            self.assertNotIn("Generator only", text)
            self.assertNotIn("Query movement", text)
            self.assertNotIn("Positive net repair gain", text)
            self.assertNotIn("Negative net repair gain", text)
            self.assertNotIn("Corrected:", text)
            self.assertNotIn("Corrupted:", text)

    def test_edit_scope_heatmap_separates_proposed_and_accepted_edits(self) -> None:
        rows = [
            {
                "question_id": "d3-accepted",
                "repair_attempt_sequence": [
                    repair_attempt(
                        "computation_logic_error",
                        "accepted",
                        ["SELECT"],
                    )
                ],
            },
            {
                "question_id": "d3-rejected",
                "repair_attempt_sequence": [
                    repair_attempt(
                        "computation_logic_error",
                        "rejected",
                        ["SELECT", "JOIN"],
                    )
                ],
            },
            {
                "question_id": "d2-accepted",
                "repair_attempt_sequence": [
                    repair_attempt(
                        "financial_measure_error",
                        "accepted",
                        ["SELECT"],
                    )
                ],
            },
            {
                "question_id": "d2-rejected",
                "repair_attempt_sequence": [
                    repair_attempt(
                        "financial_measure_error",
                        "rejected",
                        ["SELECT", "WHERE"],
                    )
                ],
            },
        ]

        data = build_edit_scope_heatmap(rows)
        by_dimension = {
            row["dimension"]: row
            for row in data["dimensions"]
        }

        d3 = by_dimension["computation_logic_error"]
        self.assertEqual(d3["proposed_denominator"], 2)
        self.assertEqual(d3["accepted_denominator"], 1)
        self.assertEqual(d3["proposed"]["JOIN"], {"count": 1, "rate": 0.5})
        self.assertEqual(d3["accepted"]["JOIN"], {"count": 0, "rate": 0.0})

        d2 = by_dimension["financial_measure_error"]
        self.assertEqual(d2["proposed"]["WHERE"], {"count": 1, "rate": 0.5})
        self.assertEqual(d2["accepted"]["WHERE"], {"count": 0, "rate": 0.0})

        d1 = by_dimension["financial_object_error"]
        self.assertEqual(d1["proposed_denominator"], 0)
        self.assertIsNone(d1["accepted"]["WHERE"]["rate"])

    def test_error_resolution_sankey_conserves_all_ex_movements(self) -> None:
        finverisql_rows = [
            verifier_row("preserved", True, None),
            verifier_row("corrected", False, "financial_measure_error"),
            verifier_row("remains-wrong", False, "computation_logic_error"),
            verifier_row("corrupted", False, "financial_measure_error"),
        ]
        baseline_rows = {
            "preserved": evaluated_row("preserved", True),
            "corrected": evaluated_row("corrected", False),
            "remains-wrong": evaluated_row("remains-wrong", False),
            "corrupted": evaluated_row("corrupted", True),
        }
        final_rows = {
            "preserved": evaluated_row("preserved", True),
            "corrected": evaluated_row("corrected", True),
            "remains-wrong": evaluated_row("remains-wrong", False),
            "corrupted": evaluated_row("corrupted", False),
        }

        data = build_error_resolution_sankey(
            finverisql_rows,
            baseline_rows,
            final_rows,
        )

        self.assertEqual(data["total"], 4)
        self.assertEqual(
            data["outcome_counts"],
            {
                "preserved_correct": 1,
                "corrected": 1,
                "remains_wrong": 1,
                "corrupted": 1,
            },
        )
        self.assertEqual(sum(data["middle_counts"].values()), data["total"])
        self.assertEqual(sum(data["outcome_counts"].values()), data["total"])
        for middle, movements in data["movement_counts"].items():
            self.assertEqual(sum(movements.values()), data["middle_counts"][middle])

    def test_new_visualizations_render_valid_svg(self) -> None:
        heatmap = build_edit_scope_heatmap(
            [
                {
                    "question_id": "heatmap",
                    "repair_attempt_sequence": [
                        repair_attempt(
                            "computation_logic_error",
                            "rejected",
                            ["JOIN"],
                        ),
                        repair_attempt(
                            "computation_logic_error",
                            "accepted",
                            ["ORDER BY", "LIMIT"],
                        ),
                    ],
                }
            ]
        )
        finverisql_rows = [
            verifier_row("preserved", True, None),
            verifier_row("corrected", False, "financial_measure_error"),
        ]
        baseline_rows = {
            "preserved": evaluated_row("preserved", True),
            "corrected": evaluated_row("corrected", False),
        }
        final_rows = {
            "preserved": evaluated_row("preserved", True),
            "corrected": evaluated_row("corrected", True),
        }
        sankey = build_error_resolution_sankey(
            finverisql_rows,
            baseline_rows,
            final_rows,
        )

        with tempfile.TemporaryDirectory() as temporary_dir:
            heatmap_path = Path(temporary_dir) / "heatmap.svg"
            sankey_path = Path(temporary_dir) / "sankey.svg"
            render_edit_scope_heatmap(heatmap, heatmap_path, 1200)
            render_error_resolution_sankey(sankey, sankey_path, 1200)

            ET.parse(heatmap_path)
            ET.parse(sankey_path)
            heatmap_text = heatmap_path.read_text(encoding="utf-8")
            sankey_text = sankey_path.read_text(encoding="utf-8")
            self.assertIn("D1 is N/A", heatmap_text)
            self.assertIn("Deterministic Edit-Scope Enforcement", heatmap_text)
            self.assertIn("Corrupted", sankey_text)
            self.assertNotIn("ADiR Error Resolution Flow", sankey_text)
            self.assertNotIn('class="title"', sankey_text)
            self.assertIn('font-weight="700" style="font-size: 22px;">Input</text>', sankey_text)
            self.assertIn("Verifier Decision</text>", sankey_text)
            self.assertIn("Final EX Movement</text>", sankey_text)
            self.assertIn('font-size: 21px; font-weight: 700;', sankey_text)
            self.assertIn('font-size: 18px;', sankey_text)
            self.assertIn(">All Portable</tspan>", sankey_text)
            self.assertIn(">Candidates</tspan>", sankey_text)
            self.assertIn('width="1400" height="720"', sankey_text)
            self.assertNotIn("Verifier decisions do not use", sankey_text)
            self.assertNotIn("1 (50.00%)", sankey_text)
            self.assertNotIn('="nan"', (heatmap_text + sankey_text).lower())

    def test_sankey_rejects_unsupported_rejection_label(self) -> None:
        rows = [verifier_row("unsupported", False, "unknown_dimension")]
        evaluated = {"unsupported": evaluated_row("unsupported", False)}

        with self.assertRaisesRegex(ValueError, "supported primary mismatch"):
            build_error_resolution_sankey(rows, evaluated, evaluated)


if __name__ == "__main__":
    unittest.main()
