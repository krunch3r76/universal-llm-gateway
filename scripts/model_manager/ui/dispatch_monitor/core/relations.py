"""Evidence-only relationship projection for the supervisor frame.

Edges are asserted payload links (park, review-child, resume, correlation).
No timestamp-proximity joins and no HTTP lineage fetch.
"""

from __future__ import annotations

from . import signals
from .correlation import CorrelationIndex
from .dtos import CdpLegRow, RelationEdge, SdkDispatchRow
from .folds.sdk import SdkFold


def project_relations(
    *,
    fold: SdkFold,
    index: CorrelationIndex,
    dispatches: tuple[SdkDispatchRow, ...],
    legs: tuple[CdpLegRow, ...],
) -> tuple[RelationEdge, ...]:
    """Return a deterministically ordered tuple of evidence-backed edges."""
    edges: list[RelationEdge] = []
    seen: set[tuple[str, str, str]] = set()

    def add(kind: str, from_id: str, to_id: str, evidence_signal: str) -> None:
        if not from_id or not to_id or from_id == to_id:
            return
        key = (kind, from_id, to_id)
        if key in seen:
            return
        seen.add(key)
        edges.append(
            RelationEdge(
                kind=kind,
                from_id=from_id,
                to_id=to_id,
                evidence_signal=evidence_signal,
            )
        )

    for parent_id, child_id in fold.lease_parks:
        add(kind="lease_park", from_id=parent_id, to_id=child_id,
            evidence_signal=signals.SDK_LEASE_PARK_ENTER)

    for row in dispatches:
        if row.review_child and row.parent_execution_id:
            add(
                kind="review_child",
                from_id=row.parent_execution_id,
                to_id=row.dispatch_id,
                evidence_signal=signals.SDK_REVIEW_CHILD_SPAWNED,
            )
        if row.resume_of:
            add(
                kind="resume_of",
                from_id=row.dispatch_id,
                to_id=row.resume_of,
                evidence_signal=signals.SDK_WORKER_RESUMED,
            )
        if row.nest_under:
            add(
                kind="nest_under",
                from_id=row.nest_under,
                to_id=row.dispatch_id,
                evidence_signal=signals.SDK_WORKER_DISPATCHED,
            )
        root_id = row.root_id or index.root_for_dispatch(row.dispatch_id)
        if root_id:
            add(
                kind="root_dispatch",
                from_id=root_id,
                to_id=row.dispatch_id,
                evidence_signal="correlation.root_dispatch",
            )

    for row in legs:
        root_id = row.root_id or index.root_for_cdp(row.request_id)
        if root_id:
            add(
                kind="root_cdp",
                from_id=root_id,
                to_id=row.request_id,
                evidence_signal="correlation.root_cdp",
            )

    edges.sort(key=lambda e: (e.kind, e.from_id, e.to_id, e.evidence_signal))
    return tuple(edges)
