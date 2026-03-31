from __future__ import annotations

from typing import Any

from services.rag.fts_index import FtsIndex
from services.rag.search_scope import apply_bm25_sidecar


class _FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _RecordingConn:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...]) -> _FakeCursor:
        self.calls.append((sql, params))
        return _FakeCursor(self.rows)


class _FakeFts:
    def __init__(
        self,
        *,
        scoped_hits: list[tuple[str, float]],
        unscoped_hits: list[tuple[str, float]] | None = None,
    ) -> None:
        self.scoped_hits = scoped_hits
        self.unscoped_hits = unscoped_hits or []
        self.scoped_calls = 0
        self.unscoped_calls = 0

    def search_scoped(
        self, query: str, source_prefixes: list[str], *, limit: int = 50
    ) -> list[tuple[str, float]]:
        self.scoped_calls += 1
        return self.scoped_hits[:limit]

    def search(self, query: str, *, limit: int = 50) -> list[tuple[str, float]]:
        self.unscoped_calls += 1
        return self.unscoped_hits[:limit]


class _FakeCollection:
    def __init__(self, metadata_by_id: dict[str, dict[str, Any]]) -> None:
        self.metadata_by_id = metadata_by_id

    def get(self, ids: list[str], include: list[str]) -> dict[str, list[Any]]:
        return {
            "ids": ids,
            "documents": [f"doc:{chunk_id}" for chunk_id in ids],
            "metadatas": [self.metadata_by_id[chunk_id] for chunk_id in ids],
        }


def test_search_scoped_pushes_prefix_filter_into_sql() -> None:
    conn = _RecordingConn(rows=[("chunk-1", "/allowed/doc.md", -1.25)])
    index = FtsIndex()
    index._conn = conn  # type: ignore[assignment]
    index._seq = object()  # type: ignore[assignment]

    result = index.search_scoped("needle", ["/allowed", "/also"], limit=7)

    assert result == [("chunk-1", -1.25)]
    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert "chunks_fts MATCH ?" in sql
    assert "source LIKE ?" in sql
    assert params == ("needle", "/allowed%", "/also%", 7)


def test_apply_bm25_sidecar_does_not_widen_scoped_queries() -> None:
    fts = _FakeFts(
        scoped_hits=[("scoped", -1.0)],
        unscoped_hits=[("leak", -9.0)],
    )
    collection = _FakeCollection(
        {
            "scoped": {"source": "/allowed/doc.md"},
            "leak": {"source": "/other/doc.md"},
        }
    )

    ids, chunks, metadatas, distances, bm25_hits = apply_bm25_sidecar(
        ids=[],
        chunks=[],
        metadatas=[],
        distances=[],
        query="needle",
        fts=fts,  # type: ignore[arg-type]
        collection=collection,  # type: ignore[arg-type]
        source_prefixes=["/allowed"],
    )

    assert fts.scoped_calls == 1
    assert fts.unscoped_calls == 0
    assert ids == ["scoped"]
    assert chunks == ["doc:scoped"]
    assert metadatas == [{"source": "/allowed/doc.md"}]
    assert distances == [1.0]
    assert bm25_hits == 1


def test_apply_bm25_sidecar_re_filters_fetched_bm25_rows_by_scope() -> None:
    fts = _FakeFts(
        scoped_hits=[
            ("scoped", -1.0),
            ("leak", -2.0),
        ]
    )
    collection = _FakeCollection(
        {
            "scoped": {"source": "/allowed/doc.md"},
            "leak": {"source": "/other/doc.md"},
        }
    )

    ids, chunks, metadatas, distances, bm25_hits = apply_bm25_sidecar(
        ids=[],
        chunks=[],
        metadatas=[],
        distances=[],
        query="needle",
        fts=fts,  # type: ignore[arg-type]
        collection=collection,  # type: ignore[arg-type]
        source_prefixes=["/allowed"],
    )

    assert ids == ["scoped"]
    assert chunks == ["doc:scoped"]
    assert metadatas == [{"source": "/allowed/doc.md"}]
    assert distances == [1.0]
    assert bm25_hits == 2
