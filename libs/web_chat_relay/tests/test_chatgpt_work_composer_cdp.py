"""ChatGPT work composer CDP path — native review AC (578bbc72)."""

from __future__ import annotations

import json
import unittest.mock as mock

import pytest
from cursor_bridge.dom_composer_paste import (
    _ULG_NORM_COMPOSER_JS,
    _set_composer_body,
    dom_click_send,
)
from playwright.async_api import async_playwright
from web_chat_relay.chatgpt_work_composer_cdp import (
    chatgpt_work_insert_and_send,
    chatgpt_work_pre_insert_expression,
    chatgpt_work_preflight_expression,
)

_PM = "div.ProseMirror[contenteditable='true']"
_CONV = "6aed2b2e-1b88-83e8-b7ce-c8eb6c56b623"


def _empty_pm(*, wrap_form: bool = False, enable_send_on_input: bool = True) -> str:
    script = ""
    if enable_send_on_input:
        script = """
<script>
document.querySelector('.ProseMirror').addEventListener('input', () => {
  const b = document.querySelector('button[aria-label=\"Send prompt\"]');
  if (b) b.disabled = false;
});
</script>
"""
    inner = f"""
<div class="ProseMirror" contenteditable="true" role="textbox">
  <p data-empty-paragraph="true"><br class="ProseMirror-trailingBreak"></p>
</div>
<button aria-label="Send prompt" type="button" disabled>Send</button>
{script}
"""
    if wrap_form:
        return f"<form>{inner}</form>"
    return inner


async def _runtime(page, expression: str, *, await_promise: bool = False) -> object:
    session = getattr(page, "_ulg_cdp", None)
    if session is None:
        session = await page.context.new_cdp_session(page)
        page._ulg_cdp = session
    raw = await session.send(
        "Runtime.evaluate",
        {
            "expression": expression.strip(),
            "returnByValue": True,
            "awaitPromise": await_promise,
        },
    )
    if raw.get("exceptionDetails"):
        raise AssertionError(str(raw["exceptionDetails"]))
    res = raw.get("result") or {}
    if "value" in res:
        return res["value"]
    return (res.get("result") or {}).get("value")


async def _preflight(page, html: str, *, line: str = "wake", cid: str = _CONV) -> dict:
    await page.goto(f"https://chatgpt.com/c/{cid}")
    await page.set_content(html)
    expr = chatgpt_work_preflight_expression(
        selector=_PM,
        line=line,
        preserve_newlines=False,
        expected_conversation_id=cid,
    )
    out = await _runtime(page, expr)
    assert isinstance(out, dict)
    return out


@pytest.mark.asyncio
async def test_structural_empty_with_form_wrapper() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        state = await _preflight(page, _empty_pm(wrap_form=True))
        await browser.close()
    assert state.get("ok") is True
    assert state.get("state") == "empty"


@pytest.mark.asyncio
async def test_structural_empty_without_form() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        state = await _preflight(page, _empty_pm(wrap_form=False))
        await browser.close()
    assert state.get("ok") is True
    assert state.get("state") == "empty"


@pytest.mark.asyncio
async def test_rejects_nbsp_in_text_node() -> None:
    html = _empty_pm().replace("</p>", "\u00a0</p>")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        state = await _preflight(page, html)
        await browser.close()
    assert state.get("reason") == "composer_not_empty"


@pytest.mark.asyncio
async def test_rejects_literal_space_in_placeholder_text_node() -> None:
    html = _empty_pm().replace(
        '<p data-empty-paragraph="true">',
        '<p data-empty-paragraph="true"> ',
    )
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        state = await _preflight(page, html)
        await browser.close()
    assert state.get("reason") == "composer_not_empty"


@pytest.mark.asyncio
async def test_rejects_two_paragraph_blocks() -> None:
    html = """
<div class="ProseMirror" contenteditable="true">
  <p data-empty-paragraph="true"><br class="ProseMirror-trailingBreak"></p>
  <p data-empty-paragraph="true"><br class="ProseMirror-trailingBreak"></p>
</div>
"""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        state = await _preflight(page, html)
        await browser.close()
    assert state.get("reason") == "composer_not_empty"


@pytest.mark.asyncio
async def test_rejects_multiple_br_in_block() -> None:
    html = """
<div class="ProseMirror" contenteditable="true">
  <p><br><br></p>
</div>
"""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        state = await _preflight(page, html)
        await browser.close()
    assert state.get("reason") == "composer_not_empty"


@pytest.mark.asyncio
async def test_rejects_atom_inside_composer() -> None:
    html = """
<div class="ProseMirror" contenteditable="true">
  <p data-empty-paragraph="true"><atom>chip</atom><br class="ProseMirror-trailingBreak"></p>
</div>
"""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        state = await _preflight(page, html)
        await browser.close()
    assert state.get("reason") == "composer_not_empty"


@pytest.mark.asyncio
async def test_rejects_attachment_chip_in_form_even_when_draft_equals_line() -> None:
    line = "wake line"
    html = f"""
<form>
<div class="ProseMirror" contenteditable="true">{line}</div>
<div data-testid="attachment-chip">file.pdf</div>
<button aria-label="Send prompt" type="button">Send</button>
</form>
"""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"https://chatgpt.com/c/{_CONV}")
        await page.set_content(html)
        await page.evaluate(
            f"window.__ulgOperatorActivity={{ourDraftSig:{line!r}}};"
        )
        expr = chatgpt_work_preflight_expression(
            selector=_PM,
            line=line,
            preserve_newlines=False,
            expected_conversation_id=_CONV,
        )
        state = await _runtime(page, expr)
        await browser.close()
    assert state.get("ok") is False
    assert state.get("reason") == "composer_not_empty"


@pytest.mark.asyncio
async def test_human_same_wake_text_without_our_draft_sig_refuses() -> None:
    line = "ORION-22388-BOUND"
    html = f'<div class="ProseMirror" contenteditable="true"><p>{line}</p></div>'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        state = await _preflight(page, html, line=line)
        await browser.close()
    assert state.get("reason") == "composer_not_owned"


@pytest.mark.asyncio
async def test_owned_draft_requires_matching_our_draft_sig() -> None:
    line = "same"
    html = f'<div class="ProseMirror" contenteditable="true">{line}</div>'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"https://chatgpt.com/c/{_CONV}")
        await page.set_content(html)
        await page.evaluate("window.__ulgOperatorActivity={ourDraftSig:'same'};")
        expr = chatgpt_work_preflight_expression(
            selector=_PM,
            line=line,
            preserve_newlines=False,
            expected_conversation_id=_CONV,
        )
        state = await _runtime(page, expr)
        await browser.close()
    assert state.get("state") == "already_inserted"


@pytest.mark.asyncio
async def test_stale_bridge_wake_line_without_marker_is_not_empty() -> None:
    line = "BRIDGE_WAKE thread=22388 turn=1"
    html = f'<div class="ProseMirror" contenteditable="true"><p>{line}</p></div>'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        state = await _preflight(page, html, line="other")
        await browser.close()
    assert state.get("reason") == "composer_not_empty"


@pytest.mark.asyncio
async def test_conversation_mismatch_refuses() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://chatgpt.com/c/00000000-0000-0000-0000-000000000001")
        await page.set_content(_empty_pm())
        expr = chatgpt_work_preflight_expression(
            selector=_PM,
            line="wake",
            preserve_newlines=False,
            expected_conversation_id=_CONV,
        )
        state = await _runtime(page, expr)
        await browser.close()
    assert state.get("reason") == "conversation_mismatch"


@pytest.mark.asyncio
async def test_selector_fallback_finds_prosemirror_without_prompt_textarea() -> None:
    html = _empty_pm()
    selector = "#prompt-textarea, div.ProseMirror[contenteditable='true']"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"https://chatgpt.com/c/{_CONV}")
        await page.set_content(html)
        expr = chatgpt_work_preflight_expression(
            selector=selector,
            line="wake",
            preserve_newlines=False,
            expected_conversation_id=_CONV,
        )
        state = await _runtime(page, expr)
        await browser.close()
    assert state.get("ok") is True
    assert "ProseMirror" in str(state.get("matchedSelector") or "")


@pytest.mark.asyncio
async def test_pre_insert_refuses_when_other_control_focused() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"https://chatgpt.com/c/{_CONV}")
        await page.set_content(
            _empty_pm()
            + '<textarea id="other">x</textarea>'
        )
        await page.evaluate(
            """() => {
            const el = document.querySelector('div.ProseMirror');
            window.__ulgChatgptWorkComposer = { matchedSelector: '', composer: el };
            document.getElementById('other').focus();
            }"""
        )
        out = await _runtime(
            page,
            chatgpt_work_pre_insert_expression(line="wake", preserve_newlines=False),
        )
        await browser.close()
    assert out.get("reason") == "composer_not_focused_other_control"


@pytest.mark.asyncio
async def test_preflight_refuses_non_collapsed_selection() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"https://chatgpt.com/c/{_CONV}")
        await page.set_content(_empty_pm())
        await page.evaluate(
            """() => {
            const el = document.querySelector('div.ProseMirror');
            el.focus();
            const r = document.createRange();
            r.selectNodeContents(el);
            const s = window.getSelection();
            s.removeAllRanges();
            s.addRange(r);
            }"""
        )
        expr = chatgpt_work_preflight_expression(
            selector=_PM,
            line="wake",
            preserve_newlines=False,
            expected_conversation_id=_CONV,
        )
        out = await _runtime(page, expr)
        await browser.close()
    assert out.get("reason") == "composer_selection_active"


@pytest.mark.asyncio
async def test_foreign_draft_zero_insert_via_insert_and_send() -> None:
    insert_calls: list[str] = []

    class _Ws:
        def __init__(self, page):
            self.page = page
            self.session = None
            self._pending: dict[int, dict] = {}

        async def __aenter__(self):
            self.session = await self.page.context.new_cdp_session(self.page)
            return self

        async def __aexit__(self, *_a):
            return None

        async def send(self, data: str) -> None:
            msg = json.loads(data)
            self._pending[msg["id"]] = msg

        async def recv(self) -> str:
            mid = min(self._pending)
            msg = self._pending.pop(mid)
            method = msg.get("method")
            if method == "Runtime.evaluate":
                params = msg.get("params") or {}
                val = await _runtime(
                    self.page,
                    params["expression"],
                    await_promise=bool(params.get("awaitPromise", False)),
                )
                return json.dumps({"id": mid, "result": {"result": {"value": val}}})
            if method == "Input.insertText":
                insert_calls.append(msg["params"]["text"])
                return json.dumps({"id": mid, "result": {}})
            return json.dumps({"id": mid, "result": {}})

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"https://chatgpt.com/c/{_CONV}")
        await page.set_content(
            '<div class="ProseMirror" contenteditable="true"><p>foreign</p></div>'
        )
        with mock.patch("websockets.connect", return_value=_Ws(page)):
            out = await chatgpt_work_insert_and_send(
                "ws://fake",
                selector=_PM,
                line="wake",
                expected_conversation_id=_CONV,
            )
        await browser.close()
    assert not insert_calls
    assert out.get("reason") == "composer_not_empty"


@pytest.mark.asyncio
async def test_insert_outcome_unknown_after_insert_when_readback_fails() -> None:
    with mock.patch(
        "web_chat_relay.chatgpt_work_composer_cdp._input_insert_text_ws",
        return_value={"ok": True},
    ), mock.patch(
        "web_chat_relay.chatgpt_work_composer_cdp._runtime_eval_ws",
        side_effect=[
            None,
            {"ok": True, "state": "empty", "matchedSelector": _PM},
            {"ok": True},
            None,
            None,
            {"ok": False, "reason": "insert_readback_mismatch"},
        ],
    ), mock.patch("websockets.connect") as conn:
        conn.return_value.__aenter__ = mock.AsyncMock(return_value=object())
        conn.return_value.__aexit__ = mock.AsyncMock(return_value=None)
        out = await chatgpt_work_insert_and_send(
            "ws://fake", selector=_PM, line="x", expected_conversation_id=_CONV
        )
    assert out.get("reason") == "insert_outcome_unknown"
    assert out.get("insert_attempted") is True
    assert out.get("retryable") is False


@pytest.mark.asyncio
async def test_cdp_protocol_error_on_input_is_not_retryable() -> None:
    with mock.patch(
        "web_chat_relay.chatgpt_work_composer_cdp._runtime_eval_ws",
        side_effect=[
            None,
            {"ok": True, "state": "empty", "matchedSelector": _PM},
            {"ok": True},
            {"ok": True},
            {
                "ok": False,
                "reason": "cdp_protocol_error",
                "insert_attempted": True,
                "retryable": False,
            },
        ],
    ), mock.patch(
        "web_chat_relay.chatgpt_work_composer_cdp._input_insert_text_ws",
        return_value={
            "ok": False,
            "reason": "cdp_protocol_error",
            "insert_attempted": True,
            "retryable": False,
        },
    ), mock.patch("websockets.connect") as conn:
        conn.return_value.__aenter__ = mock.AsyncMock(return_value=object())
        conn.return_value.__aexit__ = mock.AsyncMock(return_value=None)
        out = await chatgpt_work_insert_and_send(
            "ws://fake", selector=_PM, line="x", expected_conversation_id=_CONV
        )
    assert out.get("retryable") is False
    assert out.get("insert_attempted") is True


@pytest.mark.asyncio
async def test_dom_click_send_clicks_send_prompt_without_mock() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            '<div id="c" contenteditable="true">hello</div>'
            '<button aria-label="Send prompt" type="button">Send</button>'
        )

        async def _bridge(
            ws_url: str, expression: str, *, await_promise: bool = False, **_k: object
        ) -> object:
            del ws_url
            return await _runtime(page, expression, await_promise=await_promise)

        with mock.patch("cursor_bridge.dom_composer_paste._cdp_eval", side_effect=_bridge):
            out = await dom_click_send("ws://fake", selector="#c", line="hello")
        await browser.close()
    assert out.get("ok") is True
    assert out.get("send_clicked") is True


@pytest.mark.asyncio
async def test_full_insert_and_send_one_cdp_insert_real_send_click() -> None:
    insert_calls: list[str] = []

    class _Ws:
        def __init__(self, page):
            self.page = page
            self._pending: dict[int, dict] = {}

        async def __aenter__(self):
            self.session = await self.page.context.new_cdp_session(self.page)
            return self

        async def __aexit__(self, *_a):
            return None

        async def send(self, data: str) -> None:
            msg = json.loads(data)
            self._pending[msg["id"]] = msg

        async def recv(self) -> str:
            mid = min(self._pending)
            msg = self._pending.pop(mid)
            method = msg.get("method")
            if method == "Runtime.evaluate":
                params = msg.get("params") or {}
                val = await _runtime(
                    self.page,
                    params["expression"],
                    await_promise=bool(params.get("awaitPromise", False)),
                )
                return json.dumps({"id": mid, "result": {"result": {"value": val}}})
            if method == "Input.insertText":
                text = msg["params"]["text"]
                insert_calls.append(text)
                await self.session.send("Input.insertText", {"text": text})
                return json.dumps({"id": mid, "result": {}})
            return json.dumps({"id": mid, "result": {}})

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"https://chatgpt.com/c/{_CONV}")
        await page.set_content(_empty_pm(enable_send_on_input=True))

        async def _bridge(
            ws_url: str, expression: str, *, await_promise: bool = False, **_k: object
        ) -> object:
            del ws_url
            return await _runtime(page, expression, await_promise=await_promise)

        with mock.patch("websockets.connect", return_value=_Ws(page)), mock.patch(
            "cursor_bridge.dom_composer_paste._cdp_eval", side_effect=_bridge
        ):
            out = await chatgpt_work_insert_and_send(
                "ws://fake",
                selector=_PM,
                line="ORION-TEST",
                expected_conversation_id=_CONV,
            )
        await browser.close()

    assert len(insert_calls) == 1
    assert out.get("ok") is True, out


@pytest.mark.asyncio
async def test_send_disabled_forever_retains_owned_draft_pending() -> None:
    class _Ws:
        def __init__(self, page):
            self.page = page
            self._pending: dict[int, dict] = {}

        async def __aenter__(self):
            self.session = await self.page.context.new_cdp_session(self.page)
            return self

        async def __aexit__(self, *_a):
            return None

        async def send(self, data: str) -> None:
            self._pending[json.loads(data)["id"]] = json.loads(data)

        async def recv(self) -> str:
            mid = min(self._pending)
            msg = self._pending.pop(mid)
            if msg.get("method") == "Runtime.evaluate":
                params = msg.get("params") or {}
                val = await _runtime(
                    self.page,
                    params["expression"],
                    await_promise=bool(params.get("awaitPromise", False)),
                )
                return json.dumps({"id": mid, "result": {"result": {"value": val}}})
            if msg.get("method") == "Input.insertText":
                await self.session.send("Input.insertText", msg["params"])
                return json.dumps({"id": mid, "result": {}})
            return json.dumps({"id": mid, "result": {}})

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"https://chatgpt.com/c/{_CONV}")
        await page.set_content(_empty_pm(enable_send_on_input=False))

        async def _bridge(
            ws_url: str, expression: str, *, await_promise: bool = False, **_k: object
        ) -> object:
            del ws_url
            return await _runtime(page, expression, await_promise=await_promise)

        with mock.patch("websockets.connect", return_value=_Ws(page)), mock.patch(
            "cursor_bridge.dom_composer_paste._cdp_eval", side_effect=_bridge
        ):
            out = await chatgpt_work_insert_and_send(
                "ws://fake",
                selector=_PM,
                line="OWNED-LINE",
                expected_conversation_id=_CONV,
            )
        draft = await page.evaluate(
            "document.querySelector('div.ProseMirror').innerText.trim()"
        )
        await browser.close()

    assert out.get("reason") == "send_button_disabled"
    assert out.get("insert_attempted") is True
    assert out.get("retryable") is True
    assert draft == "OWNED-LINE"


@pytest.mark.asyncio
async def test_pre_insert_rejects_nonempty_exact_line_human_draft() -> None:
    line = "exact human line"
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"https://chatgpt.com/c/{_CONV}")
        await page.set_content(
            f'<div class="ProseMirror" contenteditable="true"><p>{line}</p></div>'
        )
        await page.evaluate(
            """() => {
            window.__ulgChatgptWorkComposer = {
              composer: document.querySelector('div.ProseMirror'),
              matchedSelector: 'div.ProseMirror',
            };
            }"""
        )
        out = await _runtime(
            page,
            chatgpt_work_pre_insert_expression(
                line=line,
                preserve_newlines=False,
                expected_conversation_id=_CONV,
            ),
        )
        await browser.close()
    assert out.get("reason") == "composer_not_empty"


@pytest.mark.asyncio
async def test_positive_attachment_chip_outside_form_refuses() -> None:
    html = """
<div class="ProseMirror" contenteditable="true">
  <p data-empty-paragraph="true"><br class="ProseMirror-trailingBreak"></p>
</div>
<div data-testid="attachment-chip">uploaded.pdf</div>
"""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        state = await _preflight(page, html)
        await browser.close()
    assert state.get("reason") == "composer_not_empty"


OBSERVED_NO_EDITOR_HTML = """
<div class="ProseMirror" contenteditable="true" role="textbox">
  <p><br class="ProseMirror-trailingBreak"></p>
</div>
<button aria-label="Send prompt" type="button">Send</button>
"""


@pytest.mark.asyncio
async def test_observed_no_editor_div_empty_passes_ulg_composer_dom_empty() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        empty = await page.evaluate(
            "(() => {\n"
            + _ULG_NORM_COMPOSER_JS
            + """
              const el = document.createElement('div');
              el.setAttribute('contenteditable', 'true');
              el.innerHTML = '<p><br class="ProseMirror-trailingBreak"></p>';
              Object.defineProperty(el, 'innerText', { get() { return '\\n'; } });
              return ulgComposerDomEmpty(el);
            })()"""
        )
        await browser.close()
    assert empty is True


@pytest.mark.asyncio
async def test_observed_no_editor_div_generic_insert_refuses_without_mutation() -> None:
    line = "WAKE — lane probe"
    setter = _set_composer_body(_PM, line, preserve_newlines=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        row = await page.evaluate(
            """({ norm, js }) => {
              document.body.innerHTML = '';
              const el = document.createElement('div');
              el.className = 'ProseMirror';
              el.setAttribute('contenteditable', 'true');
              el.innerHTML = '<p><br class="ProseMirror-trailingBreak"></p>';
              Object.defineProperty(el, 'innerText', { get() { return '\\n'; } });
              document.body.appendChild(el);
              const before = el.innerHTML;
              window.__ulgOperatorActivity = {};
              const ret = eval(js);
              return { ret, before, after: el.innerHTML, calls: el.editor ? 1 : 0 };
            }""",
            {"norm": _ULG_NORM_COMPOSER_JS, "js": setter},
        )
        await browser.close()
    assert row["ret"]["reason"] == "insertion_failed"
    assert row["ret"]["inserted"] is False
    assert row["before"] == row["after"]


@pytest.mark.asyncio
async def test_observed_no_editor_div_chatgpt_work_inserts_once_with_readback() -> None:
    line = "ORION-NO-EDITOR"
    insert_calls: list[str] = []

    class _Ws:
        def __init__(self, page: object) -> None:
            self.page = page
            self._pending: dict[int, dict] = {}

        async def __aenter__(self) -> _Ws:
            self.session = await self.page.context.new_cdp_session(self.page)
            return self

        async def __aexit__(self, *_a: object) -> None:
            return None

        async def send(self, data: str) -> None:
            msg = json.loads(data)
            self._pending[msg["id"]] = msg

        async def recv(self) -> str:
            mid = min(self._pending)
            msg = self._pending.pop(mid)
            method = msg.get("method")
            if method == "Runtime.evaluate":
                params = msg.get("params") or {}
                val = await _runtime(
                    self.page,
                    params["expression"],
                    await_promise=bool(params.get("awaitPromise", False)),
                )
                return json.dumps({"id": mid, "result": {"result": {"value": val}}})
            if method == "Input.insertText":
                text = str((msg.get("params") or {}).get("text") or "")
                insert_calls.append(text)
                await self.session.send("Input.insertText", {"text": text})
                return json.dumps({"id": mid, "result": {}})
            return json.dumps({"id": mid, "result": {}})

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"https://chatgpt.com/c/{_CONV}")
        await page.set_content(OBSERVED_NO_EDITOR_HTML)

        async def _bridge(
            ws_url: str, expression: str, *, await_promise: bool = False, **_k: object
        ) -> object:
            del ws_url
            return await _runtime(page, expression, await_promise=await_promise)

        with mock.patch("websockets.connect", return_value=_Ws(page)), mock.patch(
            "cursor_bridge.dom_composer_paste._cdp_eval", side_effect=_bridge
        ):
            out = await chatgpt_work_insert_and_send(
                "ws://fake",
                selector=_PM,
                line=line,
                expected_conversation_id=_CONV,
            )
        draft = await page.evaluate(
            "document.querySelector('div.ProseMirror').innerText.trim()"
        )
        await browser.close()

    assert len(insert_calls) == 1
    assert insert_calls[0] == line
    assert out.get("ok") is True, out
    assert draft == line


@pytest.mark.asyncio
async def test_rejects_zwsp_and_tab_in_text_nodes() -> None:
    html = _empty_pm().replace(
        "<br class=\"ProseMirror-trailingBreak\">",
        "\t<br class=\"ProseMirror-trailingBreak\">",
    )
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        state = await _preflight(page, html)
        await browser.close()
    assert state.get("reason") == "composer_not_empty"
