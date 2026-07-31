"""Tests for RAG admin /indexing/status endpoint behavior and degraded flags."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.rag.admin_routes import register_admin_routes
from services.rag.property_index import FailureSnapshot, PendingSnapshot


class _FakeCollection:
    def __init__(self, count: int, *, raise_count: bool = False) -> None:
        self._count = count
        self._raise_count = raise_count

    def count(self) -> int:
        if self._raise_count:
            raise RuntimeError("chroma unavailable")
        return self._count


class _FakeWatcher:
    def get_status(self) -> list[dict[str, object]]:
        return [
            {
                "path": "/data/docs",
                "enabled": True,
                "reload_count": 4,
                "error_count": 0,
            }
        ]


class _FakePropertyIndex:
    def get_pending_snapshot(self, sample_limit: int) -> PendingSnapshot:
        return PendingSnapshot(count=3, sample=["/a.md", "/b.md"][:sample_limit])

    def get_failure_snapshot(self) -> FailureSnapshot:
        return FailureSnapshot(
            failed_extractions_count=2,
            failed_extractions_permanent_count=1,
        )


def _make_client(
    *,
    property_index: _FakePropertyIndex | None,
    collection: _FakeCollection,
) -> TestClient:
    app = FastAPI()
    router = register_admin_routes(
        index_file_fn=lambda *_args, **_kwargs: None,
        get_collection_fn=lambda: collection,
        get_watcher_manager_fn=lambda: _FakeWatcher(),
        get_chroma_fn=lambda: None,
        set_collection_fn=lambda _collection: None,
        collection_name="universal_rag",
        get_property_index_fn=lambda: property_index,
    )
    app.include_router(router)
    return TestClient(app)


def test_indexing_status_happy_path() -> None:
    """Healthy dependencies return counts, watcher rows, and not-degraded flags."""
    client = _make_client(
        property_index=_FakePropertyIndex(),
        collection=_FakeCollection(count=42),
    )
    response = client.get("/indexing/status", params={"sample_limit": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["pending_count"] == 3
    assert body["pending_sample"] == ["/a.md"]
    assert body["pending_sample_truncated"] is True
    assert body["failed_extractions_count"] == 2
    assert body["failed_extractions_permanent_count"] == 1
    assert body["property_index_available"] is True
    assert body["chroma_available"] is True
    assert body["chunks"] == 42
    assert len(body["watchers"]) == 1


def test_indexing_status_degrades_when_property_index_unavailable() -> None:
    """Missing property index still returns HTTP 200 with degraded availability flag."""
    client = _make_client(property_index=None, collection=_FakeCollection(count=7))
    response = client.get("/indexing/status")
    assert response.status_code == 200
    body = response.json()
    assert body["property_index_available"] is False
    assert body["pending_count"] == 0
    assert body["failed_extractions_count"] == 0


def test_indexing_status_degrades_when_chroma_count_fails() -> None:
    """Chroma count failures surface through chroma_available/chroma_error fields."""
    client = _make_client(
        property_index=_FakePropertyIndex(),
        collection=_FakeCollection(count=0, raise_count=True),
    )
    response = client.get("/indexing/status")
    assert response.status_code == 200
    body = response.json()
    assert body["chroma_available"] is False
    assert body["chunks"] is None
    assert isinstance(body["chroma_error"], str)
