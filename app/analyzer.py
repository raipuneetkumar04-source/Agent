from typing import List, Dict, Any, Optional
from app.models import ParsedCoverageReport, UncoveredBinRef, CoverageBin


class UncoveredContext:
    """Rich context for an individual uncovered bin."""

    def __init__(
        self,
        covergroup: str,
        coverpoint: str,
        bin_name: str,
        target_bin_id: str,
        covered_siblings: List[CoverageBin],
        other_covered_coverpoints: Dict[str, List[CoverageBin]],
        associated_cross_holes: List[str],
        cg_coverage: float,
        overall_coverage: float,
        design: str,
    ):
        self.covergroup = covergroup
        self.coverpoint = coverpoint
        self.bin_name = bin_name
        self.target_bin_id = target_bin_id
        self.covered_siblings = covered_siblings
        self.other_covered_coverpoints = other_covered_coverpoints
        self.associated_cross_holes = associated_cross_holes
        self.cg_coverage = cg_coverage
        self.overall_coverage = overall_coverage
        self.design = design

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_bin": self.target_bin_id,
            "covergroup": self.covergroup,
            "coverpoint": self.coverpoint,
            "bin": self.bin_name,
            "cg_coverage": self.cg_coverage,
            "covered_siblings": [b.display_name for b in self.covered_siblings],
            "other_covered_points": {
                cp: [b.display_name for b in bins]
                for cp, bins in self.other_covered_coverpoints.items()
            },
            "associated_cross_holes": self.associated_cross_holes,
        }

    def format_prompt_context(self) -> str:
        """Format human/LLM readable context of what is working vs what is missing."""
        siblings_str = (
            ", ".join([f"{b.display_name} ({b.hits} hits)" for b in self.covered_siblings])
            if self.covered_siblings
            else "None"
        )

        other_cp_parts = []
        for cp_name, bins in self.other_covered_coverpoints.items():
            b_list = ", ".join([b.display_name for b in bins])
            other_cp_parts.append(f"{cp_name}: [{b_list}]")
        other_cp_str = "; ".join(other_cp_parts) if other_cp_parts else "None"

        cross_str = (
            ", ".join(self.associated_cross_holes)
            if self.associated_cross_holes
            else "None"
        )

        return (
            f"IP Design: {self.design}\n"
            f"Target Uncovered Bin: {self.target_bin_id}\n"
            f"Covergroup: {self.covergroup} (Current Coverage: {self.cg_coverage}%)\n"
            f"Working bins in same coverpoint: {siblings_str}\n"
            f"Related working coverpoints: {other_cp_str}\n"
            f"Impacting Cross-Coverage Holes: {cross_str}"
        )


class CoverageAnalyzer:
    """
    Analyzes parsed coverage reports to identify holes, patterns,
    and correlations between covered and uncovered areas.
    """

    def __init__(self, report: ParsedCoverageReport):
        self.report = report

    def get_uncovered_contexts(self) -> List[UncoveredContext]:
        """Build rich contextual information for every uncovered bin."""
        contexts: List[UncoveredContext] = []

        # Map cross coverage holes for fast lookup
        all_cross_uncovered: List[str] = []
        for cc in self.report.cross_coverage:
            all_cross_uncovered.extend(cc.uncovered)

        for ub in self.report.uncovered_bins:
            # Locate covergroup
            cg = next((g for g in self.report.covergroups if g.name == ub.covergroup), None)
            if not cg:
                continue

            # Locate coverpoint
            cp = next((p for p in cg.coverpoints if p.name == ub.coverpoint), None)
            if not cp:
                continue

            # Find covered siblings in the same coverpoint
            covered_siblings = [b for b in cp.bins if b.covered]

            # Find covered bins in other coverpoints of the same covergroup
            other_covered: Dict[str, List[CoverageBin]] = {}
            for other_cp in cg.coverpoints:
                if other_cp.name != cp.name:
                    cov_bins = [b for b in other_cp.bins if b.covered]
                    if cov_bins:
                        other_covered[other_cp.name] = cov_bins

            # Clean bin name without bracket range for cross lookup
            base_bin_name = ub.bin.split("[")[0].strip()

            # Find matching cross coverage holes
            associated_cross = [
                ch for ch in all_cross_uncovered
                if f"<{base_bin_name}," in ch
                or f", {base_bin_name}>" in ch
                or f",{base_bin_name}>" in ch
                or f"<{ub.bin}," in ch
                or f", {ub.bin}>" in ch
            ]

            target_id = f"{ub.covergroup}.{ub.coverpoint}.{base_bin_name}"

            ctx = UncoveredContext(
                covergroup=ub.covergroup,
                coverpoint=ub.coverpoint,
                bin_name=ub.bin,
                target_bin_id=target_id,
                covered_siblings=covered_siblings,
                other_covered_coverpoints=other_covered,
                associated_cross_holes=associated_cross,
                cg_coverage=cg.coverage,
                overall_coverage=self.report.overall_coverage,
                design=self.report.design,
            )
            contexts.append(ctx)

        return contexts

    def summarize_patterns(self) -> Dict[str, Any]:
        """Summarize high-level coverage patterns and bottlenecks."""
        total_bins = sum(
            len(cp.bins)
            for cg in self.report.covergroups
            for cp in cg.coverpoints
        )
        total_covered = sum(
            sum(1 for b in cp.bins if b.covered)
            for cg in self.report.covergroups
            for cp in cg.coverpoints
        )
        total_uncovered = len(self.report.uncovered_bins)

        cross_total = sum(
            len(cc.all_points) if cc.all_points else len(cc.uncovered)
            for cc in self.report.cross_coverage
        )
        cross_uncovered = sum(len(cc.uncovered) for cc in self.report.cross_coverage)

        return {
            "design": self.report.design,
            "overall_coverage": self.report.overall_coverage,
            "total_regular_bins": total_bins,
            "covered_regular_bins": total_covered,
            "uncovered_regular_bins": total_uncovered,
            "cross_coverage_holes": cross_uncovered,
            "critical_bottlenecks": [
                cg.name for cg in self.report.covergroups if cg.coverage < 50.0
            ],
        }
