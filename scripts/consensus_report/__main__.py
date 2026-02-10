"""CLI entry point for consensus report generator."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .extract import extract_pipeline_report
from .format import format_report, format_report_plain


def find_latest_run(pipeline_id: str = "consensus-basic-v3.3") -> Path | None:
    """Find the most recent run directory for a pipeline."""
    base = Path("/tmp/logs/universal-stargate/pipeline_summaries") / pipeline_id
    if not base.exists():
        return None

    runs = sorted(base.iterdir(), key=lambda p: p.name, reverse=True)
    return runs[0] if runs else None


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate report from consensus pipeline run",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Latest run (auto-detect)
  python -m scripts.consensus_report --latest

  # With timing and token usage
  python -m scripts.consensus_report --latest --timing

  # Specific run directory
  python -m scripts.consensus_report /tmp/logs/.../20260125_182809_9ae8ee58

  # Save to file (no colors)
  python -m scripts.consensus_report --latest --timing --output report.txt
""",
    )

    parser.add_argument(
        "run_dir", nargs="?", type=Path, help="Path to pipeline run directory"
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Use most recent run from consensus-basic-v3.3",
    )
    parser.add_argument(
        "--pipeline",
        default="consensus-basic-v3.3",
        help="Pipeline ID for --latest (default: consensus-basic-v3.3)",
    )
    parser.add_argument(
        "--output", "-o", type=Path, help="Write report to file (plain text, no colors)"
    )
    parser.add_argument(
        "--truncate",
        type=int,
        default=500,
        metavar="N",
        help="Truncate original responses to N chars (0 = full, default: 500)",
    )
    parser.add_argument(
        "--no-color", action="store_true", help="Disable ANSI colors in stdout output"
    )
    parser.add_argument(
        "--timing",
        action="store_true",
        help="Show step-by-step timing and token usage breakdown",
    )
    parser.add_argument(
        "--prompts", action="store_true", help="Show prompts sent to answer_all step"
    )

    args = parser.parse_args(argv)

    # Determine run directory
    if args.latest:
        run_dir = find_latest_run(args.pipeline)
        if run_dir is None:
            print(
                f"Error: No runs found for pipeline '{args.pipeline}'", file=sys.stderr
            )
            return 1
    elif args.run_dir:
        run_dir = args.run_dir
        if not run_dir.exists():
            print(f"Error: Run directory does not exist: {run_dir}", file=sys.stderr)
            return 1
    else:
        parser.print_help()
        return 1

    # Verify minimal required files exist (auto-detect others)
    required = [
        "01_rewrite_prompt.md",
        "02_answer_all.md",
    ]
    missing = [f for f in required if not (run_dir / f).exists()]
    if missing:
        print(f"Error: Missing required files in {run_dir}: {missing}", file=sys.stderr)
        return 1

    # Extract and format
    try:
        report = extract_pipeline_report(run_dir)
    except Exception as e:
        print(f"Error extracting report: {e}", file=sys.stderr)
        return 1

    # Output
    if args.output:
        text = format_report_plain(
            report,
            show_timing=args.timing,
            show_prompts=args.prompts,
        )
        args.output.write_text(text)
        print(f"Report written to {args.output}")
    else:
        if args.no_color:
            print(
                format_report_plain(
                    report,
                    show_timing=args.timing,
                    show_prompts=args.prompts,
                )
            )
        else:
            print(
                format_report(
                    report,
                    truncate_responses=args.truncate,
                    show_timing=args.timing,
                    show_prompts=args.prompts,
                )
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
