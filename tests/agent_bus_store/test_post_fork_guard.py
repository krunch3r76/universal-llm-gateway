"""Regression tests for post-path fork guard (guardrails A + B).

Reproduces the 1140->1142 silent-fork footgun: numeric slug and after_turn on
POST /threads/with-turn must return structured 400, not mint a new thread.
"""

from __future__ import annotations

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from fastapi.testclient import TestClient


def _post_body(**overrides):
    body = {
        "slug": "deploy-failure-report",
        "from": "claude-web",
        "to": "cursor",
        "subject": "s",
        "body": "b",
    }
    body.update(overrides)
    return body


def _app(tmp_path):
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app


def _post(client: TestClient, **overrides):
    return client.post("/threads/with-turn", json=_post_body(**overrides))


def test_numeric_slug_rejected(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        resp = _post(client, slug="1140")
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason"] == "slug_looks_like_thread_id"


def test_after_turn_rejected_on_post(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        resp = _post(client, after_turn=4)
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason"] == "after_turn_not_valid_on_post"


def test_after_turn_zero_skip_sentinel_allowed_on_post(tmp_path) -> None:
    """_post_impl always injects after_turn=0; must not trip the post guard."""
    with TestClient(_app(tmp_path)) as client:
        resp = _post(client, after_turn=0)
    assert resp.status_code == 201


def test_descriptive_slug_creates(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        resp = _post(client)
    assert resp.status_code == 201
