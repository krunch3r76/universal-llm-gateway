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
    consult     Query consultant models for prompt improvements (chained by default)
    ingest-papers  Copy PDFs into RAG corpus directory for indexing
    sandbox     Manage pipeline sandboxes for experimentation
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import compare as compare_svc
from . import consult as consult_svc
from . import format as format_svc
from . import ingest as ingest_svc
from . import measure as measure_svc
from . import measure_analysis as measure_analysis_svc
from . import replay as replay_svc
from . import sandbox as sandbox_svc
from . import snapshot as snapshot_svc
from .models import ConsultResult, ReplayOverrides

FIXTURES_DIR = Path(__file__).parent / "fixtures"
DEFAULT_REPLAY_DIR = Path("/tmp/replay")


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
    _add_ingest_papers_parser(sub)
    _add_measure_profile_parser(sub)
    _add_sandbox_parser(sub)

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
    p.add_argument(
        "--execution", "-e", help="Specific execution ID (default: latest completed)"
    )
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
    p.add_argument(
        "--output",
        "-o",
        help=(
            f"Save replay result JSON. If relative, saved under {DEFAULT_REPLAY_DIR}."
        ),
    )
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
            pipeline_dir=args.pipeline_dir,
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
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = DEFAULT_REPLAY_DIR / output_path
        replay_svc.save_replay_result(result, output_path)
        print(f"\nResult saved: {output_path}")


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
    replay_path = Path(args.replay_file)
    if not replay_path.is_absolute() and not replay_path.exists():
        candidate = DEFAULT_REPLAY_DIR / replay_path
        if candidate.exists():
            replay_path = candidate
    replay_result = replay_svc.load_replay_result(replay_path)

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
        help="Query other models for prompt improvement suggestions (chained by default)",
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
        help="Consultant model IDs (default: role-based auto-selection)",
    )
    p.add_argument(
        "--url",
        default="http://localhost:9999",
        help="Stargate URL",
    )
    p.add_argument("--timeout", type=float, default=300.0, help="Request timeout (s)")
    p.add_argument(
        "--no-rag",
        action="store_true",
        help="Disable RAG augmentation entirely",
    )
    p.add_argument(
        "--scope",
        default=None,
        help=(
            "RAG scope override (default: auto-detected from model tier). "
            "See ~/.gateway/rag.yaml for available scopes."
        ),
    )
    p.add_argument(
        "--parallel",
        action="store_true",
        help=(
            "Run models in parallel instead of chained "
            "(default: chained — each model reviews the prior model's output)"
        ),
    )
    p.add_argument(
        "--output-limit",
        type=int,
        default=None,
        metavar="CHARS",
        help="Truncate model output to this many chars in the consultation context",
    )
    p.set_defaults(func=_cmd_consult)


def _cmd_consult(args: argparse.Namespace) -> None:
    snap = _load_snapshot(args)
    step = _resolve_step(snap, args.step)

    mode = "parallel" if args.parallel else "chained"
    print(f"Consulting about: {step.step_name} ({mode})")
    print(f"  Problem: {args.problem}")
    if args.scope:
        print(f"  Scope: {args.scope} (explicit)")
    else:
        detected = consult_svc.detect_scope(step)
        print(f"  Scope: {detected} (auto-detected from model tier)")
    print()

    results = consult_svc.consult_step_via_lib(
        step=step,
        problem=args.problem,
        call_label=args.call,
        models=args.models,
        scope=args.scope,
        parallel=args.parallel,
        stargate_url=args.url,
        timeout=args.timeout,
        output_limit_chars=args.output_limit,
        no_rag=args.no_rag,
    )

    for result in results:
        _print_result(result, "CONSULTANT")


def _print_result(result: ConsultResult, label: str) -> None:
    """Print a single model result with separator."""
    print("=" * 72)
    print(f"{label}: {result.model_id}")
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
                "Add it to ~/.gateway/rag.yaml watch_directories."
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
# measure-profile
# ---------------------------------------------------------------------------


def _add_measure_profile_parser(sub: Any) -> None:
    p = sub.add_parser(
        "measure-profile",
        help="Sweep RAG tunables to find optimal values for a consumer model",
    )
    p.add_argument(
        "--model",
        "-m",
        required=True,
        help="Consumer model ID for the resulting retrieval profile",
    )
    p.add_argument(
        "--sweep",
        nargs="+",
        choices=["rrf_k", "max_chunks", "recency"],
        default=["rrf_k", "max_chunks", "recency"],
        help="Which parameter sweeps to run (default: all three)",
    )
    p.add_argument(
        "--scopes",
        nargs="+",
        choices=["research", "project", "both"],
        default=["research", "project"],
        help="Scopes for recency sweep (default: research project)",
    )
    p.add_argument(
        "--questions",
        nargs="+",
        help=(
            "Test questions for the sweep "
            f"(default: {len(measure_svc.DEFAULT_QUESTIONS)} standard queries)"
        ),
    )
    p.add_argument(
        "--url",
        default=measure_svc.DEFAULT_STARGATE_URL,
        help="Stargate URL",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=measure_svc.DEFAULT_TIMEOUT,
        help="Per-request timeout in seconds",
    )
    p.add_argument(
        "--output",
        "-o",
        default=None,
        help=(
            "Override output path for retrieval-profiles.yaml "
            f"(default: {measure_analysis_svc.PROFILES_PATH})"
        ),
    )
    p.add_argument(
        "--save-results",
        metavar="DIR",
        default=None,
        help="Save raw JSONL results to DIR (e.g. ./tmp/rag-measure)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show recommended profile without writing to disk",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print progress for each individual run",
    )
    p.set_defaults(func=_cmd_measure_profile)


def _cmd_measure_profile(args: argparse.Namespace) -> None:
    output_path = Path(args.output) if args.output else None
    results_dir = Path(args.save_results) if args.save_results else None

    print(f"Measuring RAG retrieval profile for: {args.model}")
    print(f"  Sweeps: {', '.join(args.sweep)}")
    print(f"  Scopes: {', '.join(args.scopes)}")
    qs = args.questions or measure_svc.DEFAULT_QUESTIONS
    print(f"  Questions: {len(qs)}")
    print()

    measure_svc.measure_profile(
        args.model,
        questions=args.questions,
        sweeps=args.sweep,
        scopes=args.scopes,
        stargate_url=args.url,
        timeout=args.timeout,
        output_path=output_path,
        results_dir=results_dir,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )


# ---------------------------------------------------------------------------
# sandbox
# ---------------------------------------------------------------------------


def _add_sandbox_parser(sub: Any) -> None:
    p = sub.add_parser("sandbox", help="Manage pipeline sandboxes for experimentation")
    sp = p.add_subparsers(dest="sandbox_cmd", required=True)

    create = sp.add_parser("create", help="Copy a pipeline directory into sandbox")
    create.add_argument("source", help="Pipeline directory to copy")
    create.add_argument("--name", help="Sandbox name (default: derived from path)")
    create.set_defaults(func=_cmd_sandbox_create)

    apply = sp.add_parser("apply", help="Copy changed files back to repo")
    apply.add_argument("name", help="Sandbox name")
    apply.add_argument("target", help="Repository pipeline directory to update")
    apply.set_defaults(func=_cmd_sandbox_apply)

    listing = sp.add_parser("list", help="List active sandboxes")
    listing.set_defaults(func=_cmd_sandbox_list)

    clean = sp.add_parser("clean", help="Delete one or all sandboxes")
    clean.add_argument("name", nargs="?", help="Sandbox name (omit to delete all)")
    clean.set_defaults(func=_cmd_sandbox_clean)


def _cmd_sandbox_create(args: argparse.Namespace) -> None:
    try:
        path = sandbox_svc.create_sandbox(args.source, name=args.name)
    except (FileNotFoundError, FileExistsError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print(f"Sandbox created: {path}")
    print(f"  Use with: --pipeline-dir {path}")


def _cmd_sandbox_apply(args: argparse.Namespace) -> None:
    try:
        updated = sandbox_svc.apply_sandbox(args.name, args.target)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    if not updated:
        print("No files changed; target already matches sandbox.")
        return
    print(f"Updated {len(updated)} file(s):")
    for rel in updated:
        print(f"  {rel}")


def _cmd_sandbox_list(_args: argparse.Namespace) -> None:
    sandboxes = sandbox_svc.list_sandboxes()
    if not sandboxes:
        print("No sandboxes found.")
        return
    print("Sandboxes:")
    for path in sandboxes:
        print(f"  {path.name}  ({path})")


def _cmd_sandbox_clean(args: argparse.Namespace) -> None:
    sandbox_svc.clean_sandbox(args.name)
    if args.name:
        print(f"Sandbox '{args.name}' deleted.")
    else:
        print("All sandboxes deleted.")


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
