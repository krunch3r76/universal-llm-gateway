"""RAG scope, search, and corpus-hint event factories."""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def rag_scope_resolved(
    *,
    scope: str | list[str],
    prefix_count: int,
) -> Event:
    return Event(
        signal="rag.scope.resolved",
        payload={"scope": scope, "prefix_count": prefix_count},
    )


@event_factory
def rag_scope_rejected(
    *,
    scope: str | list[str],
    reason: str,
    available: list[str],
) -> Event:
    return Event(
        signal="rag.scope.rejected",
        payload={"scope": scope, "reason": reason, "available": available},
    )


@event_factory
def rag_scopes_listed(*, count: int) -> Event:
    return Event(signal="rag.scopes.listed", payload={"count": count})


@event_factory
def rag_search_embedding_failed(
    *,
    model_id: str,
    attempts: int,
    last_status: int | None,
    query_len: int,
    scope: str | list[str] | None,
) -> Event:
    """Emitted when embed_query retries are exhausted during a search request.

    Proves transient embedding unavailability from event logs alone.
    """
    return Event(
        signal="rag.search.embedding.failed",
        payload={
            "model_id": model_id,
            "attempts": attempts,
            "last_status": last_status,
            "query_len": query_len,
            "scope": scope,
        },
    )


@event_factory
def rag_embedding_query_success(
    *,
    model_id: str,
    query_len: int,
    scope: str | list[str] | None,
) -> Event:
    """Emitted when a query embedding call succeeds."""
    return Event(
        signal="rag.embedding.query.success",
        payload={"model_id": model_id, "query_len": query_len, "scope": scope},
    )


@event_factory
def rag_embedding_query_failed(
    *,
    model_id: str,
    attempts: int,
    last_status: int | None,
    query_len: int,
    scope: str | list[str] | None,
) -> Event:
    """Emitted when query embedding retries are exhausted."""
    return Event(
        signal="rag.embedding.query.failed",
        payload={
            "model_id": model_id,
            "attempts": attempts,
            "last_status": last_status,
            "query_len": query_len,
            "scope": scope,
        },
    )


@event_factory
def rag_search_executed(
    *,
    query_len: int,
    top_k: int,
    results: int,
    scope: str | list[str] | None,
) -> Event:
    """Emitted after a search query completes."""
    return Event(
        signal="rag.search.executed",
        payload={
            "query_len": query_len,
            "top_k": top_k,
            "results": results,
            "scope": scope,
        },
    )


@event_factory
def rag_search_no_results(
    *,
    query_len: int,
    scope: str | list[str] | None,
) -> Event:
    """Emitted when a search returns zero results."""
    return Event(
        signal="rag.search.no.results",
        payload={"query_len": query_len, "scope": scope},
    )


@event_factory
def rag_search_tier_applied(
    *,
    tier_hits: int,
    scope: str | list[str] | None,
) -> Event:
    """Emitted when tier_weight is applied to a search request and at least one chunk matched.

    tier_hits: number of chunks whose distance was adjusted by a provenance_tier weight.
    """
    return Event(
        signal="rag.search.tier.applied",
        payload={"tier_hits": tier_hits, "scope": scope},
    )


@event_factory
def rag_corpus_hints_updated(
    *,
    path: str,
    scopes_updated: list[str],
    timestamp: str,
) -> Event:
    """Emitted after corpus_hints.yaml is written following aggregation from the property index."""
    return Event(
        signal="rag.corpus.hints.updated",
        payload={
            "path": path,
            "scopes_updated": scopes_updated,
            "timestamp": timestamp,
        },
    )


@event_factory
def rag_corpus_hints_update_failed(
    *,
    path: str,
    error: str,
) -> Event:
    """Emitted when corpus_hints.yaml update fails after indexing."""
    return Event(
        signal="rag.corpus.hints.update.failed",
        payload={"path": path, "error": error},
    )


@event_factory
def rag_corpus_hints_load_failed(*, path: str, error: str) -> Event:
    """Emitted when corpus_hints.yaml cannot be loaded."""
    return Event(
        signal="rag.corpus.hints.load.failed",
        payload={"path": path, "error": error},
    )


@event_factory
def rag_scope_vocabulary_load_failed(*, path: str, error: str) -> Event:
    """Emitted when scope_vocabulary.yaml cannot be loaded."""
    return Event(
        signal="rag.scope.vocabulary.load.failed",
        payload={"path": path, "error": error},
    )


@event_factory
def rag_corpus_hints_filter_failed(*, error: str) -> Event:
    """Emitted when co-occurrence hint filtering fails."""
    return Event(
        signal="rag.corpus.hints.filter.failed",
        payload={"error": error},
    )


@event_factory
def rag_corpus_hints_skipped(*, reason: str) -> Event:
    """Emitted when corpus-hints generation is intentionally skipped."""
    return Event(
        signal="rag.corpus.hints.skipped",
        payload={"reason": reason},
    )
