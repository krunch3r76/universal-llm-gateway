"""Wait route incremental store reads and off-loop DB (cpu-pin H1)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import (
    get_thread,
    get_thread_turns_asc,
    get_thread_with_links,
    get_thread_wait_activity_probe,
    get_wait_snapshot_turns,
)
from agent_bus_store.db.threads import _WAIT_TURN_HEADER_COLUMNS
from agent_bus_store.routes import wait as wait_route


def _app(tmp_path):
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app


def _seed_thread(client: TestClient, *, slug: str) -> str:
    created = client.post(
        "/threads/with-turn",
        json={
            "slug": slug,
            "from": "claude-cursor",
            "to": "web",
            "subject": "handoff",
            "body": "brief",
            "tags": ["bus_lifecycle:persistent"],
        },
    )
    assert created.status_code == 201
    return created.json()["thread"]["id"]


def test_wait_snapshot_turns_skips_pre_pointer_bodies(tmp_path) -> None:
    with TestClient(_app(tmp_path)) as client:
        thread_id = _seed_thread(client, slug="incr-body-skip")
        for i in range(2, 6):
            client.post(
                "/turns",
                json={
                    "thread": thread_id,
                    "from": "claude-cursor",
                    "to": "web",
                    "subject": f"filler {i}",
                    "body": f"large-body-{'x' * 2000}-{i}",
                },
            )
        client.post(
            "/turns",
            json={
                "thread": thread_id,
                "from": "claude-web",
                "to": "cursor",
                "subject": "re: handoff",
                "body": "done",
            },
        )
        turns, turn_count = get_wait_snapshot_turns(
            thread_id, after_turn=1, include_pointer_body=False
        )
        assert turn_count == 6
        assert len(turns) == 6  # pointer header + turns 2..6
        pointer = next(t for t in turns if t["turn_number"] == 1)
        assert pointer["body"] == ""
        assert all(
            t.get("body") not in ("", None)
            for t in turns
            if t["turn_number"] > 1
        )
        bodies_before = [
            t.get("body") for t in get_thread_turns_asc(thread_id) if t["turn_number"] < 2
        ]
        assert bodies_before and bodies_before[0]

        with patch(
            "agent_bus_store.routes.wait.get_wait_snapshot_turns",
            wraps=get_wait_snapshot_turns,
        ) as snap_fn:
            resp = client.get(
                f"/threads/{thread_id}/wait"
                "?after_turn=1&wait=0&completion=first_reply_from&from_agent=claude-web"
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["complete"] is True
        assert body["status"] == "complete"
        poll_calls = [
            c
            for c in snap_fn.call_args_list
            if not c.kwargs.get("include_pointer_body")
        ]
        assert poll_calls, "expected poll-path snapshot load without pointer body"


@pytest.mark.parametrize(
    "completion,extra_params,reply",
    [
        (
            "first_reply_from",
            "&from_agent=claude-web",
            {"from": "claude-web", "subject": "re: handoff", "body": "ok"},
        ),
        (
            "thread_closed",
            "",
            None,
        ),
        (
            "status:done",
            "",
            {"from": "cursor-sdk", "subject": "status:done ship", "body": "x"},
        ),
    ],
)
def test_incremental_matches_full_turns_derivation(
    tmp_path,
    completion: str,
    extra_params: str,
    reply: dict | None,
) -> None:
    """Snapshot payload matches full-thread load for completion modes."""
    with TestClient(_app(tmp_path)) as client:
        thread_id = _seed_thread(client, slug=f"parity-{completion}")
        if reply is not None:
            client.post("/turns", json={"thread": thread_id, **reply})
        if completion == "thread_closed":
            client.post(f"/threads/{thread_id}/close")

        full_turns = get_thread_turns_asc(thread_id)
        incr_turns, incr_count = get_wait_snapshot_turns(
            thread_id, after_turn=1, include_pointer_body=False
        )
        from agent_bus_store.db import get_thread_with_links

        thread_row = get_thread_with_links(thread_id)
        assert thread_row is not None
        comp = {"mode": completion}  # type: ignore[typeddict-item]
        if completion == "first_reply_from":
            comp["from_agent"] = "claude-web"

        from datetime import UTC, datetime

        clock = datetime.now(UTC)
        full_payload = wait_route._build_snapshot_payload(
            thread_row,
            full_turns,
            turn_count=len(full_turns),
            after_turn=1,
            completion=comp,
            execution_id=None,
            pinned_witness=None,
            witness_map=None,
            clock=clock,
        )
        incr_payload = wait_route._build_snapshot_payload(
            thread_row,
            incr_turns,
            turn_count=incr_count,
            after_turn=1,
            completion=comp,
            execution_id=None,
            pinned_witness=None,
            witness_map=None,
            clock=clock,
        )
        for key in (
            "complete",
            "status",
            "qualifying_reply_turn",
            "turn_count",
            "suggested_next",
        ):
            assert full_payload[key] == incr_payload[key]


def test_wait_poll_uses_to_thread_for_db(tmp_path, monkeypatch) -> None:
    with TestClient(_app(tmp_path)) as client:
        thread_id = _seed_thread(client, slug="to-thread")
        calls: list[object] = []
        real_to_thread = asyncio.to_thread

        async def recording_to_thread(func, /, *args, **kwargs):
            calls.append(func)
            return await real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", recording_to_thread)
        with patch(
            "agent_bus_store.routes.wait.reader_liveness_witness_async",
            new=AsyncMock(return_value=None),
        ):
            resp = client.get(
                f"/threads/{thread_id}/wait"
                "?after_turn=1&wait=0&completion=first_reply_from&from_agent=claude-web"
            )
        assert resp.status_code == 200
        db_funcs = {
            get_wait_snapshot_turns,
            get_thread,
            get_thread_with_links,
            get_thread_wait_activity_probe,
        }
        assert db_funcs.intersection(calls)


def test_wait_activity_probe_header_sql_is_header_only() -> None:
    assert "body" not in _WAIT_TURN_HEADER_COLUMNS


def test_wait_long_poll_reuses_turns_on_unchanged_probe(
    tmp_path, monkeypatch
) -> None:
    with TestClient(_app(tmp_path)) as client:
        thread_id = _seed_thread(client, slug="probe-cache")
        loads = 0
        orig = get_wait_snapshot_turns

        def counting(*args, **kwargs):
            nonlocal loads
            loads += 1
            return orig(*args, **kwargs)

        monkeypatch.setattr(wait_route, "get_wait_snapshot_turns", counting)
        monkeypatch.setattr(wait_route, "_POLL_INTERVAL_SECONDS", 0.01)
        with patch(
            "agent_bus_store.routes.wait.reader_liveness_witness_async",
            new=AsyncMock(return_value=None),
        ):
            resp = client.get(
                f"/threads/{thread_id}/wait"
                "?after_turn=1&wait=0.05&completion=first_reply_from&from_agent=claude-web"
            )
        assert resp.status_code == 200
        assert loads >= 1
        assert loads <= 2
