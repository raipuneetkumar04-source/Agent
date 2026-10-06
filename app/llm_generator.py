import os
import json
import time
import hashlib
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

import requests
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

from app.models import ParsedCoverageReport, TestSuggestion
from app.analyzer import CoverageAnalyzer, UncoveredContext

load_dotenv()
logger = logging.getLogger("CoverageLLM")


FEW_SHOT_SYSTEM_PROMPT = """You are a Principal ASIC/SoC Design Verification (DV) Engineer with deep expertise in UVM, SystemVerilog, and functional coverage closure.
Your job is to analyze uncovered functional coverage bins and cross-coverage holes, and generate actionable, concrete test scenarios to hit them.

Guidelines:
1. Always analyze what IS working vs what is NOT working from the provided context.
2. Formulate specific test configurations (register programming, address boundaries, bus transaction types, error injections).
3. Identify external testbench dependencies (e.g., AXI slave support, error injectors, memory map setup).
4. Provide technical reasoning referencing architectural requirements and protocol specifications (e.g., AXI, DMA, bus protocols).
5. Output MUST be valid JSON strictly matching the required schema.

FEW-SHOT EXAMPLES:
Context: IP dma_controller, target cg_transfer_size.cp_burst_type.wrap. Covered: single (2341 hits), incr (1822 hits), fixed (234 hits).
Output:
{
  "target_bin": "cg_transfer_size.cp_burst_type.wrap",
  "priority": "high",
  "difficulty": "medium",
  "suggestion": "Create a test sequence that configures the DMA for wrap burst mode with a burst length that causes address wrapping. Set base address near a wrap boundary (e.g., 0xFFC for 4KB boundary) with burst length of 4.",
  "test_outline": [
    "1. Configure DMA channel with wrap burst type",
    "2. Set transfer size to trigger address wrapping",
    "3. Set base address near boundary (0xFFC)",
    "4. Start transfer and verify wrap behavior"
  ],
  "dependencies": ["Ensure AXI slave supports wrap bursts"],
  "reasoning": "Wrap bursts are used for cache-line fills. The coverage shows INCR and SINGLE work, suggesting basic DMA functionality is correct. Wrap mode likely needs specific configuration."
}

Context: IP dma_controller, target cg_error_scenarios.cp_error_type.decode_error. Covered: no_error (10000 hits), slave_error (234 hits).
Output:
{
  "target_bin": "cg_error_scenarios.cp_error_type.decode_error",
  "priority": "medium",
  "difficulty": "hard",
  "suggestion": "Inject a decode error by programming DMA to access an unmapped address region. Configure error injection in the AXI interconnect or use a testbench force.",
  "test_outline": [
    "1. Identify unmapped address range in memory map",
    "2. Configure DMA source/dest to unmapped region",
    "3. Start transfer",
    "4. Verify DECERR response and DMA error handling"
  ],
  "dependencies": ["Testbench error injection capability", "Knowledge of memory map"],
  "reasoning": "Decode errors require accessing invalid addresses. Since no_error and slave_error are covered, the basic error path works. Need to specifically target decode error response."
}
"""


class LLMCache:
    """Simple JSON-based disk cache for LLM responses."""

    def __init__(self, cache_file: str = ".cache/llm_cache.json"):
        self.cache_file = Path(cache_file)
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self.cache_file.exists():
            try:
                self._data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}

    def _save(self) -> None:
        try:
            self.cache_file.write_text(
                json.dumps(self._data, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"Could not write cache file: {e}")

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self._data.get(key)

    def set(self, key: str, value: Dict[str, Any]) -> None:
        self._data[key] = value
        self._save()


class RateLimiter:
    """
    Token-bucket / sliding-window rate limiter enforcing Requests Per Minute (RPM).
    For 30 RPM, ensures no more than 30 calls per 60 seconds and smoothly
    spaces requests (~2.0s minimum interval).
    """

    def __init__(self, rpm: int = 30):
        self.rpm = max(1, int(rpm))
        self.min_interval = 60.0 / self.rpm
        self.timestamps: List[float] = []
        self._last_call = 0.0

    def wait(self) -> None:
        """Block if necessary to enforce RPM and interval spacing."""
        now = time.time()

        # 1. Enforce smooth spacing between consecutive requests (>= 2.0s for 30 RPM)
        elapsed = now - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

        # 2. Sliding window check: no more than rpm requests within the last 60 seconds
        now = time.time()
        window_start = now - 60.0
        self.timestamps = [t for t in self.timestamps if t > window_start]
        if len(self.timestamps) >= self.rpm:
            sleep_needed = 60.0 - (now - self.timestamps[0]) + 0.05
            if sleep_needed > 0:
                logger.info(
                    f"RateLimiter: 60s capacity ({self.rpm} RPM) reached. Waiting {sleep_needed:.2f}s..."
                )
                time.sleep(sleep_needed)

        # Record timestamp
        self._last_call = time.time()
        self.timestamps.append(self._last_call)


GEMINI_MODEL_ALIASES = {
    "gemini-f3.5-flash-latest": [
        "gemini-f3.5-flash-latest",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-flash-latest",
    ],
    "gemini-flash-latest": [
        "gemini-flash-latest",
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-3.5-flash",
    ],
    "gemini-3.5-flash": [
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-flash-latest",
    ],
    "gemini-3.8-flash": [
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
    ],
    "gemini-3.5-flash-lite": [
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
    ],
    "gemini-flash-lite-latest": [
        "gemini-flash-lite-latest",
        "gemini-3.5-flash-lite",
    ],
}


class OfflineVerificationExpert:
    """
    Built-in domain-expert rule engine for chip verification.
    Provides deterministic, high-quality test scenarios matching the assignment's exact style.
    Used when running without an API key or as fallback when API limits are reached.
    """

    KNOWLEDGE_BASE: Dict[str, Dict[str, Any]] = {
        # DMA controller scenarios
        "cg_transfer_size.cp_size.max": {
            "priority": "high",
            "difficulty": "easy",
            "suggestion": "Configure a DMA transfer with maximum allowable transfer size of 4096 bytes (4KB boundary) on an idle channel.",
            "test_outline": [
                "1. Select channel 0 and initialize control registers",
                "2. Program transfer length to 4096 bytes",
                "3. Verify byte counter decrements to zero without premature burst termination",
                "4. Check completion interrupt and status register"
            ],
            "dependencies": [],
            "reasoning": "Small, medium, and large transfers are already covered with thousands of hits. The max boundary (4096 bytes) is simply an untested edge condition that requires programming length to full capacity."
        },
        "cg_transfer_size.cp_burst_type.wrap": {
            "priority": "high",
            "difficulty": "medium",
            "suggestion": "Create a test sequence that configures the DMA for wrap burst mode with a burst length that causes address wrapping. Set base address near a wrap boundary (e.g., 0xFFC for 4KB boundary) with burst length of 4.",
            "test_outline": [
                "1. Configure DMA channel with wrap burst type",
                "2. Set transfer size to trigger address wrapping",
                "3. Set base address near boundary (0xFFC)",
                "4. Start transfer and verify wrap behavior"
            ],
            "dependencies": ["Ensure AXI slave supports wrap bursts"],
            "reasoning": "Wrap bursts are used for cache-line fills. The coverage shows INCR and SINGLE work, suggesting basic DMA functionality is correct. Wrap mode likely needs specific configuration."
        },
        "cg_channel_arbitration.cp_active_channels.four_channels": {
            "priority": "medium",
            "difficulty": "medium",
            "suggestion": "Initiate concurrent transfers across 4 active DMA channels simultaneously with equal priority to exercise round-robin and fixed-priority arbitration.",
            "test_outline": [
                "1. Configure channels 0, 1, 2, and 3 with independent source/dest buffers",
                "2. Set priority arbitration weights in the arbiter configuration register",
                "3. Assert start trigger simultaneously across all 4 channels",
                "4. Check arbiter grant signals and ensure fair service without starvation"
            ],
            "dependencies": ["Multi-channel testbench driver support"],
            "reasoning": "Channels 1, 2, and 3 have been tested (one_channel, two_channels, three_channels covered). Testing 4 active channels ensures multi-master arbitration logic scales without deadlock."
        },
        "cg_channel_arbitration.cp_active_channels.all_eight": {
            "priority": "medium",
            "difficulty": "hard",
            "suggestion": "Create a stress test that configures and kicks off all 8 DMA channels concurrently under heavy memory contention.",
            "test_outline": [
                "1. Program descriptors and buffer pointers for all 8 DMA channels",
                "2. Enable continuous burst traffic across all channels concurrently",
                "3. Monitor bus request/grant latency for channel 7 (lowest priority)",
                "4. Verify all channels complete transfers and clear busy flags"
            ],
            "dependencies": ["High memory bandwidth testbench environment", "Sufficient AXI outstanding transactions capacity"],
            "reasoning": "Full channel saturation (all 8 channels) is an extreme stress condition. Since 1-3 channels are covered, testing all eight ensures arbitration queue depths do not overflow under peak load."
        },
        "cg_error_scenarios.cp_error_type.decode_error": {
            "priority": "medium",
            "difficulty": "hard",
            "suggestion": "Inject a decode error by programming DMA to access an unmapped address region. Configure error injection in the AXI interconnect or use a testbench force.",
            "test_outline": [
                "1. Identify unmapped address range in memory map",
                "2. Configure DMA source/dest to unmapped region",
                "3. Start transfer",
                "4. Verify DECERR response and DMA error handling"
            ],
            "dependencies": ["Testbench error injection capability", "Knowledge of memory map"],
            "reasoning": "Decode errors require accessing invalid addresses. Since no_error and slave_error are covered, the basic error path works. Need to specifically target decode error response."
        },
        "cg_error_scenarios.cp_error_type.timeout": {
            "priority": "medium",
            "difficulty": "hard",
            "suggestion": "Simulate a bus stall or unresponsive target device by delaying AXI RVALID/BVALID signals beyond the watchdog timer threshold.",
            "test_outline": [
                "1. Program DMA watchdog timer register to a low timeout threshold",
                "2. Configure DMA read transfer to memory address",
                "3. Assert testbench delay on AXI slave RVALID to exceed timer",
                "4. Check that DMA aborts transfer, sets timeout status bit, and raises error IRQ"
            ],
            "dependencies": ["Configurable slave model with stall injection capability"],
            "reasoning": "No timeout events have occurred in normal runs. Hardware watchdog triggers only when the slave fails to respond within programmed cycles."
        },
        "cg_error_scenarios.cp_error_recovery.retry_success": {
            "priority": "high",
            "difficulty": "medium",
            "suggestion": "Configure DMA auto-retry on slave error, inject a single transient SLVERR response, then allow subsequent retry to succeed.",
            "test_outline": [
                "1. Enable DMA channel auto-retry feature (MAX_RETRIES = 3)",
                "2. Program slave agent to respond with SLVERR on first attempt only",
                "3. Ensure second attempt returns OKAY response",
                "4. Verify retry counter incremented and transfer finished successfully"
            ],
            "dependencies": ["Programmable AXI error injector with single-pulse failure mode"],
            "reasoning": "Slave error is covered, but recovery logic has 0 hits. Testing recovery proves the hardware can cleanly resume after transient interconnect glitches."
        },
        "cg_error_scenarios.cp_error_recovery.abort": {
            "priority": "medium",
            "difficulty": "medium",
            "suggestion": "Issue an explicit software abort command via the channel control register while a multi-beat transfer is actively in flight.",
            "test_outline": [
                "1. Start a large 4KB transfer on channel 0",
                "2. Wait for transfer to enter active burst phase (e.g. 50% progress)",
                "3. Write channel abort bit in DMA_CH_CTRL register",
                "4. Verify DMA cleanly finishes current AXI burst, terminates pipeline, and asserts aborted status"
            ],
            "dependencies": [],
            "reasoning": "Aborts are software-driven commands. Without explicit testbench intervention mid-transfer, this state will never be reached in random testing."
        },
        # AXI Interconnect Crossbar scenarios
        "cg_transaction_burst.cp_burst_length.len_max": {
            "priority": "high",
            "difficulty": "medium",
            "suggestion": "Drive an AXI4 maximum allowable burst length (AxLEN=255, 256 beats) across crossbar to verify interconnect beat counter rollover.",
            "test_outline": [
                "1. Configure master VIP to issue INCR write burst with AxLEN=255",
                "2. Target high-throughput slave memory port",
                "3. Verify interconnect propagates all 256 beats without stall or lost data beats",
                "4. Check WLAST and BVALID handshakes match protocol compliance"
            ],
            "dependencies": ["Slave VIP configured with deep burst buffer"],
            "reasoning": "Short (2-8) and medium (9-64) bursts are covered. 256-beat bursts test crossbar data buffer FIFO depth and internal credit counters at maximum capacity."
        },
        "cg_transaction_burst.cp_burst_type.burst_wrap": {
            "priority": "high",
            "difficulty": "medium",
            "suggestion": "Issue AXI cacheline WRAP4, WRAP8, and WRAP16 read bursts targeted at slave cache memory.",
            "test_outline": [
                "1. Set master VIP AxBURST to 2'b10 (WRAP) with AxLEN = 3, 7, and 15",
                "2. Align start address within cache line boundary",
                "3. Verify slave return beats wrap correctly to lower boundary",
                "4. Check crossbar address generation unit preserves wrap boundary"
            ],
            "dependencies": ["Slave memory model supporting wrapping addresses"],
            "reasoning": "Fixed and INCR bursts have thousands of hits, but wrap bursts require specific burst length (2, 4, 8, 16) and aligned addresses, typically generated by CPU cache controllers."
        },
        "cg_qos_priority_arbitration.cp_master_priority.qos_high": {
            "priority": "high",
            "difficulty": "easy",
            "suggestion": "Assert high Quality of Service (QoS=8 to 11) on Master 2 while low QoS traffic is active on Master 1 to test priority preemption.",
            "test_outline": [
                "1. Start steady stream of low QoS (QoS=2) traffic on Master 1",
                "2. Issue high priority read transaction (QoS=10) on Master 2 to contending slave port",
                "3. Measure arbitration grant latency to verify Master 2 gets preemptive grant",
                "4. Verify high QoS transaction completes ahead of pending Master 1 requests"
            ],
            "dependencies": [],
            "reasoning": "Low and normal QoS traffic are covered. Programming master QoS registers to range [8:11] exercises the higher tiers of multi-level priority arbiters."
        },
        "cg_qos_priority_arbitration.cp_master_priority.qos_urgent": {
            "priority": "high",
            "difficulty": "easy",
            "suggestion": "Issue highest emergency QoS level transactions (QoS=15) during crossbar bandwidth saturation.",
            "test_outline": [
                "1. Saturate all interconnect slave ports with concurrent normal transactions",
                "2. Inject single emergency write transaction with AxQOS = 4'b1111 (qos_urgent)",
                "3. Check that arbiter immediately halts lower priority scheduling and routes urgent packet",
                "4. Validate zero cycle starvation on urgent channel"
            ],
            "dependencies": [],
            "reasoning": "Urgent QoS (12-15) is designed for real-time display/camera subsystem traffic. Simply requires setting AxQOS[3:0] >= 12 on the master interface."
        },
        "cg_response_errors.cp_resp_status.slverr": {
            "priority": "medium",
            "difficulty": "medium",
            "suggestion": "Program slave VIP responder to return SLVERR on address access to protected peripheral aperture.",
            "test_outline": [
                "1. Configure slave peripheral VIP error callback to assert RRESP=2'b10 (SLVERR)",
                "2. Issue master read transaction to peripheral address",
                "3. Verify interconnect passes SLVERR back to the initiating master without dropping channel",
                "4. Check interconnect error logging status register"
            ],
            "dependencies": ["Slave VIP error injection callback support"],
            "reasoning": "OKAY and EXOKAY are covered under normal operations. SLVERR requires either slave VIP error configuration or attempting an illegal access to a slave register."
        },
        "cg_response_errors.cp_resp_status.decerr": {
            "priority": "medium",
            "difficulty": "hard",
            "suggestion": "Drive master transactions to unmapped address holes in interconnect address map to trigger internal default slave DECERR.",
            "test_outline": [
                "1. Identify unmapped holes in interconnect address decode table",
                "2. Master VIP sends write request to unmapped address 0xDEAD_0000",
                "3. Verify internal default slave responds with BRESP=2'b11 (DECERR)",
                "4. Ensure bus pipeline drains cleanly and crossbar remains operational"
            ],
            "dependencies": ["Knowledge of interconnect system memory map"],
            "reasoning": "DECERR is generated by the crossbar's internal error responder when no slave port matches the requested address decoding window."
        },
        "cg_response_errors.cp_interconnect_timeout.timer_expired": {
            "priority": "medium",
            "difficulty": "hard",
            "suggestion": "Configure slave agent to permanently withhold BVALID/RVALID handshake and verify crossbar protocol watchdog timer triggers timeout.",
            "test_outline": [
                "1. Set interconnect bus timeout counter register to 256 cycles",
                "2. Master issues read request to Slave 3",
                "3. Force Slave 3 VIP to withhold ARREADY or RVALID indefinitely",
                "4. Verify interconnect raises timeout interrupt, terminates transaction with DECERR, and unblocks arbiter"
            ],
            "dependencies": ["Slave VIP unresponsive stall mode capability"],
            "reasoning": "Watchdog timeout protects against system lockup caused by unresponsive third-party IP cores. Requires artificial slave stalling."
        },
    }

    @classmethod
    def generate_suggestion(cls, ctx: UncoveredContext) -> TestSuggestion:
        """Generate high-quality test suggestion from domain knowledge or heuristic synthesis."""
        key = ctx.target_bin_id
        # Also try matching without range
        base_key = f"{ctx.covergroup}.{ctx.coverpoint}.{ctx.bin_name.split('[')[0]}"

        match = cls.KNOWLEDGE_BASE.get(key) or cls.KNOWLEDGE_BASE.get(base_key)
        if match:
            return TestSuggestion(
                target_bin=ctx.target_bin_id,
                priority=match["priority"],
                difficulty=match["difficulty"],
                suggestion=match["suggestion"],
                test_outline=match["test_outline"],
                dependencies=match["dependencies"],
                reasoning=match["reasoning"],
            )

        # Generic heuristic generator for other IP / arbitrary coverage reports
        bin_clean = ctx.bin_name.replace("_", " ").title()
        has_cross = len(ctx.associated_cross_holes) > 0
        diff = "hard" if "error" in ctx.covergroup.lower() or "timeout" in ctx.bin_name.lower() else "medium"
        priority = "high" if has_cross else "medium"

        return TestSuggestion(
            target_bin=ctx.target_bin_id,
            priority=priority,
            difficulty=diff,
            suggestion=f"Develop a dedicated directed test targeting '{ctx.bin_name}' in {ctx.coverpoint} under {ctx.design} IP.",
            test_outline=[
                f"1. Initialize {ctx.design} test environment with default configuration",
                f"2. Force or program stimulus parameters to activate condition '{ctx.bin_name}'",
                f"3. Run test sequence and monitor interface protocol signals",
                f"4. Confirm coverage monitor registers hit on bin '{ctx.bin_name}'"
            ],
            dependencies=["Testbench driver support for " + ctx.bin_name] if diff == "hard" else [],
            reasoning=f"Coverage analysis shows {len(ctx.covered_siblings)} sibling bins covered while '{ctx.bin_name}' has 0 hits. Directed sequence is needed to close this verification hole.",
        )


class LLMTestSuggestionGenerator:
    """
    LLM-based generator for verification test suggestions.
    Supports OpenAI, Claude, Gemini, Ollama, and built-in offline mode.
    Includes caching, retries, and rate limiting.
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        use_cache: bool = True,
        rate_limit_delay: float = 0.5,
        max_retries: int = 3,
        retry_backoff: bool = True,
        rpm: Optional[int] = None,
    ):
        self.provider = (provider or os.getenv("LLM_PROVIDER", "auto")).lower()
        self.model = model or os.getenv("LLM_MODEL")
        self.use_cache = use_cache
        self.rate_limit_delay = rate_limit_delay
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.cache = LLMCache() if use_cache else None

        # RPM Rate Limiting (Default 30 RPM for Gemini)
        rpm_env = os.getenv("GEMINI_RPM") or os.getenv("LLM_RPM")
        self.rpm = int(rpm if rpm is not None else (rpm_env if rpm_env else 30))
        self.rate_limiter = RateLimiter(rpm=self.rpm) if self.rpm > 0 else None

        # Resolve provider
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

        if self.provider == "auto":
            if self.gemini_key:
                self.provider = "gemini"
                self.model = self.model or "gemini-f3.5-flash-latest"
            elif self.openai_key:
                self.provider = "openai"
                self.model = self.model or "gpt-4o-mini"
            elif self.anthropic_key:
                self.provider = "anthropic"
                self.model = self.model or "claude-3-5-sonnet-20241022"
            else:
                self.provider = "offline"

        if self.provider == "gemini":
            self.model = self.model or "gemini-f3.5-flash-latest"
            self.gemini_candidates = GEMINI_MODEL_ALIASES.get(
                self.model, [self.model, "gemini-3.5-flash", "gemini-flash-latest"]
            )
        else:
            self.gemini_candidates = []

    def _generate_cache_key(self, ctx: UncoveredContext) -> str:
        data = f"{ctx.design}:{ctx.target_bin_id}:{ctx.format_prompt_context()}"
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def generate_suggestions(
        self, report: ParsedCoverageReport
    ) -> List[TestSuggestion]:
        """Generate suggestions for all uncovered bins in the report."""
        analyzer = CoverageAnalyzer(report)
        contexts = analyzer.get_uncovered_contexts()

        suggestions: List[TestSuggestion] = []
        for ctx in contexts:
            suggestion = self.generate_for_context(ctx)
            suggestions.append(suggestion)
            if self.provider not in ("offline", "mock") and self.rate_limit_delay > 0:
                time.sleep(self.rate_limit_delay)

        return suggestions

    def generate_for_context(self, ctx: UncoveredContext) -> TestSuggestion:
        """Generate test suggestion for a single uncovered context."""
        cache_key = self._generate_cache_key(ctx)

        # Check Cache
        if self.cache:
            cached_data = self.cache.get(cache_key)
            if cached_data:
                try:
                    return TestSuggestion(**cached_data)
                except Exception:
                    pass

        # If offline or mock, use built-in expert
        if self.provider in ("offline", "mock"):
            suggestion = OfflineVerificationExpert.generate_suggestion(ctx)
            if self.cache:
                self.cache.set(cache_key, suggestion.model_dump())
            return suggestion

        # Try API with retries
        try:
            prompt = self._construct_prompt(ctx)
            raw_response = self._call_llm_with_retry(prompt)
            parsed_json = self._parse_llm_json(raw_response)

            # Extract suggestion dict
            if "suggestions" in parsed_json and isinstance(parsed_json["suggestions"], list):
                sug_data = parsed_json["suggestions"][0]
            else:
                sug_data = parsed_json

            # Ensure target_bin matches
            sug_data["target_bin"] = ctx.target_bin_id

            suggestion = TestSuggestion(**sug_data)

            if self.cache:
                self.cache.set(cache_key, suggestion.model_dump())

            return suggestion
        except Exception as e:
            logger.warning(
                f"LLM API call failed ({e}). Falling back gracefully to built-in expert."
            )
            fallback = OfflineVerificationExpert.generate_suggestion(ctx)
            return fallback

    def _construct_prompt(self, ctx: UncoveredContext) -> str:
        return (
            f"Please generate a verification test scenario suggestion for the following uncovered coverage hole:\n\n"
            f"{ctx.format_prompt_context()}\n\n"
            f"Respond ONLY with a valid JSON object matching this schema:\n"
            f"{{\n"
            f'  "target_bin": "{ctx.target_bin_id}",\n'
            f'  "priority": "high" | "medium" | "low",\n'
            f'  "difficulty": "easy" | "medium" | "hard",\n'
            f'  "suggestion": "Concrete test sequence description...",\n'
            f'  "test_outline": [\n'
            f'    "1. Step 1",\n'
            f'    "2. Step 2",\n'
            f'    "3. Step 3",\n'
            f'    "4. Step 4"\n'
            f'  ],\n'
            f'  "dependencies": ["Any dependencies or prerequisites"],\n'
            f'  "reasoning": "Technical explanation referencing working vs missing coverage..."\n'
            f"}}"
        )

    def _call_llm_with_retry(self, prompt: str, max_retries: Optional[int] = None) -> str:
        """Call external LLM API with exponential backoff."""
        retries = max_retries if max_retries is not None else self.max_retries
        for attempt in range(retries):
            try:
                if self.provider == "openai":
                    return self._call_openai(prompt)
                elif self.provider == "anthropic":
                    return self._call_anthropic(prompt)
                elif self.provider == "gemini":
                    return self._call_gemini(prompt)
                elif self.provider == "ollama":
                    return self._call_ollama(prompt)
                else:
                    raise ValueError(f"Unknown provider: {self.provider}")
            except Exception as e:
                if attempt == retries - 1:
                    raise e
                if self.retry_backoff:
                    backoff = 2 ** (attempt + 1)
                    time.sleep(backoff)
        raise RuntimeError("LLM retries exhausted")

    def _call_openai(self, prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.openai_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model or "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": FEW_SHOT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        res = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
        )
        res.raise_for_status()
        data = res.json()
        return data["choices"][0]["message"]["content"]

    def _call_anthropic(self, prompt: str) -> str:
        headers = {
            "x-api-key": self.anthropic_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model or "claude-3-5-sonnet-20241022",
            "system": FEW_SHOT_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1024,
            "temperature": 0.2,
        }
        res = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
            timeout=30,
        )
        res.raise_for_status()
        data = res.json()
        return data["content"][0]["text"]

    def _call_gemini(self, prompt: str) -> str:
        """Call Gemini REST API enforcing RPM rate limits and model alias fallbacks."""
        candidates = self.gemini_candidates or [self.model or "gemini-f3.5-flash-latest", "gemini-3.5-flash", "gemini-flash-latest"]
        last_error = None

        for model in candidates:
            # Enforce 30 RPM rate limiting (e.g. 2.0s pacing)
            if self.rate_limiter:
                self.rate_limiter.wait()

            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.gemini_key}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": f"{FEW_SHOT_SYSTEM_PROMPT}\n\n{prompt}"}
                        ]
                    }
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.2,
                },
            }

            try:
                res = requests.post(url, headers=headers, json=payload, timeout=30)
                if res.status_code == 200:
                    data = res.json()
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                elif res.status_code in (404, 503) and len(candidates) > 1:
                    reason = "404 Not Found" if res.status_code == 404 else "503 High Demand Overload"
                    logger.info(f"Gemini model '{model}' returned {reason}. Falling back to alias candidate...")
                    last_error = requests.HTTPError(f"{reason} for {model}", response=res)
                    continue
                elif res.status_code == 429:
                    retry_after = float(res.headers.get("Retry-After", 2.0))
                    logger.warning(f"Gemini 429 Rate Limit hit. Backing off for {retry_after}s...")
                    time.sleep(retry_after)
                    res.raise_for_status()
                else:
                    res.raise_for_status()
            except Exception as e:
                last_error = e
                # If 503 or transient, let retry loop handle it
                raise e

        if last_error:
            raise last_error
        raise RuntimeError("Gemini call failed across all candidate models")

    def _call_ollama(self, prompt: str) -> str:
        url = f"{self.ollama_url.rstrip('/')}/api/generate"
        payload = {
            "model": self.model or "llama3",
            "system": FEW_SHOT_SYSTEM_PROMPT,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }
        res = requests.post(url, json=payload, timeout=60)
        res.raise_for_status()
        data = res.json()
        return data["response"]

    def _parse_llm_json(self, raw_text: str) -> Dict[str, Any]:
        """Strip markdown fences and parse valid JSON."""
        cleaned = raw_text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        return json.loads(cleaned.strip())
