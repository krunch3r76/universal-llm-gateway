"""Write ops with fake CDP transport — grants, locks, operator backoff, receipts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from cursor_bridge.dom_composer_paste import composer_draft_sha256_hex

from web_tab.cdp_actuation import RecordingActuationPort
from web_tab.grants import require_grant
from web_tab.tab_binding_store import load_binding
from web_tab.write_ops import (
    _resolve_write_cdp_url,
    dispatch_write_op,
    execute_bind,
    execute_open,
    execute_paste,
    execute_submit,
    execute_verify_committed,
)

_AUTH = "agent-bus:21642#13"


@pytest.mark.asyncio
async def test_grant_required_for_paste() -> None:
    port = RecordingActuationPort(
        tabs=[
            {
                "id": "t1",
                "url": "https://grok.com/c/x",
                "web_socket_debugger_url": "ws://fake/t1",
            }
        ]
    )
    result = await execute_paste(
        cdp_url="http://127.0.0.1:9222",
        target_id="t1",
        text="hi",
        grant=None,
        surface="grok",
        port=port,
    )
    assert result["code"] == "grant_required"


def test_require_grant_accepts_operator() -> None:
    assert require_grant("operator") is None


@pytest.mark.asyncio
async def test_open_default_force_new_skips_reuse(tmp_path: Path) -> None:
    port = RecordingActuationPort(
        tabs=[
            {
                "id": "existing",
                "url": "https://chatgpt.com/c/abc",
                "web_socket_debugger_url": "ws://fake/existing",
            }
        ]
    )
    store = tmp_path / "bindings.json"
    out = await execute_open(
        cdp_url="http://127.0.0.1:9342",
        url="https://chatgpt.com/c/abc",
        grant="explicit",
        authorization_source=_AUTH,
        store_path=store,
        port=port,
    )
    assert out["ok"] is True
    assert out["reused"] is False
    assert out["created_by_agent"] is True
    assert port.created == ["https://chatgpt.com/c/abc"]
    loaded = load_binding(
        cdp_url="http://127.0.0.1:9342",
        conversation_url="https://chatgpt.com/c/abc",
        path=store,
    )
    assert loaded is not None
    assert loaded.created_by_agent is True


@pytest.mark.asyncio
async def test_open_reuses_tab_when_reuse_true(tmp_path: Path) -> None:
    port = RecordingActuationPort(
        tabs=[
            {
                "id": "existing",
                "url": "https://chatgpt.com/c/abc",
                "web_socket_debugger_url": "ws://fake/existing",
            }
        ]
    )
    out = await execute_open(
        cdp_url="http://127.0.0.1:9342",
        url="https://chatgpt.com/c/abc",
        grant="explicit",
        authorization_source=_AUTH,
        reuse=True,
        store_path=tmp_path / "bindings.json",
        port=port,
    )
    assert out["ok"] is True
    assert out["reused"] is True
    assert out["created_by_agent"] is False
    assert out["target_id"] == "existing"
    assert port.created == []


@pytest.mark.asyncio
async def test_bind_persists_session(tmp_path: Path) -> None:
    store = tmp_path / "bindings.json"
    port = RecordingActuationPort(
        tabs=[
            {
                "id": "t1",
                "url": "https://chatgpt.com/c/abc",
                "web_socket_debugger_url": "ws://fake/t1",
            }
        ]
    )
    out = await execute_bind(
        cdp_url="http://127.0.0.1:9342",
        conversation_url="https://chatgpt.com/c/abc",
        target_id="t1",
        grant="explicit",
        authorization_source=_AUTH,
        store_path=store,
        port=port,
    )
    assert out["ok"] is True
    loaded = load_binding(
        cdp_url="http://127.0.0.1:9342",
        conversation_url="https://chatgpt.com/c/abc",
        path=store,
    )
    assert loaded is not None
    assert loaded.target_id == "t1"


@pytest.mark.asyncio
async def test_paste_operator_active_retryable() -> None:
    port = RecordingActuationPort(
        tabs=[
            {
                "id": "t1",
                "url": "https://grok.com/c/x",
                "web_socket_debugger_url": "ws://fake/t1",
            }
        ]
    )
    with patch(
        "web_tab.write_ops.wait_operator_idle_before_lock",
        AsyncMock(
            return_value={
                "ok": False,
                "code": "operator_active",
                "retryable": True,
            }
        ),
    ):
        out = await execute_paste(
            cdp_url="http://127.0.0.1:9222",
            target_id="t1",
            text="hello",
            grant="explicit",
            authorization_source=_AUTH,
            surface="grok",
            port=port,
        )
    assert out["code"] == "operator_active"
    assert out.get("retryable") is True


@pytest.mark.asyncio
async def test_open_then_paste_reports_created_by_agent(tmp_path: Path) -> None:
    """Paste without conversation_url still resolves agent-open binding by target_id."""
    store = tmp_path / "bindings.json"
    port = RecordingActuationPort(
        tabs=[
            {
                "id": "new-tab",
                "url": "https://grok.com/",
                "web_socket_debugger_url": "ws://fake/new-tab",
            }
        ]
    )
    opened = await execute_open(
        cdp_url="http://127.0.0.1:9342",
        url="https://grok.com/",
        grant="explicit",
        authorization_source=_AUTH,
        store_path=store,
        port=port,
    )
    assert opened["created_by_agent"] is True
    tid = str(opened["target_id"])
    with (
        patch(
            "web_tab.write_ops.wait_operator_idle_before_lock",
            AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "web_tab.write_ops.operator_recheck_under_lock",
            AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "web_tab.write_ops.dom_set_composer_text",
            AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "web_tab.write_ops.capture_dom_baseline",
            AsyncMock(
                return_value={
                    "ok": True,
                    "user_count": 0,
                    "assistant_count": 0,
                    "last_assistant_text": "",
                }
            ),
        ),
    ):
        pasted = await execute_paste(
            cdp_url="http://127.0.0.1:9342",
            target_id=tid,
            text="hello",
            grant="explicit",
            authorization_source=_AUTH,
            store_path=store,
            port=port,
        )
    assert pasted["ok"] is True
    assert pasted["created_by_agent"] is True


@pytest.mark.asyncio
async def test_paste_dom_paste_receipt() -> None:
    port = RecordingActuationPort(
        tabs=[
            {
                "id": "t1",
                "url": "https://grok.com/c/x",
                "web_socket_debugger_url": "ws://fake/t1",
            }
        ]
    )
    with (
        patch(
            "web_tab.write_ops.wait_operator_idle_before_lock",
            AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "web_tab.write_ops.operator_recheck_under_lock",
            AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "web_tab.write_ops.dom_set_composer_text",
            AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "web_tab.write_ops.capture_dom_baseline",
            AsyncMock(
                return_value={
                    "ok": True,
                    "user_count": 0,
                    "assistant_count": 2,
                    "last_assistant_text": "prior",
                }
            ),
        ),
    ):
        out = await execute_paste(
            cdp_url="http://127.0.0.1:9222",
            target_id="t1",
            text="hello",
            grant="explicit",
            authorization_source=_AUTH,
            port=port,
        )
    assert out["ok"] is True
    assert out["receipt"] == "dom_paste"
    assert out["assistant_count"] == 2
    assert out["last_assistant_text"] == "prior"


@pytest.mark.asyncio
async def test_verify_committed_receipt_from_dom() -> None:
    port = RecordingActuationPort(
        tabs=[
            {
                "id": "t1",
                "url": "https://grok.com/c/x",
                "web_socket_debugger_url": "ws://fake/t1",
            }
        ]
    )
    port.evaluate_script["ws://fake/t1"] = {
        "ok": True,
        "user_count": 0,
        "user_texts": [],
        "assistant_count": 0,
        "last_assistant_text": "",
        "receipt": "dom_committed",
        "has_user_turn": True,
        "composer_empty": True,
    }
    out = await execute_verify_committed(
        cdp_url="http://127.0.0.1:9342",
        target_id="t1",
        prompt_text="hello",
        baseline_user_count=0,
        port=port,
    )
    assert out["receipt"] == "dom_committed"
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_verify_committed_standalone_downgrades_to_dom_present() -> None:
    port = RecordingActuationPort(
        tabs=[
            {
                "id": "t1",
                "url": "https://grok.com/c/x",
                "web_socket_debugger_url": "ws://fake/t1",
            }
        ]
    )
    port.evaluate_script["ws://fake/t1"] = {
        "ok": True,
        "user_count": 1,
        "user_texts": ["hello"],
        "receipt": "dom_committed",
        "has_user_turn": True,
        "composer_empty": True,
    }
    out = await execute_verify_committed(
        cdp_url="http://127.0.0.1:9342",
        target_id="t1",
        prompt_text="hello",
        port=port,
    )
    assert out["receipt"] == "dom_present"
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_target_lock_refuses_nonblocking_fcntl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second fcntl acquire on the same target id fails without blocking."""
    from web_tab.target_lock import _FcntlFileLock, _lock_path

    tid = "lock-test-target-fcntl"
    monkeypatch.setenv("ULG_WEB_TAB_LOCK_DIR", "/tmp/web-tab-test-locks")
    path = _lock_path(tid)
    first = _FcntlFileLock(path)
    assert first.acquire(blocking=False) is True
    second = _FcntlFileLock(path)
    assert second.acquire(blocking=False) is False
    first.release()


@pytest.mark.asyncio
async def test_execute_paste_idle_wait_runs_before_target_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    async def _pre(_ws: str, **_: object) -> dict:
        order.append("idle_before_lock")
        return {"ok": True}

    @asynccontextmanager
    async def _track_lock(
        _target_id: str, *, op: str = "paste", timeout_s: float = 120
    ):
        order.append("lock_acquire")
        yield
        order.append("lock_release")

    port = RecordingActuationPort(
        tabs=[
            {
                "id": "t-order",
                "url": "https://grok.com/c/x",
                "web_socket_debugger_url": "ws://fake/t-order",
            }
        ]
    )
    monkeypatch.setattr("web_tab.write_ops.wait_operator_idle_before_lock", _pre)
    monkeypatch.setattr("web_tab.write_ops.target_write_lock", _track_lock)
    with (
        patch(
            "web_tab.write_ops.dom_set_composer_text",
            AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "web_tab.write_ops.capture_dom_baseline",
            AsyncMock(
                return_value={
                    "ok": True,
                    "user_count": 0,
                    "assistant_count": 0,
                    "last_assistant_text": "",
                }
            ),
        ),
        patch(
            "web_tab.write_ops.operator_recheck_under_lock",
            AsyncMock(return_value={"ok": True, "probe": {}}),
        ),
    ):
        out = await execute_paste(
            cdp_url="http://127.0.0.1:9222",
            target_id="t-order",
            text="ordered paste",
            grant="explicit",
            authorization_source=_AUTH,
            port=port,
        )
    assert out["ok"] is True
    assert order == ["idle_before_lock", "lock_acquire", "lock_release"]


def test_resolve_write_cdp_url_maps_surface_without_explicit() -> None:
    url, err = _resolve_write_cdp_url({"surface": "anthropic_console"})
    assert err is None
    assert url == "http://127.0.0.1:9343"


@pytest.mark.asyncio
async def test_dispatch_refuses_claude_ai_chat_url() -> None:
    result = await dispatch_write_op(
        "open",
        {
            "surface": "anthropic_console",
            "url": "https://claude.ai/chat/x",
            "grant": "explicit",
            "authorization_source": _AUTH,
        },
    )
    assert result["ok"] is False
    assert result["code"] == "surface_not_web_tab_owned"
    assert result["owner"] == "cdp_ask"


@pytest.mark.asyncio
async def test_dispatch_refuses_anthropic_console_on_chatgpt_cdp_port() -> None:
    result = await dispatch_write_op(
        "bind",
        {
            "surface": "anthropic_console",
            "cdp_url": "http://127.0.0.1:9342",
            "conversation_url": "https://console.anthropic.com/settings",
            "target_id": "t1",
            "grant": "explicit",
            "authorization_source": _AUTH,
        },
    )
    assert result == {"ok": False, "code": "cdp_surface_mismatch"}


def _grok_port(target_id: str) -> RecordingActuationPort:
    return RecordingActuationPort(
        tabs=[
            {
                "id": target_id,
                "url": "https://grok.com/c/x",
                "web_socket_debugger_url": f"ws://fake/{target_id}",
            }
        ]
    )


@pytest.mark.asyncio
async def test_other_writer_expect_sha_mismatch_refuses() -> None:
    """Writer B's 64-hex expect that is not the digest of writer A's hello draft is refused."""
    port = _grok_port("t-writers")
    send = AsyncMock(return_value={"ok": True})
    probe = {
        "ok": True,
        "probe": {"composer_draft": "hello", "foreign_draft": True},
    }
    with (
        patch(
            "web_tab.write_ops.wait_operator_idle_before_lock",
            AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "web_tab.write_ops.operator_recheck_under_lock",
            AsyncMock(return_value=probe),
        ),
        patch(
            "web_tab.write_ops.dom_set_composer_text",
            AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "web_tab.write_ops.capture_dom_baseline",
            AsyncMock(
                return_value={
                    "ok": True,
                    "user_count": 0,
                    "assistant_count": 0,
                    "last_assistant_text": "",
                }
            ),
        ),
        patch("web_tab.write_ops.dom_click_send", send),
        patch("web_tab.write_ops.dom_click_send_own_draft", send),
    ):
        pasted = await execute_paste(
            cdp_url="http://127.0.0.1:9222",
            target_id="t-writers",
            text="hello",
            grant="explicit",
            authorization_source="agent-bus:1#1",
            surface="grok",
            port=port,
        )
        out = await execute_submit(
            cdp_url="http://127.0.0.1:9222",
            target_id="t-writers",
            grant="explicit",
            authorization_source="agent-bus:2#2",
            expect_draft_sha256="0" * 64,
            surface="grok",
            port=port,
        )
    assert pasted["ok"] is True
    assert out["ok"] is False
    assert out["code"] == "draft_sha_mismatch"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_bridge_wake_sha_of_other_text_refuses() -> None:
    """A BRIDGE_WAKE draft is refused when the submit expect is the SHA of different text."""
    held = "BRIDGE_WAKE thread=21207 turn=2"
    port = _grok_port("t-bridge")
    send = AsyncMock(return_value={"ok": True})
    probe = {
        "ok": True,
        "probe": {"composer_draft": held, "foreign_draft": False},
    }
    with (
        patch(
            "web_tab.write_ops.wait_operator_idle_before_lock",
            AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "web_tab.write_ops.operator_recheck_under_lock",
            AsyncMock(return_value=probe),
        ),
        patch("web_tab.write_ops.dom_click_send", send),
        patch("web_tab.write_ops.dom_click_send_own_draft", send),
    ):
        out = await execute_submit(
            cdp_url="http://127.0.0.1:9222",
            target_id="t-bridge",
            grant="explicit",
            authorization_source="agent-bus:1#1",
            expect_draft_sha256=composer_draft_sha256_hex("other wake text"),
            surface="grok",
            port=port,
        )
    assert out["ok"] is False
    assert out["code"] == "draft_sha_mismatch"
    send.assert_not_awaited()
