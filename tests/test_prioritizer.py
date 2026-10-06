import unittest
from app.parser import CoverageReportParser
from app.prioritizer import SuggestionPrioritizer
from app.models import TestSuggestion


class TestSuggestionPrioritizer(unittest.TestCase):

    def setUp(self):
        self.parser = CoverageReportParser()
        self.report = self.parser.parse_file("examples/sample_report.txt")
        self.prioritizer = SuggestionPrioritizer(self.report)

    def test_inverse_difficulty_mapping(self):
        self.assertAlmostEqual(self.prioritizer.calculate_inverse_difficulty("easy"), 1.0, places=3)
        self.assertAlmostEqual(self.prioritizer.calculate_inverse_difficulty("medium"), 0.5, places=3)
        self.assertAlmostEqual(self.prioritizer.calculate_inverse_difficulty("hard"), 0.3333, places=3)

    def test_dependency_score_mapping(self):
        self.assertEqual(self.prioritizer.calculate_dependency_score([]), 1.0)
        self.assertEqual(self.prioritizer.calculate_dependency_score(["Some dependency"]), 0.5)
        self.assertEqual(self.prioritizer.calculate_dependency_score(["Dep 1", "Dep 2"]), 0.5)

    def test_priority_score_formula(self):
        """
        Verify exact formula:
        Priority Score = (Coverage Impact × 0.4) + (Inverse Difficulty × 0.3) + (Dependency Score × 0.3)
        """
        sug = TestSuggestion(
            target_bin="cg_transfer_size.cp_size.max",
            priority="high",
            difficulty="easy",
            suggestion="Test max size",
            test_outline=["1. Step"],
            dependencies=[],
            reasoning="Reason",
        )
        scored = self.prioritizer.score_suggestion(sug)

        expected_inv_diff = 1.0  # easy = 1 -> 1/1
        expected_dep_score = 1.0  # no deps
        cov_impact = scored.coverage_impact

        expected_score = round(
            (cov_impact * 0.4) + (expected_inv_diff * 0.3) + (expected_dep_score * 0.3),
            4,
        )

        self.assertEqual(scored.inverse_difficulty, expected_inv_diff)
        self.assertEqual(scored.dependency_score, expected_dep_score)
        self.assertEqual(scored.priority_score, expected_score)

    def test_hard_with_dependencies_scoring(self):
        sug = TestSuggestion(
            target_bin="cg_error_scenarios.cp_error_type.decode_error",
            priority="medium",
            difficulty="hard",
            suggestion="Test decode error",
            test_outline=["1. Step"],
            dependencies=["Testbench error injection capability"],
            reasoning="Reason",
        )
        scored = self.prioritizer.score_suggestion(sug)

        self.assertEqual(scored.inverse_difficulty, 0.3333)
        self.assertEqual(scored.dependency_score, 0.5)
        expected = round(
            (scored.coverage_impact * 0.4) + (0.3333 * 0.3) + (0.5 * 0.3),
            4,
        )
        self.assertEqual(scored.priority_score, expected)

    def test_prioritize_sorting_descending(self):
        sug_easy = TestSuggestion(
            target_bin="cg_transfer_size.cp_size.max",
            priority="low",
            difficulty="easy",
            suggestion="Test max",
            test_outline=["1. Step"],
            dependencies=[],
            reasoning="Reason",
        )
        sug_hard = TestSuggestion(
            target_bin="cg_error_scenarios.cp_error_type.decode_error",
            priority="high",
            difficulty="hard",
            suggestion="Test decode error",
            test_outline=["1. Step"],
            dependencies=["Needs error injection", "Memory map"],
            reasoning="Reason",
        )
        sorted_list = self.prioritizer.prioritize([sug_hard, sug_easy])

        # Easy test with no dependencies should outrank hard test with dependencies
        self.assertGreater(sorted_list[0].priority_score, sorted_list[1].priority_score)
        self.assertEqual(sorted_list[0].target_bin, "cg_transfer_size.cp_size.max")


if __name__ == "__main__":
    unittest.main()
