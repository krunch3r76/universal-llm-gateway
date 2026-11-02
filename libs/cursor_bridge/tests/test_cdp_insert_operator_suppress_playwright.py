"""Playwright CDP session: trusted Input.insertText must not mark operator active."""

from __future__ import annotations

import json

import pytest
from cursor_bridge.dom_composer_paste import (
    INSTALL_OPERATOR_ACTIVITY_PROBE_JS,
    operator_activity_clear_suppress_js,
    operator_activity_suppress_js,
    operator_busy_probe_js,
)
from playwright.async_api import async_playwright

_SELECTOR = "#composer"


@pytest.mark.asyncio
async def test_cdp_insert_text_between_suppress_and_clear_leaves_operator_idle() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(
            '<div id="composer" contenteditable="true">start</div>'
        )
        session = await page.context.new_cdp_session(page)
        await session.send(
            "Runtime.evaluate",
            {
                "expression": INSTALL_OPERATOR_ACTIVITY_PROBE_JS,
                "returnByValue": True,
            },
        )
        await session.send(
            "Runtime.evaluate",
            {
                "expression": operator_activity_suppress_js(),
                "returnByValue": True,
            },
        )
        await session.send("Input.insertText", {"text": " agent"})
        await session.send(
            "Runtime.evaluate",
            {
                "expression": operator_activity_clear_suppress_js(),
                "returnByValue": True,
            },
        )
        probe_raw = await session.send(
            "Runtime.evaluate",
            {
                "expression": operator_busy_probe_js(selector=_SELECTOR),
                "returnByValue": True,
            },
        )
        await browser.close()

    value = (probe_raw.get("result") or {}).get("value")
    if isinstance(value, str):
        value = json.loads(value)
    assert isinstance(value, dict)
    assert value.get("operator_active") is False
