"""Argument parser and administrative CLI flag handlers for consult commands.

This module isolates parser construction and command-level management operations
so the main orchestration path remains focused on runtime execution only.
Administrative flags are resolved through ``_handle_admin_flags`` before normal
consult dispatch.
"""

from __future__ import annotations

import argparse
import sys
import textwrap

from transport_utils import DEFAULT_RAG_URL

from consult_lib.constants import DEFAULT_ROLE, DEFAULT_STARGATE_URL
from consult_lib.exclusion_cli import (
    add_exclusion_cli,
    remove_exclusion_cli,
    show_exclusions,
    show_rankings,
)
from consult_lib.pipeline import get_pipeline_id


def _build_parser(role_prompts: dict[str, str]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Multi-model consultation with role templates and RAG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Roles:
              architect    Evaluate architecture, trade-offs, improvements
              reviewer     Code review via code-review pipeline (RAG flags ignored)
              planner      Implementation plan: files, steps, risks
              researcher   Research-grounded Q&A (default)
              modularizer  Split oversized files respecting SLOC and SRP
              prompt_engineer  Diagnose pipeline step failures, suggest fixes

            Examples:
              consult "How should we handle reconnection?"
              consult -r architect "Event bus: pub/sub vs direct?" -f docs/event-contracts.md
              consult -r reviewer -f capacity/ledger.py  # code-review pipeline; no RAG retrieval
              consult -r planner "Add per-model timeouts" -o plan.md
              consult -r planner -Q /tmp/question.txt -o plan.md
              consult --chain -r planner --models local cloud "complex question"
              consult --models qwen3-32b-awq-32768 openrouter/google/gemini-2.5-pro "question"
              consult --cloud-only -r reviewer -f myfile.py "question"
              consult --cloud-only --models openrouter/google/gemini-2.5-pro "question"
              consult -r architect --validate-files "scope a refactor"
        """),
    )
    parser.add_argument("question", nargs="?", help="Question to consult on")
    parser.add_argument(
        "-Q",
        "--question-file",
        default=None,
        metavar="PATH",
        help=(
            "Read question from file instead of positional arg. "
            "Use '-' to read from stdin. "
            "Cannot be combined with the positional question argument."
        ),
    )
    parser.add_argument(
        "-r",
        "--role",
        default=DEFAULT_ROLE,
        choices=list(role_prompts),
        help=f"Consultation role (default: {DEFAULT_ROLE})",
    )
    parser.add_argument(
        "-f",
        "--context-files",
        action="append",
        default=[],
        metavar="PATH",
        help="Files/directories to include as context (repeatable)",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=None,
        metavar="MODEL",
        help="Models to query (default: role-based auto-selection)",
    )
    parser.add_argument(
        "-o", "--output", default=None, metavar="FILE", help="Save results to file"
    )
    parser.add_argument(
        "-u", "--stargate-url", default=DEFAULT_STARGATE_URL, help="Stargate URL"
    )
    parser.add_argument(
        "--rag-url",
        default=DEFAULT_RAG_URL,
        help="RAG service URL (default: unix socket)",
    )
    parser.add_argument("--no-rag", action="store_true", help="Disable RAG")
    parser.add_argument(
        "--scope",
        nargs="*",
        default=["project"],
        metavar="SCOPE",
        help=(
            "RAG retrieval scope(s), space-separated (default: project). "
            "E.g. --scope research, or --scope project research for union."
        ),
    )
    rag_mode_group = parser.add_mutually_exclusive_group()
    rag_mode_group.add_argument(
        "--rag-pipeline",
        nargs="?",
        const="rag-context",
        default="rag-context",
        metavar="PIPELINE",
        help="RAG pipeline to use (default: rag-context). Use --no-rag-pipeline for direct search.",
    )
    rag_mode_group.add_argument(
        "--no-rag-pipeline",
        action="store_true",
        help="Use direct RAG search instead of the pipeline",
    )
    parser.add_argument(
        "--rag-top-k",
        type=int,
        default=None,
        metavar="N",
        help="With --no-rag-pipeline: number of chunks to retrieve (default 5)",
    )
    parser.add_argument(
        "--timeout", type=float, default=300.0, help="Per-model timeout in seconds"
    )
    parser.add_argument(
        "--require-warm",
        action="store_true",
        help=(
            "Direct-query path only: probe GET /v1/models/{id}?include_status=true "
            "and refuse cold/loading seats (try --fallback first). Prevents "
            "latency-sensitive one-shots from hanging on large GGUF cold-load."
        ),
    )
    parser.add_argument(
        "--fallback",
        nargs="+",
        metavar="MODEL",
        default=None,
        help=(
            "Ordered warm-seat substitutes when the primary is cold/loading "
            "(used with --require-warm, or as soft fallbacks when probing)."
        ),
    )
    parser.add_argument("--list-roles", action="store_true", help="List roles and exit")
    parser.add_argument(
        "--chain",
        action="store_true",
        help="Chain models sequentially: first as analyst, rest as reviewers",
    )
    parser.add_argument(
        "--chain-directive",
        default=None,
        metavar="TEXT",
        help="Custom reviewer directive for chained mode",
    )
    parser.add_argument(
        "--cloud-only",
        action="store_true",
        help=(
            "Restrict to cloud models only (IDs containing '/'). "
            "Errors if none are available."
        ),
    )
    parser.add_argument("--pipeline", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--exclude",
        nargs=2,
        metavar=("TASK", "MODEL"),
        help="Add model to exclusion list (e.g. --exclude code_review bad-model)",
    )
    parser.add_argument(
        "--unexclude",
        nargs=2,
        metavar=("TASK", "MODEL"),
        help="Remove model from exclusion list",
    )
    parser.add_argument(
        "--show-exclusions",
        action="store_true",
        help="Show current model exclusions and exit",
    )
    parser.add_argument(
        "--rankings",
        metavar="TASK",
        help="Show model rankings for a task and exit",
    )
    parser.add_argument(
        "--validate-files",
        action="store_true",
        help="After output is produced, validate FILE: lines against the repo. "
        "Reports valid/invalid paths. Appended to run artifact metadata.",
    )
    parser.add_argument(
        "--pipeline-options",
        default=None,
        metavar="JSON",
        help=(
            "Extra pipeline_options as a JSON object, merged into the request sent "
            "to Stargate (e.g. '{\"metadata_boost_enabled\": false}'). "
            "Applies to both the RAG retrieval pipeline and the consult pipeline."
        ),
    )
    return parser


def _handle_admin_flags(args: argparse.Namespace) -> bool:
    """Handle admin-only flags and return True when command already completed.

    Supported flags:
    - ``--show-exclusions``
    - ``--exclude TASK MODEL``
    - ``--unexclude TASK MODEL``
    - ``--rankings TASK``
    """
    if args.show_exclusions:
        show_exclusions()
        return True
    if args.exclude:
        add_exclusion_cli(args.exclude[0], args.exclude[1])
        return True
    if args.unexclude:
        remove_exclusion_cli(args.unexclude[0], args.unexclude[1])
        return True
    if args.rankings:
        show_rankings(args.rankings, args.stargate_url)
        return True
    return False


def print_role_listing(role_prompts: dict[str, str]) -> None:
    """Print role prompt text and attached pipeline identifiers when available."""
    for name, prompt in role_prompts.items():
        marker = " (default)" if name == DEFAULT_ROLE else ""
        print(f"\n=== {name}{marker} ===")
        print(prompt)
        pipeline_id = get_pipeline_id(name)
        if pipeline_id:
            print(f"  Pipeline: {pipeline_id}")
    sys.exit(0)
