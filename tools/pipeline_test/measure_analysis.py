"""RAG profile measurement: data structures, analysis, and profile I/O.

Companion to ``measure.py`` which handles sweep execution. This module
owns the metric extraction, aggregation, optimal-value selection, and
profile persistence to ``retrieval-profiles.yaml``.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROFILES_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "pipelines"
    / "rag"
    / "retrieval-profiles.yaml"
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class SweepPoint:
    """Single measurement from one parameter configuration + question."""

    parameter: str
    value: int | float
    question_idx: int
    scope: str = "research"
    chunks: int = 0
    unique_sources: int = 0
    words: int = 0
    year_citations: int = 0
    source_mentions: int = 0
    latency_ms: float = 0.0
    error: str | None = None


@dataclass(slots=True, kw_only=True)
class SweepSummary:
    """Aggregated metrics for one (parameter, value, scope) combination."""

    parameter: str
    value: int | float
    scope: str = "research"
    avg_chunks: float = 0.0
    avg_unique_sources: float = 0.0
    avg_words: float = 0.0
    avg_year_citations: float = 0.0
    avg_source_mentions: float = 0.0
    n: int = 0


@dataclass(slots=True, kw_only=True)
class ProfileResult:
    """Recommended profile values from measurement run."""

    model_id: str
    rag_max_chunks: int | None = None
    rag_max_chunks_evidence: str = ""
    rag_rrf_k: int | None = None
    rag_rrf_k_evidence: str = ""
    scope_recency: dict[str, float] = field(default_factory=dict)
    scope_recency_evidence: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------


def parse_context_metrics(text: str) -> tuple[int, int]:
    """Extract (chunk_count, unique_source_count) from rag-context output.

    Parses the ``[Source: path | Last changed: ts]`` headers separated by
    ``---`` delimiters produced by ``_format_context``.
    """
    if not text.strip():
        return 0, 0
    chunks = text.split("\n\n---\n\n")
    sources: set[str] = set()
    for chunk in chunks:
        m = re.search(r"\[Source:\s*(.+?)\s*\|", chunk)
        if m:
            sources.add(m.group(1).strip())
    return len(chunks), len(sources)


def parse_answer_metrics(text: str) -> tuple[int, int, int]:
    """Extract (words, year_citations, source_mentions) from rag-answer output."""
    words = len(text.split())
    year_cites = len(re.findall(r"\b(?:19|20)\d{2}\b", text))
    source_mentions = len(re.findall(r"[Ss]ource", text))
    return words, year_cites, source_mentions


# ---------------------------------------------------------------------------
# Aggregation and recommendation
# ---------------------------------------------------------------------------


def aggregate(points: list[SweepPoint]) -> list[SweepSummary]:
    """Group sweep points by (parameter, value, scope) and average metrics."""
    groups: dict[tuple[str, int | float, str], list[SweepPoint]] = defaultdict(list)
    for p in points:
        if p.error is None:
            groups[(p.parameter, p.value, p.scope)].append(p)

    summaries: list[SweepSummary] = []
    for (param, val, scope), pts in sorted(groups.items()):
        n = len(pts)
        if n == 0:
            continue
        summaries.append(
            SweepSummary(
                parameter=param,
                value=val,
                scope=scope,
                n=n,
                avg_chunks=sum(p.chunks for p in pts) / n,
                avg_unique_sources=sum(p.unique_sources for p in pts) / n,
                avg_words=sum(p.words for p in pts) / n,
                avg_year_citations=sum(p.year_citations for p in pts) / n,
                avg_source_mentions=sum(p.source_mentions for p in pts) / n,
            )
        )
    return summaries


def _best_by_metric(
    summaries: list[SweepSummary],
    parameter: str,
    metric: str,
    scope: str | None = None,
) -> SweepSummary | None:
    """Pick the summary with the highest metric for a given parameter."""
    filtered = [
        s
        for s in summaries
        if s.parameter == parameter and (scope is None or s.scope == scope)
    ]
    return max(filtered, key=lambda s: getattr(s, metric)) if filtered else None


def recommend_profile(
    model_id: str,
    all_points: list[SweepPoint],
) -> ProfileResult:
    """Analyze sweep data and produce a profile recommendation."""
    summaries = aggregate(all_points)
    result = ProfileResult(model_id=model_id)

    best_mc = _best_by_metric(summaries, "rag_max_chunks", "avg_year_citations")
    if best_mc:
        result.rag_max_chunks = int(best_mc.value)
        result.rag_max_chunks_evidence = (
            f"peak year_citations={best_mc.avg_year_citations:.1f} (n={best_mc.n})"
        )

    best_k = _best_by_metric(summaries, "rag_rrf_k", "avg_unique_sources")
    if best_k:
        result.rag_rrf_k = int(best_k.value)
        result.rag_rrf_k_evidence = (
            f"peak unique_sources={best_k.avg_unique_sources:.1f} (n={best_k.n})"
        )

    for scope in ("research", "project", "both"):
        best_rw = _best_by_metric(
            summaries, "rag_recency_weight", "avg_unique_sources", scope=scope
        )
        if best_rw:
            result.scope_recency[scope] = float(best_rw.value)
            result.scope_recency_evidence[scope] = (
                f"rw={best_rw.value} unique_sources={best_rw.avg_unique_sources:.1f}"
            )

    return result


# ---------------------------------------------------------------------------
# Profile I/O
# ---------------------------------------------------------------------------

_PROFILES_HEADER = """\
# Empirically measured optimal RAG retrieval tunables.
# Source: docs/engram/rag-tunable-optimization-findings.md
#
# Loaded by rag_query_retrieve handler at first use.
# Keyed by consumer model (the model that reads the retrieved context).
#
# Resolution order in the handler:
#   runtime pipeline_options  >  profile[consumer_model]  >  scope_defaults[scope]  >  YAML defaults
#
# Callers pass consumer_model via pipeline_options (or consumer_model_ref
# on pipeline_call_v1 steps for automatic alias resolution).
#
# Measurement scripts can append profiles here; the handler re-reads
# on next cold start.

"""


def load_profiles(path: Path | None = None) -> dict[str, Any]:
    """Load existing retrieval-profiles.yaml."""
    target = path or PROFILES_PATH
    if not target.exists():
        return {"profiles": {}, "scope_defaults": {}}
    with target.open() as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("profiles", {})
    data.setdefault("scope_defaults", {})
    return data


def write_profile(
    rec: ProfileResult,
    path: Path | None = None,
    *,
    dry_run: bool = False,
) -> str:
    """Merge recommendation into retrieval-profiles.yaml.

    Returns a summary string describing what was written.
    """
    target = path or PROFILES_PATH
    data = load_profiles(target)

    profile_entry: dict[str, Any] = {}
    if rec.rag_max_chunks is not None:
        profile_entry["rag_max_chunks"] = rec.rag_max_chunks
    if rec.rag_rrf_k is not None:
        profile_entry["rag_rrf_k"] = rec.rag_rrf_k

    if profile_entry:
        data["profiles"][rec.model_id] = profile_entry

    for scope, weight in rec.scope_recency.items():
        if scope not in data["scope_defaults"]:
            data["scope_defaults"][scope] = {}
        data["scope_defaults"][scope]["rag_recency_weight"] = weight

    rendered = yaml.dump(data, default_flow_style=False, sort_keys=False)
    if dry_run:
        return f"[dry-run] Would write to {target}:\n{rendered}"

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w") as f:
        f.write(_PROFILES_HEADER)
        f.write(rendered)
    return f"Profile for '{rec.model_id}' written to {target}"


# ---------------------------------------------------------------------------
# Results persistence
# ---------------------------------------------------------------------------


def save_results(
    points: list[SweepPoint],
    path: Path,
) -> None:
    """Save raw sweep points as JSONL for reproducibility."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for p in points:
            f.write(json.dumps(asdict(p)) + "\n")


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def format_summary_tables(summaries: list[SweepSummary]) -> str:
    """Format sweep results as aligned text tables."""
    lines: list[str] = []
    parameters = sorted(set(s.parameter for s in summaries))
    for param in parameters:
        param_sums = [s for s in summaries if s.parameter == param]
        scopes = sorted(set(s.scope for s in param_sums))
        for scope in scopes:
            scope_sums = [s for s in param_sums if s.scope == scope]
            if not scope_sums:
                continue
            lines.append(f"\n--- {param} (scope={scope}) ---")
            if param == "rag_max_chunks":
                lines.append(
                    f"{'value':>8} {'avg_words':>10} "
                    f"{'avg_yr_cite':>12} {'avg_src_ment':>13} {'n':>4}"
                )
                for s in scope_sums:
                    lines.append(
                        f"{s.value:>8} {s.avg_words:>10.1f} "
                        f"{s.avg_year_citations:>12.1f} "
                        f"{s.avg_source_mentions:>13.1f} {s.n:>4}"
                    )
            else:
                lines.append(
                    f"{'value':>8} {'avg_chunks':>11} {'avg_unique_src':>15} {'n':>4}"
                )
                for s in scope_sums:
                    lines.append(
                        f"{s.value:>8} {s.avg_chunks:>11.1f} "
                        f"{s.avg_unique_sources:>15.1f} {s.n:>4}"
                    )
    return "\n".join(lines)


def format_recommendation(rec: ProfileResult) -> str:
    """Format recommended profile values."""
    lines = [f"\n=== Recommendation for '{rec.model_id}' ==="]
    if rec.rag_max_chunks is not None:
        lines.append(
            f"  rag_max_chunks: {rec.rag_max_chunks}  ({rec.rag_max_chunks_evidence})"
        )
    if rec.rag_rrf_k is not None:
        lines.append(f"  rag_rrf_k: {rec.rag_rrf_k}  ({rec.rag_rrf_k_evidence})")
    for scope, weight in sorted(rec.scope_recency.items()):
        evidence = rec.scope_recency_evidence.get(scope, "")
        lines.append(f"  recency_weight[{scope}]: {weight}  ({evidence})")
    return "\n".join(lines)
