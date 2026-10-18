"""Acceptance checks for the grok-bot context-hop observer."""

from __future__ import annotations

import ast
import asyncio
import sqlite3
import subprocess
from pathlib import Path
from urllib.error import URLError

import pytest
from deploy_identity.mcp_edge_events import (
    mcp_edge_grokbot_context_hop_due,
    mcp_edge_grokbot_context_hop_notify_failed,
    mcp_edge_grokbot_probe_failed,
)

from scripts.model_manager.ui.controller.grokbot_context_hop_io import (
    DEFAULT_CDP_PORTS,
    hop_reply_argv,
    probe_grok_agent_context,
    probe_ports,
    reply_context_hop,
)
from scripts.model_manager.ui.controller.grokbot_egress_watch_logic import (
    GC1_STEP_INDEX_DUE,
    HOUSE_STEP_INDEX_DUE,
    TURN_INDEX_DUE,
    ContextHopState,
    GrokbotEgressSnapshot,
    context_row_due,
    decide_context_hop,
    hop_subject,
    parse_last_message_id,
    record_hop_notified,
    roster_name_allowed,
)

_NOW_MS = 1_700_000_000_000
_FRESH = _NOW_MS - 60_000


def _row(
    *,
    agent_id: str = "a1",
    name: str = "Impresario Alpha",
    last: str = "t25s1",
    running: bool = False,
    updated: int = _FRESH,
    loaded: int = 0,
) -> dict[str, object]:
    return {
        "agent_id": agent_id,
        "name": name,
        "lastMessageId": last,
        "is_running": running,
        "updated_at_ms": updated,
        "loaded_entry_count": loaded,
    }


def test_ac1_probe_returns_row_keys() -> None:
    async def evaluate(expression: str) -> list[dict[str, object]]:
        assert "lastMessageId" in expression
        return [
            {
                "agent_id": "a1",
                "name": "Impresario Alpha",
                "lastMessageId": "t3s4",
                "is_running": False,
                "updated_at_ms": 10,
            }
        ]

    rows = asyncio.run(probe_grok_agent_context(evaluate=evaluate))
    assert isinstance(rows, list)
    assert set(rows[0]) >= {
        "agent_id",
        "name",
        "lastMessageId",
        "is_running",
        "updated_at_ms",
    }
    assert rows[0]["lastMessageId"] == "t3s4"


def test_ac1b_context_js_updated_at_fallback_chain() -> None:
    io_path = Path(__file__).with_name("grokbot_context_hop_io.py")
    text = io_path.read_text(encoding="utf-8")
    assert "r.updatedAt" in text
    assert "r.lastActivityAt" in text
    assert "r.updatedAtMs" in text


def test_ac1c_probe_default_multi_port_dedupe_and_tolerate_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ports_probed: list[int] = []

    def fake_resolve(cdp_port: int, *, timeout_s: float) -> str:
        del timeout_s
        ports_probed.append(cdp_port)
        if cdp_port == 9339:
            raise URLError("offline")
        return f"ws://127.0.0.1:{cdp_port}"

    async def fake_eval(
        ws_url: str,
        expression: str,
        *,
        await_promise: bool = True,
        timeout_s: float = 30.0,
    ) -> list[dict[str, object]]:
        del expression, await_promise, timeout_s
        if ws_url.endswith(":9340"):
            return [
                {
                    "agent_id": "dup",
                    "name": "low-ts",
                    "lastMessageId": "t1s1",
                    "is_running": False,
                    "updated_at_ms": 50,
                },
                {
                    "agent_id": "only-9340",
                    "name": "gc1",
                    "lastMessageId": "t2s2",
                    "is_running": True,
                    "updated_at_ms": 500,
                },
            ]
        return []

    monkeypatch.setattr("cursor_bridge.grok_computer._resolve_ws_url", fake_resolve)
    monkeypatch.setattr("cursor_bridge.grok_computer._cdp_eval", fake_eval)

    rows = asyncio.run(probe_grok_agent_context())
    assert DEFAULT_CDP_PORTS == (9340, 9339)
    assert ports_probed == [9340, 9339]
    by_id = {str(r["agent_id"]): r for r in rows}
    assert by_id["only-9340"]["updated_at_ms"] == 500
    assert by_id["dup"]["updated_at_ms"] == 50


def test_probe_ports_skips_runtime_error_and_falls_back_to_9339(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probed_ports: list[int] = []

    def fake_resolve(cdp_port: int, *, timeout_s: float) -> str:
        del timeout_s
        probed_ports.append(cdp_port)
        return f"ws://127.0.0.1:{cdp_port}"

    async def fake_eval(
        ws_url: str,
        expression: str,
        *,
        await_promise: bool = True,
        timeout_s: float = 30.0,
    ) -> list[dict[str, object]]:
        del expression, await_promise, timeout_s
        if ws_url.endswith(":9340"):
            raise RuntimeError(
                "grok-bot-cdp list-agents failed on port 9340 (rc=1)"
            )
        return [
            {
                "agent_id": "gc1",
                "name": "Stage Manager",
                "lastMessageId": "t4s1",
                "is_running": False,
                "updated_at_ms": _FRESH,
                "loaded_entry_count": 2,
            }
        ]

    monkeypatch.setattr("cursor_bridge.grok_computer._resolve_ws_url", fake_resolve)
    monkeypatch.setattr("cursor_bridge.grok_computer._cdp_eval", fake_eval)

    rows = asyncio.run(probe_ports())
    assert probed_ports == [9340, 9339]
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "gc1"


def test_ac1d_probe_dedup_keeps_highest_updated_at_ms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_resolve(cdp_port: int, *, timeout_s: float) -> str:
        del timeout_s
        return f"ws://127.0.0.1:{cdp_port}"

    async def fake_eval(
        ws_url: str,
        expression: str,
        *,
        await_promise: bool = True,
        timeout_s: float = 30.0,
    ) -> list[dict[str, object]]:
        del expression, await_promise, timeout_s
        if ws_url.endswith(":9340"):
            return [
                {
                    "agent_id": "dup",
                    "name": "low-ts",
                    "lastMessageId": "t1s1",
                    "is_running": False,
                    "updated_at_ms": 50,
                }
            ]
        return [
            {
                "agent_id": "dup",
                "name": "high-ts",
                "lastMessageId": "t9s9",
                "is_running": True,
                "updated_at_ms": 200,
            }
        ]

    monkeypatch.setattr("cursor_bridge.grok_computer._resolve_ws_url", fake_resolve)
    monkeypatch.setattr("cursor_bridge.grok_computer._cdp_eval", fake_eval)

    rows = asyncio.run(probe_grok_agent_context())
    assert len(rows) == 1
    assert rows[0]["updated_at_ms"] == 200
    assert rows[0]["name"] == "high-ts"


def test_ac2_thresholds_retired_stale_and_loaded_count() -> None:
    assert TURN_INDEX_DUE == 25
    assert GC1_STEP_INDEX_DUE == 180
    assert HOUSE_STEP_INDEX_DUE == 160
    assert GC1_STEP_INDEX_DUE != HOUSE_STEP_INDEX_DUE
    gc1 = _row(name="Stage Manager (gc1)", last="t1s180")
    house = _row(name="Impresario Alpha", last="t1s160")
    assert context_row_due(gc1, now_ms=_NOW_MS) is True
    assert context_row_due(house, now_ms=_NOW_MS) is True
    swapped_gc1 = _row(name="Stage Manager (gc1)", last="t1s160")
    swapped_house = _row(name="Impresario Alpha", last="t1s180")
    assert context_row_due(swapped_gc1, now_ms=_NOW_MS) is False
    assert context_row_due(swapped_house, now_ms=_NOW_MS) is True
    retired = _row(name="Impresario Alpha (retired)", last="t40s200")
    assert context_row_due(retired, now_ms=_NOW_MS) is False
    stale = _row(updated=_NOW_MS - (6 * 60 * 60 * 1000) - 1)
    assert context_row_due(stale, now_ms=_NOW_MS) is False
    low = _row(last="t1s1", loaded=999)
    high = _row(last="t1s1", loaded=0)
    assert context_row_due(low, now_ms=_NOW_MS) is context_row_due(high, now_ms=_NOW_MS)


def test_ac3_subject_lexicographic_and_bl1() -> None:
    subject = hop_subject(["Reviewer R1 Zed", "Impresario Alpha"])
    assert subject == "NUDGE|context-hop-due|Impresario Alpha,Reviewer R1 Zed"
    assert hop_subject(["Impresario Alpha"]) == "NUDGE|context-hop-due|Impresario Alpha"
    assert not subject.startswith("DIRECTIVE")
    assert not subject.startswith("HOP")
    state = ContextHopState()
    notice = decide_context_hop(
        rows=[
            _row(agent_id="b", name="Impresario Backlog (bl1)", last="t40s200"),
            _row(agent_id="a", name="Impresario Alpha"),
        ],
        now=0.0,
        now_ms=_NOW_MS,
        state=state,
        open_gc1_bus_turn=False,
        in_flight_gc1_mcp=False,
    )
    assert notice is not None
    assert "bl1" not in notice.subject
    assert notice.names == ["Impresario Alpha"]


def test_ac4_manage_rows_excluded_before_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.model_manager.ui.controller.grokbot_egress_watch_io import (
        open_gc1_bus_turn,
    )

    db = tmp_path / "messages.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE turns (thread TEXT, turn_number INTEGER, from_agent TEXT, subject TEXT, created_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO turns VALUES (?, ?, ?, ?, ?)",
        [
            ("15851", 1, "grok-bot-gc1", "REQUEST|working", "2026-10-17T00:00:00Z"),
            (
                "15851",
                2,
                "manage",
                "NUDGE|context-hop-due|Impresario Alpha",
                "2026-10-17T00:01:00Z",
            ),
        ],
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db))
    assert open_gc1_bus_turn(thread="15851", now_ms=_NOW_MS) is True

    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM turns")
    conn.execute(
        "INSERT INTO turns VALUES (?, ?, ?, ?, ?)",
        ("15851", 3, "grok-bot-gc1", "REQUEST|still working", "2026-10-17T00:02:00Z"),
    )
    conn.commit()
    conn.close()
    assert open_gc1_bus_turn(thread="15851", now_ms=_NOW_MS) is True


def test_ac5_allowlist() -> None:
    assert roster_name_allowed("Stage Manager (gc1)") is True
    assert roster_name_allowed("Impresario Alpha") is True
    assert roster_name_allowed("Reviewer R1 Zed") is True
    assert roster_name_allowed("Operator Liaison Kay") is True
    assert roster_name_allowed("Impresario Backlog (bl1)") is False
    assert roster_name_allowed("Stage Manager extra") is False
    assert roster_name_allowed("other gc1") is False


def test_ac6_cooldown_successor_and_failed_reply() -> None:
    state = ContextHopState()
    row = _row()
    first = decide_context_hop(
        rows=[row],
        now=0.0,
        now_ms=_NOW_MS,
        state=state,
        open_gc1_bus_turn=False,
        in_flight_gc1_mcp=False,
    )
    assert first is not None
    record_hop_notified(state, first.agent_ids, now=0.0)
    second = decide_context_hop(
        rows=[row],
        now=10.0,
        now_ms=_NOW_MS,
        state=state,
        open_gc1_bus_turn=False,
        in_flight_gc1_mcp=False,
    )
    assert second is None
    successor = decide_context_hop(
        rows=[_row(agent_id="a2")],
        now=10.0,
        now_ms=_NOW_MS,
        state=state,
        open_gc1_bus_turn=False,
        in_flight_gc1_mcp=False,
    )
    assert successor is not None
    failed_state = ContextHopState()
    due = decide_context_hop(
        rows=[row],
        now=0.0,
        now_ms=_NOW_MS,
        state=failed_state,
        open_gc1_bus_turn=False,
        in_flight_gc1_mcp=False,
    )
    assert due is not None
    assert failed_state.cooldown_until == {}
    again = decide_context_hop(
        rows=[row],
        now=1.0,
        now_ms=_NOW_MS,
        state=failed_state,
        open_gc1_bus_turn=False,
        in_flight_gc1_mcp=False,
    )
    assert again is not None


def test_ac7_deferral_ceiling() -> None:
    state = ContextHopState()
    row = _row(running=True)
    held = decide_context_hop(
        rows=[row],
        now=0.0,
        now_ms=_NOW_MS,
        state=state,
        open_gc1_bus_turn=True,
        in_flight_gc1_mcp=False,
    )
    assert held is None
    still = decide_context_hop(
        rows=[row],
        now=599.0,
        now_ms=_NOW_MS,
        state=state,
        open_gc1_bus_turn=False,
        in_flight_gc1_mcp=True,
    )
    assert still is None
    forced = decide_context_hop(
        rows=[row],
        now=600.0,
        now_ms=_NOW_MS,
        state=state,
        open_gc1_bus_turn=True,
        in_flight_gc1_mcp=True,
    )
    assert forced is not None
    idle = ContextHopState()
    immediate = decide_context_hop(
        rows=[_row()],
        now=0.0,
        now_ms=_NOW_MS,
        state=idle,
        open_gc1_bus_turn=False,
        in_flight_gc1_mcp=False,
    )
    assert immediate is not None


def test_ac8_parse_and_module_split() -> None:
    assert parse_last_message_id("t25s180") == (25, 180)
    assert parse_last_message_id("not-an-id") is None
    assert context_row_due(_row(last="nope"), now_ms=_NOW_MS) is False
    logic_path = Path(__file__).with_name("grokbot_egress_watch_logic.py")
    io_path = Path(__file__).with_name("grokbot_context_hop_io.py")
    assert "TURN_INDEX_DUE" in logic_path.read_text(encoding="utf-8")
    io_tree = ast.parse(io_path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in io_tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "context_row_due" not in imported
    assert "sqlite3" not in io_path.read_text(encoding="utf-8")


def test_ac9_tick_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.model_manager.ui.controller.grokbot_egress_watch_loop import (
        GrokbotEgressWatchLoop,
    )

    calls: list[str] = []

    async def _hop(self, **kwargs: object) -> None:
        del self, kwargs
        calls.append("hop")

    monkeypatch.setattr(GrokbotEgressWatchLoop, "_context_hop", _hop)

    async def _emit(*args: object, **kwargs: object) -> None:
        del args, kwargs

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_loop._emit",
        _emit,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.grokbot_egress_watch_loop.keep_alive_grokbot_presence",
        lambda: None,
    )

    def snapshot(*, desktop: bool, unreadable: bool) -> GrokbotEgressSnapshot:
        return GrokbotEgressSnapshot(
            desktop_running=desktop,
            desktop_pid=1,
            watch_connected=True,
            watch_disconnected_s=None,
            daemon_log_readable=not unreadable,
            health_http_status=200,
            tunnel_stale=False,
            loader_stuck=False,
            loader_stuck_s=None,
            hm1_tools_call_ok=True,
            undici_idle_s=0.0,
            in_flight_gc1_mcp=False,
            open_gc1_bus_turn=False,
        )

    async def run(snap: GrokbotEgressSnapshot) -> None:
        loop = GrokbotEgressWatchLoop(
            workspace_root=Path("."),
            snapshot_fn=lambda: _done(snap),
        )
        await loop._tick()

    async def _done(snap: GrokbotEgressSnapshot) -> GrokbotEgressSnapshot:
        return snap

    asyncio.run(run(snapshot(desktop=False, unreadable=False)))
    assert calls == []
    asyncio.run(run(snapshot(desktop=True, unreadable=True)))
    assert calls == []
    asyncio.run(run(snapshot(desktop=True, unreadable=False)))
    assert calls == ["hop"]


def test_ac10_reply_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    threaded: list[bool] = []
    real = asyncio.to_thread

    async def tracking(fn, *args, **kwargs):  # type: ignore[no-untyped-def]
        threaded.append(True)
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", tracking)
    asyncio.run(
        reply_context_hop(
            subject="NUDGE|context-hop-due|Impresario Alpha",
            body="body text",
            after_turn=41,
            runner=runner,
        )
    )
    assert threaded
    assert seen[0][1:] == [
        "reply",
        "--thread",
        "15851",
        "--to",
        "grok-bot-gc1",
        "--from-agent",
        "manage",
        "--subject",
        "NUDGE|context-hop-due|Impresario Alpha",
        "--body",
        "body text",
        "--after-turn",
        "41",
    ]
    assert seen[0][0].endswith("scripts/agent-bus")
    built = hop_reply_argv(
        subject="NUDGE|context-hop-due|Impresario Alpha",
        body="body text",
        after_turn=41,
    )
    assert built == seen[0]


def test_ac11_event_factories(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.model_manager.ui.controller import grokbot_egress_watch_loop
    from scripts.model_manager.ui.controller.grokbot_egress_watch_loop import (
        GrokbotEgressWatchLoop,
    )

    due = mcp_edge_grokbot_context_hop_due(
        names=["Impresario Alpha", "Reviewer R1 Zed"], thread_id="15851"
    )
    assert due.signal == "mcp.edge.grokbot.context_hop.due"
    assert due.role == "observation"
    assert due.scope == "node"
    assert due.payload["names"] == ["Impresario Alpha", "Reviewer R1 Zed"]
    assert due.payload["thread_id"] == "15851"
    failed = mcp_edge_grokbot_context_hop_notify_failed(
        error_class="RuntimeError", detail="exit 1"
    )
    probe = mcp_edge_grokbot_probe_failed(error_class="x", detail="y")
    assert set(failed.payload) == set(probe.payload)
    assert failed.signal == "mcp.edge.grokbot.context_hop.notify_failed"
    assert "hop.observed" not in failed.signal
    assert "context.hop.observed" not in due.signal

    emitted: list[tuple[str, dict[str, object], str, str]] = []

    async def fake_emit(
        signal: str,
        payload: dict[str, object],
        *,
        source: str,
        role: str,
        scope: str,
    ) -> None:
        del source
        emitted.append((signal, payload, role, scope))

    async def fake_probe() -> list[dict[str, object]]:
        return [
            _row(agent_id="b", name="Reviewer R1 Zed"),
            _row(agent_id="a", name="Impresario Alpha"),
        ]

    monkeypatch.setattr(grokbot_egress_watch_loop, "_emit", fake_emit)
    monkeypatch.setattr(
        grokbot_egress_watch_loop, "probe_grok_agent_context", fake_probe
    )

    snap = GrokbotEgressSnapshot(
        desktop_running=True,
        desktop_pid=1,
        watch_connected=True,
        watch_disconnected_s=None,
        daemon_log_readable=True,
        health_http_status=200,
        tunnel_stale=False,
        loader_stuck=False,
        loader_stuck_s=None,
        hm1_tools_call_ok=True,
        undici_idle_s=0.0,
        in_flight_gc1_mcp=False,
        open_gc1_bus_turn=False,
    )

    async def fake_reply_fail(**kwargs: object) -> None:
        del kwargs
        raise RuntimeError("bus down")

    monkeypatch.setattr(
        grokbot_egress_watch_loop, "reply_context_hop", fake_reply_fail
    )
    loop = GrokbotEgressWatchLoop(
        workspace_root=Path("."),
        snapshot_fn=lambda: asyncio.sleep(0, result=snap),
    )
    asyncio.run(loop._context_hop(now=100.0, now_ms=_NOW_MS, snapshot=snap))
    assert len(emitted) == 2
    assert emitted[0][0] == "mcp.edge.grokbot.context_hop.due"
    assert emitted[0][1] == {
        "names": ["Impresario Alpha", "Reviewer R1 Zed"],
        "thread_id": "15851",
    }
    assert emitted[0][2:] == ("observation", "node")
    assert emitted[1][0] == "mcp.edge.grokbot.context_hop.notify_failed"
    assert emitted[1][1] == {"error_class": "RuntimeError", "detail": "bus down"}
    assert emitted[1][2:] == ("observation", "node")
    assert loop._hop_state.cooldown_until == {}

    emitted.clear()

    async def fake_reply_ok(**kwargs: object) -> None:
        del kwargs

    monkeypatch.setattr(grokbot_egress_watch_loop, "reply_context_hop", fake_reply_ok)
    asyncio.run(loop._context_hop(now=101.0, now_ms=_NOW_MS, snapshot=snap))
    assert len(emitted) == 1
    assert emitted[0][0] == "mcp.edge.grokbot.context_hop.due"
    assert set(loop._hop_state.cooldown_until) == {"a", "b"}

