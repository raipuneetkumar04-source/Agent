import unittest
from app.parser import CoverageReportParser
from app.analyzer import CoverageAnalyzer


class TestCoverageAnalyzer(unittest.TestCase):

    def setUp(self):
        self.parser = CoverageReportParser()
        self.report = self.parser.parse_file("examples/sample_report.txt")
        self.analyzer = CoverageAnalyzer(self.report)

    def test_get_uncovered_contexts(self):
        contexts = self.analyzer.get_uncovered_contexts()
        self.assertEqual(len(contexts), len(self.report.uncovered_bins))

        # Test context for 'wrap'
        wrap_ctx = next((c for c in contexts if "wrap" in c.target_bin_id), None)
        self.assertIsNotNone(wrap_ctx)
        self.assertEqual(wrap_ctx.covergroup, "cg_transfer_size")
        self.assertEqual(wrap_ctx.coverpoint, "cp_burst_type")

        # Covered siblings of wrap: single, incr, fixed
        sibling_names = [b.name for b in wrap_ctx.covered_siblings]
        self.assertIn("single", sibling_names)
        self.assertIn("incr", sibling_names)
        self.assertIn("fixed", sibling_names)

        # Related coverpoints: cp_size
        self.assertIn("cp_size", wrap_ctx.other_covered_coverpoints)
        cp_size_bins = [b.name for b in wrap_ctx.other_covered_coverpoints["cp_size"]]
        self.assertIn("small", cp_size_bins)
        self.assertIn("medium", cp_size_bins)
        self.assertIn("large", cp_size_bins)

        # Associated cross coverage holes: <small, wrap>, <medium, wrap>, <large, wrap>
        self.assertTrue(len(wrap_ctx.associated_cross_holes) >= 3)
        self.assertIn("<small, wrap>", wrap_ctx.associated_cross_holes)
        self.assertIn("<medium, wrap>", wrap_ctx.associated_cross_holes)
        self.assertIn("<large, wrap>", wrap_ctx.associated_cross_holes)

    def test_format_prompt_context(self):
        contexts = self.analyzer.get_uncovered_contexts()
        wrap_ctx = next((c for c in contexts if "wrap" in c.target_bin_id), None)
        prompt_str = wrap_ctx.format_prompt_context()

        self.assertIn("IP Design: dma_controller", prompt_str)
        self.assertIn("Target Uncovered Bin: cg_transfer_size.cp_burst_type.wrap", prompt_str)
        self.assertIn("Working bins in same coverpoint: single", prompt_str)
        self.assertIn("Impacting Cross-Coverage Holes:", prompt_str)

    def test_summarize_patterns(self):
        summary = self.analyzer.summarize_patterns()
        self.assertEqual(summary["design"], "dma_controller")
        self.assertAlmostEqual(summary["overall_coverage"], 54.84, places=2)
        self.assertEqual(summary["uncovered_regular_bins"], 8)
        self.assertIn("cg_error_scenarios", summary["critical_bottlenecks"])


if __name__ == "__main__":
    unittest.main()
