import re
from typing import Dict, Any, List, Optional
from pathlib import Path

from app.models import (
    CoverageBin,
    Coverpoint,
    Covergroup,
    UncoveredBinRef,
    CrossCoverageItem,
    ParsedCoverageReport,
)


class CoverageReportParser:
    """
    Parser for functional coverage reports.
    Extracts design, date, overall coverage, covergroups, coverpoints, bins, and cross coverage.
    """

    RE_DESIGN = re.compile(r"^\s*Design:\s*(\S+)", re.IGNORECASE)
    RE_DATE = re.compile(r"^\s*Date:\s*(\S+)", re.IGNORECASE)
    RE_OVERALL = re.compile(r"^\s*Overall Coverage:\s*([\d.]+)%", re.IGNORECASE)

    RE_COVERGROUP = re.compile(r"^\s*Covergroup:\s*([a-zA-Z0-9_]+)", re.IGNORECASE)
    RE_COVERPOINT = re.compile(r"^\s*Coverpoint:\s*([a-zA-Z0-9_]+)", re.IGNORECASE)
    RE_COVERAGE_LINE = re.compile(
        r"^\s*Coverage:\s*([\d.]+)%(?:\s*\(\s*(\d+)/(\d+)\s*bins\s*\))?",
        re.IGNORECASE,
    )

    RE_BIN = re.compile(
        r"^\s*bin\s+([a-zA-Z0-9_]+)(\[[^\]]+\])?\s+hits:\s*(\d+)\s+(covered|UNCOVERED)",
        re.IGNORECASE,
    )

    RE_CROSS_COVERAGE = re.compile(
        r"^\s*Cross\s+Coverage:\s*([a-zA-Z0-9_]+)", re.IGNORECASE
    )
    RE_CROSS_BIN = re.compile(
        r"^\s*(<[^>]+>)\s+hits:\s*(\d+)\s+(covered|UNCOVERED)", re.IGNORECASE
    )

    def parse_file(self, file_path: str) -> ParsedCoverageReport:
        """Parse coverage report from file path."""
        content = Path(file_path).read_text(encoding="utf-8")
        return self.parse_text(content)

    def parse_text(self, text: str) -> ParsedCoverageReport:
        """Parse coverage report text into structured ParsedCoverageReport."""
        lines = text.splitlines()

        design = "unknown_design"
        date = None
        overall_coverage = 0.0

        covergroups: List[Covergroup] = []
        cross_coverages: List[CrossCoverageItem] = []
        uncovered_bins: List[UncoveredBinRef] = []

        current_mode = None  # "COVERGROUP" or "CROSS_COVERAGE"
        current_cg: Optional[Covergroup] = None
        current_cp: Optional[Coverpoint] = None
        current_cross: Optional[CrossCoverageItem] = None

        for line in lines:
            line_str = line.strip()
            if not line_str or line_str.startswith("=") or line_str.startswith("-"):
                # Potential delimiter - don't reset state completely if delimiter separates covergroups
                continue

            # Header matchers
            m_design = self.RE_DESIGN.match(line_str)
            if m_design:
                design = m_design.group(1).strip()
                continue

            m_date = self.RE_DATE.match(line_str)
            if m_date:
                date = m_date.group(1).strip()
                continue

            m_overall = self.RE_OVERALL.match(line_str)
            if m_overall:
                overall_coverage = float(m_overall.group(1))
                continue

            # Check for Covergroup
            m_cg = self.RE_COVERGROUP.match(line_str)
            if m_cg:
                current_mode = "COVERGROUP"
                cg_name = m_cg.group(1).strip()
                current_cg = Covergroup(name=cg_name, coverage=0.0)
                covergroups.append(current_cg)
                current_cp = None
                continue

            # Check for Cross Coverage
            m_cross = self.RE_CROSS_COVERAGE.match(line_str)
            if m_cross:
                current_mode = "CROSS_COVERAGE"
                cross_name = m_cross.group(1).strip()
                current_cross = CrossCoverageItem(name=cross_name, coverage=0.0)
                cross_coverages.append(current_cross)
                continue

            # Check for Coverage percentage line
            m_cov = self.RE_COVERAGE_LINE.match(line_str)
            if m_cov:
                cov_pct = float(m_cov.group(1))
                covered_cnt = int(m_cov.group(2)) if m_cov.group(2) else None
                total_cnt = int(m_cov.group(3)) if m_cov.group(3) else None
                if current_mode == "COVERGROUP" and current_cg:
                    current_cg.coverage = cov_pct
                    current_cg.covered_bins_count = covered_cnt
                    current_cg.total_bins = total_cnt
                elif current_mode == "CROSS_COVERAGE" and current_cross:
                    current_cross.coverage = cov_pct
                    current_cross.covered_bins_count = covered_cnt
                    current_cross.total_bins = total_cnt
                continue

            # Check for Coverpoint under Covergroup
            if current_mode == "COVERGROUP":
                m_cp = self.RE_COVERPOINT.match(line_str)
                if m_cp:
                    cp_name = m_cp.group(1).strip()
                    current_cp = Coverpoint(name=cp_name)
                    if current_cg:
                        current_cg.coverpoints.append(current_cp)
                    continue

                # Check for regular bin
                m_bin = self.RE_BIN.match(line_str)
                if m_bin and current_cp and current_cg:
                    b_name = m_bin.group(1).strip()
                    b_range = m_bin.group(2).strip() if m_bin.group(2) else None
                    b_hits = int(m_bin.group(3))
                    b_status = m_bin.group(4).lower()
                    is_covered = (b_status == "covered") and (b_hits > 0)

                    c_bin = CoverageBin(
                        name=b_name,
                        range=b_range,
                        hits=b_hits,
                        covered=is_covered,
                    )
                    current_cp.bins.append(c_bin)

                    if not is_covered:
                        uncovered_bins.append(
                            UncoveredBinRef(
                                covergroup=current_cg.name,
                                coverpoint=current_cp.name,
                                bin=c_bin.display_name,
                            )
                        )
                    continue

            # Check for Cross Coverage items
            if current_mode == "CROSS_COVERAGE" and current_cross:
                m_cbin = self.RE_CROSS_BIN.match(line_str)
                if m_cbin:
                    bin_desc = m_cbin.group(1).strip()
                    c_hits = int(m_cbin.group(2))
                    c_status = m_cbin.group(3).lower()
                    is_cross_covered = (c_status == "covered") and (c_hits > 0)

                    current_cross.all_points.append(
                        {
                            "point": bin_desc,
                            "hits": c_hits,
                            "covered": is_cross_covered,
                        }
                    )

                    if not is_cross_covered:
                        current_cross.uncovered.append(bin_desc)
                    continue

        return ParsedCoverageReport(
            design=design,
            date=date,
            overall_coverage=overall_coverage,
            covergroups=covergroups,
            uncovered_bins=uncovered_bins,
            cross_coverage=cross_coverages,
        )


def parse_coverage_report(file_path_or_content: str) -> ParsedCoverageReport:
    """Convenience helper to parse from a file path or string content."""
    parser = CoverageReportParser()
    path = Path(file_path_or_content)
    if path.is_file():
        return parser.parse_file(str(path))
    return parser.parse_text(file_path_or_content)
