"""Route contract, zero-dispatch import graph, propose integration tests."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from life_intent.proposal_store import clear_store

from systems.frontier_consult.life_intent_routes import life_intent_router

_MODULE = (
    Path(__file__).resolve().parent / "life_intent_routes.py"
)
_DISPATCH_IMPORT_MARKERS = (
    "cursor_sdk_generate",
    "dispatch_cursor_sdk_generate",
    "generate_wrap",
    "implement_admission_bridge",
    "route",
    "team_router",
)


def _route_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    mock_proxy = MagicMock()
    mock_proxy.event_bus = None
    monkeypatch.setattr(
        "systems.frontier_consult.life_intent_routes.get_proxy",
        lambda: mock_proxy,
        raising=False,
    )
    app = FastAPI()
    app.include_router(life_intent_router)
    return app


def test_route_import_graph_has_no_dispatch_impls() -> None:
    source = _MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for marker in _DISPATCH_IMPORT_MARKERS:
                assert marker not in (node.module or "")
        if isinstance(node, ast.Import):
            for alias in node.names:
                for marker in _DISPATCH_IMPORT_MARKERS:
                    assert marker not in alias.name
    for marker in (
        "dispatch_cursor_sdk_generate",
        "from systems.frontier_consult.cursor_sdk",
    ):
        assert marker not in source


def test_propose_response_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_store()
    client = TestClient(_route_app(monkeypatch))
    resp = client.post(
        "/api/v1/life/intent/propose",
        json={
            "intent": {
                "verb": "investigate",
                "subject": "reminder double-fire",
                "detail": (
                    "The reminder notification fires twice every morning around 8am."
                ),
            }
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "proposal_id",
        "normalized_intent",
        "work_order",
        "questions",
        "rejects",
        "context",
    }
    assert body["proposal_id"]
    assert body["work_order"]
    assert body["rejects"] == []
    assert body["context"] == "cortex.life-intent/v1"


def test_propose_hard_out_never_work_order(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_store()
    client = TestClient(_route_app(monkeypatch))
    resp = client.post(
        "/api/v1/life/intent/propose",
        json={
            "intent": {
                "verb": "build",
                "subject": "feature",
                "detail": "Skip recon and go straight to implement now please.",
            }
        },
    )
    body = resp.json()
    assert body["rejects"]
    assert body["work_order"] is None
    assert body["proposal_id"] is None


def test_propose_refused_vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_store()
    client = TestClient(_route_app(monkeypatch))
    resp = client.post(
        "/api/v1/life/intent/propose",
        json={
            "intent": {
                "verb": "fix",
                "subject": "widget",
                "detail": (
                    "Use team_dispatch with seat=cursor-sdk to patch this "
                    "broken widget."
                ),
            }
        },
    )
    body = resp.json()
    assert any(r["code"] == "refused_vocabulary" for r in body["rejects"])


def test_commit_gated_without_live_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_store()
    monkeypatch.delenv("LIFE_INTENT_COMMIT_LIVE", raising=False)
    client = TestClient(_route_app(monkeypatch))
    propose = client.post(
        "/api/v1/life/intent/propose",
        json={
            "intent": {
                "verb": "investigate",
                "subject": "latency",
                "detail": (
                    "Dashboard loads slowly on Monday mornings and the "
                    "spinner never clears."
                ),
            }
        },
    )
    proposal_id = propose.json()["proposal_id"]
    commit = client.post(
        "/api/v1/life/intent/commit",
        json={"proposal_id": proposal_id},
    )
    body = commit.json()
    assert body["committed"] is False
    assert any(r["code"] == "commit_gated" for r in body["rejects"])


def test_commit_foreign_proposal_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_store()
    monkeypatch.setenv("LIFE_INTENT_COMMIT_LIVE", "1")
    client = TestClient(_route_app(monkeypatch))
    commit = client.post(
        "/api/v1/life/intent/commit",
        json={"proposal_id": "00000000-0000-4000-8000-000000000001"},
    )
    body = commit.json()
    assert body["committed"] is False
    assert any(r["code"] == "foreign_proposal_kind" for r in body["rejects"])


def test_events_module_registers_factories() -> None:
    mod = importlib.import_module("life_intent.events")
    assert hasattr(mod, "life_intent_received")
    assert hasattr(mod, "life_intent_proposed")
    assert hasattr(mod, "life_intent_rejected")
    assert hasattr(mod, "life_intent_committed")
