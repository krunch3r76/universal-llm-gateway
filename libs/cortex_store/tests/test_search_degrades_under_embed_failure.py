"""Regression: hybrid search degrades to FTS-only under embed failure."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(migrated_db_path) -> TestClient:
    os.environ["CORTEX_DB_PATH"] = str(migrated_db_path)
    from cortex_store.main import create_app

    return TestClient(create_app(db_path=str(migrated_db_path)))


def _seed_searchable_assertion(client: TestClient, keyword: str) -> None:
    from cortex_store.enrichment import reindex_assertion_fts

    eid = f"test:search-degrade-{uuid4().hex[:8]}"
    r = client.post(
        "/entities",
        json={"id": eid, "type": "test", "name": f"Search degrade {eid}"},
    )
    assert r.status_code in (200, 201), r.text
    body: dict[str, Any] = {
        "entity_id": eid,
        "claim": f"Unique searchable claim about {keyword} for hybrid degrade test",
        "confidence": "believed",
        "evidence": "search degrade regression fixture",
        "derivation_type": "agent_observation",
        "observed_at": datetime.now(UTC).isoformat(),
        "reasoning_summary": "fixture",
    }
    r = client.post("/assertions", json=body)
    assert r.status_code in (200, 201), r.text
    assertion_id = r.json()["item"]["id"]
    reindex_assertion_fts(assertion_id)


def _raise_timeout(_text: str) -> list[float]:
    raise httpx.TimeoutException("timed out")


def _slow_then_timeout(_text: str) -> list[float]:
    time.sleep(2.5)
    raise httpx.TimeoutException("slow")


@pytest.mark.parametrize(
    "embed_side_effect",
    [
        pytest.param(_raise_timeout, id="timeout_exception"),
        pytest.param(_slow_then_timeout, id="sleep_beyond_budget"),
    ],
)
def test_search_degrades_to_fulltext_on_embed_failure(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    embed_side_effect,
) -> None:
    keyword = f"degrade{uuid4().hex[:6]}"
    _seed_searchable_assertion(client, keyword)

    emitted: list[tuple[str, dict[str, Any]]] = []

    def capture_record(signal: str, **payload: Any) -> None:
        emitted.append((signal, payload))

    monkeypatch.setattr(
        "cortex_store.routes.assertions._search.cortex_embeddings.is_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._search.vector_store.is_initialized",
        lambda: True,
    )
    monkeypatch.setattr(
        "cortex_store.routes.assertions._search.cortex_embeddings.embed_query",
        embed_side_effect,
    )
    monkeypatch.setattr("cortex_store.event_publisher.record", capture_record)

    t0 = time.monotonic()
    response = client.get("/assertions/search", params={"q": keyword})
    elapsed = time.monotonic() - t0

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["search_mode"] == "fulltext"
    assert data["total"] >= 1

    degraded = [item for item in emitted if item[0] == "cortex.search.vector.degraded"]
    assert len(degraded) == 1
    _signal, payload = degraded[0]
    assert payload["reason"] in {"vector_embed_timeout", "vector_error"}
    assert payload["duration_s"] <= 3.0
    assert elapsed < 5.0
