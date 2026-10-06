import unittest
from app.parser import CoverageReportParser
from app.prioritizer import SuggestionPrioritizer
from app.llm_generator import LLMTestSuggestionGenerator
from app.predictor import CoveragePredictor


class TestCoveragePredictor(unittest.TestCase):

    def setUp(self):
        self.parser = CoverageReportParser()
        self.report = self.parser.parse_file("examples/sample_report.txt")
        self.generator = LLMTestSuggestionGenerator(provider="offline")
        self.suggestions = self.generator.generate_suggestions(self.report)
        self.prioritizer = SuggestionPrioritizer(self.report)
        self.prioritized = self.prioritizer.prioritize(self.suggestions)
        self.predictor = CoveragePredictor()

    def test_predict_metrics(self):
        pred = self.predictor.predict(self.report, self.prioritized)

        self.assertAlmostEqual(pred.current_coverage, 54.84, places=2)
        self.assertAlmostEqual(pred.coverage_gap, 45.16, places=2)
        self.assertEqual(pred.total_uncovered_bins, 8)
        self.assertGreater(pred.estimated_iterations_needed, 0)
        self.assertGreater(pred.estimated_time_to_closure_hours, 0)
        self.assertTrue(0.0 <= pred.closure_probability_pct <= 100.0)
        self.assertIn("RISK", pred.verdict)

    def test_blocking_bins_identified(self):
        pred = self.predictor.predict(self.report, self.prioritized)
        blocking_bin_names = [b.bin for b in pred.blocking_bins]

        # decode_error depends on "Testbench error injection capability"
        self.assertTrue(
            any("decode_error" in name for name in blocking_bin_names),
            f"Expected decode_error to be flagged as blocking, got: {blocking_bin_names}",
        )


if __name__ == "__main__":
    unittest.main()
