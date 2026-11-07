"""Guarded CDP native insert for hydrated ChatGPT Work composers."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from cursor_bridge.cdp_composer_input import cdp_work_native_insert_text


class _FakeWS:
    def __init__(self, *, preflight: dict[str, Any], readback: dict[str, Any]) -> None:
        self._preflight = preflight
        self._readback = readback
        self.sent: list[dict[str, Any]] = []
        self._recv_q: list[dict[str, Any]] = [{"id": 4, "result": {}}]

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def recv(self) -> str:
        if self._recv_q:
            return json.dumps(self._recv_q.pop(0))
        await asyncio.sleep(0)
        return json.dumps({"id": 99, "result": {}})

    async def __aenter__(self) -> _FakeWS:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.mark.asyncio
async def test_work_native_preflight_refusal_before_insert(monkeypatch) -> None:
    """Pre-input refusal must not send Input.insertText."""
    fake = _FakeWS(
        preflight={"ok": False, "reason": "composer_not_empty"},
        readback={"ok": True},
    )
    async def _eval(ws: Any, mid: int, expression: str) -> Any:
        if mid == 1:
            return None
        if mid == 2:
            return fake._preflight
        return None

    monkeypatch.setattr(
        "cursor_bridge.cdp_composer_input._runtime_eval",
        _eval,
    )

    class _Connect:
        def __init__(self, *a: Any, **kw: Any) -> None:
            self._fake = fake

        async def __aenter__(self) -> _FakeWS:
            return self._fake

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(
        "websockets.connect",
        lambda *a, **kw: _Connect(),
    )

    out = await cdp_work_native_insert_text(
        "ws://fake",
        text="hello",
        expected_url="https://chatgpt.com/c/abc",
        draft_writer="test-writer",
    )
    assert out["ok"] is False
    assert out["reason"] == "composer_not_empty"
    assert out.get("pasted") is False
    assert out.get("insertion_unknown") is not True
    assert not any(m.get("method") == "Input.insertText" for m in fake.sent)


@pytest.mark.asyncio
async def test_work_native_readback_mismatch_is_insertion_unknown(monkeypatch) -> None:
    """Transport/readback uncertainty must not claim pasted."""
    fake = _FakeWS(
        preflight={"ok": True},
        readback={"ok": False, "reason": "composer_text_mismatch"},
    )

    async def _eval(ws: Any, mid: int, expression: str) -> Any:
        if mid == 1:
            return None
        if mid == 2:
            return fake._preflight
        if mid in (3, 5):
            return None
        if mid == 6:
            return fake._readback
        return None

    monkeypatch.setattr(
        "cursor_bridge.cdp_composer_input._runtime_eval",
        _eval,
    )

    class _Connect:
        async def __aenter__(self) -> _FakeWS:
            return fake

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(
        "websockets.connect",
        lambda *a, **kw: _Connect(),
    )

    out = await cdp_work_native_insert_text(
        "ws://fake",
        text="hello",
        expected_url="https://chatgpt.com/c/abc",
        draft_writer="test-writer",
    )
    assert out.get("insertion_unknown") is True
    assert out.get("pasted") is False
    assert out.get("retry_eligible") is False
    assert out["reason"] == "composer_text_mismatch"
    insert_cmds = [m for m in fake.sent if m.get("method") == "Input.insertText"]
    assert len(insert_cmds) == 1
