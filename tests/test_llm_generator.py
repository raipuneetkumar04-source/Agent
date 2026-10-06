import time
import unittest
import tempfile
import shutil
from pathlib import Path

from app.parser import CoverageReportParser
from app.analyzer import CoverageAnalyzer
from app.llm_generator import (
    LLMTestSuggestionGenerator,
    OfflineVerificationExpert,
    LLMCache,
    RateLimiter,
)


class TestLLMGenerator(unittest.TestCase):

    def setUp(self):
        self.parser = CoverageReportParser()
        self.report = self.parser.parse_file("examples/sample_report.txt")
        self.analyzer = CoverageAnalyzer(self.report)
        self.contexts = self.analyzer.get_uncovered_contexts()

    def test_offline_expert_exact_sample_match(self):
        wrap_ctx = next((c for c in self.contexts if "wrap" in c.target_bin_id), None)
        self.assertIsNotNone(wrap_ctx)
        sug = OfflineVerificationExpert.generate_suggestion(wrap_ctx)

        self.assertEqual(sug.target_bin, "cg_transfer_size.cp_burst_type.wrap")
        self.assertEqual(sug.difficulty, "medium")
        self.assertIn("wrap burst mode", sug.suggestion)
        self.assertTrue(len(sug.test_outline) >= 4)
        self.assertIn("Ensure AXI slave supports wrap bursts", sug.dependencies)
        self.assertIn("Wrap bursts are used for cache-line fills", sug.reasoning)

    def test_cache_mechanism(self):
        temp_dir = tempfile.mkdtemp()
        try:
            cache_file = Path(temp_dir) / "test_cache.json"
            cache = LLMCache(str(cache_file))

            test_payload = {
                "target_bin": "test.bin",
                "priority": "high",
                "difficulty": "easy",
                "suggestion": "Do test",
                "test_outline": ["1. Step"],
                "dependencies": [],
                "reasoning": "Reason",
            }
            cache.set("key_123", test_payload)

            # Reload cache from disk
            cache_reloaded = LLMCache(str(cache_file))
            retrieved = cache_reloaded.get("key_123")
            self.assertEqual(retrieved, test_payload)
            self.assertIsNone(cache_reloaded.get("nonexistent_key"))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_json_markdown_cleaning(self):
        gen = LLMTestSuggestionGenerator(provider="offline")
        markdown_json = '```json\n{"target_bin": "test", "priority": "high"}\n```'
        parsed = gen._parse_llm_json(markdown_json)
        self.assertEqual(parsed["target_bin"], "test")

    def test_graceful_fallback_on_network_error(self):
        # Point to invalid Ollama port to trigger connection error
        gen = LLMTestSuggestionGenerator(
            provider="ollama",
            use_cache=False,
            max_retries=1,
            retry_backoff=False,
        )
        gen.ollama_url = "http://127.0.0.1:54321/invalid"

    def test_rate_limiter_interval_and_capacity(self):
        # Test 600 RPM (min interval 0.1s)
        limiter = RateLimiter(rpm=600)
        self.assertAlmostEqual(limiter.min_interval, 0.1, places=3)
        self.assertEqual(limiter.rpm, 600)

        t0 = time.time()
        limiter.wait()
        limiter.wait()
        t1 = time.time()
        # Should have spaced calls by at least ~0.08s
        self.assertGreaterEqual(t1 - t0, 0.08)

    def test_gemini_configuration_and_defaults(self):
        gen = LLMTestSuggestionGenerator(
            provider="gemini",
            model="gemini-f3.5-flash-latest",
            rpm=30,
            use_cache=False,
        )
        self.assertEqual(gen.provider, "gemini")
        self.assertEqual(gen.model, "gemini-f3.5-flash-latest")
        self.assertEqual(gen.rpm, 30)
        self.assertIsNotNone(gen.rate_limiter)
        self.assertAlmostEqual(gen.rate_limiter.min_interval, 2.0, places=2)
        self.assertIn("gemini-f3.5-flash-latest", gen.gemini_candidates)
        self.assertIn("gemini-3.5-flash", gen.gemini_candidates)


if __name__ == "__main__":
    unittest.main()

