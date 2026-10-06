import unittest
from pathlib import Path
from app.parser import CoverageReportParser, parse_coverage_report


class TestCoverageReportParser(unittest.TestCase):

    def setUp(self):
        self.sample_file = "examples/sample_report.txt"
        self.parser = CoverageReportParser()

    def test_parse_sample_report(self):
        report = self.parser.parse_file(self.sample_file)

        self.assertEqual(report.design, "dma_controller")
        self.assertEqual(report.date, "2025-01-02")
        self.assertAlmostEqual(report.overall_coverage, 54.84, places=2)
        self.assertEqual(len(report.covergroups), 3)

        # Check cg_transfer_size
        cg0 = report.covergroups[0]
        self.assertEqual(cg0.name, "cg_transfer_size")
        self.assertAlmostEqual(cg0.coverage, 75.0, places=1)
        self.assertEqual(cg0.covered_bins_count, 6)
        self.assertEqual(cg0.total_bins, 8)
        self.assertEqual(len(cg0.coverpoints), 2)

        # Check cp_size bins
        cp_size = cg0.coverpoints[0]
        self.assertEqual(cp_size.name, "cp_size")
        self.assertEqual(len(cp_size.bins), 4)

        b_small = cp_size.bins[0]
        self.assertEqual(b_small.name, "small")
        self.assertEqual(b_small.range, "[0:255]")
        self.assertEqual(b_small.hits, 1523)
        self.assertTrue(b_small.covered)

        b_max = cp_size.bins[3]
        self.assertEqual(b_max.name, "max")
        self.assertEqual(b_max.range, "[4096]")
        self.assertEqual(b_max.hits, 0)
        self.assertFalse(b_max.covered)

        # Check cp_burst_type bins
        cp_burst = cg0.coverpoints[1]
        self.assertEqual(cp_burst.name, "cp_burst_type")
        self.assertIsNone(cp_burst.bins[0].range)
        self.assertEqual(cp_burst.bins[0].name, "single")

        # Check uncovered bins list
        uncovered_names = [f"{u.covergroup}.{u.coverpoint}.{u.bin}" for u in report.uncovered_bins]
        self.assertIn("cg_transfer_size.cp_size.max[4096]", uncovered_names)
        self.assertIn("cg_transfer_size.cp_burst_type.wrap", uncovered_names)
        self.assertIn("cg_channel_arbitration.cp_active_channels.four_channels", uncovered_names)
        self.assertIn("cg_channel_arbitration.cp_active_channels.all_eight", uncovered_names)
        self.assertIn("cg_error_scenarios.cp_error_type.decode_error", uncovered_names)
        self.assertIn("cg_error_scenarios.cp_error_type.timeout", uncovered_names)
        self.assertIn("cg_error_scenarios.cp_error_recovery.retry_success", uncovered_names)
        self.assertIn("cg_error_scenarios.cp_error_recovery.abort", uncovered_names)

        # Check cross coverage
        self.assertEqual(len(report.cross_coverage), 1)
        cross0 = report.cross_coverage[0]
        self.assertEqual(cross0.name, "cross_size_burst")
        self.assertAlmostEqual(cross0.coverage, 50.0, places=1)
        self.assertEqual(len(cross0.uncovered), 6)
        self.assertIn("<small, wrap>", cross0.uncovered)
        self.assertIn("<medium, wrap>", cross0.uncovered)
        self.assertIn("<large, wrap>", cross0.uncovered)

    def test_to_dict_matches_expected_schema(self):
        report = self.parser.parse_file(self.sample_file)
        d = report.to_dict()

        self.assertIn("design", d)
        self.assertIn("overall_coverage", d)
        self.assertIn("covergroups", d)
        self.assertIn("uncovered_bins", d)
        self.assertIn("cross_coverage", d)

        # Verify structure of covergroups
        self.assertIsInstance(d["covergroups"], list)
        self.assertEqual(d["covergroups"][0]["name"], "cg_transfer_size")
        self.assertEqual(d["covergroups"][0]["coverpoints"][0]["name"], "cp_size")
        self.assertEqual(d["covergroups"][0]["coverpoints"][0]["bins"][0]["name"], "small")

    def test_parse_custom_content_string(self):
        custom_text = """
        =======================================================
        Functional Coverage Report
        Design: test_spi
        Date: 2025-01-05
        =======================================================
        Covergroup: cg_spi_modes
        Coverage: 50.00% (2/4 bins)
        Coverpoint: cp_mode
        bin mode_0 hits: 100 covered
        bin mode_1 hits: 0 UNCOVERED
        bin mode_2 hits: 50 covered
        bin mode_3 hits: 0 UNCOVERED
        =======================================================
        Overall Coverage: 50.00%
        =======================================================
        """
        report = parse_coverage_report(custom_text)
        self.assertEqual(report.design, "test_spi")
        self.assertEqual(len(report.uncovered_bins), 2)
        self.assertEqual(report.uncovered_bins[0].bin, "mode_1")
        self.assertEqual(report.uncovered_bins[1].bin, "mode_3")


if __name__ == "__main__":
    unittest.main()
