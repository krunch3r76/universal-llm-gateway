"""E2 tests: threads(query=) free-text lookup."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "mcp-server"))

from tools.agent_bus.threads import _threads_dispatch  # noqa: E402


def _app(tmp_path):
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app


def _new_thread(
    client,
    *,
    slug: str,
    summary: str | None = None,
    frm: str = "cursor",
    to: str = "web",
    subject: str = "init",
    body: str = "body",
    tags: list[str] | None = None,
):
    payload: dict = {
        "slug": slug,
        "from": frm,
        "to": to,
        "subject": subject,
        "body": body,
    }
    if summary is not None:
        payload["summary"] = summary
    if tags:
        payload["tags"] = tags
    resp = client.post("/threads/with-turn", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()["thread"]["id"]


def _add_turn(client, *, thread: str, subject: str, frm: str = "cursor", to: str = "web"):
    resp = client.post(
        "/turns",
        json={
            "thread": thread,
            "from": frm,
            "to": to,
            "subject": subject,
            "body": "turn-body",
        },
    )
    assert resp.status_code == 201, resp.text


def _thread_slugs(client, **params) -> set[str]:
    resp = client.get("/threads", params=params)
    assert resp.status_code == 200, resp.text
    return {row["slug"] for row in resp.json()["threads"]}


def test_query_matches_slug_case_insensitive(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        _new_thread(client, slug="Wave-B-Alpha", subject="s1")
        _new_thread(client, slug="other-thread", subject="s2")

        assert _thread_slugs(client, query="wave-b") == {"Wave-B-Alpha"}
        assert _thread_slugs(client, query="WAVE-B") == {"Wave-B-Alpha"}


def test_query_matches_summary(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        _new_thread(
            client,
            slug="slug-only",
            summary="E2 ergonomics backlog item",
            subject="s1",
        )
        _new_thread(client, slug="miss", summary="unrelated", subject="s2")

        assert _thread_slugs(client, query="ergonomics") == {"slug-only"}


def test_query_matches_last_subject(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        thread_id = _new_thread(client, slug="subject-hit", subject="initial")
        _add_turn(client, thread=thread_id, subject="CHECKPOINT closeout")
        _new_thread(client, slug="subject-miss", subject="other")

        assert _thread_slugs(client, query="checkpoint") == {"subject-hit"}


def test_query_and_composes_with_status_and_tags(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        hit = _new_thread(
            client,
            slug="compose-hit",
            summary="wave-b query",
            subject="s1",
            tags=["project:ulg"],
        )
        _new_thread(
            client,
            slug="compose-miss-status",
            summary="wave-b query",
            subject="s2",
            tags=["project:ulg"],
        )
        client.patch(f"/threads/{hit}/close", json={})

        assert _thread_slugs(
            client,
            query="compose-hit",
            status="closed",
            tags=["project:ulg"],
        ) == {"compose-hit"}
        assert _thread_slugs(
            client,
            query="compose-miss",
            status="active",
            tags=["project:ulg"],
        ) == {"compose-miss-status"}


def test_query_clamped_at_200_chars(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        needle = "x" * 200
        _new_thread(client, slug="clamp-hit", summary=needle, subject="s1")
        _new_thread(client, slug="clamp-miss", summary="y" * 200, subject="s2")

        overlong = needle + "extra-tail-not-in-clamp"
        assert _thread_slugs(client, query=overlong) == {"clamp-hit"}


def test_query_empty_result_is_http_200(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        _new_thread(client, slug="present", subject="s1")
        resp = client.get("/threads", params={"query": "no-such-thread-token"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["threads"] == []


def test_threads_dispatch_forwards_query_param() -> None:
    captured: dict[str, str] = {}

    def relay(service: str, method: str, path: str, **kwargs) -> dict:
        del service, method, kwargs
        qs = urlparse(path).query
        captured.update({k: v[0] for k, v in parse_qs(qs).items()})
        return {"threads": []}

    with patch("tools.agent_bus.threads.relay", side_effect=relay):
        _threads_dispatch(query="wave-b", status="active", tags=["project:ulg"])

    assert captured["query"] == "wave-b"
    assert captured["status"] == "active"
    assert captured["tags"] == "project:ulg"
