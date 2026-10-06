from typing import List, Dict, Any, Optional
# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field

from app.models import ParsedCoverageReport, TestSuggestion


class BlockingBinAnalysis(BaseModel):
    bin: str
    reason: str
    required_infrastructure: str
    risk_level: str  # HIGH, MEDIUM, LOW


class CoverageClosurePrediction(BaseModel):
    current_coverage: float
    target_coverage: float = 100.0
    coverage_gap: float
    total_uncovered_bins: int
    estimated_iterations_needed: int
    estimated_time_to_closure_hours: float
    closure_probability_pct: float
    blocking_bins: List[BlockingBinAnalysis] = Field(default_factory=list)
    verdict: str


class CoveragePredictor:
    """
    Part 4 (Bonus): Coverage Closure Prediction Model.
    Predicts:
    1. Estimated time / test iterations to closure based on velocity.
    2. Closure probability with current testbench approach.
    3. Blocking bins that may require specialized infrastructure.
    """

    # Infrastructure keywords indicating external dependencies / blocking risks
    BLOCKING_KEYWORDS = {
        "error injection": "Interconnect / AXI Error Injector IP missing or disabled",
        "slave stall": "Reactive slave agent stall injector not integrated",
        "axi slave supports wrap": "Downstream memory controller does not support AXI WRAP bursts",
        "memory map": "Unmapped decerr address space undefined in system top",
        "abort": "Asynchronous abort interrupt sequence not supported in standard UVM sequence",
    }

    def predict(
        self,
        report: ParsedCoverageReport,
        suggestions: List[TestSuggestion],
        velocity_bins_per_day: float = 2.5,
    ) -> CoverageClosurePrediction:
        """Run coverage closure prediction model."""
        current_cov = report.overall_coverage
        gap = round(100.0 - current_cov, 2)
        total_uncovered = len(report.uncovered_bins)

        # Analyze blocking bins
        blocking_bins: List[BlockingBinAnalysis] = []
        for s in suggestions:
            is_blocked = False
            for dep in s.dependencies:
                dep_lower = dep.lower()
                for kw, explanation in self.BLOCKING_KEYWORDS.items():
                    if kw in dep_lower:
                        blocking_bins.append(
                            BlockingBinAnalysis(
                                bin=s.target_bin,
                                reason=f"Dependency on: {dep}",
                                required_infrastructure=explanation,
                                risk_level="HIGH" if s.difficulty == "hard" else "MEDIUM",
                            )
                        )
                        is_blocked = True
                        break
                if is_blocked:
                    break

            # Also check if it's an error recovery bin with 0 hits
            if not is_blocked and "recovery" in s.target_bin.lower() and s.difficulty == "hard":
                blocking_bins.append(
                    BlockingBinAnalysis(
                        bin=s.target_bin,
                        reason="Hardware error recovery requires forced bus abort or parity corruption",
                        required_infrastructure="Fault injection VIP / Protocol Checker",
                        risk_level="HIGH",
                    )
                )

        # Calculate closure probability
        # Base factor starts at 95%
        # Reduced by each blocking bin (12% penalty for high risk, 6% for medium)
        penalty = 0.0
        for b in blocking_bins:
            if b.risk_level == "HIGH":
                penalty += 12.0
            else:
                penalty += 6.0

        # Hard bins also slightly penalize random closure
        hard_count = sum(1 for s in suggestions if s.difficulty == "hard")
        penalty += hard_count * 3.0

        closure_prob = max(10.0, round(95.0 - penalty, 1))

        # Time estimation
        # Assuming current velocity of closing bins
        effective_bins_to_close = max(1, total_uncovered)
        # Average iterations per directed test: ~400 runs for easy, ~1500 for medium, ~4000 for hard
        iterations_map = {"easy": 400, "medium": 1200, "hard": 3500}
        total_estimated_iterations = sum(
            iterations_map.get(s.difficulty.lower(), 1200) for s in suggestions
        )

        # Time to closure in engineering hours (directed test development + simulation)
        time_hours = round(
            sum(
                1.5 if s.difficulty == "easy" else (4.0 if s.difficulty == "medium" else 8.0)
                for s in suggestions
            ),
            1,
        )

        if closure_prob >= 75.0:
            verdict = "HIGH CONFIDENCE: Closure achievable with directed UVM test sequences."
        elif closure_prob >= 50.0:
            verdict = "MODERATE RISK: Closure achievable, but requires testbench infrastructure enhancements (e.g. error injection)."
        else:
            verdict = "CRITICAL BOTTLENECK: 100% closure unachievable without missing VIP/testbench features."

        return CoverageClosurePrediction(
            current_coverage=current_cov,
            coverage_gap=gap,
            total_uncovered_bins=total_uncovered,
            estimated_iterations_needed=total_estimated_iterations,
            estimated_time_to_closure_hours=time_hours,
            closure_probability_pct=closure_prob,
            blocking_bins=blocking_bins,
            verdict=verdict,
        )
