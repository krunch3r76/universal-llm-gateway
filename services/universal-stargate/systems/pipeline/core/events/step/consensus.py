"""Consensus pipeline bus event factories (coverage, organize facts, combine passages).

Callers: consensus pipeline handlers. Zero active call sites at time of split
(recorder dataclass versions in verification.py are used instead); preserved for
bus subscriber compatibility. Signals in namespace pipeline.consensus.*.
"""

from universal_event_bus import Event, event_factory


@event_factory
def CoverageAuditCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    total_facts: int,
    covered_count: int,
    uncovered_count: int,
    mean_score: float,
    coverage_pct: float,
    threshold: float,
) -> Event:
    """
    Emitted after embedding-based fact coverage audit completes.

    Payload:
        total_facts: Verified facts checked against the answer
        covered_count: Facts with best-sentence similarity >= threshold
        uncovered_count: Facts below the threshold
        mean_score: Mean of per-fact best-sentence similarity scores
        coverage_pct: covered_count / total_facts * 100
        threshold: Similarity threshold used
    """
    return Event(
        signal="pipeline.consensus.coverage.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "total_facts": total_facts,
            "covered_count": covered_count,
            "uncovered_count": uncovered_count,
            "mean_score": mean_score,
            "coverage_pct": coverage_pct,
            "threshold": threshold,
        },
    )


@event_factory
def OrganizeFactsCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    total_facts: int,
    sections_created: int,
    facts_assigned: int,
    valid_json: bool,
) -> Event:
    """
    Emitted after organize_facts generates and validates an outline.

    Payload:
        total_facts: Verified facts provided to organize_facts
        sections_created: Number of sections produced in the outline
        facts_assigned: Unique fact indices assigned across all sections
        valid_json: True when outline JSON is valid and assignment-complete
    """
    return Event(
        signal="pipeline.consensus.organize.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "total_facts": total_facts,
            "sections_created": sections_created,
            "facts_assigned": facts_assigned,
            "valid_json": valid_json,
        },
    )


@event_factory
def CombinePassagesCompleted(  # noqa: N802
    pipeline_id: str,
    execution_id: str,
    step_name: str,
    fact_count: int,
    chunk_count: int,
    cited_count: int,
    uncited_count: int,
    coverage_pct: float,
) -> Event:
    """
    Emitted after verified facts are synthesised into a combined answer.

    Payload:
        fact_count: Total verified facts sent to combine
        chunk_count: Number of synthesis chunks (1 = bootstrap only)
        cited_count: Unique fact indices cited at least once in the output
        uncited_count: Fact indices with no citation in the output
        coverage_pct: cited_count / fact_count * 100
    """
    return Event(
        signal="pipeline.consensus.combine.completed",
        payload={
            "pipeline_id": pipeline_id,
            "execution_id": execution_id,
            "step_name": step_name,
            "fact_count": fact_count,
            "chunk_count": chunk_count,
            "cited_count": cited_count,
            "uncited_count": uncited_count,
            "coverage_pct": coverage_pct,
        },
    )
