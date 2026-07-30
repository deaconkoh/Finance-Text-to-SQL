from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.dev.build_additional_publication_figures import (
    build_difficulty_net_gain,
    build_ex_asa_transfer,
    build_repair_yield,
    eligible_question_ids,
    render_difficulty_net_gain,
    render_ex_asa_transfer,
    render_repair_yield,
)


def evaluated(
    question_id: str,
    execution_match: bool,
    level: str,
    repaired: bool = False,
) -> dict[str, object]:
    return {
        "question_id": question_id,
        "execution_match": execution_match,
        "level": level,
        "final_sql_repaired": repaired,
    }


def asa_metrics(
    label: str,
    total: int,
    passes: int,
    decision_available: int,
) -> dict[str, object]:
    return {
        "sets": [
            {
                "label": label,
                "total_rows": total,
                "asa_lower_bound_pass_rows": passes,
                "asa_lower_bound_accuracy": passes / total,
                "asa_decision_available_rows": decision_available,
            }
        ]
    }


class AdditionalPublicationFigureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = {
            "m-correct": evaluated("m-correct", True, "medium"),
            "m-wrong": evaluated("m-wrong", False, "medium"),
            "h-correct": evaluated("h-correct", True, "hard"),
            "h-wrong": evaluated("h-wrong", False, "hard"),
        }
        self.systems = {
            "generic_self_refine": {
                "m-correct": evaluated("m-correct", False, "medium", True),
                "m-wrong": evaluated("m-wrong", True, "medium", True),
                "h-correct": evaluated("h-correct", False, "hard", True),
                "h-wrong": evaluated("h-wrong", False, "hard", True),
            },
            "generic_execution_guided_refine": {
                "m-correct": evaluated("m-correct", True, "medium", True),
                "m-wrong": evaluated("m-wrong", True, "medium", True),
                "h-correct": evaluated("h-correct", True, "hard", False),
                "h-wrong": evaluated("h-wrong", False, "hard", False),
            },
            "adir_full": {
                "m-correct": evaluated("m-correct", True, "medium", False),
                "m-wrong": evaluated("m-wrong", True, "medium", True),
                "h-correct": evaluated("h-correct", True, "hard", False),
                "h-wrong": evaluated("h-wrong", True, "hard", True),
            },
        }
        self.eligible = eligible_question_ids(self.baseline, self.systems)

    def test_repair_yield_uses_applied_repairs_as_denominator(self) -> None:
        data = build_repair_yield(self.eligible, self.baseline, self.systems)
        by_key = {row["key"]: row for row in data["systems"]}

        self.assertEqual(by_key["generic_self_refine"]["applied_repair_count"], 4)
        self.assertEqual(by_key["generic_self_refine"]["correction_count"], 1)
        self.assertEqual(by_key["generic_self_refine"]["corruption_count"], 2)
        self.assertEqual(by_key["generic_self_refine"]["correction_yield"], 0.25)
        self.assertEqual(by_key["adir_full"]["applied_repair_count"], 2)
        self.assertEqual(by_key["adir_full"]["correction_count"], 2)
        self.assertEqual(by_key["adir_full"]["correction_yield"], 1.0)
        self.assertIn("95% Wilson confidence intervals", data["suggested_caption"])
        self.assertIn("1.0 applied repairs per correction", data["suggested_caption"])

    def test_difficulty_net_gain_is_conditioned_on_each_level(self) -> None:
        data = build_difficulty_net_gain(self.eligible, self.baseline, self.systems)
        by_key = {row["key"]: row for row in data["systems"]}
        self_refine = {
            cell["difficulty"]: cell
            for cell in by_key["generic_self_refine"]["cells"]
        }
        adir = {
            cell["difficulty"]: cell
            for cell in by_key["adir_full"]["cells"]
        }

        self.assertEqual(self_refine["medium"]["net_gain_count"], 0)
        self.assertEqual(self_refine["hard"]["net_gain_count"], -1)
        self.assertEqual(self_refine["hard"]["net_gain_rate"], -0.5)
        self.assertEqual(adir["medium"]["net_gain_rate"], 0.5)
        self.assertEqual(adir["hard"]["net_gain_rate"], 0.5)

    def test_ex_asa_transfer_uses_complete_fixed_denominator(self) -> None:
        baseline_asa = asa_metrics("before", 4, 2, 3)
        system_asa = {
            "generic_self_refine": asa_metrics("after", 4, 1, 4),
            "generic_execution_guided_refine": asa_metrics("after", 4, 2, 2),
            "adir_full": asa_metrics("after", 4, 3, 3),
        }

        data = build_ex_asa_transfer(
            self.eligible,
            self.baseline,
            self.systems,
            baseline_asa,
            system_asa,
        )
        by_key = {row["key"]: row for row in data["systems"]}

        self.assertEqual(by_key["generic_self_refine"]["ex_delta"], -0.25)
        self.assertEqual(
            by_key["generic_execution_guided_refine"]["asa_lower_bound_delta"],
            0.0,
        )
        self.assertEqual(by_key["adir_full"]["ex_delta"], 0.5)
        self.assertEqual(by_key["adir_full"]["asa_lower_bound_delta"], 0.25)
        self.assertIn(
            "Execution-guided refinement improves EX while reducing ASA",
            data["suggested_caption"],
        )

    def test_renderers_write_valid_svg_with_required_annotations(self) -> None:
        yield_data = build_repair_yield(
            self.eligible,
            self.baseline,
            self.systems,
        )
        difficulty_data = build_difficulty_net_gain(
            self.eligible,
            self.baseline,
            self.systems,
        )
        transfer_data = build_ex_asa_transfer(
            self.eligible,
            self.baseline,
            self.systems,
            asa_metrics("before", 4, 2, 3),
            {
                "generic_self_refine": asa_metrics("after", 4, 1, 4),
                "generic_execution_guided_refine": asa_metrics("after", 4, 2, 2),
                "adir_full": asa_metrics("after", 4, 3, 3),
            },
        )
        transfer_values = {
            "generic_self_refine": (-0.0322, -0.0240),
            "generic_execution_guided_refine": (0.0037, -0.0072),
            "adir_full": (0.0062, 0.0049),
        }
        for row in transfer_data["systems"]:
            row["ex_delta"], row["asa_lower_bound_delta"] = transfer_values[row["key"]]

        with tempfile.TemporaryDirectory() as temporary_dir:
            paths = {
                "yield": Path(temporary_dir) / "yield.svg",
                "difficulty": Path(temporary_dir) / "difficulty.svg",
                "transfer": Path(temporary_dir) / "transfer.svg",
            }
            render_repair_yield(yield_data, paths["yield"])
            render_difficulty_net_gain(difficulty_data, paths["difficulty"])
            render_ex_asa_transfer(transfer_data, paths["transfer"])

            for path in paths.values():
                ET.parse(path)
                self.assertNotIn('="nan"', path.read_text(encoding="utf-8").lower())
            yield_text = paths["yield"].read_text(encoding="utf-8")
            difficulty_text = paths["difficulty"].read_text(encoding="utf-8")
            transfer_text = paths["transfer"].read_text(encoding="utf-8")
            self.assertNotIn("Corrections per 100 applied repairs", yield_text)
            self.assertNotIn("Dots show observed yield", yield_text)
            self.assertNotIn("1 correction per", yield_text)
            self.assertIn("Repair yield (%)", yield_text)
            self.assertIn(">Generic</tspan>", yield_text)
            self.assertIn(">self-refine</tspan>", yield_text)
            self.assertIn(">Execution-guided</text>", yield_text)
            self.assertNotIn("Generic self-refine", yield_text)
            yield_root = ET.parse(paths["yield"]).getroot()
            self.assertEqual(yield_root.attrib["width"], "900")
            self.assertEqual(yield_root.attrib["height"], "600")
            yield_text_nodes = {
                "".join(node.itertext()): node
                for node in yield_root.iter()
                if node.tag.endswith("text")
            }
            yield_circles = [
                node
                for node in yield_root.iter()
                if node.tag.endswith("circle")
            ]
            self.assertEqual(len(yield_circles), 3)
            for index, system in enumerate(yield_data["systems"]):
                yield_label = f'{system["correction_yield"] * 100:.2f}%'
                point_y = float(yield_circles[index].attrib["cy"])
                label_y = float(yield_text_nodes[yield_label].attrib["y"])
                if system["key"] == "adir_full":
                    self.assertLess(label_y, point_y)
                else:
                    self.assertGreater(label_y, point_y)
                    self.assertEqual(
                        yield_text_nodes[yield_label].attrib["text-anchor"],
                        "middle",
                    )
            self.assertNotIn("corrected", difficulty_text)
            self.assertNotIn("corrupted", difficulty_text)
            self.assertNotIn("n =", difficulty_text)
            self.assertIn("Negative net gain", difficulty_text)
            self.assertIn("Near zero", difficulty_text)
            self.assertIn("Positive net gain", difficulty_text)
            self.assertIn("Net repair gain (percentage points)", transfer_text)
            self.assertIn("ASA lower bound", transfer_text)
            self.assertIn('id="exBarPattern"', transfer_text)
            self.assertIn('id="asaBarPattern"', transfer_text)
            self.assertIn('fill="url(#exBarPattern)"', transfer_text)
            self.assertIn('fill="url(#asaBarPattern)"', transfer_text)
            self.assertIn('data-role="zero-line"', transfer_text)
            self.assertIn(">−3.5</text>", transfer_text)
            self.assertIn(">+1.0</text>", transfer_text)
            self.assertNotIn(">+4.0</text>", transfer_text)

    def test_movement_without_applied_repair_is_rejected(self) -> None:
        invalid = {
            key: {question_id: dict(row) for question_id, row in rows.items()}
            for key, rows in self.systems.items()
        }
        invalid["adir_full"]["m-wrong"]["final_sql_repaired"] = False

        with self.assertRaisesRegex(ValueError, "without an applied repair"):
            build_repair_yield(self.eligible, self.baseline, invalid)


if __name__ == "__main__":
    unittest.main()
