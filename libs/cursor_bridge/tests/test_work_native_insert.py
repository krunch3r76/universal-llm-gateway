"""Guarded CDP native insert tests for hydrated ChatGPT Work composers that lack a supported setter API."""

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
    """A pre-input refusal must not send Input.insertText or claim a paste."""
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
    """Transport or readback uncertainty must not claim the draft was pasted."""
    fake = _FakeWS(
        preflight={"ok": True},
        readback={"ok": False, "reason": "composer_text_mismatch"},
    )

    async def _eval(ws: Any, mid: int, expression: str) -> Any:
        if mid == 1:
            return None
        if mid == 2:
            return fake._preflight
        if mid == 3:
            return {"ok": True}
        if mid == 5:
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
    """One Input.insertText, and the prior eval is the guarded gate."""
    fake = _FakeWS(
        preflight={"ok": True},
        readback={"ok": True, "phase": "inserted", "inserted": True, "pasted": True},
    )
    eval_mids: list[int] = []
    eval_exprs: dict[int, str] = {}

    async def _eval(ws: Any, mid: int, expression: str) -> Any:
        eval_mids.append(mid)
        eval_exprs[mid] = expression
        if mid == 1:
            return None
        if mid == 2:
            return fake._preflight
        if mid == 3:
            return {"ok": True}
        if mid == 5:
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
    gate_expr = eval_exprs[3]
    assert "ulgResolveVisibleWorkComposer" in gate_expr
    assert "suppressUntilMs" in gate_expr
    assert "ulgHasPopulatedAttachment" in gate_expr
    assert eval_mids[eval_mids.index(3) + 1] == 5


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
    from cursor_bridge.cdp_composer_input import _work_native_preflight_js
    from playwright.sync_api import sync_playwright

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
    """A hydrated Work editor without setContent refuses with work_native_unsupported before mutation."""
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


@pytest.mark.asyncio
async def test_work_native_insert_ack_error_is_insertion_unknown(monkeypatch) -> None:
    """CDP Input acknowledgement error after dispatch → insertion_unknown, no retry."""
    fake = _FakeWS(
        preflight={"ok": True},
        readback={"ok": True, "pasted": True, "inserted": True},
    )
    fake._recv_q = [{"id": 4, "error": {"message": "insert rejected"}}]

    async def _eval(ws: Any, mid: int, expression: str) -> Any:
        if mid == 1:
            return None
        if mid == 2:
            return fake._preflight
        if mid == 3:
            return {"ok": True}
        if mid == 5:
            return None
        return None

    monkeypatch.setattr("cursor_bridge.cdp_composer_input._runtime_eval", _eval)

    class _Connect:
        async def __aenter__(self) -> _FakeWS:
            return fake

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr("websockets.connect", lambda *a, **kw: _Connect())

    out = await cdp_work_native_insert_text(
        "ws://fake",
        text="hello",
        expected_url="https://chatgpt.com/c/abc",
        draft_writer="w",
    )
    assert out.get("insertion_unknown") is True
    assert out.get("pasted") is False
    assert out["reason"] == "native_insert_error"


@pytest.mark.asyncio
async def test_work_native_readback_transport_is_insertion_unknown(monkeypatch) -> None:
    """A readback evaluate failure after Input must not assign draft ownership."""
    fake = _FakeWS(preflight={"ok": True}, readback={"ok": True})

    async def _eval(ws: Any, mid: int, expression: str) -> Any:
        if mid == 1:
            return None
        if mid == 2:
            return fake._preflight
        if mid == 3:
            return {"ok": True}
        if mid == 5:
            return None
        if mid == 6:
            raise TimeoutError("readback stalled")
        return None

    monkeypatch.setattr("cursor_bridge.cdp_composer_input._runtime_eval", _eval)

    class _Connect:
        async def __aenter__(self) -> _FakeWS:
            return fake

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr("websockets.connect", lambda *a, **kw: _Connect())

    out = await cdp_work_native_insert_text(
        "ws://fake",
        text="hello",
        expected_url="https://chatgpt.com/c/abc",
        draft_writer="w",
    )
    assert out.get("insertion_unknown") is True
    assert out["reason"] == "readback_timeout"


@pytest.mark.asyncio
async def test_work_native_cleanup_failure_after_dispatch_unknown(monkeypatch) -> None:
    """Suppress clear failure after Input dispatch must fence as insertion_unknown."""
    fake = _FakeWS(preflight={"ok": True}, readback={"ok": True})

    async def _eval(ws: Any, mid: int, expression: str) -> Any:
        if mid == 1:
            return None
        if mid == 2:
            return fake._preflight
        if mid == 3:
            return {"ok": True}
        if mid == 5:
            raise RuntimeError("clear failed")
        return None

    monkeypatch.setattr("cursor_bridge.cdp_composer_input._runtime_eval", _eval)

    class _Connect:
        async def __aenter__(self) -> _FakeWS:
            return fake

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr("websockets.connect", lambda *a, **kw: _Connect())

    out = await cdp_work_native_insert_text(
        "ws://fake",
        text="hello",
        expected_url="https://chatgpt.com/c/abc",
        draft_writer="w",
    )
    assert out.get("insertion_unknown") is True
    assert out["reason"] == "native_insert_cleanup"


@_PLAYWRIGHT_SKIP
def test_work_native_preflight_url_mismatch_refuses() -> None:
    """Conversation URL drift before insert must refuse without sending Input.insertText."""
    from cursor_bridge.cdp_composer_input import _work_native_preflight_js
    from playwright.sync_api import sync_playwright

    html = """<!DOCTYPE html><html><body>
<div contenteditable="true" role="textbox" aria-label="Work with ChatGPT on your tasks"
  style="min-height:40px;width:200px" id="main"></div>
</body></html>"""
    pre_js = _work_native_preflight_js(
        expected_url="https://chatgpt.com/c/expected",
        text="wake",
        draft_writer="w",
        preserve_newlines=False,
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://chatgpt.com/c/actual", wait_until="domcontentloaded")
        page.set_content(html)
        page.evaluate(
            "() => { document.getElementById('main').pmViewDesc = {}; "
            "window.__ulgOperatorActivity = {}; }"
        )
        result = page.evaluate(pre_js)
        browser.close()
    assert result.get("ok") is False
    assert result.get("reason") == "url_mismatch"


@_PLAYWRIGHT_SKIP
def test_work_native_readback_rejects_editor_identity_change() -> None:
    """Readback must not assign ownership if pinned editor no longer matches resolver."""
    from cursor_bridge.cdp_composer_input import _work_native_readback_js
    from playwright.sync_api import sync_playwright

    html = """<!DOCTYPE html><html><body>
<div contenteditable="true" role="textbox" aria-label="Work with ChatGPT on your tasks"
  style="min-height:40px;width:200px" id="main">hello</div>
</body></html>"""
    rb_js = _work_native_readback_js(
        text="hello",
        draft_writer="w",
        preserve_newlines=False,
        expected_url="https://chatgpt.com/c/abc",
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://chatgpt.com/c/abc", wait_until="domcontentloaded")
        page.set_content(html)
        page.evaluate(
            """() => {
              const el = document.getElementById('main');
              window.__ulgWorkNativeEditor = document.createElement('div');
              window.__ulgOperatorActivity = {};
            }"""
        )
        result = page.evaluate(rb_js)
        browser.close()
    assert result.get("ok") is False
    assert result.get("reason") == "editor_changed"


@pytest.mark.asyncio
async def test_work_native_success_assigns_writer_only_after_readback(monkeypatch) -> None:
    """Ownership fields are set only when readback confirms exact text."""
    fake = _FakeWS(
        preflight={"ok": True},
        readback={
            "ok": True,
            "pasted": True,
            "inserted": True,
            "phase": "inserted",
            "sig": "hello",
        },
    )

    async def _eval(ws: Any, mid: int, expression: str) -> Any:
        if mid == 1:
            return None
        if mid == 2:
            return fake._preflight
        if mid == 3:
            return {"ok": True}
        if mid == 5:
            return None
        if mid == 6:
            return fake._readback
        return None

    monkeypatch.setattr("cursor_bridge.cdp_composer_input._runtime_eval", _eval)

    class _Connect:
        async def __aenter__(self) -> _FakeWS:
            return fake

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr("websockets.connect", lambda *a, **kw: _Connect())

    out = await cdp_work_native_insert_text(
        "ws://fake",
        text="hello",
        expected_url="https://chatgpt.com/c/abc",
        draft_writer="orion:lane",
    )
    assert out.get("ok") is True
    assert out.get("pasted") is True
    assert out.get("sig") == "hello"
    assert len([m for m in fake.sent if m.get("method") == "Input.insertText"]) == 1


def _eval_native_page(html: str, expression: str, *, url: str, setup: str = "") -> Any:
    """Run production page JS in a disposable browser at ``url``."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.set_content(html)
        if setup:
            page.evaluate(setup)
        value = page.evaluate(expression)
        browser.close()
    return value


_NATIVE_EDITOR_HTML = """<!DOCTYPE html><html><body>
<div class="composer-layout">
  <div class="text-size-chat">
    <div class="ProseMirror-shell">
      <div contenteditable="true" role="textbox"
        aria-label="Work with ChatGPT on your tasks"
        style="min-height:40px;width:200px" id="main"></div>
    </div>
  </div>
  <div class="ComposerLayoutFooter" id="footer"></div>
</div>
<textarea id="other"></textarea>
</body></html>"""

_NATIVE_READY = (
    "() => { const el = document.getElementById('main');"
    " el.pmViewDesc = {}; window.__ulgWorkNativeEditor = el;"
    " window.__ulgOperatorActivity = {}; el.focus(); }"
)


@pytest.mark.asyncio
async def test_work_native_gate_refusal_does_not_insert(monkeypatch) -> None:
    """A failing last-eval gate is pre-input: no Input.insertText and not unknown."""
    fake = _FakeWS(
        preflight={"ok": True},
        readback={"ok": True, "pasted": True},
    )

    async def _eval(ws: Any, mid: int, expression: str) -> Any:
        if mid == 2:
            return fake._preflight
        if mid == 3:
            return {"ok": False, "reason": "editor_changed"}
        return None

    monkeypatch.setattr("cursor_bridge.cdp_composer_input._runtime_eval", _eval)

    class _Connect:
        async def __aenter__(self) -> _FakeWS:
            return fake

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr("websockets.connect", lambda *a, **kw: _Connect())
    out = await cdp_work_native_insert_text(
        "ws://fake",
        text="hello",
        expected_url="https://chatgpt.com/c/abc",
        draft_writer="w",
    )
    assert out["ok"] is False
    assert out["reason"] == "editor_changed"
    assert out.get("insertion_unknown") is not True
    assert out.get("pasted") is False
    assert not any(m.get("method") == "Input.insertText" for m in fake.sent)


@pytest.mark.asyncio
async def test_work_native_cancel_after_insert_does_not_send_again(monkeypatch) -> None:
    """Cancellation during readback leaves one insert and does not claim pasted."""
    fake = _FakeWS(preflight={"ok": True}, readback={"ok": True, "pasted": True})

    async def _eval(ws: Any, mid: int, expression: str) -> Any:
        if mid == 2:
            return fake._preflight
        if mid == 3:
            return {"ok": True}
        if mid == 5:
            return None
        if mid == 6:
            raise asyncio.CancelledError()
        return None

    monkeypatch.setattr("cursor_bridge.cdp_composer_input._runtime_eval", _eval)

    class _Connect:
        async def __aenter__(self) -> _FakeWS:
            return fake

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr("websockets.connect", lambda *a, **kw: _Connect())
    with pytest.raises(asyncio.CancelledError):
        await cdp_work_native_insert_text(
            "ws://fake",
            text="hello",
            expected_url="https://chatgpt.com/c/abc",
            draft_writer="w",
        )
    assert len([m for m in fake.sent if m.get("method") == "Input.insertText"]) == 1


@_PLAYWRIGHT_SKIP
def test_work_native_footer_file_refuses_and_empty_attach_allows() -> None:
    """A footer file chip outside the shell refuses; an empty Attach button does not."""
    from cursor_bridge.cdp_composer_input import _work_native_preflight_js

    footer = """<!DOCTYPE html><html><body>
<div class="composer-layout">
  <div class="text-size-chat">
    <div class="ProseMirror-shell">
      <div contenteditable="true" role="textbox"
        aria-label="Work with ChatGPT on your tasks"
        style="min-height:40px;width:200px" id="main"></div>
    </div>
  </div>
  <div class="ComposerLayoutFooter"><div class="file-chip">notes.txt</div></div>
</div>
</body></html>"""
    attach = """<!DOCTYPE html><html><body>
<div class="composer-layout">
  <div class="text-size-chat">
    <div class="ProseMirror-shell">
      <button data-testid="composer-attachment" class="attachment">Attach</button>
      <div contenteditable="true" role="textbox"
        aria-label="Work with ChatGPT on your tasks"
        style="min-height:40px;width:200px" id="main"></div>
    </div>
  </div>
  <div class="ComposerLayoutFooter"></div>
</div>
</body></html>"""
    outside = """<!DOCTYPE html><html><body>
<div class="file-chip">secret.pdf</div>
<div class="composer-layout">
  <div class="text-size-chat">
    <div class="ProseMirror-shell">
      <div contenteditable="true" role="textbox"
        aria-label="Work with ChatGPT on your tasks"
        style="min-height:40px;width:200px" id="main"></div>
    </div>
  </div>
  <div class="ComposerLayoutFooter"></div>
</div>
</body></html>"""
    pre = _work_native_preflight_js(
        expected_url="https://chatgpt.com/c/abc",
        text="wake",
        draft_writer="orion:test",
        preserve_newlines=False,
    )
    url = "https://chatgpt.com/c/abc"
    refused = _eval_native_page(footer, pre, url=url, setup=_NATIVE_READY)
    assert refused.get("ok") is False
    assert refused.get("reason") == "foreign_attachment"
    allowed = _eval_native_page(attach, pre, url=url, setup=_NATIVE_READY)
    assert allowed.get("ok") is True
    bounded = _eval_native_page(outside, pre, url=url, setup=_NATIVE_READY)
    assert bounded.get("ok") is True


@_PLAYWRIGHT_SKIP
def test_work_native_gate_refuses_focus_url_and_editor_drift() -> None:
    """The last eval refuses when focus, URL, or the visible editor drifts."""
    from cursor_bridge.cdp_composer_input import _work_native_insert_gate_js

    gate = _work_native_insert_gate_js(
        expected_url="https://chatgpt.com/c/abc",
        text="wake",
        draft_writer="orion:test",
        preserve_newlines=False,
    )
    url = "https://chatgpt.com/c/abc"
    focused = _eval_native_page(
        _NATIVE_EDITOR_HTML, gate, url=url, setup=_NATIVE_READY
    )
    assert focused.get("ok") is True
    stolen = _eval_native_page(
        _NATIVE_EDITOR_HTML,
        gate,
        url=url,
        setup=(
            "() => { const el = document.getElementById('main');"
            " el.pmViewDesc = {}; window.__ulgWorkNativeEditor = el;"
            " window.__ulgOperatorActivity = {};"
            " document.getElementById('other').focus(); }"
        ),
    )
    assert stolen.get("ok") is False
    assert stolen.get("reason") == "focus_failed"
    replaced = """<!DOCTYPE html><html><body>
<div contenteditable="true" role="textbox" aria-label="Work with ChatGPT on your tasks"
  style="width:200px;height:30px" id="main"></div>
<div contenteditable="true" role="textbox" aria-label="Work with ChatGPT on your tasks"
  style="width:200px;height:30px" id="second"></div>
</body></html>"""
    ambiguous = _eval_native_page(
        replaced,
        gate,
        url=url,
        setup=(
            "() => { const el = document.getElementById('main');"
            " el.pmViewDesc = {}; document.getElementById('second').pmViewDesc = {};"
            " window.__ulgWorkNativeEditor = el; window.__ulgOperatorActivity = {};"
            " el.focus(); }"
        ),
    )
    assert ambiguous.get("ok") is False
    assert ambiguous.get("reason") == "ambiguous_work_composer"
    wrong_url = _eval_native_page(
        _NATIVE_EDITOR_HTML,
        gate,
        url="https://chatgpt.com/c/other",
        setup=_NATIVE_READY,
    )
    assert wrong_url.get("ok") is False
    assert wrong_url.get("reason") == "url_mismatch"
