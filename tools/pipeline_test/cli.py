"""CLI controller for pipeline testing infrastructure.

Usage:
    python -m tools.pipeline_test.cli <subcommand> [args]

Subcommands:
    list        List available pipeline executions
    snapshot    Capture an execution into a fixture file
    inspect     Show step details from a fixture
    refine-context  Agent-optimized context views for one step
    replay      Re-execute a model call against running Stargate
    compare     Diff original vs replay output
    consult     Query consultant models for prompt improvements
    ask         Free-form question to local models with RAG context
    ingest-papers  Copy PDFs into RAG corpus directory for indexing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import ask as ask_svc
from . import compare as compare_svc
from . import consult as consult_svc
from . import context_budget as budget_svc
from . import format as format_svc
from . import ingest as ingest_svc
from . import replay as replay_svc
from . import snapshot as snapshot_svc
from .models import ReplayOverrides

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="pipeline-test",
        description="Pipeline testing: snapshot, inspect, replay, compare.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    _add_list_parser(sub)
    _add_snapshot_parser(sub)
    _add_inspect_parser(sub)
    _add_refine_parser(sub)
    _add_replay_parser(sub)
    _add_compare_parser(sub)
    _add_consult_parser(sub)
    _add_ask_parser(sub)
    _add_ingest_papers_parser(sub)

    args = parser.parse_args(argv)
    args.func(args)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def _add_list_parser(sub: Any) -> None:
    p = sub.add_parser("list", help="List available pipeline executions")
    p.add_argument("pipeline_id", nargs="?", help="Pipeline ID to filter by")
    p.set_defaults(func=_cmd_list)


def _cmd_list(args: argparse.Namespace) -> None:
    if args.pipeline_id:
        _list_pipeline(args.pipeline_id)
    else:
        _list_all_pipelines()


def _list_pipeline(pipeline_id: str) -> None:
    executions = snapshot_svc.list_executions(pipeline_id)
    if not executions:
        print(f"No executions found for '{pipeline_id}'")
        return
    print(f"Executions for {pipeline_id}:")
    for ex in executions:
        print(f"  {ex['dir_name']}  (id: {ex['execution_id']})")


def _list_all_pipelines() -> None:
    root = snapshot_svc.SUMMARIES_ROOT
    if not root.is_dir():
        print(f"No pipeline summaries found at {root}")
        return
    for d in sorted(root.iterdir()):
        if d.is_dir():
            count = sum(
                1 for e in d.iterdir() if e.is_dir() and (e / "events.jsonl").exists()
            )
            if count:
                print(f"  {d.name}  ({count} executions)")


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


def _add_snapshot_parser(sub: Any) -> None:
    p = sub.add_parser("snapshot", help="Capture execution into a fixture")
    p.add_argument("pipeline_id", help="Pipeline ID")
    p.add_argument("--execution", "-e", help="Specific execution ID (default: latest)")
    p.add_argument("--output", "-o", help="Output fixture path")
    p.set_defaults(func=_cmd_snapshot)


def _cmd_snapshot(args: argparse.Namespace) -> None:
    executions = snapshot_svc.list_executions(args.pipeline_id)
    if not executions:
        print(f"No executions found for '{args.pipeline_id}'", file=sys.stderr)
        sys.exit(1)

    if args.execution:
        match = [e for e in executions if args.execution in e["execution_id"]]
        if not match:
            print(f"Execution '{args.execution}' not found", file=sys.stderr)
            sys.exit(1)
        chosen = match[0]
    else:
        chosen = executions[0]

    print(f"Loading execution: {chosen['dir_name']}")
    snap = snapshot_svc.load_execution(chosen["path"])

    output = args.output
    if not output:
        FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        output = FIXTURES_DIR / f"{snap.pipeline_id}_{snap.execution_id[:8]}.json"

    snapshot_svc.save_fixture(snap, output)
    print(f"Fixture saved: {output}")
    print(f"  Pipeline: {snap.pipeline_id}")
    print(f"  Execution: {snap.execution_id}")
    print(f"  Steps: {len(snap.step_order)}")
    print(f"  Duration: {snap.total_duration_ms:.0f}ms")


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


def _add_inspect_parser(sub: Any) -> None:
    p = sub.add_parser("inspect", help="Inspect a fixture or specific step")
    p.add_argument("fixture", help="Path to fixture JSON")
    p.add_argument("--step", "-s", help="Step name to inspect")
    p.add_argument("--show-prompts", action="store_true", help="Show full prompts")
    p.add_argument(
        "--show-inputs", action="store_true", help="Show full resolved inputs"
    )
    p.add_argument("--show-output", action="store_true", help="Show full raw output")
    p.add_argument("--call", "-c", help="Specific call label within a step")
    p.set_defaults(func=_cmd_inspect)


def _cmd_inspect(args: argparse.Namespace) -> None:
    snap = snapshot_svc.load_fixture(args.fixture)

    if not args.step:
        print(format_svc.format_inspect_summary(snap))
        return

    step = _resolve_step(snap, args.step)
    print(
        format_svc.format_inspect_detail(
            step,
            show_inputs=args.show_inputs,
            show_output=args.show_output,
            show_prompts=args.show_prompts,
            call_label=args.call,
        )
    )


# ---------------------------------------------------------------------------
# refine-context
# ---------------------------------------------------------------------------


def _add_refine_parser(sub: Any) -> None:
    p = sub.add_parser(
        "refine-context",
        help="Agent-optimized step view for prompt refinement",
    )
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("fixture", nargs="?", help="Path to fixture JSON")
    source.add_argument(
        "--latest",
        metavar="PIPELINE_ID",
        help="Load latest execution directly (no snapshot needed)",
    )
    p.add_argument("--step", "-s", required=True, help="Step name")
    p.add_argument("--call", "-c", help="Target specific call (e.g., assess_0)")

    view = p.add_mutually_exclusive_group()
    view.add_argument(
        "--summary", action="store_true", help="Structural overview only (no full text)"
    )
    view.add_argument("--input", metavar="KEY", help="Show full value of one input")
    view.add_argument(
        "--prompt", action="store_true", help="Show system + user prompt only"
    )
    view.add_argument("--output", action="store_true", help="Show output only")
    p.set_defaults(func=_cmd_refine_context)


def _cmd_refine_context(args: argparse.Namespace) -> None:
    snap = _load_snapshot(args)
    step = _resolve_step(snap, args.step)

    if args.summary:
        print(format_svc.format_summary(step))
    elif args.input:
        print(format_svc.format_input(step, args.input))
    elif args.prompt:
        print(format_svc.format_prompt(step, call_label=args.call))
    elif args.output:
        print(format_svc.format_output(step, call_label=args.call))
    else:
        print(format_svc.format_refine_context(step, call_label=args.call))


def _load_snapshot(args: argparse.Namespace) -> Any:
    """Load snapshot from fixture file or directly from latest execution."""
    if args.latest:
        executions = snapshot_svc.list_executions(args.latest)
        if not executions:
            print(f"No executions found for '{args.latest}'", file=sys.stderr)
            sys.exit(1)
        return snapshot_svc.load_execution(executions[0]["path"])
    return snapshot_svc.load_fixture(args.fixture)


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def _add_replay_parser(sub: Any) -> None:
    p = sub.add_parser("replay", help="Replay a model call against Stargate")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("fixture", nargs="?", help="Path to fixture JSON")
    source.add_argument(
        "--latest",
        metavar="PIPELINE_ID",
        help="Load latest execution directly (no snapshot needed)",
    )
    p.add_argument("--step", "-s", required=True, help="Step name")
    p.add_argument("--call", "-c", help="Call label (e.g., assess_0)")
    p.add_argument("--model", "-m", help="Override model ID")
    p.add_argument("--temperature", "-t", type=float, help="Override temperature")
    p.add_argument("--max-tokens", type=int, help="Override max_tokens")
    p.add_argument("--recorded", action="store_true", help="Use exact recorded request")
    p.add_argument(
        "--prompt-ref",
        help="Override prompt name for re-render (e.g., critique_redundancy_global)",
    )
    p.add_argument("--output", "-o", help="Save result to file")
    p.add_argument(
        "--url", default=replay_svc.DEFAULT_STARGATE_URL, help="Stargate URL"
    )
    p.add_argument("--pipeline-dir", help="Pipeline directory for YAML re-render")
    p.add_argument("--timeout", type=float, default=300.0, help="Request timeout (s)")
    p.set_defaults(func=_cmd_replay)


def _cmd_replay(args: argparse.Namespace) -> None:
    snap = _load_snapshot(args)
    model = args.model
    if model and args.pipeline_dir:
        resolved = replay_svc.resolve_model_alias(model, args.pipeline_dir)
        if resolved != model:
            print(f"  Resolved '{model}' → '{resolved}'")
            model = resolved
    overrides = ReplayOverrides(
        model=model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        prompt_ref=args.prompt_ref,
    )

    if args.recorded:
        result = replay_svc.replay_recorded(
            snap,
            args.step,
            args.call,
            overrides,
            stargate_url=args.url,
            timeout=args.timeout,
        )
    else:
        result = replay_svc.replay_rerender(
            snap,
            args.step,
            args.call,
            overrides,
            stargate_url=args.url,
            pipeline_dir=args.pipeline_dir,
            timeout=args.timeout,
        )

    print(f"Replay complete: {result.step_name}")
    print(f"  Model: {result.model_id}")
    print(f"  Tokens: {result.prompt_tokens}+{result.completion_tokens}")
    print(f"  Latency: {result.latency_ms:.0f}ms")
    print(f"\nResponse ({len(result.response_text)} chars):")
    print(result.response_text)

    if args.output:
        replay_svc.save_replay_result(result, args.output)
        print(f"\nResult saved: {args.output}")


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def _add_compare_parser(sub: Any) -> None:
    p = sub.add_parser("compare", help="Compare original vs replay output")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("fixture", nargs="?", help="Path to fixture JSON")
    source.add_argument(
        "--latest",
        metavar="PIPELINE_ID",
        help="Load latest execution directly (no snapshot needed)",
    )
    p.add_argument("replay_file", help="Path to replay result JSON")
    p.add_argument("--step", "-s", required=True, help="Step name to compare")
    p.add_argument("--call", "-c", help="Call label to compare")
    p.set_defaults(func=_cmd_compare)


def _cmd_compare(args: argparse.Namespace) -> None:
    snap = _load_snapshot(args)
    replay_result = replay_svc.load_replay_result(args.replay_file)

    step = _resolve_step(snap, args.step)

    if args.call:
        original = ""
        for call in step.model_calls:
            if call.call_label == args.call:
                original = call.response_text
                break
        if not original:
            print(f"Call '{args.call}' not found in step", file=sys.stderr)
            sys.exit(1)
    else:
        original = step.raw_output

    result = compare_svc.compare_outputs(
        step_name=step.step_name,
        original_text=original,
        replay_text=replay_result.response_text,
        call_label=args.call,
    )
    print(compare_svc.format_comparison(result))


# ---------------------------------------------------------------------------
# consult
# ---------------------------------------------------------------------------


def _add_consult_parser(sub: Any) -> None:
    p = sub.add_parser(
        "consult",
        help="Query other models for prompt improvement suggestions",
    )
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("fixture", nargs="?", default=None, help="Path to fixture JSON")
    source.add_argument(
        "--latest",
        metavar="PIPELINE_ID",
        help="Load latest execution directly (no snapshot needed)",
    )
    p.add_argument("--step", "-s", required=True, help="Step name")
    p.add_argument("--call", "-c", help="Target specific call (e.g., assess_0)")
    p.add_argument("--problem", "-p", required=True, help="Problem description")
    p.add_argument(
        "--models",
        nargs="+",
        metavar="MODEL",
        help=(
            "Consultant model IDs "
            f"(default: {', '.join(consult_svc.DEFAULT_CONSULTANTS)})"
        ),
    )
    p.add_argument(
        "--url",
        default=consult_svc.DEFAULT_STARGATE_URL,
        help="Stargate URL",
    )
    p.add_argument("--timeout", type=float, default=300.0, help="Request timeout (s)")
    rag_mode_c = p.add_mutually_exclusive_group()
    rag_mode_c.add_argument(
        "--no-rag",
        action="store_true",
        help="Disable RAG augmentation entirely",
    )
    rag_mode_c.add_argument(
        "--rag-pipeline",
        metavar="PIPELINE_ID",
        nargs="?",
        const=consult_svc.DEFAULT_RAG_PIPELINE_ID,
        default=consult_svc.DEFAULT_RAG_PIPELINE_ID,
        help=(
            "RAG pipeline for intelligent query rewriting + RRF retrieval "
            f"(default: {consult_svc.DEFAULT_RAG_PIPELINE_ID}). "
            "Use --no-rag to disable all retrieval."
        ),
    )
    p.add_argument(
        "--rag-url",
        default=consult_svc.DEFAULT_RAG_URL,
        help="RAG service URL (direct search path only)",
    )
    p.add_argument(
        "--rag-top-k",
        type=int,
        default=budget_svc.MAX_RAG_TOP_K,
        help="RAG chunk cap (direct search path only, adapts to context budget)",
    )
    p.add_argument(
        "--rag-timeout",
        type=float,
        default=consult_svc.DEFAULT_RAG_TIMEOUT,
        help="RAG request timeout (s)",
    )
    p.add_argument(
        "--rag-corpus",
        default=str(consult_svc.DEFAULT_RAG_CORPUS_DIR),
        help="Default source prefix for RAG lookup (direct search path only)",
    )
    p.add_argument(
        "--rag-source-prefix",
        action="append",
        default=[],
        help="Repeatable RAG source prefix override (direct search path only)",
    )
    p.add_argument(
        "--rag-recency",
        type=float,
        default=consult_svc.DEFAULT_RAG_RECENCY_WEIGHT,
        metavar="WEIGHT",
        help=(
            "Recency weight for RAG search [0.0–1.0]; "
            f"0 disables (default: {consult_svc.DEFAULT_RAG_RECENCY_WEIGHT})"
        ),
    )
    p.set_defaults(func=_cmd_consult)


def _cmd_consult(args: argparse.Namespace) -> None:
    snap = _load_snapshot(args)
    step = _resolve_step(snap, args.step)

    consultant_models = args.models or consult_svc.DEFAULT_CONSULTANTS
    print(f"Consulting about: {step.step_name}")
    print(f"  Models: {', '.join(consultant_models)}")
    print(f"  Problem: {args.problem}")

    ctx_len = budget_svc.resolve_min_context_length(
        consultant_models,
        stargate_url=args.url,
    )
    fixed_chars, output_chars = consult_svc.estimate_fixed_chars(
        step,
        args.call,
        args.problem,
    )

    rag_findings: list[str] | None = None
    output_limit: int | None = None

    if args.no_rag:
        print(f"  Budget: {ctx_len}tok context (no RAG)")
        print()
    elif args.rag_pipeline:
        output_limit = budget_svc.compute_budget(
            ctx_len,
            fixed_chars,
            output_chars,
            top_k_cap=0,
        ).output_limit_chars
        print(
            f"  Budget: {ctx_len}tok context (RAG via pipeline '{args.rag_pipeline}')"
        )
        print()
        rag_findings, rag_error = consult_svc.fetch_rag_via_pipeline(
            args.problem,
            pipeline_id=args.rag_pipeline,
            stargate_url=args.url,
            timeout=consult_svc.DEFAULT_RAG_PIPELINE_TIMEOUT,
        )
        if rag_error:
            print(f"  RAG pipeline '{args.rag_pipeline}': unavailable ({rag_error})")
        elif rag_findings:
            print(f"  RAG pipeline '{args.rag_pipeline}': assembled context injected")
        else:
            print(f"  RAG pipeline '{args.rag_pipeline}': no context returned")
    else:
        budget = budget_svc.compute_budget(
            ctx_len,
            fixed_chars,
            output_chars,
            top_k_cap=args.rag_top_k,
        )
        output_limit = budget.output_limit_chars
        print(
            f"  Budget: {ctx_len}tok context → "
            f"output≤{budget.output_limit_chars} chars, "
            f"RAG≤{budget.adaptive_top_k} chunks"
        )
        print()

        source_prefixes = (
            [
                str(Path(prefix).expanduser().resolve())
                for prefix in args.rag_source_prefix
            ]
            if args.rag_source_prefix
            else [str(Path(args.rag_corpus).expanduser().resolve())]
        )
        rag_findings, rag_error = consult_svc.fetch_rag_findings(
            args.problem,
            rag_url=args.rag_url,
            top_k=budget.adaptive_top_k,
            timeout=args.rag_timeout,
            source_prefixes=source_prefixes,
            recency_weight=args.rag_recency,
        )
        if rag_error:
            print(f"  RAG: unavailable ({rag_error})")
        elif rag_findings:
            sources = ", ".join(source_prefixes)
            print(f"  RAG: injected {len(rag_findings)} finding(s) from {sources}")
        else:
            print("  RAG: no matching findings")

    results = consult_svc.consult_step(
        step=step,
        problem=args.problem,
        call_label=args.call,
        models=args.models,
        rag_findings=rag_findings,
        stargate_url=args.url,
        timeout=args.timeout,
        output_limit_chars=output_limit,
    )

    for result in results:
        print("=" * 72)
        print(f"CONSULTANT: {result.model_id}")
        if result.error:
            print(f"[ERROR] {result.error}")
        else:
            print(
                f"Tokens: {result.prompt_tokens}+{result.completion_tokens} | "
                f"Latency: {result.latency_ms:.0f}ms"
            )
            print("=" * 72)
            print(result.response_text)
        print()


# ---------------------------------------------------------------------------
# ask
# ---------------------------------------------------------------------------


def _add_ask_parser(sub: Any) -> None:
    p = sub.add_parser(
        "ask",
        help="Ask local models a question with optional RAG research context",
    )
    p.add_argument(
        "--question",
        "-q",
        required=True,
        help="Free-form question to ask",
    )
    p.add_argument(
        "--models",
        nargs="+",
        metavar="MODEL",
        help=(f"Model IDs (default: {', '.join(ask_svc.DEFAULT_ASK_MODELS)})"),
    )
    p.add_argument(
        "--url",
        default=ask_svc.DEFAULT_STARGATE_URL,
        help="Stargate URL",
    )
    p.add_argument("--timeout", type=float, default=300.0, help="Request timeout (s)")
    rag_mode = p.add_mutually_exclusive_group()
    rag_mode.add_argument(
        "--no-rag",
        action="store_true",
        help="Disable RAG augmentation entirely",
    )
    rag_mode.add_argument(
        "--rag-pipeline",
        metavar="PIPELINE_ID",
        nargs="?",
        const=consult_svc.DEFAULT_RAG_PIPELINE_ID,
        default=consult_svc.DEFAULT_RAG_PIPELINE_ID,
        help=(
            "RAG pipeline for intelligent query rewriting + RRF retrieval "
            f"(default: {consult_svc.DEFAULT_RAG_PIPELINE_ID}). "
            "Use --no-rag to disable all retrieval."
        ),
    )
    p.add_argument(
        "--rag-url",
        default=consult_svc.DEFAULT_RAG_URL,
        help="RAG service URL (direct search path only)",
    )
    p.add_argument(
        "--rag-top-k",
        type=int,
        default=budget_svc.MAX_RAG_TOP_K,
        help="RAG chunk cap (direct search path only, adapts to context budget)",
    )
    p.add_argument(
        "--rag-timeout",
        type=float,
        default=consult_svc.DEFAULT_RAG_TIMEOUT,
        help="RAG request timeout (s)",
    )
    p.add_argument(
        "--rag-corpus",
        default=str(consult_svc.DEFAULT_RAG_CORPUS_DIR),
        help="Default source prefix for RAG lookup (direct search path only)",
    )
    p.add_argument(
        "--rag-source-prefix",
        action="append",
        default=[],
        help="Repeatable RAG source prefix override (direct search path only)",
    )
    p.add_argument(
        "--rag-recency",
        type=float,
        default=consult_svc.DEFAULT_RAG_RECENCY_WEIGHT,
        metavar="WEIGHT",
        help=(
            "Recency weight for RAG search [0.0-1.0]; "
            f"0 disables (default: {consult_svc.DEFAULT_RAG_RECENCY_WEIGHT})"
        ),
    )
    p.set_defaults(func=_cmd_ask)


def _cmd_ask(args: argparse.Namespace) -> None:
    target_models = args.models or ask_svc.DEFAULT_ASK_MODELS
    print(f"Question: {args.question}")
    print(f"  Models: {', '.join(target_models)}")

    rag_findings: list[str] | None = None
    if args.no_rag:
        print("  RAG: disabled")
    elif args.rag_pipeline:
        rag_findings, rag_error = consult_svc.fetch_rag_via_pipeline(
            args.question,
            pipeline_id=args.rag_pipeline,
            stargate_url=args.url,
            timeout=consult_svc.DEFAULT_RAG_PIPELINE_TIMEOUT,
        )
        if rag_error:
            print(f"  RAG pipeline '{args.rag_pipeline}': unavailable ({rag_error})")
        elif rag_findings:
            print(f"  RAG pipeline '{args.rag_pipeline}': assembled context injected")
        else:
            print(f"  RAG pipeline '{args.rag_pipeline}': no context returned")
    else:
        ctx_len = budget_svc.resolve_min_context_length(
            target_models,
            stargate_url=args.url,
        )
        fixed_chars = ask_svc.estimate_fixed_chars(args.question)
        budget = budget_svc.compute_budget(
            ctx_len,
            fixed_chars,
            output_chars=0,
            top_k_cap=args.rag_top_k,
        )
        print(f"  Budget: {ctx_len}tok context → RAG≤{budget.adaptive_top_k} chunks")

        source_prefixes = (
            [
                str(Path(prefix).expanduser().resolve())
                for prefix in args.rag_source_prefix
            ]
            if args.rag_source_prefix
            else [str(Path(args.rag_corpus).expanduser().resolve())]
        )
        rag_findings, rag_error = consult_svc.fetch_rag_findings(
            args.question,
            rag_url=args.rag_url,
            top_k=budget.adaptive_top_k,
            timeout=args.rag_timeout,
            source_prefixes=source_prefixes,
            recency_weight=args.rag_recency,
        )
        if rag_error:
            print(f"  RAG: unavailable ({rag_error})")
        elif rag_findings:
            sources = ", ".join(source_prefixes)
            print(f"  RAG: injected {len(rag_findings)} finding(s) from {sources}")
        else:
            print("  RAG: no matching findings")

    print()

    results = ask_svc.ask_models(
        args.question,
        models=args.models,
        rag_findings=rag_findings,
        stargate_url=args.url,
        timeout=args.timeout,
    )

    for result in results:
        print("=" * 72)
        print(f"MODEL: {result.model_id}")
        if result.error:
            print(f"[ERROR] {result.error}")
        else:
            print(
                f"Tokens: {result.prompt_tokens}+{result.completion_tokens} | "
                f"Latency: {result.latency_ms:.0f}ms"
            )
            print("=" * 72)
            print(result.response_text)
        print()


# ---------------------------------------------------------------------------
# ingest-papers
# ---------------------------------------------------------------------------


def _add_ingest_papers_parser(sub: Any) -> None:
    p = sub.add_parser(
        "ingest-papers",
        help="Copy prompt-engineering PDFs into RAG corpus directory",
    )
    p.add_argument(
        "sources",
        nargs="*",
        help="PDF files or directories (default: corpus dir)",
    )
    p.add_argument(
        "--corpus-dir",
        default=str(ingest_svc.DEFAULT_CORPUS_DIR),
        help="Corpus directory for PDF storage",
    )
    p.add_argument(
        "--non-recursive",
        action="store_true",
        help="Only scan top-level files in source directories",
    )
    p.add_argument(
        "--no-index",
        action="store_true",
        help="Skip immediate RAG /index_directory call",
    )
    p.add_argument(
        "--published",
        metavar="YYYY-MM-DD",
        help="Publication date to inject as metadata (e.g. 2023-07-06)",
    )
    p.add_argument(
        "--rag-url",
        default=ingest_svc.DEFAULT_RAG_URL,
        help="RAG service URL for indexing/status checks",
    )
    p.add_argument(
        "--rag-timeout",
        type=float,
        default=ingest_svc.DEFAULT_RAG_TIMEOUT,
        help="RAG request timeout (s)",
    )
    p.set_defaults(func=_cmd_ingest_papers)


def _cmd_ingest_papers(args: argparse.Namespace) -> None:
    corpus_dir = Path(args.corpus_dir).expanduser().resolve()
    records = ingest_svc.ingest_pdfs(
        sources=args.sources,
        corpus_dir=corpus_dir,
        recursive=not args.non_recursive,
    )
    if not records:
        print("No PDFs found to ingest.")
        return

    copied = [
        r for r in records if r.archived_to is not None and not r.skipped_duplicate
    ]
    duplicates = [r for r in records if r.skipped_duplicate]
    failed = [r for r in records if r.error]
    print(f"Ingested {len(copied)}/{len(records)} PDF(s) into {corpus_dir}")
    for record in copied:
        print(f"  [OK] {record.source_pdf.name} -> {record.archived_to}")
    for record in duplicates:
        print(f"  [DUP] {record.source_pdf.name} (duplicate of {record.duplicate_of})")
    for record in failed:
        print(f"  [ERR] {record.source_pdf.name} ({record.error})")

    if args.no_index:
        return

    metadata_overrides: dict[str, str | int | float | bool] | None = None
    if args.published:
        metadata_overrides = {"published_date": f"{args.published}T00:00:00+00:00"}

    status, status_error = ingest_svc.rag_watch_status(
        rag_url=args.rag_url,
        timeout=args.rag_timeout,
    )
    if status_error:
        print(f"RAG watch status unavailable: {status_error}")
    elif status is not None:
        watched_paths = [str(item.get("watch_path", "")) for item in status]
        if str(corpus_dir) not in watched_paths:
            print(
                "RAG watcher does not include corpus directory. "
                "Add it to ~/.rag/config.yaml watch_directories."
            )

    index_result, index_error = ingest_svc.index_corpus_directory(
        directory=corpus_dir,
        rag_url=args.rag_url,
        timeout=args.rag_timeout,
        metadata_overrides=metadata_overrides,
    )
    if index_error:
        print(f"RAG index skipped: {index_error}")
        return
    print(f"RAG indexed PDFs: {index_result}")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _resolve_step(snap: Any, step_name: str) -> Any:
    if step_name in snap.steps:
        return snap.steps[step_name]
    matches = [
        s
        for s in snap.steps.values()
        if s.step_name.endswith(f"__{step_name}") or s.step_name == step_name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = [m.step_name for m in matches]
        print(f"Ambiguous step '{step_name}', matches: {names}", file=sys.stderr)
        sys.exit(1)
    print(f"Step '{step_name}' not found.", file=sys.stderr)
    print("Available steps:", file=sys.stderr)
    for s in snap.step_order:
        print(f"  {s}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
