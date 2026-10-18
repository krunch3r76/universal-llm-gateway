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
from cursor_bridge.wake_delivery import (
    _busy_or_draft_reason,
    _is_stale_wake_draft,
    _paste,
    deliver_wake,
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
        "_paste",
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
    wd._paste.assert_awaited_once()
    assert wd._paste.await_args.args[1] == wd._COMPOSER_JS["cursor"]


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
