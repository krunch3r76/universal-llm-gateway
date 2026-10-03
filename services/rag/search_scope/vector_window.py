"""Bounded Chroma vector window for scoped and unscoped search.

``execute_search`` calls ``query_vector_window`` before the noise strip and
``apply_source_prefix_filter_with_ids``. Unscoped requests keep the historical
single query at ``top_k * UNSCOPED_FETCH_MULTIPLIER`` with no metadata ``where``.

chromadb 1.5.1 rejects a metadata prefix: ``where`` ``$regex`` is invalid, and
``$gte`` accepts only numbers, so an anchored path regex cannot be pushed down.
``source`` ``$in`` of the exact indexed paths for those prefixes does prefilter
ANN (nearer out-of-scope neighbors are dropped). ``execute_search`` supplies
that path list from the FTS index when it is loaded. Path lists longer than
``SCOPED_IN_MAX_SOURCES`` use over-fetch instead. A rejected ``$in`` call
counts against the trip budget.

When the path list is missing, the window falls back to geometric over-fetch:
start at ``top_k * SCOPED_FETCH_MULTIPLIER`` and double ``n_results`` until
``top_k`` non-noise in-scope rows are present or the cap is reached. The cap is
``min(collection.count(), SCOPED_N_RESULTS_CEILING)``.

Latency bound, per scoped request, either path:
- at most ``MAX_SCOPED_CHROMA_ROUND_TRIPS`` (10) ``collection.query`` calls
- each call uses ``n_results <= SCOPED_N_RESULTS_CEILING`` (2000)
The ``$in`` path is usually one query at ``top_k * 5`` inside the filter and
does not call ``collection.count()``. It stops early when Chroma returns fewer
rows than requested.
Over-fetch worst case is 5, 10, 20, …, 1280, 2000 when ``top_k`` is 1. The last
allowed trip jumps to the cap so the ceiling is read inside that trip budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from universal_logging import get_logger

from services.rag.chunk_filters import chunk_metadata_is_noise
from services.rag.search_scope.prefix_filter import matches_source_prefix

logger = get_logger(__name__)

# Historical unscoped over-fetch. Do not change: callers depend on this width.
UNSCOPED_FETCH_MULTIPLIER = 3
# Previous scoped window (top_k * 5). Still the first scoped probe.
SCOPED_FETCH_MULTIPLIER = 5
# Upper end of the 500–2000 band from the scoped-search brief. Live probes missed
# in-scope hits inside fetch_k=200 (writing_exemplars, top_k=40) and first saw
# journal hits by fetch_k=250. 2000 is eight times that miss window and stays
# short of a full-collection scan.
SCOPED_N_RESULTS_CEILING = 2000
# Doubling from the minimum start (top_k * 5 >= 5) reaches 2000 in 10 queries:
# 5, 10, 20, 40, 80, 160, 320, 640, 1280, 2000.
MAX_SCOPED_CHROMA_ROUND_TRIPS = 10
# Above this many exact paths, skip the source $in push-down and over-fetch.
# A scope this wide is dense among global neighbors, and one where clause of
# this size risks backend parameter or payload limits (untested on 1.5.1).
SCOPED_IN_MAX_SOURCES = 10_000

_QUERY_INCLUDE = ["documents", "metadatas", "distances"]


class VectorCollection(Protocol):
    """Chroma collection surface this window uses."""

    def query(
        self,
        *,
        query_embeddings: list[list[float]],
        n_results: int,
        include: list[str],
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def count(self) -> int: ...


@dataclass(frozen=True, slots=True)
class VectorWindowResult:
    """One vector-stage window plus the bound that produced it.

    ``cap_hit`` is true when a scoped query returned fewer than ``top_k``
    non-noise in-scope rows, for any reason: the cap or trip budget was
    reached, or (on the ``$in`` path) the filtered scope holds fewer rows.
    Unscoped queries never set it.
    """

    ids: list[str]
    documents: list[str]
    metadatas: list[Any]
    distances: list[float]
    round_trips: int
    n_results_final: int
    cap: int
    cap_hit: bool


def query_vector_window(
    collection: VectorCollection,
    query_embedding: list[float],
    *,
    top_k: int,
    source_prefixes: list[str] | None,
    indexed_sources: list[str] | None = None,
    n_results_ceiling: int = SCOPED_N_RESULTS_CEILING,
    max_round_trips: int = MAX_SCOPED_CHROMA_ROUND_TRIPS,
    max_in_sources: int = SCOPED_IN_MAX_SOURCES,
) -> VectorWindowResult:
    """Return the Chroma neighbor window for one query embedding.

    Unscoped (empty prefixes): one query, ``n_results=top_k * 3``, no ``where``.
    Scoped with ``indexed_sources``: one or more queries whose ``where`` is
    ``{"source": {"$in": indexed_sources}}``, so ANN runs inside those paths.
    Scoped without a path list: geometric ``n_results`` growth bounded by
    ``max_round_trips`` and ``min(collection.count(), n_results_ceiling)``.
    """
    if not source_prefixes:
        n_results = top_k * UNSCOPED_FETCH_MULTIPLIER
        ids, documents, metadatas, distances = _query_once(
            collection, query_embedding, n_results
        )
        return VectorWindowResult(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            distances=distances,
            round_trips=1,
            n_results_final=n_results,
            cap=n_results,
            cap_hit=False,
        )

    if indexed_sources is not None and len(indexed_sources) > max_in_sources:
        logger.info(
            "scoped source list has %s paths (> %s); using over-fetch",
            len(indexed_sources),
            max_in_sources,
        )
        indexed_sources = None

    if indexed_sources is not None:
        if not indexed_sources:
            return _empty_window(cap=0, cap_hit=False)
        where: dict[str, Any] | None = {"source": {"$in": list(indexed_sources)}}
        try:
            return _scoped_grow(
                collection,
                query_embedding,
                top_k=top_k,
                source_prefixes=source_prefixes,
                where=where,
                n_results_ceiling=n_results_ceiling,
                max_round_trips=max_round_trips,
            )
        except ValueError as exc:
            # The where is identical on every trip, so validation rejects it on
            # trip 1. Charge that call so the total stays <= max_round_trips.
            logger.warning(
                "scoped source $in where failed (%s); falling back to over-fetch",
                exc,
            )
            max_round_trips = max(max_round_trips - 1, 1)

    return _scoped_grow(
        collection,
        query_embedding,
        top_k=top_k,
        source_prefixes=source_prefixes,
        where=None,
        n_results_ceiling=n_results_ceiling,
        max_round_trips=max_round_trips,
    )


def _scoped_grow(
    collection: VectorCollection,
    query_embedding: list[float],
    *,
    top_k: int,
    source_prefixes: list[str],
    where: dict[str, Any] | None,
    n_results_ceiling: int,
    max_round_trips: int,
) -> VectorWindowResult:
    if top_k <= 0:
        return _empty_window(cap=0, cap_hit=False)

    # Adaptive over-fetch must not request past the index. The $in path does
    # not: Chroma already restricts to those paths, and n_results above the
    # filter size returns a short page. Skipping count() avoids a full-index
    # count on every scoped search (measured at several seconds on the live
    # store when the client cache is cold).
    if where is None:
        indexed = collection.count()
        cap = min(max(indexed, 0), n_results_ceiling)
    else:
        cap = n_results_ceiling
    if cap <= 0:
        return _empty_window(cap=0, cap_hit=True)

    n_results = min(top_k * SCOPED_FETCH_MULTIPLIER, cap)
    trips = 0
    while True:
        trips += 1
        ids, documents, metadatas, distances = _query_once(
            collection, query_embedding, n_results, where=where
        )
        exhausted = where is not None and len(ids) < n_results
        kept = _non_noise_in_scope(metadatas, source_prefixes, top_k)
        if kept >= top_k or n_results >= cap or trips >= max_round_trips or exhausted:
            cap_hit = kept < top_k
            if cap_hit:
                logger.debug(
                    "scoped vector window cap_hit cap=%s round_trips=%s "
                    "in_scope=%s top_k=%s n_results=%s where=%s",
                    cap,
                    trips,
                    kept,
                    top_k,
                    n_results,
                    where is not None,
                )
            return VectorWindowResult(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                distances=distances,
                round_trips=trips,
                n_results_final=n_results,
                cap=cap,
                cap_hit=cap_hit,
            )
        grown = min(n_results * 2, cap)
        if trips + 1 >= max_round_trips:
            grown = cap
        n_results = grown


def _empty_window(*, cap: int, cap_hit: bool) -> VectorWindowResult:
    return VectorWindowResult(
        ids=[],
        documents=[],
        metadatas=[],
        distances=[],
        round_trips=0,
        n_results_final=0,
        cap=cap,
        cap_hit=cap_hit,
    )


def _query_once(
    collection: VectorCollection,
    query_embedding: list[float],
    n_results: int,
    *,
    where: dict[str, Any] | None = None,
) -> tuple[list[str], list[Any], list[Any], list[float]]:
    if where is None:
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            include=list(_QUERY_INCLUDE),
        )
    else:
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            include=list(_QUERY_INCLUDE),
            where=where,
        )
    return _unpack(results)


def _unpack(
    results: dict[str, Any],
) -> tuple[list[str], list[Any], list[Any], list[float]]:
    ids = results["ids"][0] if results.get("ids") else []
    documents = results["documents"][0] if results.get("documents") else []
    metadatas = results["metadatas"][0] if results.get("metadatas") else []
    distances = results["distances"][0] if results.get("distances") else []
    return list(ids), list(documents), list(metadatas), list(distances)


def _non_noise_in_scope(
    metadatas: list[Any],
    source_prefixes: list[str],
    top_k: int,
) -> int:
    """Count non-noise prefix matches, stopping once ``top_k`` is reached."""
    kept = 0
    for meta in metadatas:
        if isinstance(meta, dict) and chunk_metadata_is_noise(meta):
            continue
        source = str(meta.get("source", "")) if isinstance(meta, dict) else ""
        if matches_source_prefix(source, source_prefixes):
            kept += 1
            if kept >= top_k:
                return kept
    return kept
