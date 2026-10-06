from typing import List, Dict, Optional
from app.models import TestSuggestion, ParsedCoverageReport
from app.analyzer import CoverageAnalyzer


class SuggestionPrioritizer:
    """
    Implements the assignment prioritization algorithm:
    Priority Score = (Coverage Impact × 0.4) + (Inverse Difficulty × 0.3) + (Dependency Score × 0.3)

    Where:
    - Coverage Impact: How much overall coverage improves (higher = better, normalized [0.0 - 1.0])
    - Inverse Difficulty: 1/difficulty (easy=1 -> 1.0, medium=2 -> 0.5, hard=3 -> 0.3333)
    - Dependency Score: 1.0 if no dependencies, 0.5 if dependencies exist
    """

    DIFFICULTY_MAP = {
        "easy": 1,
        "medium": 2,
        "hard": 3,
    }

    def __init__(self, report: Optional[ParsedCoverageReport] = None):
        self.report = report
        self.total_bins = 1
        self.total_uncovered = 1
        self.cross_holes_map: Dict[str, int] = {}

        if report:
            self._init_coverage_metrics(report)

    def _init_coverage_metrics(self, report: ParsedCoverageReport) -> None:
        """Calculate design-wide bin counts to accurately model coverage impact."""
        # Total regular bins
        total_regular = sum(
            len(cp.bins) for cg in report.covergroups for cp in cg.coverpoints
        )
        # Total cross bins
        total_cross = sum(
            len(cc.all_points) if cc.all_points else len(cc.uncovered)
            for cc in report.cross_coverage
        )
        self.total_bins = max(1, total_regular + total_cross)
        self.total_uncovered = max(1, len(report.uncovered_bins))

        # Count cross coverage holes associated with each bin name
        analyzer = CoverageAnalyzer(report)
        contexts = analyzer.get_uncovered_contexts()
        for ctx in contexts:
            # Number of cross holes resolved by this bin
            self.cross_holes_map[ctx.target_bin_id] = len(ctx.associated_cross_holes)
            self.cross_holes_map[ctx.bin_name] = len(ctx.associated_cross_holes)

    def calculate_inverse_difficulty(self, difficulty: str) -> float:
        """Inverse Difficulty: 1 / difficulty (easy=1 -> 1.0, medium=2 -> 0.5, hard=3 -> 0.3333)."""
        diff_lower = (difficulty or "medium").strip().lower()
        diff_val = self.DIFFICULTY_MAP.get(diff_lower, 2)
        return round(1.0 / diff_val, 4)

    def calculate_dependency_score(self, dependencies: List[str]) -> float:
        """Dependency Score: 1.0 if no dependencies, 0.5 if dependencies exist."""
        clean_deps = [d.strip() for d in dependencies if d and d.strip()]
        return 1.0 if len(clean_deps) == 0 else 0.5

    def calculate_coverage_impact(self, target_bin: str) -> float:
        """
        Coverage Impact: How much overall coverage improves (higher = better).
        Directly measures primary bin impact plus correlated cross-coverage holes resolved.
        Normalized to a [0.0, 1.0] scale.
        """
        # Look up cross holes resolved by this bin
        clean_target = target_bin.strip()
        last_part = clean_target.split(".")[-1]
        cross_count = self.cross_holes_map.get(
            clean_target, self.cross_holes_map.get(last_part, 0)
        )

        # 1 primary bin + cross_count cross bins
        bins_resolved = 1 + cross_count

        # Relative impact normalized against total uncovered items or scaled fraction
        # If max potential gain per test is ~5 bins, normalizing bins_resolved gives a calibrated impact score
        impact = min(1.0, bins_resolved / max(4.0, float(self.total_uncovered)))
        return round(impact, 4)

    def score_suggestion(self, suggestion: TestSuggestion) -> TestSuggestion:
        """Score an individual suggestion using the official formula."""
        inv_diff = self.calculate_inverse_difficulty(suggestion.difficulty)
        dep_score = self.calculate_dependency_score(suggestion.dependencies)
        cov_impact = self.calculate_coverage_impact(suggestion.target_bin)

        # Assignment formula:
        # Priority Score = (Coverage Impact × 0.4) + (Inverse Difficulty × 0.3) + (Dependency Score × 0.3)
        priority_score = round(
            (cov_impact * 0.4) + (inv_diff * 0.3) + (dep_score * 0.3),
            4,
        )

        suggestion.inverse_difficulty = inv_diff
        suggestion.dependency_score = dep_score
        suggestion.coverage_impact = cov_impact
        suggestion.priority_score = priority_score

        # Automatically update priority label based on computed score
        if priority_score >= 0.65:
            suggestion.priority = "high"
        elif priority_score >= 0.45:
            suggestion.priority = "medium"
        else:
            suggestion.priority = "low"

        return suggestion

    def prioritize(
        self, suggestions: List[TestSuggestion]
    ) -> List[TestSuggestion]:
        """Score and sort suggestions in descending order of priority score."""
        scored = [self.score_suggestion(s) for s in suggestions]
        scored.sort(key=lambda s: (s.priority_score or 0.0), reverse=True)
        return scored
