"""Scoped vector window: over-fetch past the old top_k*5 cut, leave unscoped alone."""

from __future__ import annotations

from typing import Any

import chromadb
import pytest

from services.rag.search_scope.prefix_filter import apply_source_prefix_filter_with_ids
from services.rag.search_scope.vector_window import (
    SCOPED_FETCH_MULTIPLIER,
    SCOPED_N_RESULTS_CEILING,
    UNSCOPED_FETCH_MULTIPLIER,
    query_vector_window,
)

_PREFIX = "/scope/journals"


def _row(
    row_id: str,
    source: str,
    distance: float,
    *,
    noise: bool = False,
) -> tuple[str, str, dict[str, Any], float]:
    return (
        row_id,
        f"text {row_id}",
        {"source": source, "is_noise": noise},
        distance,
    )


class _RecordingCollection:
    def __init__(self, rows: list[tuple[str, str, dict[str, Any], float]]) -> None:
        self.rows = rows
        self.queries: list[int] = []
        self.kwargs: list[dict[str, Any]] = []
        self.count_calls = 0

    def count(self) -> int:
        self.count_calls += 1
        return len(self.rows)

    def query(
        self,
        *,
        query_embeddings: list[list[float]],
        n_results: int,
        include: list[str],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.queries.append(n_results)
        self.kwargs.append(kwargs)
        take = self.rows[:n_results]
        return {
            "ids": [[row[0] for row in take]],
            "documents": [[row[1] for row in take]],
            "metadatas": [[row[2] for row in take]],
            "distances": [[row[3] for row in take]],
        }


class _QueryOnlyCollection:
    """Unscoped search must not call count()."""

    def __init__(self, rows: list[tuple[str, str, dict[str, Any], float]]) -> None:
        self.rows = rows
        self.queries: list[int] = []
        self.kwargs: list[dict[str, Any]] = []

    def query(
        self,
        *,
        query_embeddings: list[list[float]],
        n_results: int,
        include: list[str],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.queries.append(n_results)
        self.kwargs.append(kwargs)
        take = self.rows[:n_results]
        return {
            "ids": [[row[0] for row in take]],
            "documents": [[row[1] for row in take]],
            "metadatas": [[row[2] for row in take]],
            "distances": [[row[3] for row in take]],
        }


def _scoped_rows() -> list[tuple[str, str, dict[str, Any], float]]:
    """Forty nearer out-of-scope neighbors, then three in-scope hits."""
    rows = [
        _row(f"out-{index}", f"/other/{index}.md", float(index)) for index in range(40)
    ]
    rows.extend(
        _row(f"in-{index}", f"{_PREFIX}/{index}.md", 100.0 + index)
        for index in range(3)
    )
    return rows


def test_scoped_window_returns_hits_beyond_old_top_k_times_five() -> None:
    collection = _RecordingCollection(_scoped_rows())
    top_k = 3
    window = query_vector_window(
        collection,
        [1.0, 0.0],
        top_k=top_k,
        source_prefixes=[_PREFIX],
    )

    old_fetch = top_k * SCOPED_FETCH_MULTIPLIER
    assert old_fetch == 15
    assert collection.queries[0] == old_fetch
    assert collection.queries[-1] > old_fetch
    assert all(
        passed.get("where", "ABSENT") == "ABSENT" for passed in collection.kwargs
    )

    ids, _chunks, metadatas, distances = apply_source_prefix_filter_with_ids(
        ids=window.ids,
        chunks=window.documents,
        metadatas=window.metadatas,
        distances=window.distances,
        source_prefixes=[_PREFIX],
        top_k=top_k,
    )
    assert ids == ["in-0", "in-1", "in-2"]
    assert [meta["source"] for meta in metadatas] == [
        f"{_PREFIX}/0.md",
        f"{_PREFIX}/1.md",
        f"{_PREFIX}/2.md",
    ]
    assert distances == [100.0, 101.0, 102.0]
    assert window.cap_hit is False


def test_unscoped_query_keeps_fetch_multiplier_and_omits_where() -> None:
    rows = [
        _row(f"id-{index}", f"/any/{index}.md", float(index)) for index in range(20)
    ]
    collection = _QueryOnlyCollection(rows)
    top_k = 4
    window = query_vector_window(
        collection,
        [1.0, 0.0],
        top_k=top_k,
        source_prefixes=None,
    )

    assert collection.queries == [top_k * UNSCOPED_FETCH_MULTIPLIER]
    assert collection.kwargs == [{}]
    assert window.ids == [f"id-{index}" for index in range(12)]
    assert window.round_trips == 1
    assert window.cap_hit is False

    empty_prefixes = query_vector_window(
        collection,
        [1.0, 0.0],
        top_k=top_k,
        source_prefixes=[],
    )
    assert empty_prefixes.round_trips == 1
    assert collection.queries == [12, 12]


def test_cap_stops_with_fewer_than_top_k_and_is_deterministic() -> None:
    rows = [
        _row(f"out-{index}", f"/other/{index}.md", float(index)) for index in range(80)
    ]
    rows.append(_row("late", f"{_PREFIX}/late.md", 900.0))
    first = _RecordingCollection(rows)
    second = _RecordingCollection(rows)
    kwargs = {
        "top_k": 3,
        "source_prefixes": [_PREFIX],
        "n_results_ceiling": 32,
        "max_round_trips": 10,
    }
    first_window = query_vector_window(first, [0.0], **kwargs)
    second_window = query_vector_window(second, [0.0], **kwargs)

    assert first.queries == second.queries == [15, 30, 32]
    assert first_window.cap == 32
    assert first_window.cap_hit is True
    assert first_window.ids == second_window.ids
    assert "late" not in first_window.ids
    assert first_window.round_trips == 3
    assert max(first.queries) <= 32


def test_noise_in_scope_rows_do_not_satisfy_top_k() -> None:
    rows = [
        _row(f"noise-{index}", f"{_PREFIX}/noise-{index}.md", float(index), noise=True)
        for index in range(20)
    ]
    rows.extend(
        _row(f"keep-{index}", f"{_PREFIX}/keep-{index}.md", 50.0 + index)
        for index in range(2)
    )
    collection = _RecordingCollection(rows)
    window = query_vector_window(
        collection,
        [1.0],
        top_k=2,
        source_prefixes=[_PREFIX],
    )
    assert collection.queries[0] == 10
    assert window.ids[-2:] == ["keep-0", "keep-1"]
    assert window.cap_hit is False


def test_empty_index_is_a_cap_hit_without_query() -> None:
    collection = _RecordingCollection([])
    window = query_vector_window(
        collection,
        [1.0],
        top_k=3,
        source_prefixes=[_PREFIX],
    )
    assert collection.queries == []
    assert window.round_trips == 0
    assert window.cap_hit is True
    assert window.ids == []


def test_indexed_sources_push_down_source_in_where() -> None:
    rows = [
        _row(f"in-{index}", f"{_PREFIX}/{index}.md", float(index)) for index in range(3)
    ]
    rows.extend(
        _row(f"pad-{index}", f"/other/{index}.md", 10.0 + index) for index in range(20)
    )
    collection = _RecordingCollection(rows)
    window = query_vector_window(
        collection,
        [1.0, 0.0],
        top_k=3,
        source_prefixes=[_PREFIX],
        indexed_sources=[f"{_PREFIX}/0.md", f"{_PREFIX}/1.md", f"{_PREFIX}/2.md"],
    )
    assert collection.queries == [15]
    assert collection.kwargs == [
        {
            "where": {
                "source": {
                    "$in": [
                        f"{_PREFIX}/0.md",
                        f"{_PREFIX}/1.md",
                        f"{_PREFIX}/2.md",
                    ]
                }
            }
        }
    ]
    assert window.round_trips == 1
    assert window.cap_hit is False
    assert window.ids[:3] == ["in-0", "in-1", "in-2"]


def test_where_stops_when_filtered_page_is_short() -> None:
    """A $in filter smaller than top_k does not walk the global cap."""
    rows = [_row("only", f"{_PREFIX}/only.md", 0.2)]
    collection = _RecordingCollection(rows)
    collection.count = lambda: 90000  # type: ignore[method-assign]
    window = query_vector_window(
        collection,
        [1.0, 0.0],
        top_k=3,
        source_prefixes=[_PREFIX],
        indexed_sources=[f"{_PREFIX}/only.md"],
        n_results_ceiling=2000,
    )
    assert collection.queries == [15]
    assert window.cap == 2000
    assert window.cap_hit is True
    assert window.ids == ["only"]
    assert window.round_trips == 1


def test_empty_indexed_sources_skip_chroma() -> None:
    collection = _RecordingCollection(_scoped_rows())
    window = query_vector_window(
        collection,
        [1.0, 0.0],
        top_k=3,
        source_prefixes=[_PREFIX],
        indexed_sources=[],
    )
    assert collection.queries == []
    assert collection.count_calls == 0
    assert window.ids == []
    assert window.cap_hit is False


class _RejectWhereCollection(_RecordingCollection):
    def query(
        self,
        *,
        query_embeddings: list[list[float]],
        n_results: int,
        include: list[str],
        **kwargs: Any,
    ) -> dict[str, Any]:
        if "where" in kwargs:
            raise ValueError("where $in rejected")
        return super().query(
            query_embeddings=query_embeddings,
            n_results=n_results,
            include=include,
            **kwargs,
        )


def test_where_rejection_falls_back_to_over_fetch() -> None:
    collection = _RejectWhereCollection(_scoped_rows())
    window = query_vector_window(
        collection,
        [1.0, 0.0],
        top_k=3,
        source_prefixes=[_PREFIX],
        indexed_sources=[f"{_PREFIX}/0.md"],
    )
    assert collection.queries[0] == 15
    assert all("where" not in passed for passed in collection.kwargs)
    assert "in-0" in window.ids
    assert window.cap_hit is False


def test_installed_chromadb_rejects_metadata_prefix_operators() -> None:
    """Prefix push-down is unavailable; adaptive over-fetch is the scoped path.

    If a future chromadb accepts metadata ``$regex`` or string ``$gte``, this
    test fails so the window can move to a ``where`` predicate.
    """
    client = chromadb.EphemeralClient()
    collection = client.create_collection("prefix_ops")
    collection.add(
        ids=["in-0", "out-0"],
        embeddings=[[0.0, 1.0], [1.0, 0.0]],
        metadatas=[
            {"source": "/scope/journals/a.md"},
            {"source": "/other/b.md"},
        ],
        documents=["in", "out"],
    )
    with pytest.raises(ValueError, match=r"\$regex"):
        collection.query(
            query_embeddings=[[1.0, 0.0]],
            n_results=1,
            where={"source": {"$regex": r"^/scope/journals"}},
        )
    with pytest.raises(ValueError, match=r"\$gte"):
        collection.query(
            query_embeddings=[[1.0, 0.0]],
            n_results=1,
            where={"source": {"$gte": "/scope/journals"}},
        )


def test_real_chromadb_source_in_prefilters_before_n_results() -> None:
    """``source`` ``$in`` keeps a far in-scope row inside a tiny neighbor window."""
    client = chromadb.EphemeralClient()
    collection = client.create_collection(
        "source_in",
        metadata={"hnsw:space": "cosine"},
    )
    ids: list[str] = []
    embeddings: list[list[float]] = []
    metadatas: list[dict[str, Any]] = []
    documents: list[str] = []
    for index in range(30):
        ids.append(f"out-{index}")
        embeddings.append([1.0, 0.001 * index])
        metadatas.append({"source": f"/other/{index}.md", "is_noise": False})
        documents.append("out")
    ids.append("in-0")
    embeddings.append([0.0, 1.0])
    metadatas.append({"source": f"{_PREFIX}/hit.md", "is_noise": False})
    documents.append("in")
    collection.add(
        ids=ids,
        embeddings=embeddings,
        metadatas=metadatas,
        documents=documents,
    )
    window = query_vector_window(
        collection,
        [1.0, 0.0],
        top_k=1,
        source_prefixes=[_PREFIX],
        indexed_sources=[f"{_PREFIX}/hit.md"],
    )
    assert window.round_trips == 1
    assert window.n_results_final == 5
    filtered, _, metas, _ = apply_source_prefix_filter_with_ids(
        ids=window.ids,
        chunks=window.documents,
        metadatas=window.metadatas,
        distances=window.distances,
        source_prefixes=[_PREFIX],
        top_k=1,
    )
    assert filtered == ["in-0"]
    assert metas[0]["source"] == f"{_PREFIX}/hit.md"
    assert all(not str(row_id).startswith("out-") for row_id in window.ids)


def test_real_chromadb_scoped_window_skips_nearer_out_of_scope() -> None:
    client = chromadb.EphemeralClient()
    collection = client.create_collection(
        "scoped_window",
        metadata={"hnsw:space": "cosine"},
    )
    ids: list[str] = []
    embeddings: list[list[float]] = []
    metadatas: list[dict[str, Any]] = []
    documents: list[str] = []
    for index in range(30):
        ids.append(f"out-{index}")
        embeddings.append([1.0, 0.001 * index])
        metadatas.append({"source": f"/other/{index}.md", "is_noise": False})
        documents.append("out")
    ids.append("in-0")
    embeddings.append([0.0, 1.0])
    metadatas.append({"source": f"{_PREFIX}/hit.md", "is_noise": False})
    documents.append("in")
    collection.add(
        ids=ids,
        embeddings=embeddings,
        metadatas=metadatas,
        documents=documents,
    )

    window = query_vector_window(
        collection,
        [1.0, 0.0],
        top_k=1,
        source_prefixes=[_PREFIX],
        n_results_ceiling=SCOPED_N_RESULTS_CEILING,
    )
    filtered, _, metas, _ = apply_source_prefix_filter_with_ids(
        ids=window.ids,
        chunks=window.documents,
        metadatas=window.metadatas,
        distances=window.distances,
        source_prefixes=[_PREFIX],
        top_k=1,
    )
    assert filtered == ["in-0"]
    assert metas[0]["source"] == f"{_PREFIX}/hit.md"
    assert window.round_trips >= 2
    assert window.round_trips <= 10
    assert window.n_results_final <= SCOPED_N_RESULTS_CEILING
