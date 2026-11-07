"""Guarded CDP native insert for hydrated ChatGPT Work composers."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from typing import Any

import pytest

from cursor_bridge.cdp_composer_input import cdp_work_native_insert_text
from cursor_bridge.dom_composer_paste import (
    _set_composer_body,
    work_native_unsupported_probe_js,
)

_PLAYWRIGHT_SKIP = pytest.mark.skipif(
    importlib.util.find_spec("playwright") is None,
    reason="playwright not installed",
)


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


@pytest.mark.asyncio
async def test_work_native_insert_socket_command_order(monkeypatch) -> None:
    """Exactly one Input.insertText between suppress eval and clear eval."""
    fake = _FakeWS(
        preflight={"ok": True},
        readback={"ok": True, "phase": "inserted", "inserted": True, "pasted": True},
    )
    eval_mids: list[int] = []

    async def _eval(ws: Any, mid: int, expression: str) -> Any:
        eval_mids.append(mid)
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
    assert out.get("ok") is True
    assert 1 in eval_mids and 2 in eval_mids
    assert 3 in eval_mids and 5 in eval_mids and 6 in eval_mids
    insert_cmds = [m for m in fake.sent if m.get("method") == "Input.insertText"]
    assert len(insert_cmds) == 1
    methods = [m.get("method") for m in fake.sent]
    assert methods.count("Input.insertText") == 1
    assert eval_mids.index(3) < eval_mids.index(5)


@_PLAYWRIGHT_SKIP
def test_work_native_probe_ignores_hidden_twin() -> None:
    """Visible editor counts; 0×0 twin must not make probe ambiguous."""
    from playwright.sync_api import sync_playwright

    html = """<!DOCTYPE html><html><body>
<div contenteditable="true" role="textbox" aria-label="Work with ChatGPT on your tasks"
  style="position:absolute;width:0;height:0;overflow:hidden" id="twin"></div>
<div contenteditable="true" role="textbox" aria-label="Work with ChatGPT on your tasks"
  style="min-height:40px" id="main" pmViewDesc="1"></div>
</body></html>"""
    probe = work_native_unsupported_probe_js(
        expected_url="https://chatgpt.com/c/abc"
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://chatgpt.com/c/abc", wait_until="domcontentloaded")
        page.set_content(html)
        page.evaluate(
            """() => {
              const el = document.getElementById('main');
              el.pmViewDesc = {};
            }"""
        )
        result = page.evaluate(probe)
        browser.close()
    assert result.get("eligible") is True


@_PLAYWRIGHT_SKIP
def test_work_native_preflight_refuses_populated_sibling_attachment() -> None:
    """Sibling file chip with content must refuse native insert before Input."""
    from playwright.sync_api import sync_playwright

    from cursor_bridge.cdp_composer_input import _work_native_preflight_js

    html = """<!DOCTYPE html><html><body>
<div class="text-size-chat LegacyExpandableEditor">
  <div class="ProseMirror-shell">
    <div class="file-chip">report.pdf</div>
    <div contenteditable="true" role="textbox" aria-label="Work with ChatGPT on your tasks"
      style="min-height:40px;width:200px" id="main"></div>
  </div>
</div>
</body></html>"""
    pre_js = _work_native_preflight_js(
        expected_url="https://chatgpt.com/c/abc",
        text="wake",
        draft_writer="orion:test",
        preserve_newlines=False,
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://chatgpt.com/c/abc", wait_until="domcontentloaded")
        page.set_content(html)
        page.evaluate(
            "() => { document.getElementById('main').pmViewDesc = {}; "
            "window.__ulgOperatorActivity = {}; }"
        )
        result = page.evaluate(pre_js)
        browser.close()
    assert result.get("ok") is False
    assert result.get("reason") == "foreign_attachment"


@_PLAYWRIGHT_SKIP
def test_dom_set_signals_work_native_unsupported_before_mutation() -> None:
    """Hydrated Work without setContent refuses with work_native_unsupported."""
    from playwright.sync_api import sync_playwright

    html = """<!DOCTYPE html><html><body>
<div contenteditable="true" role="textbox" aria-label="Work with ChatGPT on your tasks"
  style="min-height:40px;width:200px" id="main"></div>
</body></html>"""
    ins_js = _set_composer_body(
        '[contenteditable="true"][role="textbox"]',
        "wake line",
        draft_writer="orion:test",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        page.evaluate(
            """() => {
              const el = document.getElementById('main');
              el.pmViewDesc = {};
              window.__ulgOperatorActivity = {};
            }"""
        )
        result = page.evaluate(ins_js)
        text = page.evaluate("() => document.getElementById('main').innerText")
        browser.close()
    assert result.get("ok") is False
    assert result.get("reason") == "work_native_unsupported"
    assert text == ""
