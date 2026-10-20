"""Offline tests for Xvfb :8 CDP hop helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from bus_watch import ide_hop

from cursor_bridge.cursor_agents_archive import (
    _list_root_sidebar_rows_js,
    _rename_agent_row_js,
)
from cursor_bridge.cursor_agents_hop import (
    _archive_agent_js,
    _archive_async,
    _prune_predecessors_async,
    _root_row_pattern,
    archive_cursor_agents_tab,
    is_cursor_xvfb_host,
    launch_cursor_agents_tab,
    prune_cursor_agents_predecessors,
)

pytestmark = pytest.mark.offline


@pytest.mark.parametrize(
    "host",
    [":8", "8", "io:8", "127.0.0.1:9338", "http://127.0.0.1:9338"],
)
def test_is_cursor_xvfb_host(host: str) -> None:
    assert is_cursor_xvfb_host(host) is True
    assert is_cursor_xvfb_host("jupiter") is False


def test_session_unreachable_xvfb_returns_none() -> None:
    assert ide_hop.session_unreachable(":8") is None


def test_launch_cursor_agents_tab_ok() -> None:
    page = {"title": "Cursor Agents", "web_socket_debugger_url": "ws://fake"}

    def _run(coro):  # type: ignore[no-untyped-def]
        coro.close()
        return {
            "ok": True,
            "transport": "cdp_cursor_agents",
            "page_title": "Cursor Agents",
            "editor_agent_id": "ed-1",
            "send_fallback_clicked": False,
        }

    with (
        patch("cursor_bridge.cursor_agents_hop.list_pages", return_value=[page]),
        patch("cursor_bridge.cursor_agents_hop.asyncio.run", side_effect=_run),
    ):
        out = launch_cursor_agents_tab("resume 10479")
    assert out["ok"] is True
    assert out["transport"] == "cdp_cursor_agents"


def test_archive_cursor_agents_tab_empty_id() -> None:
    assert archive_cursor_agents_tab("") == {"ok": False, "reason": "empty_agent_id"}
    assert archive_cursor_agents_tab("   ") == {"ok": False, "reason": "empty_agent_id"}


def test_archive_cursor_agents_tab_ok() -> None:
    page = {"title": "Cursor Agents", "web_socket_debugger_url": "ws://fake"}

    def _run(coro):  # type: ignore[no-untyped-def]
        coro.close()
        return {"ok": True, "archived": "agent-abc"}

    with (
        patch("cursor_bridge.cursor_agents_hop.list_pages", return_value=[page]),
        patch("cursor_bridge.cursor_agents_hop.asyncio.run", side_effect=_run),
    ):
        out = archive_cursor_agents_tab("agent-abc")
    assert out == {"ok": True, "archived": "agent-abc"}


def test_root_row_pattern_matches_retired_prefix() -> None:
    pat = _root_row_pattern("10479")
    assert r"\[retired\]" in pat
    js = _list_root_sidebar_rows_js("10479")
    assert "[retired]" in js or "retired" in js


def test_rename_agent_row_js_skips_streaming_refusal() -> None:
    js = _rename_agent_row_js("agent-x", "[retired] 10479 slug")
    assert "agent_streaming" not in js
    assert "persist_path" in js and "dom_only" in js
    assert "updateAgentName" not in js
    assert "setAgentName" not in js


def test_archive_agent_js_streaming_guard() -> None:
    js = _archive_agent_js("agent-x")
    assert '[aria-label="In progress"], .ui-dot-grid-loader' in js
    assert "agent_streaming" in js
    assert "meta.unknown" in js
    assert "btn.click()" in js
    assert "getComposerBackgroundWork" in js
    assert "backgroundWorkService" in js
    assert "in_progress" in js
    assert "getAgentHeader" in js
    # streaming check precedes archive click
    assert js.index("agent_streaming") < js.index("btn.click()")


@pytest.mark.asyncio
async def test_archive_async_polls_until_idle() -> None:
    calls: list[int] = []

    async def fake_eval(ws, mid, expression, timeout_s):  # type: ignore[no-untyped-def]
        calls.append(mid)
        if len(calls) == 1:
            return {"ok": False, "reason": "agent_streaming", "agent_id": "agent-abc"}
        return {"ok": True, "archived": "agent-abc"}

    class _WsCtx:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return object()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    with (
        patch("websockets.connect", return_value=_WsCtx()),
        patch("cursor_bridge.cursor_agents_hop._eval", side_effect=fake_eval),
        patch("cursor_bridge.cursor_agents_hop.asyncio.sleep", new_callable=AsyncMock),
    ):
        out = await _archive_async(
            "ws://fake",
            "agent-abc",
            timeout_s=5.0,
            wait_until_idle_s=10.0,
            poll_interval_s=0.01,
        )
    assert out == {"ok": True, "archived": "agent-abc"}
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_archive_async_streaming_timeout() -> None:
    async def fake_eval(ws, mid, expression, timeout_s):  # type: ignore[no-untyped-def]
        return {"ok": False, "reason": "agent_streaming", "agent_id": "agent-abc"}

    class _WsCtx:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return object()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    with patch("websockets.connect", return_value=_WsCtx()), patch(
        "cursor_bridge.cursor_agents_hop._eval", side_effect=fake_eval
    ):
        out = await _archive_async(
            "ws://fake",
            "agent-abc",
            timeout_s=5.0,
            wait_until_idle_s=0.0,
            poll_interval_s=0.01,
        )
    assert out == {"ok": False, "reason": "agent_streaming", "agent_id": "agent-abc"}


@pytest.mark.asyncio
async def test_prune_keeps_departing_archives_older() -> None:
    list_result = [
        {"agent_id": "new-active", "title": "10479 slug", "active": True, "streaming": False},
        {"agent_id": "n-1", "title": ". 10479 foo", "active": False, "streaming": False},
        {"agent_id": "n-2", "title": ". 10479 old", "active": False, "streaming": False},
        {"agent_id": "n-3", "title": "resume 10479", "active": False, "streaming": False},
    ]
    archived: list[str] = []

    async def fake_archive(ws_url, aid, **kwargs):  # type: ignore[no-untyped-def]
        archived.append(aid)
        return {"ok": True, "archived": aid}

    class _WsCtx:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return object()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    async def fake_eval(ws, mid, expression, timeout_s):  # type: ignore[no-untyped-def]
        return list_result

    with (
        patch("websockets.connect", return_value=_WsCtx()),
        patch("cursor_bridge.cursor_agents_hop._eval", side_effect=fake_eval),
        patch("cursor_bridge.cursor_agents_hop._archive_on_ws", side_effect=fake_archive),
    ):
        out = await _prune_predecessors_async(
            "ws://fake",
            "10479",
            active_agent_id="new-active",
            departing_agent_id="n-1",
            keep_recent_predecessors=1,
            timeout_s=5.0,
            wait_until_idle_s=60.0,
            poll_interval_s=0.5,
        )
    assert out["ok"] is True
    assert out["kept_predecessors"] == ["n-1"]
    assert set(out["archived"]) == {"n-2", "n-3"}
    assert set(archived) == {"n-2", "n-3"}


def test_prune_cursor_agents_predecessors_empty_root() -> None:
    assert prune_cursor_agents_predecessors("") == {"ok": False, "reason": "empty_root_id"}


@pytest.mark.asyncio
async def test_prune_skips_streaming_without_archive() -> None:
    list_result = [
        {"agent_id": "active", "title": ". 10479 x", "active": True, "streaming": False, "created_at": 0},
        {"agent_id": "keep", "title": ". 10479 y", "active": False, "streaming": False, "created_at": 100},
        {"agent_id": "busy", "title": ". 10479 z", "active": False, "streaming": True, "created_at": 50},
    ]
    archive_calls: list[str] = []

    async def fake_archive(ws, aid, **kwargs):  # type: ignore[no-untyped-def]
        archive_calls.append(aid)
        return {"ok": True, "archived": aid}

    class _WsCtx:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return object()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    async def fake_eval(ws, mid, expression, timeout_s):  # type: ignore[no-untyped-def]
        return list_result

    with (
        patch("websockets.connect", return_value=_WsCtx()),
        patch("cursor_bridge.cursor_agents_hop._eval", side_effect=fake_eval),
        patch("cursor_bridge.cursor_agents_hop._archive_on_ws", side_effect=fake_archive),
    ):
        out = await _prune_predecessors_async(
            "ws://fake",
            "10479",
            active_agent_id="active",
            departing_agent_id="",
            keep_recent_predecessors=1,
            timeout_s=5.0,
            wait_until_idle_s=0.0,
            poll_interval_s=0.5,
        )
    assert out["ok"] is True
    assert "busy" not in archive_calls
    assert any(
        s.get("agent_id") == "busy"
        and s.get("queued") is True
        and s.get("reason") == "agent_streaming"
        for s in out["skipped"]
    )


@pytest.mark.asyncio
async def test_prune_sorts_by_created_at_desc() -> None:
    list_result = [
        {"agent_id": "active", "title": ". 10479 a", "active": True, "streaming": False, "created_at": 300},
        {"agent_id": "old", "title": ". 10479 old", "active": False, "streaming": False, "created_at": 10},
        {"agent_id": "mid", "title": ". 10479 mid", "active": False, "streaming": False, "created_at": 100},
        {"agent_id": "new", "title": ". 10479 new", "active": False, "streaming": False, "created_at": 200},
    ]
    archived: list[str] = []

    async def fake_archive(ws, aid, **kwargs):  # type: ignore[no-untyped-def]
        archived.append(aid)
        return {"ok": True, "archived": aid}

    class _WsCtx:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return object()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    eval_calls: list[str] = []

    async def fake_eval(ws, mid, expression, timeout_s):  # type: ignore[no-untyped-def]
        eval_calls.append(expression)
        if len(eval_calls) == 1:
            return list_result
        return {"ok": True}

    with (
        patch("websockets.connect", return_value=_WsCtx()),
        patch("cursor_bridge.cursor_agents_hop._eval", side_effect=fake_eval),
        patch("cursor_bridge.cursor_agents_hop._archive_on_ws", side_effect=fake_archive),
    ):
        out = await _prune_predecessors_async(
            "ws://fake",
            "10479",
            active_agent_id="active",
            departing_agent_id="",
            keep_recent_predecessors=2,
            timeout_s=5.0,
            wait_until_idle_s=0.0,
            poll_interval_s=0.5,
        )
    assert out["ok"] is True
    assert out["kept_predecessors"] == ["new", "mid"]
    assert archived == ["old"]


@pytest.mark.asyncio
async def test_prune_selects_inactive_active_agent_id() -> None:
    list_result = [
        {
            "agent_id": "landed",
            "title": ". 10479 landed",
            "active": False,
            "streaming": False,
            "created_at": 0,
        },
    ]

    class _WsCtx:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return object()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    eval_calls: list[str] = []

    async def fake_eval(ws, mid, expression, timeout_s):  # type: ignore[no-untyped-def]
        eval_calls.append(expression)
        return list_result

    with (
        patch("websockets.connect", return_value=_WsCtx()),
        patch("cursor_bridge.cursor_agents_hop._eval", side_effect=fake_eval),
        patch("cursor_bridge.cursor_agents_hop._archive_on_ws", new_callable=AsyncMock),
    ):
        out = await _prune_predecessors_async(
            "ws://fake",
            "10479",
            active_agent_id="landed",
            departing_agent_id="",
            keep_recent_predecessors=2,
            timeout_s=5.0,
            wait_until_idle_s=0.0,
            poll_interval_s=0.5,
        )
    assert out["ok"] is True
    assert len(eval_calls) == 2
    assert "landed" in eval_calls[1]
    assert "el.click()" in eval_calls[1]


@pytest.mark.asyncio
async def test_prune_resolves_departing_index_by_agent_id() -> None:
    list_result = [
        {
            "agent_id": "lock-holder",
            "title": ". 10479 lock tab",
            "active": False,
            "streaming": False,
            "unknown": False,
            "created_at": 300,
            "sidebar_index": 0,
        },
        {
            "agent_id": "landed",
            "title": "[retired] 10479 old title",
            "active": False,
            "streaming": False,
            "unknown": False,
            "created_at": 200,
            "sidebar_index": 1,
        },
        {
            "agent_id": "depart-id",
            "title": "10479 hop",
            "active": False,
            "streaming": False,
            "unknown": False,
            "created_at": 100,
            "sidebar_index": 2,
        },
        {
            "agent_id": "older",
            "title": ". 10479 ancient",
            "active": False,
            "streaming": False,
            "unknown": False,
            "created_at": 50,
            "sidebar_index": 3,
        },
    ]
    archived: list[str] = []

    async def fake_archive(ws, aid, **kwargs):  # type: ignore[no-untyped-def]
        archived.append(aid)
        return {"ok": True, "archived": aid}

    class _WsCtx:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return object()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    async def fake_eval(ws, mid, expression, timeout_s):  # type: ignore[no-untyped-def]
        return list_result

    with (
        patch("websockets.connect", return_value=_WsCtx()),
        patch("cursor_bridge.cursor_agents_hop._eval", side_effect=fake_eval),
        patch("cursor_bridge.cursor_agents_hop._archive_on_ws", side_effect=fake_archive),
    ):
        out = await _prune_predecessors_async(
            "ws://fake",
            "10479",
            active_agent_id="landed",
            departing_agent_id="depart-id",
            holder_agent_id="lock-holder",
            keep_recent_predecessors=1,
            timeout_s=5.0,
            wait_until_idle_s=0.0,
            poll_interval_s=0.5,
        )
    assert out["ok"] is True
    assert "lock-holder" not in archived
    assert any(
        s.get("id") == "lock-holder" and s.get("reason") == "lock_holder"
        for s in out["skipped"]
    )
    assert "depart-id" not in archived or "depart-id" in out.get("kept_predecessors", [])


def test_prune_cursor_agents_predecessors_timeout_returns_cleanup_failed() -> None:
    page = {"title": "Cursor Agents", "web_socket_debugger_url": "ws://fake"}

    def _run(coro):  # type: ignore[no-untyped-def]
        coro.close()
        raise TimeoutError("cdp eval")

    with (
        patch("cursor_bridge.cursor_agents_hop.list_pages", return_value=[page]),
        patch("cursor_bridge.cursor_agents_hop.asyncio.run", side_effect=_run),
    ):
        out = prune_cursor_agents_predecessors("10479", active_agent_id="active")
    assert out == {
        "ok": False,
        "reason": "cleanup_failed:TimeoutError",
        "archived": [],
        "skipped": [],
    }


@pytest.mark.asyncio
async def test_prune_only_retired_skips_unmarked_and_no_select() -> None:
    list_result = [
        {
            "agent_id": "holder",
            "title": "10479 live",
            "active": True,
            "streaming": False,
            "unknown": False,
            "created_at": 300,
            "sidebar_index": 0,
        },
        {
            "agent_id": "side-tab",
            "title": ". 10479 operator side",
            "active": False,
            "streaming": False,
            "unknown": False,
            "created_at": 200,
            "sidebar_index": 1,
        },
        {
            "agent_id": "retired-row",
            "title": "[retired] 10479 slug",
            "active": False,
            "streaming": False,
            "unknown": False,
            "created_at": 100,
            "sidebar_index": 2,
        },
        {
            "agent_id": "listed-retired",
            "title": "10479 other",
            "active": False,
            "streaming": False,
            "unknown": False,
            "created_at": 50,
            "sidebar_index": 3,
        },
    ]
    archived: list[str] = []
    eval_calls: list[str] = []

    async def fake_archive(ws, aid, **kwargs):  # type: ignore[no-untyped-def]
        archived.append(aid)
        return {"ok": True, "archived": aid}

    class _WsCtx:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return object()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    async def fake_eval(ws, mid, expression, timeout_s):  # type: ignore[no-untyped-def]
        eval_calls.append(expression)
        return list_result

    with (
        patch("websockets.connect", return_value=_WsCtx()),
        patch("cursor_bridge.cursor_agents_hop._eval", side_effect=fake_eval),
        patch("cursor_bridge.cursor_agents_hop._archive_on_ws", side_effect=fake_archive),
    ):
        out = await _prune_predecessors_async(
            "ws://fake",
            "10479",
            active_agent_id="holder",
            departing_agent_id="",
            holder_agent_id="holder",
            keep_recent_predecessors=0,
            timeout_s=5.0,
            wait_until_idle_s=0.0,
            poll_interval_s=0.5,
            select_active=False,
            only_retired=True,
            retired_agent_ids=["listed-retired"],
        )
    assert out["ok"] is True
    assert "side-tab" not in archived
    assert set(archived) == {"retired-row", "listed-retired"}
    assert any(
        s.get("id") == "side-tab" and s.get("reason") == "not_marked_retired"
        for s in out["skipped"]
    )
    assert not any("el.click()" in expr for expr in eval_calls)


@pytest.mark.asyncio
async def test_prune_skips_unknown_busy_state() -> None:
    list_result = [
        {
            "agent_id": "active",
            "title": ". 10479 a",
            "active": True,
            "streaming": False,
            "unknown": False,
            "created_at": 300,
            "sidebar_index": 0,
        },
        {
            "agent_id": "mystery",
            "title": ". 10479 mystery",
            "active": False,
            "streaming": True,
            "unknown": True,
            "created_at": 100,
            "sidebar_index": 1,
        },
    ]
    archive_calls: list[str] = []

    async def fake_archive(ws, aid, **kwargs):  # type: ignore[no-untyped-def]
        archive_calls.append(aid)
        return {"ok": True, "archived": aid}

    class _WsCtx:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return object()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    async def fake_eval(ws, mid, expression, timeout_s):  # type: ignore[no-untyped-def]
        return list_result

    with (
        patch("websockets.connect", return_value=_WsCtx()),
        patch("cursor_bridge.cursor_agents_hop._eval", side_effect=fake_eval),
        patch("cursor_bridge.cursor_agents_hop._archive_on_ws", side_effect=fake_archive),
    ):
        out = await _prune_predecessors_async(
            "ws://fake",
            "10479",
            active_agent_id="active",
            departing_agent_id="",
            keep_recent_predecessors=0,
            timeout_s=5.0,
            wait_until_idle_s=0.0,
            poll_interval_s=0.5,
        )
    assert "mystery" not in archive_calls
    assert any(
        s.get("id") == "mystery" and s.get("reason") == "unknown_busy_state"
        for s in out["skipped"]
    )


@pytest.mark.asyncio
async def test_prune_skips_newer_than_departing_by_sidebar_index() -> None:
    list_result = [
        {
            "agent_id": "active",
            "title": ". 10479 x",
            "active": True,
            "streaming": False,
            "created_at": 0,
            "sidebar_index": 0,
        },
        {
            "agent_id": "newer",
            "title": ". 10479 newer",
            "active": False,
            "streaming": False,
            "created_at": 50,
            "sidebar_index": 0,
        },
        {
            "agent_id": "depart",
            "title": ". 10479 depart",
            "active": False,
            "streaming": False,
            "created_at": 100,
            "sidebar_index": 1,
        },
    ]
    archive_calls: list[str] = []

    async def fake_archive(ws, aid, **kwargs):  # type: ignore[no-untyped-def]
        archive_calls.append(aid)
        return {"ok": True, "archived": aid}

    class _WsCtx:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return object()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    async def fake_eval(ws, mid, expression, timeout_s):  # type: ignore[no-untyped-def]
        return list_result

    with (
        patch("websockets.connect", return_value=_WsCtx()),
        patch("cursor_bridge.cursor_agents_hop._eval", side_effect=fake_eval),
        patch("cursor_bridge.cursor_agents_hop._archive_on_ws", side_effect=fake_archive),
    ):
        out = await _prune_predecessors_async(
            "ws://fake",
            "10479",
            active_agent_id="active",
            departing_agent_id="depart",
            holder_agent_id="holder-uuid",
            keep_recent_predecessors=0,
            timeout_s=5.0,
            wait_until_idle_s=0.0,
            poll_interval_s=0.5,
        )
    assert out["ok"] is True
    assert "newer" not in archive_calls
    assert any(
        s.get("agent_id") == "newer" and s.get("reason") == "newer_than_departing"
        for s in out["skipped"]
    )


def test_finalize_xvfb_hop_sidebar_prune_before_rename() -> None:
    calls: list[str] = []

    def fake_prune(*a, **k):  # type: ignore[no-untyped-def]
        calls.append("prune")
        return {"ok": True}

    def fake_rename(*a, **k):  # type: ignore[no-untyped-def]
        calls.append("rename")
        return {"ok": True}

    def fake_sub(*a, **k):  # type: ignore[no-untyped-def]
        return {"ok": True}

    with (
        patch("bus_watch.cursor_xvfb_subscription.subscribe_cursor_xvfb_house", fake_sub),
        patch("cursor_bridge.cursor_agents_hop.prune_cursor_agents_predecessors", fake_prune),
        patch("cursor_bridge.cursor_agents_hop.rename_agent_row_in_sidebar", fake_rename),
        patch("bus_watch.ide_hop.read_lock", return_value={"holder": "ide:holder-uuid"}),
    ):
        ide_hop.finalize_xvfb_hop_sidebar(
            "10479",
            departing_agent_id="dep-1",
            landed_agent_id="land-1",
            slug_fragment="slug",
        )
    assert calls == ["prune", "rename"]


def test_archive_cursor_agents_tab_row_not_found() -> None:
    page = {"title": "Cursor Agents", "web_socket_debugger_url": "ws://fake"}

    def _run(coro):  # type: ignore[no-untyped-def]
        coro.close()
        return {"ok": False, "reason": "row_not_found"}

    with (
        patch("cursor_bridge.cursor_agents_hop.list_pages", return_value=[page]),
        patch("cursor_bridge.cursor_agents_hop.asyncio.run", side_effect=_run),
    ):
        out = archive_cursor_agents_tab("missing-id")
    assert out["reason"] == "row_not_found"
