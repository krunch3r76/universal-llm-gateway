"""GET /scopes still names scopes when enrichment overruns its budget."""

from __future__ import annotations

import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.rag.config._models import ScopeDefinition
from services.rag.rag_service import api as rag_api
from services.rag.rag_service import state


class _SlowIndex:
    def scope_list_enrichment(self) -> dict[str, dict[str, object]]:
        time.sleep(rag_api._ENRICHMENT_BUDGET_S + 0.3)
        return {"llm_prompting": {"article_count": 9, "top_topics": ["x"]}}


def test_scopes_served_when_enrichment_blocks(monkeypatch) -> None:
    """Busy property-index reads must not withhold the scope catalog."""
    monkeypatch.setattr(rag_api, "_ENRICHMENT_BUDGET_S", 0.05)
    state._config = SimpleNamespace(
        scopes={
            "llm_prompting": ScopeDefinition(prefixes=["docs/research/prompting"]),
        }
    )
    state._property_index = _SlowIndex()
    state._event_bus = None
    app = FastAPI()
    app.include_router(rag_api.router)
    try:
        response = TestClient(app).get("/scopes")
    finally:
        state._config = None
        state._property_index = None
    assert response.status_code == 200
    body = response.json()
    assert "llm_prompting" in body["scopes"]
    assert body["scopes"]["llm_prompting"]["article_count"] == 0
