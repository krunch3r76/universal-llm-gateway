"""HTTP tests for POST /extraction/queue/reset."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from services.rag.admin_routes import register_admin_routes
from services.rag.property_index import PropertyIndex


class _FakeCollection:
    def count(self) -> int:
        return 0


async def _property_index(tmp_path: Path) -> PropertyIndex:
    idx = PropertyIndex(db_path=tmp_path / "rag_metadata.db")
    await idx.start()
    return idx


def _make_app(
    *,
    property_index: PropertyIndex | None,
) -> FastAPI:
    app = FastAPI()
    router = register_admin_routes(
        index_file_fn=lambda *_args, **_kwargs: None,
        get_collection_fn=lambda: _FakeCollection(),
        get_watcher_manager_fn=lambda: None,
        get_chroma_fn=lambda: None,
        set_collection_fn=lambda _collection: None,
        collection_name="universal_rag",
        get_property_index_fn=lambda: property_index,
    )
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_reset_http_not_found_empty_and_503(tmp_path: Path) -> None:
    """Unknown source is not inserted; empty body 400; missing index 503."""
    idx = await _property_index(tmp_path)
    app = _make_app(property_index=idx)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            missing = await client.post(
                "/extraction/queue/reset",
                json={"sources": ["/no/such/file.messages.json"]},
            )
            assert missing.status_code == 200
            missing_body = missing.json()
            assert missing_body["results"][0]["outcome"] == "not_found"
            assert missing_body["reset_count"] == 0
            count = idx.get_extraction_queue_count()
            assert count == 0

            empty = await client.post(
                "/extraction/queue/reset", json={"sources": []}
            )
            assert empty.status_code == 400

        down_app = _make_app(property_index=None)
        down_transport = ASGITransport(app=down_app)
        async with AsyncClient(
            transport=down_transport, base_url="http://test"
        ) as down_client:
            unavailable = await down_client.post(
                "/extraction/queue/reset",
                json={"sources": ["/any.messages.json"]},
            )
            assert unavailable.status_code == 503
            assert unavailable.json()["detail"] == "Property index not available"
    finally:
        await idx.stop()
