"""Soft-limit auto-spill: POST /turns, with-turn, send (no sidecar_content)."""

from __future__ import annotations

import hashlib
from unittest.mock import patch

from cortex_store.dispatch_ops._thread_sidecar import SidecarWriteError
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.body_auto_spill import AUTO_OVERFLOW_SLUG
from agent_bus_store.turns_models import (
    MAX_LONG_TURN_BODY_CHARS,
    MAX_SIDECAR_CONTENT_CHARS,
    MAX_TURN_BODY_CHARS,
)


def _app(tmp_path, monkeypatch):
    cortex_root = tmp_path / "cortex-files"
    cortex_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    import cortex_store.dispatch_ops._thread_sidecar as sidecar_mod

    monkeypatch.setattr(sidecar_mod, "_FILES_ROOT", cortex_root)
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app, cortex_root


def _soft_body(n: int = MAX_TURN_BODY_CHARS + 500) -> str:
    return "x" * n


def _seed_thread(client: TestClient, slug: str = "seed") -> str:
    resp = client.post(
        "/threads/with-turn",
        json={
            "slug": slug,
            "from": "cursor",
            "to": "web",
            "subject": "seed",
            "body": "hello",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["thread"]["id"]


def test_post_turns_soft_spill_201(tmp_path, monkeypatch) -> None:
    app, cortex_root = _app(tmp_path, monkeypatch)
    body = _soft_body()
    with TestClient(app) as client:
        thread_id = _seed_thread(client)
        resp = client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "Big reply",
                "body": body,
                "after_turn": 1,
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["sidecar_uri"] == (
            f"cortex://notes/system/threads/{thread_id}-{AUTO_OVERFLOW_SLUG}.md"
        )
        assert data["sidecar_sha256"] == hashlib.sha256(body.encode()).hexdigest()

        turn = client.get(
            f"/turns/by-number?thread={thread_id}&turn_number=2"
        ).json()
        assert "Sidecar:" in turn["body"]
        assert data["sidecar_uri"] in turn["body"]
        assert body not in turn["body"]

        rel = data["sidecar_uri"].removeprefix("cortex://")
        file_path = cortex_root / rel
        assert file_path.is_file()
        text = file_path.read_text(encoding="utf-8")
        assert "oversized: true" in text
        assert "delivery_mode: sidecar" in text
        assert body in text


def test_post_turns_allow_long_inline(tmp_path, monkeypatch) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    body = _soft_body()
    with TestClient(app) as client:
        thread_id = _seed_thread(client, slug="long-inline")
        resp = client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "Long ok",
                "body": body,
                "allow_long_body": True,
                "after_turn": 1,
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data.get("sidecar_uri") is None
        turn = client.get(
            f"/turns/by-number?thread={thread_id}&turn_number=2"
        ).json()
        assert turn["body"] == body


def test_post_turns_allow_long_hard_413(tmp_path, monkeypatch) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    body = "y" * (MAX_LONG_TURN_BODY_CHARS + 1)
    with TestClient(app) as client:
        thread_id = _seed_thread(client, slug="hard-413")
        resp = client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "Too long",
                "body": body,
                "allow_long_body": True,
                "after_turn": 1,
            },
        )
        assert resp.status_code == 413
        assert resp.json()["detail"]["reason"] == "body_too_large"


def test_send_sidecar_content_no_double_spill(tmp_path, monkeypatch) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    briefing = "x" * (MAX_TURN_BODY_CHARS + 100)
    with TestClient(app) as client:
        resp = client.post(
            "/threads/send",
            json={
                "new_slug": "no-double",
                "from": "cursor",
                "to": "web",
                "subject": "Caller sidecar",
                "body": briefing,
                "sidecar_content": "payload",
            },
        )
        assert resp.status_code == 413
        assert resp.json()["detail"]["reason"] == "body_too_large"


def test_post_turns_write_failure_503_no_turn(tmp_path, monkeypatch) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    with (
        TestClient(app) as client,
        patch(
            "agent_bus_store.body_auto_spill.write_thread_sidecar_for_send",
            side_effect=SidecarWriteError("disk full"),
        ),
    ):
        thread_id = _seed_thread(client, slug="write-fail-turns")
        before = len(client.get(f"/turns?thread={thread_id}").json()["turns"])
        resp = client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "Fail",
                "body": _soft_body(),
                "after_turn": 1,
            },
        )
        assert resp.status_code == 503, resp.text
        assert resp.json()["detail"]["code"] == "sidecar_write_failed"
        after = len(client.get(f"/turns?thread={thread_id}").json()["turns"])
        assert after == before


def test_with_turn_spill_failure_no_orphan_thread(tmp_path, monkeypatch) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    with (
        TestClient(app) as client,
        patch(
            "agent_bus_store.body_auto_spill.write_thread_sidecar_for_send",
            side_effect=SidecarWriteError("injected"),
        ),
    ):
        before_ids = {t["id"] for t in client.get("/threads").json()["threads"]}
        resp = client.post(
            "/threads/with-turn",
            json={
                "slug": "orphan-guard",
                "from": "cursor",
                "to": "web",
                "subject": "Spill fail",
                "body": _soft_body(),
            },
        )
        assert resp.status_code == 503, resp.text
        assert resp.json()["detail"]["code"] == "sidecar_write_failed"
        after = client.get("/threads").json()["threads"]
        after_ids = {t["id"] for t in after}
        assert after_ids == before_ids
        assert not any(t["slug"] == "orphan-guard" for t in after)


def test_spill_over_sidecar_cap_413(tmp_path, monkeypatch) -> None:
    app, _ = _app(tmp_path, monkeypatch)
    body = "z" * (MAX_SIDECAR_CONTENT_CHARS + 1)
    with TestClient(app) as client:
        thread_id = _seed_thread(client, slug="cap-413")
        resp = client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "Cap",
                "body": body,
                "after_turn": 1,
            },
        )
        assert resp.status_code == 413
        assert resp.json()["detail"]["code"] == "sidecar_content_too_large"


def test_send_continue_soft_spill(tmp_path, monkeypatch) -> None:
    app, cortex_root = _app(tmp_path, monkeypatch)
    body = _soft_body()
    with TestClient(app) as client:
        thread_id = _seed_thread(client, slug="send-continue-spill")
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "Continue spill",
                "body": body,
                "after_turn": 1,
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["sidecar_uri"]
        assert data["sidecar_sha256"] == hashlib.sha256(body.encode()).hexdigest()
        rel = data["sidecar_uri"].removeprefix("cortex://")
        assert (cortex_root / rel).is_file()
