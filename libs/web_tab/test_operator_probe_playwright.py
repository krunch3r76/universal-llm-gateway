"""Run operator probe / DOM composer JS in Playwright (no mocked probe reads)."""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cursor_bridge.dom_composer_paste import (
    INSTALL_OPERATOR_ACTIVITY_PROBE_JS,
    OPERATOR_PROBE_VERSION,
    _click_send_own_draft_body,
    _composer_send_permit_expression,
    _set_composer_body,
    composer_draft_sha256_hex,
    operator_busy_probe_js,
    probe_foreign_draft,
)
from playwright.async_api import Page as AsyncPage
from playwright.async_api import async_playwright
from playwright.sync_api import Page, sync_playwright

from web_tab.operator_guard import operator_active_probe
from web_tab.write_ops import execute_paste, execute_submit

_WS = "ws://probe-playwright/page"
_GROK_SELECTOR = "[data-testid='chat-input'] [contenteditable='true']"
_CHATGPT_SELECTOR = "#prompt-textarea"


@contextmanager
def _cap_operator_idle_wait_at_one_second() -> Iterator[None]:
    """Force wait_for_operator_idle's max_wait_s to 1s for this test."""
    from web_tab import operator_guard

    real_wait = operator_guard.wait_for_operator_idle

    async def _capped(*args: object, **kwargs: object) -> dict[str, Any]:
        kwargs["max_wait_s"] = 1.0
        return await real_wait(*args, **kwargs)

    with patch("web_tab.operator_guard.wait_for_operator_idle", _capped):
        yield


def _composer_html(
    *,
    draft: str = "",
    with_send: bool = True,
    site: str = "grok",
) -> str:
    btn = (
        '<button type="button" aria-label="Send message">Send</button>'
        if with_send
        else ""
    )
    if site == "chatgpt":
        inner = f'<div id="prompt-textarea" contenteditable="true" role="textbox">{draft}</div>'
        focus = "document.getElementById('prompt-textarea')"
    else:
        inner = (
            '<div data-testid="chat-input">'
            f'<div id="composer" contenteditable="true" aria-label="Prompt">{draft}</div>'
            "</div>"
        )
        focus = "document.getElementById('composer')"
    body = f"<form>{inner}{btn}</form>"
    return f"{body}<script>{focus}.focus();</script>"


@contextmanager
def playwright_page(html: str) -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        try:
            yield page
        finally:
            browser.close()


def _eval_js(page: Page, expression: str) -> Any:
    return page.evaluate(expression)


async def _shim_cdp_eval(
    ws_url: str,
    expression: str,
    *,
    await_promise: bool = False,
    timeout_s: float = 8.0,
    page: AsyncPage,
) -> Any:
    del ws_url, await_promise, timeout_s
    return await page.evaluate(expression)


def _cdp_eval_shim(page: AsyncPage) -> Callable[..., Awaitable[Any]]:
    async def shim(
        ws_url: str,
        expression: str,
        *,
        await_promise: bool = False,
        timeout_s: float = 8.0,
    ) -> Any:
        return await _shim_cdp_eval(
            ws_url,
            expression,
            await_promise=await_promise,
            timeout_s=timeout_s,
            page=page,
        )

    return shim


@pytest.fixture
def probe_page() -> Iterator[Page]:
    with playwright_page(_composer_html()) as page:
        yield page


def test_probe_reinstalls_when_version_changes(probe_page: Page) -> None:
    probe_page.evaluate(
        "(() => { window.__ulgOperatorActivity = { installed: true, probeVersion: 1, "
        "lastInputMs: 0, ourDraftSig: '' }; return true; })()"
    )
    out = _eval_js(probe_page, INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
    assert out.get("probeVersion") == OPERATOR_PROBE_VERSION
    state = probe_page.evaluate("() => window.__ulgOperatorActivity.probeVersion")
    assert state == OPERATOR_PROBE_VERSION


def test_trusted_keydown_sets_operator_active_in_probe_read(probe_page: Page) -> None:
    _eval_js(probe_page, INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
    probe_page.focus("#composer")
    probe_page.keyboard.press("a")
    probe = _eval_js(probe_page, operator_busy_probe_js(selector=_GROK_SELECTOR))
    assert probe.get("operator_active") is True


def test_untrusted_keydown_does_not_mark_operator_active(probe_page: Page) -> None:
    _eval_js(probe_page, INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
    probe_page.evaluate(
        "() => { document.getElementById('composer').dispatchEvent("
        "new KeyboardEvent('keydown', { bubbles: true, key: 'a' })); }"
    )
    probe = _eval_js(probe_page, operator_busy_probe_js(selector=_GROK_SELECTOR))
    assert probe.get("operator_active") is False


@pytest.mark.asyncio
async def test_trusted_keydown_marks_operator_active_and_probe_refuses() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_composer_html())
        await page.evaluate(INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
        await page.focus("#composer")
        await page.keyboard.press("a")
        with patch(
            "cursor_bridge.dom_composer_paste._cdp_eval",
            side_effect=_cdp_eval_shim(page),
        ):
            out = await operator_active_probe(
                _WS,
                selector=_GROK_SELECTOR,
                lock_holder=True,
                wait_for_idle=False,
            )
        await browser.close()
    assert out["ok"] is False
    assert out["code"] == "operator_active"


def test_dom_paste_untrusted_input_does_not_mark_operator_active(
    probe_page: Page,
) -> None:
    _eval_js(probe_page, INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
    paste_js = _set_composer_body(_GROK_SELECTOR, "hello agent")
    _eval_js(probe_page, paste_js)
    probe = _eval_js(probe_page, operator_busy_probe_js(selector=_GROK_SELECTOR))
    assert probe.get("operator_active") is False
    assert probe.get("foreign_draft") is False


def test_foreign_draft_when_composer_differs_from_sig(probe_page: Page) -> None:
    _eval_js(probe_page, INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
    probe_page.evaluate(
        "() => { const g = window.__ulgOperatorActivity = window.__ulgOperatorActivity || {};"
        "g.ourDraftSig = 'ours'; document.getElementById('composer').textContent = 'theirs'; }"
    )
    probe = _eval_js(probe_page, operator_busy_probe_js(selector=_GROK_SELECTOR))
    assert probe.get("foreign_draft") is True
    assert probe.get("composer_draft") == "theirs"


def test_probe_foreign_draft_sha256_expect_overrides_js_foreign() -> None:
    draft = "legacy tab draft without sig"
    probe = {"foreign_draft": True, "composer_draft": draft}
    expect = composer_draft_sha256_hex(draft)
    assert probe_foreign_draft(probe, expect_draft_sha256=expect) is False
    assert probe_foreign_draft(probe, expect_draft_sha256="0" * 64) is True


def test_send_own_draft_clears_sig(probe_page: Page) -> None:
    _eval_js(probe_page, INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
    line = "send me"
    _eval_js(probe_page, _set_composer_body(_GROK_SELECTOR, line))
    nonce = "playwright-send-own-draft"
    budget_ms = 7000
    _eval_js(probe_page, _composer_send_permit_expression(nonce, budget_ms))
    send_js = _click_send_own_draft_body(_GROK_SELECTOR, abort_nonce=nonce)
    first = _eval_js(probe_page, send_js)
    assert first.get("ok") is True
    sig = probe_page.evaluate("() => window.__ulgOperatorActivity.ourDraftSig")
    assert sig == ""
    second = _eval_js(probe_page, send_js)
    assert second.get("reason") == "draft_not_ours"

    _eval_js(probe_page, _set_composer_body(_GROK_SELECTOR, line))
    no_permit_nonce = "playwright-send-no-permit"
    no_permit_js = _click_send_own_draft_body(
        _GROK_SELECTOR, abort_nonce=no_permit_nonce
    )
    blocked = _eval_js(probe_page, no_permit_js)
    assert blocked.get("ok") is not True
    assert blocked.get("reason") == "cdp_eval_timeout"


def _holder_entry(
    lock_dir: str,
    target_id: str,
    ready: mp.synchronize.Event,
    release: mp.synchronize.Event,
) -> None:
    os.environ["ULG_WEB_TAB_LOCK_DIR"] = lock_dir

    async def run() -> None:
        from web_tab.target_lock import target_write_lock

        async with target_write_lock(target_id, op="hold"):
            ready.set()
            while not release.is_set():
                await asyncio.sleep(0.02)

    asyncio.run(run())


@pytest.mark.asyncio
async def test_queued_paste_waits_operator_idle_then_pastes(
    tmp_path: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writer waits out operator activity before lock, then pastes when lock opens."""
    lock_dir = str(tmp_path / "locks")
    os.makedirs(lock_dir, exist_ok=True)
    monkeypatch.setenv("ULG_WEB_TAB_LOCK_DIR", lock_dir)
    target = "mid-queue-target"
    ready = mp.Event()
    release = mp.Event()
    holder = mp.Process(
        target=_holder_entry,
        args=(lock_dir, target, ready, release),
    )
    holder.start()
    assert ready.wait(timeout=5.0)

    from web_tab.cdp_actuation import RecordingActuationPort

    port = RecordingActuationPort(
        tabs=[
            {
                "id": target,
                "url": "https://grok.com/c/x",
                "web_socket_debugger_url": _WS,
            }
        ]
    )
    insert_mock = AsyncMock(return_value={"ok": True})

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_composer_html())
        await page.evaluate(INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
        await page.evaluate(
            "() => { window.__ulgOperatorActivity.lastInputMs = Date.now(); }"
        )
        with (
            patch(
                "cursor_bridge.dom_composer_paste._cdp_eval",
                side_effect=_cdp_eval_shim(page),
            ),
            patch("web_tab.write_ops.dom_set_composer_text", insert_mock),
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
            paste_task = asyncio.create_task(
                execute_paste(
                    cdp_url="http://127.0.0.1:9222",
                    target_id=target,
                    text="queued paste",
                    grant="explicit",
                    authorization_source="agent-bus:test#1",
                    port=port,
                )
            )
            await asyncio.sleep(0.05)
            await page.evaluate(
                "() => { window.__ulgOperatorActivity.lastInputMs = Date.now() - 11000; }"
            )
            await asyncio.sleep(0.6)
            release.set()
            holder.join(timeout=5.0)
            out = await asyncio.wait_for(paste_task, timeout=5.0)
        await browser.close()
    assert out["ok"] is True
    insert_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_operator_active_refuses_immediately_without_idle_wait() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_composer_html())
        await page.evaluate(INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
        await page.evaluate(
            "() => { window.__ulgOperatorActivity.lastInputMs = Date.now(); }"
        )
        with patch(
            "cursor_bridge.dom_composer_paste._cdp_eval",
            side_effect=_cdp_eval_shim(page),
        ):
            started = asyncio.get_running_loop().time()
            out = await operator_active_probe(
                _WS,
                selector=_GROK_SELECTOR,
                created_by_agent=True,
                wait_for_idle=False,
                lock_holder=True,
            )
            elapsed = asyncio.get_running_loop().time() - started
        await browser.close()
    assert out["ok"] is False
    assert out["code"] == "operator_active"
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_execute_submit_passes_lock_holder_with_focus() -> None:
    from web_tab.cdp_actuation import RecordingActuationPort

    line = "submit line"
    port = RecordingActuationPort(
        tabs=[
            {
                "id": "t-submit",
                "url": "https://grok.com/c/x",
                "web_socket_debugger_url": _WS,
            }
        ]
    )
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_composer_html())
        await page.evaluate(INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
        await page.evaluate(_set_composer_body(_GROK_SELECTOR, line))
        with patch(
            "cursor_bridge.dom_composer_paste._cdp_eval",
            side_effect=_cdp_eval_shim(page),
        ):
            out = await execute_submit(
                cdp_url="http://127.0.0.1:9222",
                target_id="t-submit",
                grant="explicit",
                authorization_source="agent-bus:test#2",
                expected_text=line,
                port=port,
            )
        await browser.close()
    assert out["ok"] is True


def _specimen_2153() -> str:
    base = "RELEASE 637 specimen "
    text = base
    while len(text) < 2153:
        text += "x"
    return text[:2153]


@pytest.mark.asyncio
async def test_paste_submit_2153_passes_guard_with_real_probe() -> None:
    from web_tab.cdp_actuation import RecordingActuationPort

    line = _specimen_2153()
    assert len(line) == 2153
    port = RecordingActuationPort(
        tabs=[
            {
                "id": "675D7DC7AC96D59F9D346BF09B6CE87A",
                "url": "https://chatgpt.com/c/x",
                "web_socket_debugger_url": _WS,
            }
        ]
    )
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_composer_html(site="chatgpt"))
        await page.evaluate(INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
        with (
            patch(
                "cursor_bridge.dom_composer_paste._cdp_eval",
                side_effect=_cdp_eval_shim(page),
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
                "web_tab.write_ops.poll_verify_committed",
                AsyncMock(return_value={"ok": True, "receipt": "dom_committed"}),
            ),
        ):
            out = await execute_paste(
                cdp_url="http://127.0.0.1:9342",
                target_id="675D7DC7AC96D59F9D346BF09B6CE87A",
                text=line,
                grant="explicit",
                authorization_source="agent-bus:21642#13",
                submit=True,
                port=port,
            )
        await browser.close()
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_execute_paste_refuses_foreign_draft_at_sig_level() -> None:
    from web_tab.cdp_actuation import RecordingActuationPort

    port = RecordingActuationPort(
        tabs=[
            {
                "id": "t-foreign",
                "url": "https://grok.com/c/x",
                "web_socket_debugger_url": _WS,
            }
        ]
    )
    insert_mock = AsyncMock(return_value={"ok": True})
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_composer_html())
        await page.evaluate(INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
        await page.evaluate(
            "() => { document.getElementById('composer').textContent = 'operator draft'; }"
        )
        with (
            _cap_operator_idle_wait_at_one_second(),
            patch(
                "cursor_bridge.dom_composer_paste._cdp_eval",
                side_effect=_cdp_eval_shim(page),
            ),
            patch("web_tab.write_ops.dom_set_composer_text", insert_mock),
        ):
            out = await execute_paste(
                cdp_url="http://127.0.0.1:9222",
                target_id="t-foreign",
                text="agent text",
                grant="explicit",
                authorization_source="agent-bus:test#3",
                port=port,
            )
        await browser.close()
    assert out["ok"] is False
    assert out["code"] == "operator_active"
    insert_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_submit_expect_draft_sha256_match_proceeds() -> None:
    from web_tab.cdp_actuation import RecordingActuationPort

    draft = _specimen_2153()
    expect = composer_draft_sha256_hex(draft)
    port = RecordingActuationPort(
        tabs=[
            {
                "id": "t-sha-match",
                "url": "https://grok.com/c/x",
                "web_socket_debugger_url": _WS,
            }
        ]
    )
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_composer_html(draft=draft))
        await page.evaluate(INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
        with patch(
            "cursor_bridge.dom_composer_paste._cdp_eval",
            side_effect=_cdp_eval_shim(page),
        ):
            out = await execute_submit(
                cdp_url="http://127.0.0.1:9222",
                target_id="t-sha-match",
                grant="explicit",
                authorization_source="agent-bus:test#sha-match",
                expect_draft_sha256=expect,
                port=port,
            )
        await browser.close()
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_matching_hash_recent_input_returns_operator_active_retryable() -> None:
    """A matching draft hash still returns operator_active and retryable when input is fresh."""
    from web_tab.cdp_actuation import RecordingActuationPort

    draft = "hello wake"
    expect = composer_draft_sha256_hex(draft)
    port = RecordingActuationPort(
        tabs=[
            {
                "id": "t-sha-active",
                "url": "https://grok.com/c/x",
                "web_socket_debugger_url": _WS,
            }
        ]
    )
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_composer_html(draft=draft))
        await page.evaluate(INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
        await page.evaluate(
            "() => { window.__ulgOperatorActivity.lastInputMs = Date.now(); }"
        )
        with (
            _cap_operator_idle_wait_at_one_second(),
            patch(
                "cursor_bridge.dom_composer_paste._cdp_eval",
                side_effect=_cdp_eval_shim(page),
            ),
        ):
            out = await execute_submit(
                cdp_url="http://127.0.0.1:9222",
                target_id="t-sha-active",
                grant="explicit",
                authorization_source="agent-bus:test#sha-active",
                expect_draft_sha256=expect,
                port=port,
            )
        await browser.close()
    assert out["code"] == "operator_active"
    assert out.get("retryable") is True


@pytest.mark.asyncio
async def test_execute_submit_multiline_expect_draft_sha256_match_proceeds() -> None:
    from web_tab.cdp_actuation import RecordingActuationPort

    draft = "CONTINUITY CALLBACK — block one\n\nSecond paragraph here.\nThird line."
    expect = composer_draft_sha256_hex(draft)
    port = RecordingActuationPort(
        tabs=[
            {
                "id": "t-sha-multiline",
                "url": "https://grok.com/c/x",
                "web_socket_debugger_url": _WS,
            }
        ]
    )
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_composer_html())
        await page.evaluate(INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
        await page.evaluate(
            "(d) => { document.getElementById('composer').innerText = d; }",
            draft,
        )
        with patch(
            "cursor_bridge.dom_composer_paste._cdp_eval",
            side_effect=_cdp_eval_shim(page),
        ):
            out = await execute_submit(
                cdp_url="http://127.0.0.1:9222",
                target_id="t-sha-multiline",
                grant="explicit",
                authorization_source="agent-bus:test#sha-multiline",
                expect_draft_sha256=expect,
                port=port,
            )
        await browser.close()
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_execute_submit_multiline_foreign_draft_refuses_operator_active() -> None:
    from web_tab.cdp_actuation import RecordingActuationPort

    draft = "line A\n\nline B"
    port = RecordingActuationPort(
        tabs=[
            {
                "id": "t-sha-multiline-bad",
                "url": "https://grok.com/c/x",
                "web_socket_debugger_url": _WS,
            }
        ]
    )
    send_mock = AsyncMock(return_value={"ok": True})
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_composer_html())
        await page.evaluate(INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
        await page.evaluate(
            "(d) => { document.getElementById('composer').innerText = d; }",
            draft,
        )
        with (
            _cap_operator_idle_wait_at_one_second(),
            patch(
                "cursor_bridge.dom_composer_paste._cdp_eval",
                side_effect=_cdp_eval_shim(page),
            ),
            patch("web_tab.write_ops.dom_click_send", send_mock),
            patch("web_tab.write_ops.dom_click_send_own_draft", send_mock),
        ):
            out = await execute_submit(
                cdp_url="http://127.0.0.1:9222",
                target_id="t-sha-multiline-bad",
                grant="explicit",
                authorization_source="agent-bus:test#sha-multiline-bad",
                expect_draft_sha256="a" * 64,
                port=port,
            )
        await browser.close()
    assert out["ok"] is False
    assert out["code"] == "operator_active"
    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_submit_foreign_draft_nonmatching_expect_refuses_operator_active() -> None:
    from web_tab.cdp_actuation import RecordingActuationPort

    draft = "foreign legacy body"
    port = RecordingActuationPort(
        tabs=[
            {
                "id": "t-sha-mismatch",
                "url": "https://grok.com/c/x",
                "web_socket_debugger_url": _WS,
            }
        ]
    )
    send_mock = AsyncMock(return_value={"ok": True})
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_composer_html(draft=draft))
        await page.evaluate(INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
        with (
            _cap_operator_idle_wait_at_one_second(),
            patch(
                "cursor_bridge.dom_composer_paste._cdp_eval",
                side_effect=_cdp_eval_shim(page),
            ),
            patch("web_tab.write_ops.dom_click_send", send_mock),
            patch("web_tab.write_ops.dom_click_send_own_draft", send_mock),
        ):
            out = await execute_submit(
                cdp_url="http://127.0.0.1:9222",
                target_id="t-sha-mismatch",
                grant="explicit",
                authorization_source="agent-bus:test#sha-mismatch",
                expect_draft_sha256="f" * 64,
                port=port,
            )
        await browser.close()
    assert out["ok"] is False
    assert out["code"] == "operator_active"
    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_submit_unset_still_refuses_foreign_draft() -> None:
    from web_tab.cdp_actuation import RecordingActuationPort

    port = RecordingActuationPort(
        tabs=[
            {
                "id": "t-sha-unset",
                "url": "https://grok.com/c/x",
                "web_socket_debugger_url": _WS,
            }
        ]
    )
    send_mock = AsyncMock(return_value={"ok": True})
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_composer_html(draft="operator legacy draft"))
        await page.evaluate(INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
        with (
            _cap_operator_idle_wait_at_one_second(),
            patch(
                "cursor_bridge.dom_composer_paste._cdp_eval",
                side_effect=_cdp_eval_shim(page),
            ),
            patch("web_tab.write_ops.dom_click_send", send_mock),
            patch("web_tab.write_ops.dom_click_send_own_draft", send_mock),
        ):
            out = await execute_submit(
                cdp_url="http://127.0.0.1:9222",
                target_id="t-sha-unset",
                grant="explicit",
                authorization_source="agent-bus:test#sha-unset",
                port=port,
            )
        await browser.close()
    assert out["ok"] is False
    assert out["code"] == "operator_active"
    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_matching_sha_still_refuses_when_operator_active_at_read() -> None:
    """SHA expectation clears foreign_draft only; operator_active still blocks."""
    draft = _specimen_2153()
    expect = composer_draft_sha256_hex(draft)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_composer_html(draft=draft))
        await page.evaluate(INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
        await page.evaluate(
            "() => { window.__ulgOperatorActivity.lastInputMs = Date.now(); }"
        )
        with patch(
            "cursor_bridge.dom_composer_paste._cdp_eval",
            side_effect=_cdp_eval_shim(page),
        ):
            out = await operator_active_probe(
                _WS,
                selector=_GROK_SELECTOR,
                created_by_agent=True,
                wait_for_idle=False,
                lock_holder=True,
                expect_draft_sha256=expect,
            )
        await browser.close()
    assert out["ok"] is False
    assert out["code"] == "operator_active"
    probe = out.get("probe") or {}
    assert probe_foreign_draft(probe, expect_draft_sha256=expect) is False


@pytest.mark.asyncio
async def test_submit_without_text_refuses_when_another_writer_pasted() -> None:
    from web_tab.cdp_actuation import RecordingActuationPort

    line = "writer A draft"
    port = RecordingActuationPort(
        tabs=[
            {
                "id": "t-writer-b",
                "url": "https://grok.com/c/x",
                "web_socket_debugger_url": _WS,
            }
        ]
    )
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(_composer_html())
        with patch(
            "cursor_bridge.dom_composer_paste._cdp_eval",
            side_effect=_cdp_eval_shim(page),
        ):
            await page.evaluate(INSTALL_OPERATOR_ACTIVITY_PROBE_JS)
            paste_out = await execute_paste(
                cdp_url="http://127.0.0.1:9222",
                target_id="t-writer-b",
                text=line,
                grant="explicit",
                authorization_source="agent-bus:writer-a#1",
                port=port,
            )
            assert paste_out["ok"] is True
            submit_out = await execute_submit(
                cdp_url="http://127.0.0.1:9222",
                target_id="t-writer-b",
                grant="explicit",
                authorization_source="agent-bus:writer-b#2",
                port=port,
            )
        await browser.close()
    assert submit_out["ok"] is False
    assert submit_out.get("reason") == "foreign_draft"
