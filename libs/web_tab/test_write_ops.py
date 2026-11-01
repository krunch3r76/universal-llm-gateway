"""Write ops with fake CDP transport — grants, locks, operator backoff, receipts."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from web_tab.cdp_actuation import RecordingActuationPort
from web_tab.grants import require_grant
from web_tab.tab_binding_store import load_binding
from web_tab.write_ops import (
    execute_bind,
    execute_open,
    execute_paste,
    execute_verify_committed,
)


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
async def test_open_reuses_tab_by_url() -> None:
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
        port=port,
    )
    assert out["ok"] is True
    assert out["reused"] is True
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
        "web_tab.write_ops.operator_active_probe",
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
            surface="grok",
            port=port,
        )
    assert out["code"] == "operator_active"
    assert out.get("retryable") is True


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
            "web_tab.write_ops.operator_active_probe",
            AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "web_tab.write_ops.dom_set_composer_text",
            AsyncMock(return_value={"ok": True}),
        ),
    ):
        out = await execute_paste(
            cdp_url="http://127.0.0.1:9222",
            target_id="t1",
            text="hello",
            grant="operator",
            surface="grok",
            port=port,
        )
    assert out["ok"] is True
    assert out["receipt"] == "dom_paste"


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
        "receipt": "dom_committed",
        "has_user_turn": True,
        "composer_empty": True,
    }
    out = await execute_verify_committed(
        cdp_url="http://127.0.0.1:9222",
        target_id="t1",
        prompt_text="hello",
        surface="grok",
        port=port,
    )
    assert out["receipt"] == "dom_committed"
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_target_lock_refuses_nonblocking_fcntl() -> None:
    """Second fcntl acquire on the same target id fails without blocking."""
    import os

    from web_tab.target_lock import _FcntlFileLock, _lock_path

    tid = "lock-test-target-fcntl"
    os.environ["ULG_WEB_TAB_LOCK_DIR"] = "/tmp/web-tab-test-locks"
    path = _lock_path(tid)
    first = _FcntlFileLock(path)
    assert first.acquire(blocking=False) is True
    second = _FcntlFileLock(path)
    assert second.acquire(blocking=False) is False
    first.release()
