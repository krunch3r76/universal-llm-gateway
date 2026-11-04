"""Offline tests for operator paste wrap and grant gates."""

from __future__ import annotations

import subprocess
import sys
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cursor_bridge.operator_paste import (
    OPERATOR_HEADER,
    RELAY_NONCE_LABEL,
    WATERMARK,
    _assistant_began,
    _composer_cdp_fallback_action,
    _dom_verification_conclusive,
    _insert_composer_with_fallback,
    _nonce_in_sent_transcript,
    _post_click_exception_result,
    _readback_fields,
    _sha256_dom_from_surface,
    _submit_verified,
    _target_verified,
    deliver_operator_paste,
    extract_run_nonce_from_line,
    make_run_nonce,
    watermark_with_nonce,
    wrap_operator_message,
)

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _operator_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cursor_bridge.dom_composer_paste.install_operator_activity_probe",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.wait_for_operator_idle",
        AsyncMock(return_value={"ok": True, "idle": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.read_operator_busy",
        AsyncMock(return_value={"ok": True, "operator_active": False}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft",
        AsyncMock(return_value={"ok": True, "cleared": False}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_enter",
        AsyncMock(return_value={"ok": True, "method": "cdp_enter"}),
    )


def test_wrap_operator_message_includes_header_source_watermark() -> None:
    msg = wrap_operator_message(
        text="hello",
        authorization_source="agent-bus:21614#2",
        scope="falsifier",
    )
    assert "Operator request — Kaywan" in msg
    assert "Source: agent-bus:21614#2" in msg
    assert "Scope: falsifier" in msg
    assert WATERMARK in msg
    assert RELAY_NONCE_LABEL in msg
    assert "hello" in msg


def test_wrap_operator_message_idempotent_when_already_wrapped() -> None:
    once = wrap_operator_message(
        text="payload",
        authorization_source="agent-bus:21614#2",
        scope="s",
    )
    twice = wrap_operator_message(
        text=once,
        authorization_source="other",
        scope="other",
    )
    assert twice == once
    assert twice.count(OPERATOR_HEADER) == 1
    assert twice.count(WATERMARK) == 1
    assert twice.count(RELAY_NONCE_LABEL) == 1


def test_wrap_operator_message_rewraps_when_run_nonce_changes() -> None:
    """Break: retry after post-click fault must not keep a stale embedded run nonce."""
    first_nonce = "aaa111bbb222"
    once = wrap_operator_message(
        text="payload",
        authorization_source="agent-bus:21732#1",
        scope="paste",
        run_nonce=first_nonce,
    )
    twice = wrap_operator_message(
        text=once,
        authorization_source="agent-bus:21732#2",
        scope="paste-retry",
        run_nonce="ccc333ddd444",
    )
    assert twice.count(OPERATOR_HEADER) == 1
    assert twice.count(WATERMARK) == 1
    assert extract_run_nonce_from_line(twice) == "ccc333ddd444"
    assert "Source: agent-bus:21732#2" in twice
    assert "Scope: paste-retry" in twice
    assert "payload" in twice


def test_wrap_operator_message_same_nonce_leaves_body_unchanged() -> None:
    fixed = "fixednonce01"
    once = wrap_operator_message(
        text="body",
        authorization_source="agent-bus:21732#1",
        scope="s",
        run_nonce=fixed,
    )
    twice = wrap_operator_message(
        text=once,
        authorization_source="ignored",
        scope="ignored",
        run_nonce=fixed,
    )
    assert twice == once


def test_wrap_operator_message_no_nonce_leaves_wrapped_body_unchanged() -> None:
    once = wrap_operator_message(
        text="body",
        authorization_source="agent-bus:21732#1",
        scope="s",
        run_nonce="nonce1111111",
    )
    twice = wrap_operator_message(
        text=once,
        authorization_source="other",
        scope="other",
    )
    assert twice == once


@pytest.mark.parametrize(
    ("draft", "line", "action"),
    [
        ("", "full paste line", "insert"),
        ("full paste line", "full paste line", "skip"),
        ("full", "full paste line", "clear_then_insert"),
        ("someone else's draft", "full paste line", "refuse"),
    ],
)
def test_composer_cdp_fallback_action_four_states(
    draft: str, line: str, action: str
) -> None:
    """Break: DOM set failed but CDP must not append onto foreign or duplicate text."""
    assert _composer_cdp_fallback_action(draft, line) == action


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("draft", "expect_reason", "expect_method", "expect_cdp"),
    [
        ("", None, "cdp_insert", True),
        ("LINE", None, "cdp_skipped_full_draft", False),
        ("LI", None, "cdp_insert", True),
        ("foreign draft", "composer_not_empty", None, False),
    ],
)
async def test_insert_composer_with_fallback_cdp_guard(
    monkeypatch: pytest.MonkeyPatch,
    draft: str,
    expect_reason: str | None,
    expect_method: str | None,
    expect_cdp: bool,
) -> None:
    line = "LINE"
    dom = AsyncMock(return_value={"ok": False, "reason": "paste_failed"})
    read = AsyncMock(return_value={"ok": True, "draft": draft})
    cleared = AsyncMock(return_value={"ok": True, "cleared": True})
    cdp = AsyncMock(return_value={"ok": True, "method": "cdp_insert"})
    monkeypatch.setattr("cursor_bridge.operator_paste._insert_composer_text", dom)
    monkeypatch.setattr("cursor_bridge.operator_paste._read_composer_draft", read)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_composer_draft_dom_no_focus", cleared
    )
    monkeypatch.setattr("cursor_bridge.operator_paste.cdp_insert_text", cdp)

    out = await _insert_composer_with_fallback(
        "ws://fake", selector="#composer", line=line
    )

    if expect_reason:
        assert out.get("reason") == expect_reason
        assert out.get("ok") is False
        cdp.assert_not_awaited()
        return
    assert out.get("ok") is True
    assert out.get("insert_method") == expect_method
    if expect_cdp:
        cdp.assert_awaited_once()
    else:
        cdp.assert_not_awaited()
    if draft == "LI":
        cleared.assert_awaited_once()


def test_post_click_exception_result_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Break: post-click faults must surface exception class without re-raising for retry."""
    caplog.set_level("WARNING")
    out = _post_click_exception_result(RuntimeError("readback after click"))
    assert out["detail"] == "RuntimeError"
    assert any(
        r.levelname == "WARNING" and "RuntimeError" in r.message for r in caplog.records
    )


def _provenance_counts(text: str) -> tuple[int, int, int]:
    return (
        text.count(OPERATOR_HEADER),
        text.count(WATERMARK),
        text.count(RELAY_NONCE_LABEL),
    )


@pytest.mark.asyncio
async def test_deliver_operator_paste_single_header_footer_on_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break: pre-wrapped MCP/route body must not nest another header+footer."""
    pre_wrapped = wrap_operator_message(
        text="agent body",
        authorization_source="agent-bus:21614#2",
        scope="scope-a",
    )
    surface = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    surface_post = {
        **surface,
        "generating": True,
        "recent_messages": [pre_wrapped],
    }
    probe_n = {"n": 0}

    async def _probe(_ws: str) -> dict[str, Any]:
        probe_n["n"] += 1
        return surface if probe_n["n"] <= 2 else surface_post

    inserted: list[str] = []

    async def _insert(_ws: str, *, selector: str, line: str) -> dict[str, Any]:
        inserted.append(line)
        return {"ok": True}

    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface", AsyncMock(side_effect=_probe)
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._insert_composer_text", _insert)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button",
        AsyncMock(return_value={"ok": True, "send_fallback_clicked": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )

    await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text=pre_wrapped,
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="scope-a",
    )
    assert len(inserted) == 1
    flat = inserted[0]
    h, f, n = _provenance_counts(flat)
    assert h == 1
    assert f == 1
    assert n == 1


def test_watermark_imported_from_interlocutor_package() -> None:
    from interlocutor import watermark as wm

    assert WATERMARK is wm.WATERMARK
    assert "Orion" in WATERMARK


@pytest.mark.asyncio
async def test_deliver_operator_paste_refuses_without_grant() -> None:
    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="abc",
        text="hi",
        grant="",
        authorization_source="agent-bus:1#1",
    )
    assert out["reason"] == "grant_or_source_required"
    assert out["pasted"] is False


@pytest.mark.asyncio
async def test_deliver_operator_paste_refuses_cursor_ide() -> None:
    out = await deliver_operator_paste(
        display="8",
        app="cursor",
        agent_id="abc",
        text="hi",
        grant="operator",
        authorization_source="agent-bus:1#1",
    )
    assert out["reason"] == "cursor_ide_seat_forbidden"


@pytest.mark.asyncio
async def test_deliver_operator_paste_grok_ok_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_nonce = "a1b2c3d4e5f6"
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.make_run_nonce", lambda: fixed_nonce
    )
    wm_sent = watermark_with_nonce(fixed_nonce)
    surface_pre = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    surface_post = {
        **surface_pre,
        "generating": True,
        "recent_messages": [wm_sent],
        "message_turns": [{"role": "user", "text": wm_sent}],
    }

    async def _probe(_ws: str) -> dict[str, Any]:
        return surface_post if _probe.calls else surface_pre  # type: ignore[attr-defined]

    _probe.calls = 0  # type: ignore[attr-defined]

    async def _probe_counted(ws: str) -> dict[str, Any]:
        _probe.calls += 1  # type: ignore[attr-defined]
        if _probe.calls <= 1:  # type: ignore[attr-defined]
            return surface_pre
        return surface_post

    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(side_effect=_probe_counted),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button",
        AsyncMock(return_value={"ok": True, "send_fallback_clicked": True}),
    )
    screenshot = AsyncMock(
        return_value={"sha256_png": "p" * 64, "screenshot_path": "/tmp/x.png"}
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact", screenshot
    )

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert out["ok"] is True
    assert out["readback_ok"] is True
    assert out["submitted"] is True
    assert out["target_verified"] is True
    assert out["verify_method"] == "dom"
    assert out["sha256_png"] is None
    assert out["screenshot_path"] is None
    screenshot.assert_not_called()
    assert out["sha256_dom"] == _sha256_dom_from_surface(surface_post)
    assert out["locate_method"] == "sidebar"


def test_assistant_began_false_when_only_user_watermark_lands() -> None:
    pre = ["prior turn prose"]
    post = {
        "generating": False,
        "recent_messages": ["prior turn prose", f"relay {WATERMARK}"],
        "composer_draft": "",
    }
    assert _assistant_began(post, watermark=WATERMARK, pre_messages=pre) is False


def test_assistant_began_after_watermark_in_rolling_six_message_window() -> None:
    """Fails len-only check: tail stays length 6 while assistant prose appears after wm."""
    pre = [f"older-{i}" for i in range(6)]
    post = pre[:4] + [f"user {WATERMARK}", "assistant reply body"]
    surface = {
        "generating": False,
        "recent_messages": post,
        "composer_draft": "",
    }
    assert _assistant_began(surface, watermark=WATERMARK, pre_messages=pre) is True
    assert len(post) == len(pre)


def test_draft_with_nonce_in_composer_does_not_count_delivered() -> None:
    nonce = "deadbeefcafe"
    wm = watermark_with_nonce(nonce)
    post = {
        "composer_draft": wm,
        "generating": False,
        "recent_messages": [],
        "message_turns": [],
    }
    assert _nonce_in_sent_transcript(post, run_nonce=nonce) is False
    fields = _readback_fields(post, watermark=wm, pre_messages=[], run_nonce=nonce)
    assert fields["watermark_visible"] is False
    assert fields["readback_ok"] is False


def test_stale_nonce_in_transcript_does_not_count() -> None:
    old_nonce = "111111111111"
    new_nonce = "222222222222"
    old_wm = watermark_with_nonce(old_nonce)
    post = {
        "composer_draft": "",
        "generating": True,
        "message_turns": [{"role": "user", "text": old_wm}],
        "recent_messages": [old_wm],
    }
    assert _nonce_in_sent_transcript(post, run_nonce=new_nonce) is False
    fields = _readback_fields(
        post,
        watermark=watermark_with_nonce(new_nonce),
        pre_messages=[],
        run_nonce=new_nonce,
    )
    assert fields["watermark_visible"] is False
    assert fields["readback_ok"] is False


def test_happy_path_nonce_in_transcript_counts() -> None:
    nonce = make_run_nonce()
    wm = watermark_with_nonce(nonce)
    post = {
        "composer_draft": "",
        "generating": True,
        "message_turns": [
            {"role": "user", "text": wm},
            {"role": "assistant", "text": "ack"},
        ],
        "recent_messages": [wm, "ack"],
    }
    assert _submit_verified(post, watermark=wm, body_snippet="", run_nonce=nonce)
    fields = _readback_fields(post, watermark=wm, pre_messages=[], run_nonce=nonce)
    assert fields["readback_ok"] is True


def test_assistant_began_from_grok_message_turns_fixture() -> None:
    import json
    from pathlib import Path

    fixture = json.loads(
        (
            Path(__file__).parent / "fixtures" / "grok_operator_chat_turns.json"
        ).read_text(encoding="utf-8")
    )
    pre = ["prior assistant prose"]
    assert (
        _assistant_began(
            fixture,
            watermark=WATERMARK,
            pre_messages=pre,
            body_snippet="Falsifier F1",
        )
        is True
    )
    fields = _readback_fields(
        fixture,
        watermark=WATERMARK,
        pre_messages=pre,
        body_snippet="Falsifier F1",
    )
    assert fields["watermark_visible"] is True
    assert fields["assistant_began"] is True
    assert fields["readback_ok"] is True


@pytest.mark.asyncio
async def test_deliver_operator_paste_polls_until_assistant_generating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single early probe would miss streaming; poll until generating flips true."""
    fixed_nonce = "feedface1234"
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.make_run_nonce", lambda: fixed_nonce
    )
    wm = watermark_with_nonce(fixed_nonce)
    surface_pre = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": ["prior turn prose"],
    }
    wm_only = {
        **surface_pre,
        "composer_draft": "",
        "generating": False,
        "recent_messages": ["prior turn prose", wm],
        "message_turns": [{"role": "user", "text": wm}],
    }
    streaming = {**wm_only, "generating": True}

    seq = [surface_pre, wm_only, wm_only, streaming]
    idx = {"n": 0}

    async def _probe_counted(_ws: str) -> dict[str, Any]:
        i = min(idx["n"], len(seq) - 1)
        idx["n"] += 1
        return seq[i]

    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(side_effect=_probe_counted),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button",
        AsyncMock(return_value={"ok": True, "send_fallback_clicked": True}),
    )
    screenshot = AsyncMock(
        return_value={"sha256_png": "p" * 64, "screenshot_path": "/tmp/x.png"}
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact", screenshot
    )
    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert idx["n"] >= 3
    assert out["watermark_visible"] is True
    assert out["composer_empty_after"] is True
    assert out["assistant_began"] is True
    assert out["ok"] is True


def _hidden_chat_surface(*, active_agent_id: str, ok: bool = True) -> dict[str, Any]:
    return {
        "state": "chat_list",
        "active_title": "Hidden Bot",
        "active_agent_id": active_agent_id,
        "titles": ["Hidden Bot"] if ok else [],
        "title_counts": {"Hidden Bot": 1},
        "agent_ids": {active_agent_id: "Hidden Bot"} if ok else {},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }


@pytest.mark.asyncio
async def test_deliver_operator_paste_hidden_via_cmd_k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_nonce = "cmdk00000001"
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.make_run_nonce", lambda: fixed_nonce
    )
    wm = watermark_with_nonce(fixed_nonce)
    hidden_id = "hidden-agent-cmdk"
    pre = _hidden_chat_surface(active_agent_id="", ok=False)
    pre["active_title"] = ""
    post = {
        **_hidden_chat_surface(active_agent_id=hidden_id),
        "generating": True,
        "recent_messages": [wm],
        "message_turns": [{"role": "user", "text": wm}],
    }
    probes = [pre, _hidden_chat_surface(active_agent_id=hidden_id), post]

    async def _probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else post

    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.locate_off_sidebar",
        AsyncMock(return_value={"ok": True, "locate_method": "cmd_k"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(side_effect=_probe),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button",
        AsyncMock(return_value={"ok": True, "send_fallback_clicked": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id=hidden_id,
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert out["ok"] is True
    assert out["verify_method"] == "dom"
    assert out["locate_method"] == "cmd_k"


@pytest.mark.asyncio
async def test_deliver_operator_paste_hidden_via_hidden_chats_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_nonce = "panel0000001"
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.make_run_nonce", lambda: fixed_nonce
    )
    wm = watermark_with_nonce(fixed_nonce)
    hidden_id = "hidden-agent-panel"
    pre = _hidden_chat_surface(active_agent_id="", ok=False)
    pre["active_title"] = ""
    post = {
        **_hidden_chat_surface(active_agent_id=hidden_id),
        "generating": True,
        "recent_messages": [wm],
        "message_turns": [{"role": "user", "text": wm}],
    }
    probes = [pre, _hidden_chat_surface(active_agent_id=hidden_id), post]

    async def _probe(_ws: str) -> dict[str, Any]:
        return probes.pop(0) if probes else post

    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.locate_off_sidebar",
        AsyncMock(return_value={"ok": True, "locate_method": "hidden_chats"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(side_effect=_probe),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button",
        AsyncMock(return_value={"ok": True, "send_fallback_clicked": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id=hidden_id,
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert out["ok"] is True
    assert out["verify_method"] == "dom"
    assert out["locate_method"] == "hidden_chats"


@pytest.mark.asyncio
async def test_deliver_operator_paste_refuses_agent_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = _hidden_chat_surface(active_agent_id="", ok=False)
    surface["active_title"] = ""
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.locate_off_sidebar",
        AsyncMock(return_value={"ok": False, "reason": "agent_not_found"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(return_value=surface),
    )

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="missing-agent",
        text="hi",
        grant="operator",
        authorization_source="agent-bus:21614#2",
    )
    assert out["reason"] == "agent_not_found"
    assert out["pasted"] is False


@pytest.mark.asyncio
async def test_deliver_operator_paste_refuses_agent_id_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = _hidden_chat_surface(active_agent_id="wrong-agent")
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.locate_off_sidebar",
        AsyncMock(return_value={"ok": True, "locate_method": "cmd_k"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(return_value=surface),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.select_sidebar_target",
        AsyncMock(return_value={"clicked": True}),
    )

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="expected-agent",
        text="hi",
        grant="operator",
        authorization_source="agent-bus:21614#2",
    )
    assert out["reason"] == "agent_id_mismatch"
    assert out["pasted"] is False


def test_target_verified_requires_matching_active_agent_id() -> None:
    assert _target_verified({"active_agent_id": "a"}, expected_agent_id="a")
    assert not _target_verified({"active_agent_id": "b"}, expected_agent_id="a")
    assert not _target_verified({"active_agent_id": ""}, expected_agent_id="a")


def test_submit_verified_requires_empty_composer_and_transcript() -> None:
    post = {
        "composer_draft": "",
        "recent_messages": [f"user line {WATERMARK}"],
    }
    assert _submit_verified(post, watermark=WATERMARK, body_snippet="user line")


@pytest.mark.asyncio
async def test_deliver_operator_paste_refuses_target_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = _hidden_chat_surface(active_agent_id="agent-1")
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(return_value=surface),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._target_verified",
        lambda _s, **_: False,
    )
    insert = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr("cursor_bridge.operator_paste._insert_composer_text", insert)

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="hi",
        grant="operator",
        authorization_source="agent-bus:21614#2",
    )
    assert out["reason"] == "target_unverified"
    insert.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_operator_paste_exactly_one_insert_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface_pre = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    surface_post = {
        **surface_pre,
        "generating": True,
        "recent_messages": [f"line with {WATERMARK}"],
    }

    async def _probe_counted(_ws: str) -> dict[str, Any]:
        if not hasattr(_probe_counted, "n"):
            _probe_counted.n = 0  # type: ignore[attr-defined]
        _probe_counted.n += 1  # type: ignore[attr-defined]
        return surface_pre if _probe_counted.n <= 1 else surface_post  # type: ignore[attr-defined]

    insert = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(side_effect=_probe_counted),
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._insert_composer_text", insert)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button",
        AsyncMock(return_value={"ok": True, "send_fallback_clicked": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )

    await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert insert.call_count == 1


@pytest.mark.asyncio
async def test_deliver_operator_paste_submit_retry_once_without_repaste(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    submitted = {
        **surface,
        "generating": True,
        "recent_messages": [f"relay {WATERMARK}"],
    }
    poll_calls = {"n": 0}

    async def _poll_submit(_ws: str, **_: Any) -> tuple[dict[str, Any], bool]:
        poll_calls["n"] += 1
        return submitted, True

    insert = AsyncMock(return_value={"ok": True})
    send_btn = AsyncMock(
        return_value={
            "ok": False,
            "reason": "no_send_button",
            "send_fallback_clicked": False,
        }
    )
    enter = AsyncMock(return_value={"ok": True, "method": "cdp_enter"})
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(return_value=surface),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_submit_verified", _poll_submit
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._insert_composer_text", insert)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button", send_btn
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_readback",
        AsyncMock(
            return_value=(
                submitted,
                {
                    "readback_ok": True,
                    "composer_empty_after": True,
                    "assistant_began": True,
                    "watermark_visible": True,
                },
            )
        ),
    )

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert insert.call_count == 1
    assert send_btn.call_count == 1
    enter.assert_awaited_once()
    assert poll_calls["n"] == 1
    assert out["submitted"] is True
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_submit_retry_skips_cdp_enter_when_transcript_has_watermark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_nonce = "cafebabef00d"
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.make_run_nonce", lambda: fixed_nonce
    )
    line = "Operator relay Scope: F1 skip-enter test"
    flat = " ".join(line.splitlines())
    wm = watermark_with_nonce(fixed_nonce)
    surface = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    verified_surface = {
        **surface,
        "composer_draft": "",
        "recent_messages": [f"{flat} {wm}"],
        "message_turns": [
            {"role": "user", "text": f"{flat} {wm}"},
        ],
    }

    async def _poll_submit(_ws: str, **_: Any) -> tuple[dict[str, Any], bool]:
        return {**surface, "composer_draft": "draft"}, False

    probe_calls = {"n": 0}

    async def _probe(_ws: str) -> dict[str, Any]:
        probe_calls["n"] += 1
        if probe_calls["n"] == 1:
            return surface
        return verified_surface

    insert = AsyncMock(return_value={"ok": True})
    send_btn = AsyncMock(return_value={"ok": True, "send_fallback_clicked": True})
    enter = AsyncMock(return_value={"ok": True, "method": "cdp_enter"})
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr("cursor_bridge.operator_paste.probe_surface", _probe)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_submit_verified", _poll_submit
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._insert_composer_text", insert)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button", send_btn
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_readback",
        AsyncMock(
            return_value=(
                verified_surface,
                {
                    "readback_ok": True,
                    "composer_empty_after": True,
                    "assistant_began": True,
                    "watermark_visible": True,
                },
            )
        ),
    )

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text=line,
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    enter.assert_not_awaited()
    assert out["submitted"] is True
    assert out["submit_methods"] == ["send_button"]


@pytest.mark.parametrize(
    "enter_reason",
    [
        "enter_wrong_active_agent",
        "enter_nonce_missing",
        "enter_operator_active",
        "enter_composer_not_focused_operator_active",
    ],
)
@pytest.mark.asyncio
async def test_enter_retry_refused_without_cdp_enter_success(
    monkeypatch: pytest.MonkeyPatch, enter_reason: str
) -> None:
    surface = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    stuck = {**surface, "composer_draft": "stuck draft"}

    async def _poll_submit(_ws: str, **_: Any) -> tuple[dict[str, Any], bool]:
        return stuck, False

    enter = AsyncMock(
        return_value={"ok": False, "reason": enter_reason, "method": "cdp_enter"}
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(return_value=surface),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_submit_verified", _poll_submit
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button",
        AsyncMock(
            return_value={
                "ok": False,
                "reason": "no_send_button",
                "send_fallback_clicked": False,
            }
        ),
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert out["submitted"] is False
    assert f"cdp_enter_refused:{enter_reason}" in out["submit_methods"]
    enter.assert_awaited_once()


@pytest.mark.asyncio
async def test_deliver_operator_paste_submitted_false_after_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    stuck = {**surface, "composer_draft": "stuck draft"}

    async def _poll_submit(_ws: str, **_: Any) -> tuple[dict[str, Any], bool]:
        return stuck, False

    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(return_value=surface),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_submit_verified", _poll_submit
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button",
        AsyncMock(return_value={"ok": True, "send_fallback_clicked": True}),
    )
    cleared = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft", cleared
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_enter",
        AsyncMock(return_value={"ok": True, "method": "cdp_enter"}),
    )
    screenshot = AsyncMock(
        return_value={
            "sha256_png": "p" * 64,
            "screenshot_path": "/tmp/submit-fail.png",
        }
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact", screenshot
    )

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert out["submitted"] is False
    assert out["reason"] == "submit_unverified"
    assert out["submit_methods"] == ["send_button"]
    assert out["verify_method"] == "screenshot_fallback"
    assert out["screenshot_path"] == "/tmp/submit-fail.png"
    screenshot.assert_awaited_once()
    cleared.assert_awaited_once()


@pytest.mark.asyncio
async def test_deliver_operator_paste_send_ok_unverified_clears_composer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    phase = {"sent": False, "cleared": False}

    async def _probe(_ws: str) -> dict[str, Any]:
        draft = "stuck draft" if phase["sent"] and not phase["cleared"] else ""
        return {**surface, "composer_draft": draft}

    async def _send(_ws: str, **_: Any) -> dict[str, Any]:
        from cursor_bridge.dom_composer_paste import _mark_click_dispatched

        _mark_click_dispatched()
        phase["sent"] = True
        return {"ok": True, "send_fallback_clicked": True}

    async def _clear(*_a: object, **_k: object) -> dict[str, Any]:
        phase["cleared"] = True
        return {"ok": True, "cleared": True}

    async def _poll_submit(_ws: str, **_: Any) -> tuple[dict[str, Any], bool]:
        return {**surface, "composer_draft": "stuck draft"}, False

    cleared = AsyncMock(side_effect=_clear)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr("cursor_bridge.operator_paste.probe_surface", _probe)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_submit_verified", _poll_submit
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button", _send
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft", cleared
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert out["submitted"] is False
    assert out["reason"] == "submit_unverified"
    assert out["retryable"] is False
    assert out["composer_empty_after"] is True
    assert phase["cleared"] is True
    cleared.assert_awaited_once()


@pytest.mark.asyncio
async def test_deliver_operator_paste_inconclusive_readback_screenshot_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    wm_only = {
        **surface,
        "recent_messages": [f"relay {WATERMARK}"],
        "generating": False,
    }

    async def _poll_submit(_ws: str, **_: Any) -> tuple[dict[str, Any], bool]:
        return wm_only, True

    async def _poll_readback(
        _ws: str, **_: Any
    ) -> tuple[dict[str, Any], dict[str, bool]]:
        fields = {
            "readback_ok": False,
            "composer_empty_after": True,
            "assistant_began": False,
            "watermark_visible": True,
        }
        return wm_only, fields

    screenshot = AsyncMock(
        return_value={
            "sha256_png": "p" * 64,
            "screenshot_path": "/tmp/inconclusive.png",
        }
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(return_value=surface),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_submit_verified", _poll_submit
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._poll_readback", _poll_readback)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button",
        AsyncMock(return_value={"ok": True, "send_fallback_clicked": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact", screenshot
    )

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert out["submitted"] is True
    assert out["ok"] is False
    assert out["verify_method"] == "screenshot_fallback"
    assert out["screenshot_path"] == "/tmp/inconclusive.png"
    screenshot.assert_awaited_once()


def test_dom_verification_conclusive_requires_agent_id_and_readback() -> None:
    post = {
        "active_agent_id": "agent-1",
        "composer_draft": "",
        "recent_messages": [WATERMARK, "assistant reply"],
        "generating": True,
    }
    assert _dom_verification_conclusive(
        post,
        expected_agent_id="agent-1",
        watermark=WATERMARK,
        body_snippet="relay",
        readback_ok=True,
    )
    assert not _dom_verification_conclusive(
        post,
        expected_agent_id="agent-1",
        watermark=WATERMARK,
        body_snippet="relay",
        readback_ok=False,
    )
    assert not _dom_verification_conclusive(
        {**post, "active_agent_id": ""},
        expected_agent_id="agent-1",
        watermark=WATERMARK,
        body_snippet="relay",
        readback_ok=True,
    )


@pytest.mark.asyncio
async def test_ambiguous_timeout_probes_and_skips_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    stuck = {**surface, "composer_draft": "stuck relay text"}
    enter = AsyncMock(return_value={"ok": True, "method": "cdp_enter"})
    cleared = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft", cleared
    )

    async def _probe(_ws: str) -> dict[str, Any]:
        _probe.calls += 1
        return surface if _probe.calls == 1 else stuck

    _probe.calls = 0
    probe = AsyncMock(side_effect=_probe)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr("cursor_bridge.operator_paste.probe_surface", probe)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )

    async def _dom_click(*_a: object, **_k: object) -> dict[str, Any]:
        from cursor_bridge.dom_composer_paste import _mark_click_dispatched

        _mark_click_dispatched()
        return {
            "ok": False,
            "reason": "cdp_eval_timeout",
            "send_clicked": False,
            "clicked_unknown": True,
            "retryable": False,
        }

    monkeypatch.setattr("cursor_bridge.operator_paste.dom_click_send", _dom_click)
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )
    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert out["ok"] is False
    assert out["reason"] == "submit_unverified"
    enter.assert_not_awaited()
    assert probe.await_count >= 1
    cleared.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_button_runtime_error_probes_skips_enter_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    cleared = AsyncMock(return_value={"ok": True})
    enter = AsyncMock(return_value={"ok": True, "method": "cdp_enter"})

    async def _send_then_drop(*_a: object, **_k: object) -> dict[str, Any]:
        from cursor_bridge.dom_composer_paste import _mark_click_dispatched

        _mark_click_dispatched()
        raise RuntimeError("socket dropped")

    send = AsyncMock(side_effect=_send_then_drop)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(return_value=surface),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button", send
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft", cleared
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_submit_verified",
        AsyncMock(return_value=(surface, False)),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )
    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert out["reason"] == "submit_unverified"
    assert out["retryable"] is False
    send.assert_awaited_once()
    enter.assert_not_awaited()
    cleared.assert_awaited()


@pytest.mark.asyncio
async def test_send_button_error_then_connection_closed_probe_unobserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import websockets.exceptions

    surface = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    enter = AsyncMock(return_value={"ok": True, "method": "cdp_enter"})
    cleared = AsyncMock(return_value={"ok": True, "cleared": False})
    send_awaited = False
    conn_closed = websockets.exceptions.ConnectionClosed(None, None)

    async def probe_side_effect(*_a: object, **_k: object) -> dict[str, object]:
        nonlocal send_awaited
        if send_awaited:
            raise conn_closed
        return surface

    async def send_side_effect(*_a: object, **_k: object) -> None:
        from cursor_bridge.dom_composer_paste import _mark_click_dispatched

        nonlocal send_awaited
        _mark_click_dispatched()
        send_awaited = True
        raise RuntimeError("socket dropped")

    probe = AsyncMock(side_effect=probe_side_effect)
    send = AsyncMock(side_effect=send_side_effect)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr("cursor_bridge.operator_paste.probe_surface", probe)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button", send
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft", cleared
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_submit_verified",
        AsyncMock(return_value=(surface, False)),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )
    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert out["reason"] == "submit_unverified"
    assert out["retryable"] is False
    send.assert_awaited_once()
    enter.assert_not_awaited()
    assert out["composer_empty_after"] is None
    assert out["readback_ok"] is None
    assert out["watermark_visible"] is None
    assert out["detail"] == "ConnectionClosed"
    assert cleared.await_count == 0


@pytest.mark.asyncio
async def test_ambiguous_timeout_probe_verified_skips_clear_and_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flat = "Operator relay test"
    nonce = make_run_nonce()
    wm = watermark_with_nonce(nonce)
    verified = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [f"{flat} {wm}"],
    }
    cleared = AsyncMock(return_value={"ok": True})
    enter = AsyncMock(return_value={"ok": True, "method": "cdp_enter"})
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft", cleared
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(return_value=verified),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.dom_click_send",
        AsyncMock(
            return_value={
                "ok": False,
                "reason": "cdp_eval_timeout",
                "send_clicked": False,
                "clicked_unknown": True,
                "retryable": False,
            }
        ),
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_readback",
        AsyncMock(
            return_value=(
                verified,
                {
                    "readback_ok": True,
                    "composer_empty_after": True,
                    "watermark_visible": True,
                    "assistant_began": True,
                },
            )
        ),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )
    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text=wrap_operator_message(
            text=flat,
            authorization_source="agent-bus:21614#2",
            scope="test",
            run_nonce=nonce,
        ),
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert out["submitted"] is True
    enter.assert_not_awaited()
    cleared.assert_not_awaited()


@pytest.mark.asyncio
async def test_cdp_eval_timeout_clears_our_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    cleared = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(return_value=surface),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.dom_click_send",
        AsyncMock(
            return_value={
                "ok": False,
                "reason": "cdp_eval_timeout",
                "send_clicked": False,
            }
        ),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_submit_verified",
        AsyncMock(return_value=(surface, False)),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft", cleared
    )
    await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    cleared.assert_awaited_once()


@pytest.mark.asyncio
async def test_cdp_eval_timeout_without_click_fact_allows_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timeout with no click fact may Enter once; an unverified Enter is sealed.

    Break: Enter returns ok, the submit poll stays unverified, and retryable
    is left unset so a caller can send the body again.
    """
    surface = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    enter = AsyncMock(return_value={"ok": True, "method": "cdp_enter"})
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(return_value=surface),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.dom_click_send",
        AsyncMock(
            return_value={
                "ok": False,
                "reason": "cdp_eval_timeout",
                "send_clicked": False,
            }
        ),
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_submit_verified",
        AsyncMock(return_value=(surface, False)),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )
    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert out["ok"] is False
    assert out["retryable"] is False
    enter.assert_awaited_once()


@pytest.mark.asyncio
async def test_enter_connection_closed_after_key_is_not_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break: Enter raises ConnectionClosed after keyDown and retryable stays unset.

    The click fact is still false, so Enter was allowed. ConnectionClosed is
    outside the Enter handler's former except tuple, and a caller could send
    the body again.
    """
    surface = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    enter = AsyncMock(side_effect=_post_click_fault_exc("closed"))
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.probe_surface",
        AsyncMock(return_value=surface),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.dom_click_send",
        AsyncMock(
            return_value={
                "ok": False,
                "reason": "cdp_eval_timeout",
                "send_clicked": False,
            }
        ),
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._poll_submit_verified",
        AsyncMock(return_value=(surface, False)),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft",
        AsyncMock(return_value={"ok": True, "cleared": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )
    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )
    assert out["reason"] == "submit_unverified"
    assert out["detail"] == "ConnectionClosed"
    assert out["retryable"] is False
    enter.assert_awaited_once()


def _post_click_fault_exc(name: str) -> BaseException:
    if name == "timeout":
        return TimeoutError("post-click fault")
    if name == "runtime":
        return RuntimeError("post-click fault")
    if name == "os":
        return OSError("post-click fault")
    import websockets.exceptions

    return websockets.exceptions.ConnectionClosed(None, None)


def _parse_post_click_fault(fault: str) -> tuple[str, str | None]:
    if fault.startswith("verified_match_"):
        return "verified_match", fault.removeprefix("verified_match_")
    head, sep, tail = fault.partition("_")
    if sep and head in {"probe", "match", "clear", "poll", "enter"}:
        return head, tail
    return fault, None


def _post_click_fault_ids() -> list[str]:
    faults = [
        "ambiguous_unverified",
        "ambiguous_agent_flip",
        "ok_agent_flip",
        "verified_agent_flip",
    ]
    for site in ("probe", "match", "clear", "poll", "enter"):
        for exc_name in ("timeout", "runtime", "os", "closed"):
            faults.append(f"{site}_{exc_name}")
    for exc_name in ("timeout", "runtime", "os", "closed"):
        faults.append(f"verified_match_{exc_name}")
    return faults


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", _post_click_fault_ids())
async def test_post_click_exit_is_not_retried_and_clears_at_most_once(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """A click script, or an Enter call that raised, leaves retryable false.

    A verified send is not cleared. A probe that died reports readback and
    watermark as unknown. Enter returning ok false is not in this matrix.
    """
    kind, exc_name = _parse_post_click_fault(fault)
    surface = {
        "state": "chat_list",
        "active_title": "Test Grok",
        "active_agent_id": "agent-1",
        "titles": ["Test Grok"],
        "title_counts": {"Test Grok": 1},
        "agent_ids": {"agent-1": "Test Grok"},
        "composer_draft": "",
        "generating": False,
        "recent_messages": [],
    }
    phase = {"sent": False}

    async def _probe(_ws: str) -> dict[str, Any]:
        if not phase["sent"]:
            return dict(surface)
        if kind == "probe":
            assert exc_name is not None
            raise _post_click_fault_exc(exc_name)
        if "agent_flip" in fault:
            return {**surface, "active_agent_id": "other"}
        return dict(surface)

    async def _send(_ws: str, **_: Any) -> dict[str, Any]:
        from cursor_bridge.dom_composer_paste import _mark_click_dispatched

        phase["sent"] = True
        if kind != "enter":
            _mark_click_dispatched()
        if kind in {"ambiguous_unverified", "ambiguous_agent_flip", "probe", "clear"}:
            return {
                "ok": False,
                "reason": "cdp_eval_timeout",
                "clicked_unknown": True,
                "retryable": False,
            }
        if kind == "enter":
            return {"ok": False, "reason": "send_button_error"}
        return {"ok": True, "send_fallback_clicked": True}

    async def _poll(_ws: str, **_: Any) -> tuple[dict[str, Any], bool]:
        if kind == "poll":
            assert exc_name is not None
            raise _post_click_fault_exc(exc_name)
        if kind in {"verified_match", "verified_agent_flip"}:
            return dict(surface), True
        return dict(surface), False

    match_calls = {"n": 0}

    async def _match(
        _ws: str, *, expected_agent_id: str
    ) -> tuple[bool, dict[str, Any]]:
        match_calls["n"] += 1
        if kind in {"match", "verified_match"} and match_calls["n"] >= 3:
            assert exc_name is not None
            raise _post_click_fault_exc(exc_name)
        seen = await _probe(_ws)
        return str(seen.get("active_agent_id") or "") == expected_agent_id, seen

    cleared = AsyncMock(return_value={"ok": True, "cleared": True})
    if kind == "clear":
        assert exc_name is not None
        cleared.side_effect = _post_click_fault_exc(exc_name)
    enter = AsyncMock(return_value={"ok": True, "method": "cdp_enter"})
    if kind == "enter":
        assert exc_name is not None
        enter.side_effect = _post_click_fault_exc(exc_name)

    monkeypatch.setattr(
        "cursor_bridge.operator_paste.list_pages",
        lambda _o: [{"title": "Grok Bot", "web_socket_debugger_url": "ws://x"}],
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.visible_sidebar_agent_ids",
        AsyncMock(return_value={"agent-1"}),
    )
    monkeypatch.setattr("cursor_bridge.operator_paste.probe_surface", _probe)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._insert_composer_text",
        AsyncMock(return_value={"ok": True}),
    )
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._submit_composer_send_button", _send
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._poll_submit_verified", _poll)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste.clear_our_composer_draft", cleared
    )
    monkeypatch.setattr("cursor_bridge.operator_paste._submit_composer_enter", enter)
    if kind in {"match", "verified_match"}:
        monkeypatch.setattr("cursor_bridge.operator_paste._probe_agent_match", _match)
    monkeypatch.setattr(
        "cursor_bridge.operator_paste._capture_screenshot_artifact",
        AsyncMock(return_value={"sha256_png": None, "screenshot_path": None}),
    )

    out = await deliver_operator_paste(
        display="10",
        app="grok",
        agent_id="agent-1",
        text="Operator relay test",
        grant="operator",
        authorization_source="agent-bus:21614#2",
        scope="test",
    )

    assert out.get("retryable") is False
    if "agent_flip" in fault:
        assert out["reason"] == "target_changed"
    else:
        assert out["reason"] == "submit_unverified"
    if kind == "enter":
        enter.assert_awaited_once()
    else:
        enter.assert_not_awaited()
    if fault.startswith("verified_"):
        assert cleared.await_count == 0
    else:
        assert cleared.await_count <= 1
    if out.get("detail") in {
        "TimeoutError",
        "RuntimeError",
        "OSError",
        "ConnectionClosed",
    }:
        assert out["readback_ok"] is None
        assert out["watermark_visible"] is None


def test_operator_paste_imports_in_fresh_subprocess() -> None:
    """Module-level import must not hit interlocutor circular init."""
    proc = subprocess.run(
        [sys.executable, "-c", "import cursor_bridge.operator_paste"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
