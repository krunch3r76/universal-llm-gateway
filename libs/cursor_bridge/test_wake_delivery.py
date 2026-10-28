"""Offline tests for wake_delivery stale-draft guard and send fallback."""

from __future__ import annotations

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
from cursor_bridge.glass_services_js import submit_wake_line_js
from cursor_bridge.wake_delivery import (
    _busy_or_draft_reason,
    _is_stale_wake_draft,
    _paste,
    _paste_cursor_services,
    deliver_wake,
    normalize_target,
)

_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "cursor_agents_9338_probe.json"

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
    assert wake_line("15851", "1647,1648,1649") == "BRIDGE_WAKE thread=15851 turn=1647,1648,1649"
    assert wake_line("15851", [1649, 1647, 1648]) == "BRIDGE_WAKE thread=15851 turn=1647,1648,1649"


def test_stale_wake_draft_multi_turn() -> None:
    stale = "BRIDGE_WAKE thread=15851 turn=1647,1648"
    assert _is_stale_wake_draft(stale)
    assert _busy_or_draft_reason({"composer_draft": stale}) is None


def test_grok_composer_js_clears_stale_wake_draft() -> None:
    js = wd._COMPOSER_JS["grok"]
    assert "execCommand('delete',false,null)" in js
    assert "el.innerText=''" in js
    assert "composer_not_empty" in js
    assert "BRIDGE_WAKE" in js


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
async def test_paste_send_button_fallback_when_enter_leaves_composer(monkeypatch: pytest.MonkeyPatch) -> None:
    line = "BRIDGE_WAKE thread=15851 turn=290"
    inbox = [
        _cdp_msg(1, {"ok": True}),
        json.dumps({"id": 2, "result": {}}),
        json.dumps({"id": 3, "result": {}}),
        json.dumps({"id": 4, "result": {}}),
        _cdp_msg(5, {"ok": True, "send_fallback_clicked": True}),
    ]
    fake = _FakeWS(inbox)

    def _connect(_url: str, **_kw: Any) -> _FakeWS:
        return fake

    monkeypatch.setattr("websockets.connect", _connect)
    monkeypatch.setattr(wd.asyncio, "sleep", AsyncMock())

    out = await _paste("ws://fake", wd._COMPOSER_JS["grok"], line)

    assert out == {"ok": True, "send_fallback_clicked": True}
    fallback_call = next(m for m in fake.sent if m.get("id") == 5)
    expr = fallback_call["params"]["expression"]
    assert "Send message" in expr
    assert "send_fallback_clicked" in expr
    assert line in expr


@pytest.mark.asyncio
async def test_deliver_wake_pastes_over_stale_bridge_wake_draft(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(wd, "list_pages", lambda _origin: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}])
    monkeypatch.setattr(wd, "probe_surface", _probe)
    monkeypatch.setattr(
        wd,
        "_paste",
        AsyncMock(return_value={"ok": True, "send_fallback_clicked": True}),
    )

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
    wd._paste.assert_awaited_once()


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
    assert "resolved_selector = promptSel" in guard_body or "resolved_selector = built" in guard_body


def test_cursor_composer_js_clears_stale_wake_draft() -> None:
    js = wd._COMPOSER_JS["cursor"]
    assert "execCommand('delete',false,null)" in js
    assert "el.innerText=''" in js
    assert "composer_not_empty" in js
    assert "BRIDGE_WAKE" in js


@pytest.mark.asyncio
async def test_deliver_wake_cursor_pastes_on_agents_page(monkeypatch: pytest.MonkeyPatch) -> None:
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
        lambda _origin: [{"title": "Cursor Agents", "web_socket_debugger_url": "ws://x"}],
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


def test_normalize_target_bridge_client_rewrites_only_9338_and_9339(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cursor_bridge.cdp_gateway_forwarder.local_ipv4_addresses",
        lambda: frozenset({"172.18.0.5"}),
    )
    assert normalize_target("8", "cursor")["origin"] == "http://172.18.0.1:9338"
    assert normalize_target(":9", "grok-bot")["origin"] == "http://172.18.0.1:9339"
    assert normalize_target("orion-node", "cursor")["origin"] == "http://127.0.0.1:19338"
    assert normalize_target("10", "grok")["origin"] == "http://127.0.0.1:9340"
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
async def test_paste_cursor_services_evaluates_glass_js(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert _busy_or_draft_reason({"composer_draft": "please hold"}) == "composer_not_empty"


@pytest.mark.asyncio
async def test_cursor_human_draft_blocks_selection(monkeypatch: pytest.MonkeyPatch) -> None:
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
        lambda _origin: [{"title": "Cursor Agents", "web_socket_debugger_url": "ws://x"}],
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
async def test_deliver_wake_exchange_text_refuses_non_stage_manager_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break: exchange paste must not open CDP for threads other than 19627."""
    monkeypatch.setattr(
        wd,
        "list_pages",
        lambda _origin: (_ for _ in ()).throw(AssertionError("must not open debug socket")),
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
        lambda _origin: [{"title": "Cursor Agents", "web_socket_debugger_url": "ws://x"}],
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
