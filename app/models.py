from typing import List, Optional, Dict, Any
# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field


class CoverageBin(BaseModel):
    name: str
    range: Optional[str] = None
    hits: int
    covered: bool

    @property
    def display_name(self) -> str:
        return f"{self.name}{self.range}" if self.range else self.name


class Coverpoint(BaseModel):
    name: str
    bins: List[CoverageBin] = Field(default_factory=list)


class Covergroup(BaseModel):
    name: str
    coverage: float
    total_bins: Optional[int] = None
    covered_bins_count: Optional[int] = None
    coverpoints: List[Coverpoint] = Field(default_factory=list)


class UncoveredBinRef(BaseModel):
    covergroup: str
    coverpoint: str
    bin: str


class CrossCoverageItem(BaseModel):
    name: str
    coverage: float
    total_bins: Optional[int] = None
    covered_bins_count: Optional[int] = None
    uncovered: List[str] = Field(default_factory=list)
    all_points: Optional[List[Dict[str, Any]]] = Field(default_factory=list)


class ParsedCoverageReport(BaseModel):
    design: str
    date: Optional[str] = None
    overall_coverage: float
    covergroups: List[Covergroup] = Field(default_factory=list)
    uncovered_bins: List[UncoveredBinRef] = Field(default_factory=list)
    cross_coverage: List[CrossCoverageItem] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict matching assignment JSON schema."""
        return {
            "design": self.design,
            "date": self.date,
            "overall_coverage": self.overall_coverage,
            "covergroups": [
                {
                    "name": cg.name,
                    "coverage": cg.coverage,
                    "coverpoints": [
                        {
                            "name": cp.name,
                            "bins": [
                                {
                                    "name": b.name,
                                    "range": b.range,
                                    "hits": b.hits,
                                    "covered": b.covered,
                                }
                                for b in cp.bins
                            ],
                        }
                        for cp in cg.coverpoints
                    ],
                }
                for cg in self.covergroups
            ],
            "uncovered_bins": [
                {
                    "covergroup": ub.covergroup,
                    "coverpoint": ub.coverpoint,
                    "bin": ub.bin,
                }
                for ub in self.uncovered_bins
            ],
            "cross_coverage": [
                {
                    "name": cc.name,
                    "coverage": cc.coverage,
                    "uncovered": cc.uncovered,
                }
                for cc in self.cross_coverage
            ],
        }


class TestSuggestion(BaseModel):
    target_bin: str
    priority: str  # high, medium, low
    difficulty: str  # easy, medium, hard
    suggestion: str
    test_outline: List[str]
    dependencies: List[str] = Field(default_factory=list)
    reasoning: str

    # Scoring details computed by prioritizer
    priority_score: Optional[float] = None
    coverage_impact: Optional[float] = None
    inverse_difficulty: Optional[float] = None
    dependency_score: Optional[float] = None


class PrioritizedSuggestionsResponse(BaseModel):
    design: str
    total_uncovered_bins: int
    suggestions: List[TestSuggestion]
