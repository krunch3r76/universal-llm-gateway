"""RAG retrieval profile measurement: sweep execution and orchestration.

Runs controlled parameter sweeps against rag-context and rag-answer
pipelines via the Stargate API, then delegates analysis and profile
writing to ``measure_analysis``.

Methodology from ``docs/engram/rag-tunable-optimization-findings.md``.
Only high-sensitivity tunables are swept; settled parameters are held fixed:

    Swept:
        ``rag_max_chunks``      — via rag-answer (citation quality, lost-in-middle)
        ``rag_rrf_k``           — via rag-context (unique source diversity)
        ``rag_recency_weight``  — via rag-context per scope (source diversity)

    Settled (not swept):
        ``rag_top_k_per_query``         — saturated at 10
        ``scope_confidence_threshold``  — confirmed at 0.7
        ``analyze_rewrite`` temperature — confirmed at 0.4
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from .measure_analysis import (
    ProfileResult,
    SweepPoint,
    aggregate,
    format_recommendation,
    format_summary_tables,
    parse_answer_metrics,
    parse_context_metrics,
    recommend_profile,
    save_results,
    write_profile,
)

DEFAULT_STARGATE_URL = "http://localhost:9999"
DEFAULT_TIMEOUT = 180.0

DEFAULT_QUESTIONS: list[str] = [
    "What is chain-of-thought prompting and how does it improve reasoning?",
    "How does the rag-context pipeline handle scope classification?",
    "What are the best practices for multi-agent LLM debate for factuality?",
    "Compare dense retrieval with hybrid BM25+dense approaches for technical documentation",
    "What prompt optimization techniques exist for small language models under 10B parameters?",
]

RRF_K_SWEEP: list[int] = [20, 35, 50, 60]
MAX_CHUNKS_SWEEP: list[int] = [5, 10, 15, 20, 30]
RECENCY_SWEEP: list[float] = [0.0, 0.1, 0.2, 0.3]


# ---------------------------------------------------------------------------
# Stargate API
# ---------------------------------------------------------------------------


def _call_pipeline(
    pipeline_id: str,
    question: str,
    pipeline_options: dict[str, Any],
    *,
    stargate_url: str = DEFAULT_STARGATE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[str, float]:
    """Call a pipeline via Stargate, return (content, latency_ms)."""
    url = f"{stargate_url.rstrip('/')}/v1/chat/completions"
    body: dict[str, Any] = {
        "model": pipeline_id,
        "messages": [{"role": "user", "content": question}],
        "stream": False,
        "pipeline_options": pipeline_options,
    }
    start = time.monotonic()
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=body)
    elapsed = (time.monotonic() - start) * 1000
    resp.raise_for_status()
    data = resp.json()
    content: str = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return content, elapsed


# ---------------------------------------------------------------------------
# Generic sweep runner
# ---------------------------------------------------------------------------


def _run_sweep(
    *,
    parameter: str,
    values: list[int] | list[float],
    questions: list[str],
    pipeline_id: str,
    build_options: Any,
    parse_fn: Any,
    scopes: list[str] | None = None,
    stargate_url: str = DEFAULT_STARGATE_URL,
    timeout: float = DEFAULT_TIMEOUT,
    verbose: bool = False,
) -> list[SweepPoint]:
    """Vary one parameter across values x questions x scopes, collecting metrics."""
    scope_list = scopes or ["research"]
    points: list[SweepPoint] = []
    total = len(values) * len(scope_list) * len(questions)
    done = 0
    for val in values:
        for scope in scope_list:
            for qi, q in enumerate(questions):
                done += 1
                if verbose:
                    label = f"{parameter}={val}"
                    if len(scope_list) > 1:
                        label += f" scope={scope}"
                    print(
                        f"  {label} q{qi + 1}/{len(questions)} [{done}/{total}]",
                        flush=True,
                    )
                opts = build_options(val, scope)
                try:
                    text, latency = _call_pipeline(
                        pipeline_id,
                        q,
                        opts,
                        stargate_url=stargate_url,
                        timeout=timeout,
                    )
                    metrics = parse_fn(text)
                    point = SweepPoint(
                        parameter=parameter,
                        value=val,
                        question_idx=qi,
                        scope=scope,
                        latency_ms=latency,
                        **metrics,
                    )
                except Exception as exc:  # noqa: BLE001
                    point = SweepPoint(
                        parameter=parameter,
                        value=val,
                        question_idx=qi,
                        scope=scope,
                        error=str(exc),
                    )
                points.append(point)
    return points


# ---------------------------------------------------------------------------
# Per-parameter sweep runners
# ---------------------------------------------------------------------------


def run_rrf_k_sweep(
    questions: list[str],
    *,
    k_values: list[int] | None = None,
    scope: str = "research",
    stargate_url: str = DEFAULT_STARGATE_URL,
    timeout: float = DEFAULT_TIMEOUT,
    verbose: bool = False,
) -> list[SweepPoint]:
    """Sweep ``rag_rrf_k`` via rag-context."""

    def _build(k: int | float, scope: str) -> dict[str, Any]:
        return {"rag_rrf_k": int(k), "scope_override": scope}

    def _parse(text: str) -> dict[str, Any]:
        chunks, unique = parse_context_metrics(text)
        return {"chunks": chunks, "unique_sources": unique}

    return _run_sweep(
        parameter="rag_rrf_k",
        values=k_values or RRF_K_SWEEP,
        questions=questions,
        pipeline_id="rag-context",
        build_options=_build,
        parse_fn=_parse,
        scopes=[scope],
        stargate_url=stargate_url,
        timeout=timeout,
        verbose=verbose,
    )


def run_max_chunks_sweep(
    questions: list[str],
    *,
    mc_values: list[int] | None = None,
    scope: str = "research",
    stargate_url: str = DEFAULT_STARGATE_URL,
    timeout: float = DEFAULT_TIMEOUT,
    verbose: bool = False,
) -> list[SweepPoint]:
    """Sweep ``rag_max_chunks`` via rag-answer (measures citation quality)."""

    def _build(mc: int | float, scope: str) -> dict[str, Any]:
        return {"rag_max_chunks": int(mc), "scope_override": scope}

    def _parse(text: str) -> dict[str, Any]:
        words, year_cites, src_mentions = parse_answer_metrics(text)
        return {
            "words": words,
            "year_citations": year_cites,
            "source_mentions": src_mentions,
        }

    return _run_sweep(
        parameter="rag_max_chunks",
        values=mc_values or MAX_CHUNKS_SWEEP,
        questions=questions,
        pipeline_id="rag-answer",
        build_options=_build,
        parse_fn=_parse,
        scopes=[scope],
        stargate_url=stargate_url,
        timeout=timeout,
        verbose=verbose,
    )


def run_recency_sweep(
    questions: list[str],
    *,
    rw_values: list[float] | None = None,
    scopes: list[str] | None = None,
    stargate_url: str = DEFAULT_STARGATE_URL,
    timeout: float = DEFAULT_TIMEOUT,
    verbose: bool = False,
) -> list[SweepPoint]:
    """Sweep ``rag_recency_weight`` x scope via rag-context."""

    def _build(rw: int | float, scope: str) -> dict[str, Any]:
        return {"rag_recency_weight": float(rw), "scope_override": scope}

    def _parse(text: str) -> dict[str, Any]:
        chunks, unique = parse_context_metrics(text)
        return {"chunks": chunks, "unique_sources": unique}

    return _run_sweep(
        parameter="rag_recency_weight",
        values=rw_values or RECENCY_SWEEP,
        questions=questions,
        pipeline_id="rag-context",
        build_options=_build,
        parse_fn=_parse,
        scopes=scopes or ["research", "project"],
        stargate_url=stargate_url,
        timeout=timeout,
        verbose=verbose,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def measure_profile(
    model_id: str,
    *,
    questions: list[str] | None = None,
    sweeps: list[str] | None = None,
    scopes: list[str] | None = None,
    stargate_url: str = DEFAULT_STARGATE_URL,
    timeout: float = DEFAULT_TIMEOUT,
    output_path: Path | None = None,
    results_dir: Path | None = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> ProfileResult:
    """Run full measurement suite and produce a profile recommendation.

    Args:
        model_id: Consumer model ID for the resulting profile.
        questions: Test questions (defaults to 5 standard queries).
        sweeps: Which sweeps to run: ``rrf_k``, ``max_chunks``, ``recency``.
        scopes: Scopes for recency sweep (default: research, project).
        stargate_url: Stargate endpoint.
        timeout: Per-request timeout in seconds.
        output_path: Override for retrieval-profiles.yaml location.
        results_dir: Directory for raw JSONL results.
        dry_run: Print what would be written without writing.
        verbose: Print progress per run.
    """
    qs = questions or DEFAULT_QUESTIONS
    active = set(sweeps or ["rrf_k", "max_chunks", "recency"])
    all_points: list[SweepPoint] = []

    if "rrf_k" in active:
        n = len(RRF_K_SWEEP) * len(qs)
        print(f"=== RRF k sweep ({n} runs) ===")
        all_points.extend(
            run_rrf_k_sweep(
                qs,
                stargate_url=stargate_url,
                timeout=timeout,
                verbose=verbose,
            )
        )

    if "max_chunks" in active:
        n = len(MAX_CHUNKS_SWEEP) * len(qs)
        print(f"=== max_chunks sweep ({n} runs) ===")
        all_points.extend(
            run_max_chunks_sweep(
                qs,
                stargate_url=stargate_url,
                timeout=timeout,
                verbose=verbose,
            )
        )

    if "recency" in active:
        scope_list = scopes or ["research", "project"]
        n = len(RECENCY_SWEEP) * len(scope_list) * len(qs)
        print(f"=== recency sweep ({n} runs) ===")
        all_points.extend(
            run_recency_sweep(
                qs,
                scopes=scope_list,
                stargate_url=stargate_url,
                timeout=timeout,
                verbose=verbose,
            )
        )

    errors = [p for p in all_points if p.error]
    if errors:
        print(f"\n{len(errors)} failed runs (of {len(all_points)} total)")

    if results_dir:
        results_path = results_dir / "results.jsonl"
        save_results(all_points, results_path)
        print(f"Raw results saved to {results_path}")

    summaries = aggregate(all_points)
    print(format_summary_tables(summaries))

    rec = recommend_profile(model_id, all_points)
    print(format_recommendation(rec))

    result_msg = write_profile(rec, output_path, dry_run=dry_run)
    print(f"\n{result_msg}")

    return rec
