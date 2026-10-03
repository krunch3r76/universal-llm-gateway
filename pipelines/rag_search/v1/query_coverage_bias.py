from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .retrieval_execution import RetrievedChunk


_ENUMERATION_CUES: tuple[str, ...] = (
    "what does",
    "which",
    "supported",
    "support",
    "capabilities",
    "features",
    "order types",
    "api",
    "endpoints",
    "parameters",
    "options",
    "strategies",
)


@dataclass(slots=True)
class CoverageBiasResult:
    chunks: list[RetrievedChunk]
    scores: dict[str, float]
    applied: bool
    query_class: str
    anchor_source: str | None
    boosted_chunks: int
    distinct_sections: int


def classify_query_class(query: str) -> str:
    normalized = " ".join(query.lower().split())
    if not normalized:
        return "default"
    if any(cue in normalized for cue in _ENUMERATION_CUES):
        return "enumeration"
    return "default"


def apply_query_coverage_bias(
    chunks: list[RetrievedChunk],
    scores: dict[str, float],
    *,
    query: str,
    enabled: bool,
    anchor_min_score_share: float,
    anchor_boost: float,
    section_cap: int,
) -> CoverageBiasResult:
    query_class = classify_query_class(query)
    if (
        not enabled
        or query_class != "enumeration"
        or not chunks
        or not scores
        or section_cap <= 0
        or anchor_boost == 1.0
    ):
        return CoverageBiasResult(
            chunks=chunks,
            scores=scores,
            applied=False,
            query_class=query_class,
            anchor_source=None,
            boosted_chunks=0,
            distinct_sections=0,
        )

    source_totals: dict[str, float] = {}
    for chunk in chunks:
        score = scores.get(chunk.content_hash, 0.0)
        if score <= 0.0:
            continue
        source_totals[chunk.source] = source_totals.get(chunk.source, 0.0) + score

    if not source_totals:
        return CoverageBiasResult(
            chunks=chunks,
            scores=scores,
            applied=False,
            query_class=query_class,
            anchor_source=None,
            boosted_chunks=0,
            distinct_sections=0,
        )

    total_score = sum(source_totals.values())
    anchor_source, anchor_score = max(source_totals.items(), key=lambda item: item[1])
    if total_score <= 0.0 or (anchor_score / total_score) < anchor_min_score_share:
        return CoverageBiasResult(
            chunks=chunks,
            scores=scores,
            applied=False,
            query_class=query_class,
            anchor_source=anchor_source,
            boosted_chunks=0,
            distinct_sections=0,
        )

    boosted_scores = dict(scores)
    seen_sections: set[str] = set()
    boosted_chunks = 0

    anchor_chunks = sorted(
        (chunk for chunk in chunks if chunk.source == anchor_source),
        key=lambda chunk: boosted_scores.get(chunk.content_hash, 0.0),
        reverse=True,
    )
    for chunk in anchor_chunks:
        section = str(
            chunk.metadata.get("section_path")
            or chunk.metadata.get("heading")
            or f"chunk:{chunk.content_hash}"
        )
        if section in seen_sections:
            continue
        seen_sections.add(section)
        if len(seen_sections) > section_cap:
            break
        if chunk.content_hash not in boosted_scores:
            continue
        boosted_scores[chunk.content_hash] = (
            boosted_scores[chunk.content_hash] * anchor_boost
        )
        boosted_chunks += 1

    if boosted_chunks == 0:
        return CoverageBiasResult(
            chunks=chunks,
            scores=scores,
            applied=False,
            query_class=query_class,
            anchor_source=anchor_source,
            boosted_chunks=0,
            distinct_sections=len(seen_sections),
        )

    boosted_chunks_sorted = sorted(
        chunks,
        key=lambda chunk: boosted_scores.get(chunk.content_hash, 0.0),
        reverse=True,
    )
    return CoverageBiasResult(
        chunks=boosted_chunks_sorted,
        scores=boosted_scores,
        applied=True,
        query_class=query_class,
        anchor_source=anchor_source,
        boosted_chunks=boosted_chunks,
        distinct_sections=len(seen_sections),
    )
