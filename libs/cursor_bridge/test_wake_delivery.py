"""Offline tests for wake_delivery stale-draft guard and send fallback."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from cursor_bridge import wake_delivery as wd
from cursor_bridge.chat_surface import classify_surface
from cursor_bridge.chat_target import wake_line
from cursor_bridge.composer_witness import composer_witness_expression
from cursor_bridge.display_interaction_locks import (
    _DISPLAY_SWITCH_DEPTH,
    display_switch_lock,
)
from cursor_bridge.glass_services_js import submit_wake_line_js
from cursor_bridge.wake_delivery import (
    _busy_or_draft_reason,
    _is_stale_wake_draft,
    _paste,
    _paste_cursor_services,
    deliver_wake,
    normalize_target,
)

_FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "cursor_agents_9338_probe.json"
)

pytestmark = pytest.mark.offline


_QS_CALL = re.compile(r"querySelector(?:All)?\s*\(")
_QS_ARG = re.compile(
    r"""\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|`((?:[^`\\]|\\.)*)`)"""
)


def _decode_js_string(body: str, *, quote: str) -> str:
    if quote == '"':
        return json.loads('"' + body + '"')
    text = body.replace("\\'", "'").replace("\\`", "`").replace("\\\\", "\\")
    return json.loads('"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"')


def _query_selector_literal_args(js: str) -> list[str]:
    """Every querySelector/querySelectorAll argument, any quote style.

    Break: a second call (single quotes, double quotes, backticks, or
    querySelectorAll) must show up so the witness test's count stays 1.
    A non-literal argument is recorded as a distinct sentinel.
    """
    quotes = ('"', "'", "`")
    args: list[str] = []
    for match in _QS_CALL.finditer(js):
        arg = _QS_ARG.match(js, match.end())
        if arg is None or arg.lastindex is None:
            args.append("<non-literal>")
            continue
        body = arg.group(arg.lastindex)
        args.append(_decode_js_string(body, quote=quotes[arg.lastindex - 1]))
    return args


def _match_count_guard_block(js: str) -> str:
    """Body of ``if (match_count === 1) { … }`` (break: witness fields set outside guard)."""
    marker = "if (match_count === 1)"
    idx = js.find(marker)
    if idx == -1:
        raise AssertionError("witness expression missing match_count === 1 guard")
    brace_start = js.find("{", idx)
    if brace_start == -1:
        raise AssertionError("witness guard missing opening brace")
    depth = 0
    for i in range(brace_start, len(js)):
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                return js[brace_start + 1 : i]
    raise AssertionError("witness guard has unbalanced braces")


def _witness_preamble_before_guard(js: str) -> str:
    marker = "if (match_count === 1)"
    idx = js.find(marker)
    if idx == -1:
        raise AssertionError("witness expression missing match_count === 1 guard")
    return js[:idx]


@pytest.mark.parametrize(
    "draft",
    [
        "please hold",
        "BRIDGE_WAKE thread=1 turn=2 extra",
        "prefix BRIDGE_WAKE thread=15851 turn=289",
    ],
)
def test_human_draft_still_blocks(draft: str) -> None:
    assert _busy_or_draft_reason({"composer_draft": draft}) == "composer_not_empty"


def test_stale_wake_draft_recognized_and_allowed() -> None:
    stale = "BRIDGE_WAKE thread=15851 turn=289"
    assert _is_stale_wake_draft(stale)
    assert _busy_or_draft_reason({"composer_draft": stale}) is None


def test_wake_line_single_and_multi_turn() -> None:
    assert wake_line("15851", 1647) == "BRIDGE_WAKE thread=15851 turn=1647"
    tab = "21060be7-f0a6-4fa1-9058-b117ce4fc526"
    assert (
        wake_line("15851", 1647, tab=tab)
        == f"BRIDGE_WAKE thread=15851 turn=1647 tab={tab}"
    )
    assert (
        wake_line("15851", "1647,1648,1649")
        == "BRIDGE_WAKE thread=15851 turn=1647,1648,1649"
    )
    assert (
        wake_line("15851", [1649, 1647, 1648])
        == "BRIDGE_WAKE thread=15851 turn=1647,1648,1649"
    )
    from cursor_bridge.chat_target import wake_lines

    tab = "21060be7-f0a6-4fa1-9058-b117ce4fc526"
    groups = [{"thread_id": "1", "turn": 1}, {"thread_id": "2", "turn": 2}]
    assert wake_lines(groups, tab=tab) == (
        f"BRIDGE_WAKE thread=1 turn=1 tab={tab}\nBRIDGE_WAKE thread=2 turn=2"
    )


def test_stale_wake_draft_multi_turn() -> None:
    stale = "BRIDGE_WAKE thread=15851 turn=1647,1648"
    assert _is_stale_wake_draft(stale)
    assert _busy_or_draft_reason({"composer_draft": stale}) is None


@pytest.mark.asyncio
async def test_deliver_wake_free_text_gate_unchanged() -> None:
    out = await deliver_wake(
        display="8",
        app="cursor",
        thread="22517",
        title="t",
        turn=1,
        text="x",
    )
    assert out["reason"] == "exchange_paste_cursor_stage_manager_only"
    bad = await deliver_wake(
        display="8",
        app="cursor",
        thread="1\n2",
        title="t",
        turn=1,
        wake_groups=[{"thread_id": "1\n2", "turn": 1}],
    )
    assert bad["reason"] == "bad_options"
    both = await deliver_wake(
        display="8",
        app="cursor",
        thread="22517",
        title="t",
        turn=1,
        text="x",
        wake_groups=[{"thread_id": "22517", "turn": 1}],
    )
    assert both["reason"] == "bad_options"


def test_stale_wake_predicate_shared_by_all_sites() -> None:
    from cursor_bridge import dom_composer_paste as dcp
    from cursor_bridge import glass_services_js as gsj
    from cursor_bridge import wake_delivery as wd
    from cursor_bridge.chat_target import STALE_WAKE_LINES_JS

    assert STALE_WAKE_LINES_JS in wd._COMPOSER_JS["grok"]
    assert STALE_WAKE_LINES_JS in wd._send_fallback_js("BRIDGE_WAKE thread=1 turn=1")
    js = dcp.operator_busy_probe_js(selector='[aria-label="Prompt"][contenteditable="true"]')
    assert STALE_WAKE_LINES_JS in js
    insert = dcp._set_composer_body('[aria-label="Prompt"]', "line")
    assert STALE_WAKE_LINES_JS in insert
    assert STALE_WAKE_LINES_JS in gsj.launch_new_agent_tab_js("BRIDGE_WAKE thread=1 turn=1", "")
    click = dcp._click_send_body('[aria-label="Prompt"]', "line")
    assert STALE_WAKE_LINES_JS in click and "wakeStale=(t)=>/^" not in click


def test_wake_lines_multi_thread_stale_draft_detected() -> None:
    from cursor_bridge.chat_target import wake_lines

    stale = wake_lines(
        [
            {"thread_id": "100", "turn": 1},
            {"thread_id": "200", "turn": 2},
        ]
    )
    assert _is_stale_wake_draft(stale)
    assert _busy_or_draft_reason({"composer_draft": stale}) is None
    assert _busy_or_draft_reason({"composer_draft": stale + "\nextra line"}) == (
        "composer_not_empty"
    )


def test_grok_composer_js_clears_stale_wake_draft() -> None:
    js = wd._COMPOSER_JS["grok"]
    assert "execCommand('delete',false,null)" in js
    assert "el.innerText=''" in js
    assert "composer_not_empty" in js
    assert "BRIDGE_WAKE" in js


def test_click_send_body_wake_stale_accepts_multi_line_wake_draft() -> None:
    import shutil
    import subprocess

    from cursor_bridge.dom_composer_paste import _click_send_body

    if shutil.which("node") is None:
        pytest.skip("node not installed")
    body = _click_send_body('[aria-label="Prompt"]', "BRIDGE_WAKE thread=3 turn=3")
    fn = re.search(r"const wakeStale=\(t\)=>(.+?);const composerEmpty=", body).group(1)
    w2, hu = "'BRIDGE_WAKE thread=1 turn=1\\nBRIDGE_WAKE thread=2 turn=2'", "'BRIDGE_WAKE thread=1 turn=1\\nhello'"
    js = f"const w=(t)=>{fn};process.stdout.write(JSON.stringify([w({w2}),w({hu})]));"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20, check=False)
    assert (out.returncode, out.stdout) == (0, "[true,false]"), out.stderr


def _cdp_msg(mid: int, value: Any) -> str:
    return json.dumps({"id": mid, "result": {"result": {"value": value}}})


class _FakeWS:
    def __init__(self, inbox: list[str]) -> None:
        self._inbox = inbox
        self.sent: list[dict[str, Any]] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self) -> str:
        if not self._inbox:
            raise RuntimeError("no more fake CDP messages")
        return self._inbox.pop(0)

    async def __aenter__(self) -> _FakeWS:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_paste_send_button_fallback_when_enter_leaves_composer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line = "BRIDGE_WAKE thread=15851 turn=290"
    dom = AsyncMock(return_value={"ok": True, "send_fallback_clicked": True})
    monkeypatch.setattr(wd, "dom_insert_and_send", dom)

    out = await _paste("ws://fake", wd._COMPOSER_JS["grok"], line)

    assert out == {"ok": True, "send_fallback_clicked": True}
    dom.assert_awaited_once()
    assert dom.await_args.kwargs["line"] == line
    assert "Input.insertText" not in str(dom.await_args)


@pytest.mark.asyncio
async def test_deliver_wake_pastes_over_stale_bridge_wake_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = "BRIDGE_WAKE thread=15851 turn=289"
    expected_title = "15851 slug"
    surface_match = classify_surface(
        [],
        [{"text": expected_title, "selected": True, "agent_id": ""}],
        composer_draft=stale,
    )
    surface_post = {**surface_match, "composer_draft": ""}

    async def _probe(_url: str) -> dict[str, Any]:
        if not hasattr(_probe, "n"):
            _probe.n = 0
        _probe.n += 1
        return surface_match if _probe.n == 1 else surface_post

    monkeypatch.setattr(
        wd,
        "list_pages",
        lambda _origin: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(wd, "probe_surface", _probe)
    dom_paste = AsyncMock(return_value={"ok": True, "send_fallback_clicked": True})
    monkeypatch.setattr(wd, "dom_insert_and_send", dom_paste)

    out = await deliver_wake(
        display="9",
        app="grok",
        thread="15851",
        slug="slug",
        turn=290,
    )

    assert out.get("reason") != "composer_not_empty"
    assert out.get("pasted") is True
    assert out.get("send_fallback_clicked") is True
    dom_paste.assert_awaited_once()


def test_cursor_probe_fixture_records_witness() -> None:
    payload = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    for key in (
        "pages",
        "owner",
        "probe_js_result",
        "composer_selector",
        "match_count",
        "prompt_selector_matches_same_node",
        "composer_attributes",
        "outer_html",
        "empty_inner_text",
    ):
        assert key in payload
    assert payload["match_count"] == 1
    assert isinstance(payload["prompt_selector_matches_same_node"], bool)
    assert payload["owner"]["process_name"] == "cursor"
    selector = payload["composer_selector"]
    cursor_js = wd._COMPOSER_JS["cursor"]
    assert "Prompt" in cursor_js and "contenteditable" in cursor_js
    qs_args = _query_selector_literal_args(cursor_js)
    assert len(qs_args) == 1, (
        "cursor composer JS must contain exactly one querySelector/querySelectorAll call; "
        f"got {qs_args!r}"
    )
    assert f"document.querySelector({json.dumps(selector)})" in cursor_js
    assert qs_args == [selector], (
        "cursor composer JS must use exactly one querySelector equal to fixture composer_selector; "
        f"got {qs_args!r}"
    )
    raw = _FIXTURE_PATH.read_text(encoding="utf-8")
    assert "webSocketDebuggerUrl" not in raw
    assert "ws://" not in raw
    probe = payload["probe_js_result"]
    assert "recent_messages" not in probe
    assert "labels" not in probe
    assert "items" not in probe
    for page in payload["pages"]:
        assert set(page.keys()) == {"id", "title"}


def test_composer_witness_match_count_and_same_node() -> None:
    expr = composer_witness_expression('[contenteditable="true"]')
    assert "querySelectorAll" in expr
    assert "Prompt" in expr and "contenteditable" in expr
    assert "Input." not in expr

    preamble = _witness_preamble_before_guard(expr)
    assert "attributes = {}" not in preamble
    assert "outer_html = el" not in preamble
    assert "resolved_selector = promptSel" not in preamble
    assert "resolved_selector = built" not in preamble

    guard_body = _match_count_guard_block(expr)
    assert "prompt_selector_matches_same_node = el === promptNode" in guard_body
    assert "attributes = {}" in guard_body
    assert "outer_html = el.outerHTML" in guard_body
    assert (
        "resolved_selector = promptSel" in guard_body
        or "resolved_selector = built" in guard_body
    )


def test_cursor_composer_js_clears_stale_wake_draft() -> None:
    js = wd._COMPOSER_JS["cursor"]
    assert "execCommand('delete',false,null)" in js
    assert "el.innerText=''" in js
    assert "composer_not_empty" in js
    assert "BRIDGE_WAKE" in js


@pytest.mark.asyncio
async def test_deliver_wake_cursor_pastes_on_agents_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = "BRIDGE_WAKE thread=15851 turn=289"
    expected_title = "15851 slug"
    surface_match = classify_surface(
        [],
        [{"text": expected_title, "selected": True, "agent_id": ""}],
        composer_draft=stale,
    )
    surface_post = {**surface_match, "composer_draft": ""}

    async def _probe(_url: str) -> dict[str, Any]:
        if not hasattr(_probe, "n"):
            _probe.n = 0
        _probe.n += 1
        return surface_match if _probe.n == 1 else surface_post

    monkeypatch.setattr(
        wd,
        "list_pages",
        lambda _origin: [
            {"title": "Cursor Agents", "web_socket_debugger_url": "ws://x"}
        ],
    )
    monkeypatch.setattr(wd, "probe_surface", _probe)
    monkeypatch.setattr(
        wd,
        "_paste_cursor_services",
        AsyncMock(return_value={"ok": True, "send_fallback_clicked": False}),
    )

    out = await deliver_wake(
        display="8",
        app="cursor",
        thread="15851",
        slug="slug",
        turn=290,
    )

    assert out.get("reason") != "composer_not_empty"
    assert out.get("pasted") is True
    wd._paste_cursor_services.assert_awaited_once()


def test_normalize_target_orion_node_cursor() -> None:
    out = normalize_target("orion-node", "cursor")
    assert out["ok"] is True
    assert out["origin"] == "http://127.0.0.1:19338"


def test_normalize_target_bridge_client_rewrites_forwarded_cdp_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cursor_bridge.cdp_gateway_forwarder.local_ipv4_addresses",
        lambda: frozenset({"172.18.0.5"}),
    )
    assert normalize_target("8", "cursor")["origin"] == "http://172.18.0.1:9338"
    assert normalize_target(":9", "grok-bot")["origin"] == "http://172.18.0.1:9339"
    assert (
        normalize_target("orion-node", "cursor")["origin"] == "http://172.18.0.1:19338"
    )
    assert normalize_target("10", "grok")["origin"] == "http://172.18.0.1:9340"
    assert normalize_target("99", "cursor")["reason"] == "unknown_target"


def test_normalize_target_host_gateway_stays_on_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cursor_bridge.cdp_gateway_forwarder.local_ipv4_addresses",
        lambda: frozenset({"127.0.0.1", "172.18.0.1", "10.0.0.67"}),
    )
    assert normalize_target("8", "cursor")["origin"] == "http://127.0.0.1:9338"
    assert normalize_target("9", "grok")["origin"] == "http://127.0.0.1:9339"


def test_submit_wake_line_js_no_input_events() -> None:
    js = submit_wake_line_js("BRIDGE_WAKE thread=1 turn=2", "agent-1")
    assert "setContent" not in js
    assert "submitChatMaybeAbortCurrent" in js
    assert "appendQueuedHumanMessage" not in js
    assert "Input.insertText" not in js
    assert "Input.dispatchKeyEvent" not in js


def test_submit_wake_line_js_resolves_workspace_scoped_services() -> None:
    js = submit_wake_line_js("BRIDGE_WAKE thread=1 turn=2", "agent-1")
    assert "resolveWorkspaceServices" in js
    assert "resolveComposerForAgent" in js
    assert "getAgentHeader" in js
    assert "workspaceCollectionService" in js
    assert "getWorkspaceInstance" in js


def test_submit_wake_line_js_race_reports_winner_not_timer_as_delivered() -> None:
    js = submit_wake_line_js("BRIDGE_WAKE thread=1 turn=2", "agent-1")
    assert "race_winner: raceWinner" in js
    assert "raceWinner !== 'timer'" in js
    assert "delivery_unconfirmed" in js


@pytest.mark.asyncio
async def test_paste_cursor_services_evaluates_glass_js(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line = "BRIDGE_WAKE thread=15851 turn=290"
    inbox = [_cdp_msg(1, {"ok": True, "agent_id": "agent-1"})]
    fake = _FakeWS(inbox)

    def _connect(_url: str, **_kw: Any) -> _FakeWS:
        return fake

    monkeypatch.setattr("websockets.connect", _connect)
    out = await _paste_cursor_services("ws://fake", line, agent_id="agent-1")
    assert out == {"ok": True, "send_fallback_clicked": False}
    expr = fake.sent[0]["params"]["expression"]
    assert "submitChatMaybeAbortCurrent" in expr
    assert "Input.insertText" not in expr


def test_cursor_stale_draft_same_treatment_as_grok() -> None:
    stale = "BRIDGE_WAKE thread=15851 turn=289"
    assert _busy_or_draft_reason({"composer_draft": stale}) is None
    assert (
        _busy_or_draft_reason({"composer_draft": "please hold"}) == "composer_not_empty"
    )


@pytest.mark.asyncio
async def test_cursor_human_draft_blocks_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    human = "operator draft text"
    surface = classify_surface(
        [],
        [
            {"text": "other chat", "selected": True, "agent_id": ""},
            {"text": "15851 slug", "selected": False, "agent_id": ""},
        ],
        composer_draft=human,
    )
    select_mock = AsyncMock(return_value={"clicked": True})

    monkeypatch.setattr(
        wd,
        "list_pages",
        lambda _origin: [
            {"title": "Cursor Agents", "web_socket_debugger_url": "ws://x"}
        ],
    )
    monkeypatch.setattr(wd, "probe_surface", AsyncMock(return_value=surface))
    monkeypatch.setattr(wd, "select_sidebar_target", select_mock)

    out = await deliver_wake(
        display="8",
        app="cursor",
        thread="15851",
        slug="slug",
        turn=290,
    )

    assert out.get("reason") == "composer_not_empty"
    select_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_composer_empty_probe_notifies_wake_redrive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = classify_surface(
        [],
        [
            {
                "text": "15851 slug",
                "selected": True,
                "agent_id": "agent-1",
            }
        ],
        composer_draft="",
    )
    notified: list[dict] = []

    def _notify(**kwargs: object) -> int:
        notified.append(dict(kwargs))
        return 1

    monkeypatch.setattr(
        "bus_watch.endpoint_wake_composer_defer.notify_composer_empty_for_target",
        _notify,
    )
    monkeypatch.setattr(
        wd,
        "list_pages",
        lambda _origin: [
            {"title": "Cursor Agents", "web_socket_debugger_url": "ws://x"}
        ],
    )
    monkeypatch.setattr(wd, "probe_surface", AsyncMock(return_value=surface))
    monkeypatch.setattr(
        wd,
        "_paste_cursor_services",
        AsyncMock(return_value={"ok": True, "delivered": True}),
    )

    out = await deliver_wake(
        display="8",
        app="cursor",
        thread="15851",
        slug="slug",
        agent_id="agent-1",
        turn=290,
    )
    assert out.get("delivered") is True
    assert len(notified) == 1
    assert notified[0]["thread_scope"] == "15851"


@pytest.mark.asyncio
async def test_deliver_wake_exchange_text_refuses_non_stage_manager_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break: exchange paste must not open CDP for threads other than 19627."""
    monkeypatch.setattr(
        wd,
        "list_pages",
        lambda _origin: (_ for _ in ()).throw(
            AssertionError("must not open debug socket")
        ),
    )
    out = await deliver_wake(
        display="8",
        app="cursor",
        thread="1",
        slug="slug",
        turn=1,
        text="hello",
    )
    assert out["reason"] == "exchange_paste_cursor_stage_manager_only"
    assert out["pasted"] is False


@pytest.mark.asyncio
async def test_deliver_wake_exchange_text_refuses_grok_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = await deliver_wake(
        display="10",
        app="grok",
        thread="19627",
        title="Grok Coordinator 1",
        turn=1,
        text="hello",
    )
    assert out["reason"] == "exchange_paste_cursor_stage_manager_only"
    assert out["pasted"] is False


@pytest.mark.asyncio
async def test_deliver_wake_timer_win_classifies_unknown_not_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timer win stays pasted and classifies unknown, not refused.

    Break: Glass returns ``race_winner`` timer (``ok`` false) after the
    500ms submit race. A later probe that sees a cleared draft must not
    mark the wake delivered, and ``classify_display`` must not record
    refused.
    """
    from bus_watch.endpoint_wake_transport import classify_display

    stale = "BRIDGE_WAKE thread=15851 turn=289"
    expected_title = "15851 slug"
    surface_match = classify_surface(
        [],
        [{"text": expected_title, "selected": True, "agent_id": ""}],
        composer_draft=stale,
    )
    surface_cleared = {
        **surface_match,
        "composer_draft": "",
        "recent_messages": ["BRIDGE_WAKE thread=15851 turn=290"],
    }

    async def _probe(_url: str) -> dict[str, Any]:
        if not hasattr(_probe, "n"):
            _probe.n = 0
        _probe.n += 1
        return surface_match if _probe.n == 1 else surface_cleared

    inbox = [
        _cdp_msg(
            1,
            {
                "ok": False,
                "delivered": False,
                "race_winner": "timer",
                "reason": "delivery_unconfirmed",
                "agent_id": "agent-1",
            },
        )
    ]
    fake = _FakeWS(inbox)

    def _connect(_url: str, **_kw: Any) -> _FakeWS:
        return fake

    monkeypatch.setattr("websockets.connect", _connect)
    monkeypatch.setattr(
        wd,
        "list_pages",
        lambda _origin: [
            {"title": "Cursor Agents", "web_socket_debugger_url": "ws://x"}
        ],
    )
    monkeypatch.setattr(wd, "probe_surface", _probe)
    monkeypatch.setattr(wd.asyncio, "sleep", AsyncMock())

    out = await deliver_wake(
        display="8",
        app="cursor",
        thread="15851",
        slug="slug",
        turn=290,
    )

    assert out["pasted"] is True
    assert out["delivered"] is False
    assert out["race_winner"] == "timer"
    assert out["ok"] is False
    kind, _why = classify_display(200, out, None)
    assert kind == "unknown"


def _stale_match_surface(expected_title: str) -> dict[str, Any]:
    stale = "BRIDGE_WAKE thread=15851 turn=289"
    return classify_surface(
        [],
        [{"text": expected_title, "selected": True, "agent_id": ""}],
        composer_draft=stale,
    )


def _wake_list_pages(app: str) -> list[dict[str, str]]:
    title = "Cursor Agents" if app == "cursor" else "Grok Bot"
    return [{"title": title, "web_socket_debugger_url": "ws://x"}]


@pytest.mark.asyncio
async def test_deliver_wake_paste_runs_under_display_switch_lock_grok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break: operator paste could interleave if paste ran outside the switch lock."""
    expected_title = "15851 slug"
    surface_match = _stale_match_surface(expected_title)
    surface_post = {**surface_match, "composer_draft": ""}
    lock_depth_at_paste: list[int] = []

    async def _probe(_url: str) -> dict[str, Any]:
        if not hasattr(_probe, "n"):
            _probe.n = 0
        _probe.n += 1
        return surface_match if _probe.n == 1 else surface_post

    async def _dom_paste(*_args: object, **_kwargs: object) -> dict[str, Any]:
        lock_depth_at_paste.append(_DISPLAY_SWITCH_DEPTH.get())
        return {"ok": True, "send_fallback_clicked": False}

    monkeypatch.setattr(wd, "list_pages", lambda _o: _wake_list_pages("grok"))
    monkeypatch.setattr(wd, "probe_surface", _probe)
    monkeypatch.setattr(wd, "dom_insert_and_send", _dom_paste)

    out = await deliver_wake(
        display="9",
        app="grok",
        thread="15851",
        slug="slug",
        turn=290,
    )

    assert out.get("pasted") is True
    assert lock_depth_at_paste == [1]


@pytest.mark.asyncio
async def test_deliver_wake_grok_two_thread_paste_keeps_newline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    match = _stale_match_surface("15851 slug")
    probes = iter([match])
    dom = AsyncMock(return_value={"ok": True, "send_fallback_clicked": False})
    monkeypatch.setattr(wd, "list_pages", lambda _o: _wake_list_pages("grok"))
    monkeypatch.setattr(
        wd, "probe_surface", AsyncMock(side_effect=lambda _u: next(probes, {**match, "composer_draft": ""}))
    )
    monkeypatch.setattr(wd, "dom_insert_and_send", dom)
    groups = [{"thread_id": "15851", "turn": 290}, {"thread_id": "15852", "turn": 3}]
    out = await deliver_wake(
        display="9", app="grok", thread="15851", slug="slug", turn=290, wake_groups=groups
    )
    kw = dom.await_args.kwargs
    assert out.get("pasted") is True and kw["line"] == wake_line("15851", 290) + "\n" + wake_line("15852", 3)
    assert kw.get("preserve_newlines") is True


@pytest.mark.asyncio
async def test_deliver_wake_paste_runs_under_display_switch_lock_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_title = "15851 slug"
    surface_match = _stale_match_surface(expected_title)
    surface_post = {**surface_match, "composer_draft": ""}
    lock_depth_at_paste: list[int] = []

    async def _probe(_url: str) -> dict[str, Any]:
        if not hasattr(_probe, "n"):
            _probe.n = 0
        _probe.n += 1
        return surface_match if _probe.n == 1 else surface_post

    async def _cursor_paste(*_args: object, **_kwargs: object) -> dict[str, Any]:
        lock_depth_at_paste.append(_DISPLAY_SWITCH_DEPTH.get())
        return {"ok": True, "send_fallback_clicked": False}

    monkeypatch.setattr(wd, "list_pages", lambda _o: _wake_list_pages("cursor"))
    monkeypatch.setattr(wd, "probe_surface", _probe)
    monkeypatch.setattr(wd, "_paste_cursor_services", _cursor_paste)

    out = await deliver_wake(
        display="8",
        app="cursor",
        thread="15851",
        slug="slug",
        turn=290,
    )

    assert out.get("pasted") is True
    assert lock_depth_at_paste == [1]


@pytest.mark.asyncio
async def test_deliver_wake_waits_on_display_switch_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break: concurrent display switch must delay wake paste until lock frees."""
    expected_title = "15851 slug"
    surface_match = _stale_match_surface(expected_title)
    surface_post = {**surface_match, "composer_draft": ""}
    holder_ready = asyncio.Event()
    release_holder = asyncio.Event()
    paste_started = asyncio.Event()

    async def _probe(_url: str) -> dict[str, Any]:
        if not hasattr(_probe, "n"):
            _probe.n = 0
        _probe.n += 1
        return surface_match if _probe.n == 1 else surface_post

    async def _dom_paste(*_args: object, **_kwargs: object) -> dict[str, Any]:
        paste_started.set()
        return {"ok": True, "send_fallback_clicked": False}

    async def _holder() -> None:
        async with display_switch_lock("9"):
            holder_ready.set()
            await release_holder.wait()

    monkeypatch.setattr(wd, "list_pages", lambda _o: _wake_list_pages("grok"))
    monkeypatch.setattr(wd, "probe_surface", _probe)
    monkeypatch.setattr(wd, "dom_insert_and_send", _dom_paste)

    holder = asyncio.create_task(_holder())
    await holder_ready.wait()
    wake_task = asyncio.create_task(
        deliver_wake(
            display="9",
            app="grok",
            thread="15851",
            slug="slug",
            turn=290,
        )
    )
    await asyncio.sleep(0.05)
    assert not paste_started.is_set()
    release_holder.set()
    out = await wake_task
    await holder
    assert paste_started.is_set()
    assert out.get("pasted") is True


@pytest.mark.asyncio
async def test_deliver_wake_paste_timeout_releases_display_switch_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break: paste failure must not strand the per-display switch lock."""
    expected_title = "15851 slug"
    surface_match = _stale_match_surface(expected_title)

    async def _probe(_url: str) -> dict[str, Any]:
        return surface_match

    async def _dom_paste(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise TimeoutError("paste hung")

    monkeypatch.setattr(wd, "list_pages", lambda _o: _wake_list_pages("grok"))
    monkeypatch.setattr(wd, "probe_surface", _probe)
    monkeypatch.setattr(wd, "dom_insert_and_send", _dom_paste)

    out = await deliver_wake(
        display="9",
        app="grok",
        thread="15851",
        slug="slug",
        turn=290,
    )

    assert out.get("reason") == "paste_failed"
    assert out.get("detail") == "TimeoutError"
    async with display_switch_lock("9"):
        pass


def _sidebar(rows: list[tuple[str, bool]], *, draft: str = "") -> dict[str, Any]:
    return classify_surface(
        [],
        [
            {"text": text, "selected": selected, "agent_id": ""}
            for text, selected in rows
        ],
        composer_draft=draft,
    )


async def _flip_while_switch_lock_held(
    display: str,
    phase: dict[str, Any],
    after: dict[str, Any],
    *,
    holder_ready: asyncio.Event,
    prelock_seen: asyncio.Event,
) -> None:
    async with display_switch_lock(display):
        holder_ready.set()
        await prelock_seen.wait()
        phase["surface"] = after


@pytest.mark.asyncio
async def test_deliver_wake_refuses_draft_typed_while_waiting_on_switch_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break: a draft typed while the wake waits on the switch lock is pasted over.

    The target is already selected and the pre-lock probe shows an empty
    composer. The surface gains a human draft before the lock is acquired.
    """
    expected = "15851 slug"
    phase: dict[str, Any] = {
        "surface": _sidebar([(expected, True)], draft=""),
    }
    holder_ready = asyncio.Event()
    prelock_seen = asyncio.Event()
    paste = AsyncMock(return_value={"ok": True, "send_fallback_clicked": False})

    async def _probe(_url: str) -> dict[str, Any]:
        prelock_seen.set()
        return phase["surface"]

    monkeypatch.setattr(wd, "list_pages", lambda _o: _wake_list_pages("grok"))
    monkeypatch.setattr(wd, "probe_surface", _probe)
    monkeypatch.setattr(wd, "dom_insert_and_send", paste)

    holder = asyncio.create_task(
        _flip_while_switch_lock_held(
            "9",
            phase,
            _sidebar([(expected, True)], draft="operator draft text"),
            holder_ready=holder_ready,
            prelock_seen=prelock_seen,
        )
    )
    await holder_ready.wait()
    out = await asyncio.wait_for(
        deliver_wake(
            display="9",
            app="grok",
            thread="15851",
            slug="slug",
            turn=290,
        ),
        timeout=2,
    )
    await asyncio.wait_for(holder, timeout=2)

    assert out.get("reason") == "composer_not_empty"
    assert out.get("pasted") is False
    assert out.get("delivered") is False
    paste.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("after_rows", "after_draft", "reason"),
    [
        (
            [("other chat", True), ("15851 slug", False)],
            "operator draft text",
            "composer_not_empty",
        ),
        (
            [("operator moved here", True), ("15851 slug", False)],
            "",
            "title_mismatch",
        ),
    ],
)
async def test_deliver_wake_select_path_post_lock_change_does_not_click(
    monkeypatch: pytest.MonkeyPatch,
    after_rows: list[tuple[str, bool]],
    after_draft: str,
    reason: str,
) -> None:
    """Break: a select decided before the lock still clicks after the surface changes.

    Pre-lock state is chat_list with the target unselected and an empty
    composer. While the wake waits, the surface becomes a human draft or a
    different active chat. Neither case may click or paste.
    """
    phase: dict[str, Any] = {
        "surface": _sidebar(
            [("other chat", True), ("15851 slug", False)],
            draft="",
        ),
    }
    holder_ready = asyncio.Event()
    prelock_seen = asyncio.Event()
    select_mock = AsyncMock(return_value={"clicked": True})
    paste = AsyncMock(return_value={"ok": True, "send_fallback_clicked": False})

    async def _probe(_url: str) -> dict[str, Any]:
        prelock_seen.set()
        return phase["surface"]

    monkeypatch.setattr(wd, "list_pages", lambda _o: _wake_list_pages("grok"))
    monkeypatch.setattr(wd, "probe_surface", _probe)
    monkeypatch.setattr(wd, "select_sidebar_target", select_mock)
    monkeypatch.setattr(wd, "dom_insert_and_send", paste)

    holder = asyncio.create_task(
        _flip_while_switch_lock_held(
            "9",
            phase,
            _sidebar(after_rows, draft=after_draft),
            holder_ready=holder_ready,
            prelock_seen=prelock_seen,
        )
    )
    await holder_ready.wait()
    out = await asyncio.wait_for(
        deliver_wake(
            display="9",
            app="grok",
            thread="15851",
            slug="slug",
            turn=290,
        ),
        timeout=2,
    )
    await asyncio.wait_for(holder, timeout=2)

    assert select_mock.await_count == 0
    assert paste.await_count == 0
    assert out.get("pasted") is False
    assert out.get("delivered") is False
    assert out.get("reason") == reason
