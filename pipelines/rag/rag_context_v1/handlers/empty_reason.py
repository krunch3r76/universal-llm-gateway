"""Classify why an accepted-scope RAG retrieve returned zero chunks."""

from __future__ import annotations


def classify_accepted_scope_empty_reason(
    *,
    chunks_after_merge: int,
    junk_wiped_all: bool,
    total_raw: int,
    queries_succeeded: int,
    queries_attempted: int,
) -> str | None:
    """Return ``empty_reason`` for a completed retrieve with zero chunks.

    ``None`` when chunks remain. Values:
    - ``junk_filtered`` — post-RRF noise filter removed every hit
    - ``index_miss_partial`` — some queries failed and survivors returned nothing
    - ``index_miss`` — every attempted query succeeded with zero hits
    - ``filtered`` — hits existed but other post-RRF caps wiped the set
    """
    if chunks_after_merge > 0:
        return None
    if junk_wiped_all:
        return "junk_filtered"
    if total_raw == 0:
        if queries_attempted > 0 and queries_succeeded < queries_attempted:
            return "index_miss_partial"
        return "index_miss"
    return "filtered"
