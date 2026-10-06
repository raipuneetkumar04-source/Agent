#!/usr/bin/env python3
"""
Verification Coverage Analyzer with LLM Integration
CLI Tool for Parsing, Analyzing, Generating, and Prioritizing Verification Test Scenarios.
"""

import sys
import json
import argparse
from pathlib import Path
from typing import Optional

try:
    # pyrefly: ignore [missing-import]
    from rich.console import Console
    # pyrefly: ignore [missing-import]
    from rich.table import Table
    # pyrefly: ignore [missing-import]
    from rich.panel import Panel
    # pyrefly: ignore [missing-import]
    from rich.markup import escape
    # pyrefly: ignore [missing-import]
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    escape = lambda s: s

from app.parser import CoverageReportParser
from app.analyzer import CoverageAnalyzer
from app.llm_generator import LLMTestSuggestionGenerator
from app.prioritizer import SuggestionPrioritizer
from app.predictor import CoveragePredictor


def display_results_rich(
    parsed_report, prioritized_suggestions, prediction=None
) -> None:
    """Render beautiful terminal output using Rich."""
    console = Console()

    # 1. Summary Header Panel
    summary_text = (
        f"[bold cyan]Design:[/bold cyan] {escape(parsed_report.design)}\n"
        f"[bold cyan]Date:[/bold cyan] {escape(parsed_report.date or 'N/A')}\n"
        f"[bold cyan]Overall Coverage:[/bold cyan] [bold yellow]{parsed_report.overall_coverage:.2f}%[/bold yellow]\n"
        f"[bold cyan]Covergroups:[/bold cyan] {len(parsed_report.covergroups)} | "
        f"[bold cyan]Uncovered Bins:[/bold cyan] [bold red]{len(parsed_report.uncovered_bins)}[/bold red] | "
        f"[bold cyan]Cross-Coverage Groups:[/bold cyan] {len(parsed_report.cross_coverage)}"
    )
    console.print(
        Panel(
            summary_text,
            title="[bold green]Functional Coverage Analysis Summary[/bold green]",
            box=box.ROUNDED,
        )
    )

    # 2. Covergroups Overview Table
    cg_table = Table(title="Covergroup Breakdown", box=box.SIMPLE_HEAVY)
    cg_table.add_column("Covergroup", style="cyan", no_wrap=True)
    cg_table.add_column("Coverage", justify="right")
    cg_table.add_column("Coverpoints / Bins", justify="center")
    cg_table.add_column("Status", justify="center")

    for cg in parsed_report.covergroups:
        cov_color = "green" if cg.coverage >= 80 else ("yellow" if cg.coverage >= 50 else "red")
        tot = cg.total_bins or sum(len(cp.bins) for cp in cg.coverpoints)
        cov_cnt = cg.covered_bins_count or sum(
            sum(1 for b in cp.bins if b.covered) for cp in cg.coverpoints
        )
        status = "[green]HEALTHY[/green]" if cg.coverage >= 80 else "[red]NEEDS CLOSURE[/red]"
        cg_table.add_row(
            escape(cg.name),
            f"[{cov_color}]{cg.coverage:.2f}%[/{cov_color}]",
            f"{cov_cnt}/{tot} bins ({len(cg.coverpoints)} points)",
            status,
        )

    for cc in parsed_report.cross_coverage:
        cov_color = "yellow" if cc.coverage >= 50 else "red"
        tot = cc.total_bins or (len(cc.all_points) if cc.all_points else len(cc.uncovered))
        cov_cnt = cc.covered_bins_count or (tot - len(cc.uncovered))
        cg_table.add_row(
            f"[magenta]Cross: {escape(cc.name)}[/magenta]",
            f"[{cov_color}]{cc.coverage:.2f}%[/{cov_color}]",
            f"{cov_cnt}/{tot} bins ({len(cc.uncovered)} holes)",
            "[red]CROSS HOLES[/red]",
        )

    console.print(cg_table)
    console.print()

    # 3. Prioritized Suggestions Table
    sug_table = Table(
        title="Prioritized LLM Test Suggestions",
        box=box.ROUNDED,
        show_lines=True,
    )
    sug_table.add_column("Rank", justify="center", style="bold", width=6)
    sug_table.add_column("Score", justify="right", style="bold yellow", width=8)
    sug_table.add_column("Target Bin", style="cyan", width=36)
    sug_table.add_column("Diff", justify="center", width=8)
    sug_table.add_column("Metric Breakdown\n(Impact/InvDiff/Dep)", justify="center", width=18)
    sug_table.add_column("Test Suggestion & Action Plan", style="white", width=46)

    for idx, s in enumerate(prioritized_suggestions, 1):
        diff_color = "green" if s.difficulty == "easy" else ("yellow" if s.difficulty == "medium" else "red")
        breakdown = f"{s.coverage_impact:.2f} x 0.4\n{s.inverse_difficulty:.2f} x 0.3\n{s.dependency_score:.2f} x 0.3"

        steps = "\n".join([f"  - {escape(step)}" for step in s.test_outline[:3]])
        deps_txt = f"\n[bold yellow]Deps:[/bold yellow] {escape(', '.join(s.dependencies))}" if s.dependencies else ""
        action_text = f"[bold]{escape(s.suggestion)}[/bold]\n[dim]{steps}[/dim]{deps_txt}\n[italic green]Reasoning: {escape(s.reasoning)}[/italic green]"

        sug_table.add_row(
            str(idx),
            f"{s.priority_score:.4f}",
            escape(s.target_bin),
            f"[{diff_color}]{escape(s.difficulty)}[/{diff_color}]",
            breakdown,
            action_text,
        )

    console.print(sug_table)

    # 4. Bonus Prediction Panel
    if prediction:
        console.print()
        prob_color = "green" if prediction.closure_probability_pct >= 70 else "red"
        pred_text = (
            f"[bold cyan]Current Coverage:[/bold cyan] {prediction.current_coverage:.2f}%\n"
            f"[bold cyan]Coverage Gap:[/bold cyan] {prediction.coverage_gap:.2f}%\n"
            f"[bold cyan]Closure Probability:[/bold cyan] [{prob_color}]{prediction.closure_probability_pct:.1f}%[/{prob_color}]\n"
            f"[bold cyan]Estimated Engineering Time:[/bold cyan] [bold yellow]{prediction.estimated_time_to_closure_hours:.1f} hours[/bold yellow]\n"
            f"[bold cyan]Estimated Test Iterations:[/bold cyan] {prediction.estimated_iterations_needed:,} runs\n"
            f"[bold cyan]Closure Verdict:[/bold cyan] {escape(prediction.verdict)}"
        )
        if prediction.blocking_bins:
            pred_text += "\n\n[bold red]Identified Blocking / High-Risk Bins:[/bold red]"
            for b in prediction.blocking_bins:
                pred_text += f"\n  - [bold]{escape(b.bin)}[/bold] ({escape(b.risk_level)} RISK): {escape(b.reason)} -> [italic]{escape(b.required_infrastructure)}[/italic]"

        console.print(
            Panel(
                pred_text,
                title="[bold magenta]Part 4: Coverage Closure Prediction (Bonus)[/bold magenta]",
                box=box.ROUNDED,
            )
        )


def display_results_plain(parsed_report, prioritized_suggestions, prediction=None) -> None:
    """Plain-text fallback display."""
    print("=" * 70)
    print("VERIFICATION COVERAGE ANALYSIS REPORT")
    print("=" * 70)
    print(f"Design: {parsed_report.design}")
    print(f"Overall Coverage: {parsed_report.overall_coverage:.2f}%")
    print(f"Total Uncovered Bins: {len(parsed_report.uncovered_bins)}")
    print("-" * 70)
    print("\nPRIORITIZED TEST SUGGESTIONS:")
    print("-" * 70)
    for idx, s in enumerate(prioritized_suggestions, 1):
        print(f"\n[{idx}] Target Bin: {s.target_bin}")
        print(f"    Priority Score: {s.priority_score:.4f} (Priority: {s.priority.upper()})")
        print(f"    Difficulty: {s.difficulty} | Inverse Difficulty: {s.inverse_difficulty}")
        print(f"    Dependency Score: {s.dependency_score} | Coverage Impact: {s.coverage_impact}")
        print(f"    Suggestion: {s.suggestion}")
        print("    Test Outline:")
        for step in s.test_outline:
            print(f"      {step}")
        if s.dependencies:
            print(f"    Dependencies: {', '.join(s.dependencies)}")
        print(f"    Reasoning: {s.reasoning}")

    if prediction:
        print("\n" + "=" * 70)
        print("COVERAGE CLOSURE PREDICTION (BONUS)")
        print("=" * 70)
        print(f"Closure Probability: {prediction.closure_probability_pct:.1f}%")
        print(f"Estimated Time to Closure: {prediction.estimated_time_to_closure_hours:.1f} hours")
        print(f"Verdict: {prediction.verdict}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verification Coverage Analyzer with LLM Integration",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--report",
        "-r",
        default="examples/sample_report.txt",
        help="Path to functional coverage report text file",
    )
    parser.add_argument(
        "--provider",
        "-p",
        default="auto",
        choices=["auto", "openai", "anthropic", "gemini", "ollama", "offline", "mock"],
        help="LLM provider backend. 'auto' selects based on environment keys, fallback to offline.",
    )
    parser.add_argument(
        "--model",
        "-m",
        default=None,
        help="Model name (e.g., gpt-4o-mini, claude-3-5-sonnet-20241022, llama3)",
    )
    parser.add_argument(
        "--json-out",
        "-o",
        default=None,
        help="Optional path to export structured JSON results",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable LLM response caching",
    )
    parser.add_argument(
        "--rpm",
        type=int,
        default=30,
        help="Requests per minute rate limit (default: 30 RPM)",
    )
    parser.add_argument(
        "--predict",
        action="store_true",
        default=True,
        help="Run Part 4 Coverage Closure Prediction model",
    )

    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.is_file():
        print(f"Error: Coverage report file not found at: {report_path}", file=sys.stderr)
        return 1

    # 1. Parse report
    parser_engine = CoverageReportParser()
    parsed_report = parser_engine.parse_file(str(report_path))

    # 2. Generate suggestions with LLM
    generator = LLMTestSuggestionGenerator(
        provider=args.provider,
        model=args.model,
        use_cache=not args.no_cache,
        rpm=args.rpm,
    )
    raw_suggestions = generator.generate_suggestions(parsed_report)

    # 3. Prioritize suggestions
    prioritizer = SuggestionPrioritizer(parsed_report)
    prioritized_suggestions = prioritizer.prioritize(raw_suggestions)

    # 4. Optional Closure Prediction (Bonus Part 4)
    prediction = None
    if args.predict:
        predictor = CoveragePredictor()
        prediction = predictor.predict(parsed_report, prioritized_suggestions)

    # 5. Display results
    if RICH_AVAILABLE:
        display_results_rich(parsed_report, prioritized_suggestions, prediction)
    else:
        display_results_plain(parsed_report, prioritized_suggestions, prediction)

    # 6. JSON Export
    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        export_data = {
            "parsed_report": parsed_report.to_dict(),
            "suggestions": [s.model_dump() for s in prioritized_suggestions],
            "prediction": prediction.model_dump() if prediction else None,
        }
        out_path.write_text(json.dumps(export_data, indent=2), encoding="utf-8")
        print(f"\n[OK] Results successfully exported to {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
