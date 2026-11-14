"""Hermetic DOM fixtures for CDP compose contract v2 (a:43854 round 1)."""

from __future__ import annotations

import pytest

pytest.importorskip("playwright")
from playwright.async_api import async_playwright  # noqa: E402

from claude_bundles.chat_cowork_mode import (  # noqa: E402
    ensure_compose_model_chip,
    ensure_cowork_auto,
    ensure_permission_mode,
    read_compose_model_chip,
    read_quota_weekly_pct,
)

pytestmark = pytest.mark.offline

_COMPOSE_V2_HTML = """
<!doctype html><html><head><title>New session - Claude</title></head><body>
<div role="radiogroup" aria-label="Mode">
  <div role="radio" aria-checked="true" aria-label="Claude">Claude</div>
  <div role="radio" aria-checked="false" aria-label="Code">Code</div>
</div>
<div data-testid="chat-input" contenteditable="true"></div>
<button id="perm" style="width:72px;height:28px">Manual</button>
<div id="menu" role="menu" style="display:none">
  <div role="menuitemradio" id="row-manual" aria-checked="true">Manual
    <span>asks before new tools or sites</span></div>
  <div role="menuitemradio" id="row-auto" aria-checked="false">Auto
    <span>runs on its own, pauses if unsafe</span></div>
</div>
<button data-testid="model-selector-dropdown" aria-label="Opus 5.5"
  style="width:90px;height:28px">Opus 5.5</button>
<button id="effort" style="width:48px;height:28px">High</button>
<div role="banner" id="quota">You've used 91% of your weekly limit</div>
<script>
document.getElementById('perm').addEventListener('click', () => {
  document.getElementById('menu').style.display = 'block';
});
function setAuto() {
  document.getElementById('perm').innerText = 'Auto';
  document.getElementById('row-manual').setAttribute('aria-checked', 'false');
  document.getElementById('row-auto').setAttribute('aria-checked', 'true');
  document.getElementById('menu').style.display = 'none';
}
document.getElementById('row-auto').addEventListener('click', setAuto);
</script>
</body></html>
"""

_COMPOSE_V2_NO_MENU_HTML = """
<!doctype html><html><head><title>New session - Claude</title></head><body>
<div role="radiogroup" aria-label="Mode">
  <div role="radio" aria-checked="true" aria-label="Claude">Claude</div>
  <div role="radio" aria-checked="false" aria-label="Code">Code</div>
</div>
<div data-testid="chat-input"></div>
<button style="width:72px;height:28px">Manual</button>
</body></html>
"""

_COMPOSE_V2_QUOTA_96_HTML = _COMPOSE_V2_HTML.replace(
    "91% of your weekly limit", "96% of your weekly limit"
)


@pytest.mark.asyncio
async def test_ensure_permission_mode_manual_to_auto() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_COMPOSE_V2_HTML)
            result = await ensure_permission_mode(page, "Auto")
            assert result["ok"] is True
            assert result["button_label"] == "Auto"
            assert (await page.locator("#perm").inner_text()).strip() == "Auto"
            assert result["selected_item"]["text"].startswith("Auto")
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_ensure_permission_mode_missing_menu_ui_drift() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_COMPOSE_V2_NO_MENU_HTML)
            result = await ensure_permission_mode(page, "Auto")
            assert result["ok"] is False
            assert result["failure_class"] == "ui_drift"
            assert result["step"] == "permission_control_missing"
            assert result.get("ui_census")
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_read_quota_weekly_pct_banner_91() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_COMPOSE_V2_HTML)
            quota = await read_quota_weekly_pct(page)
            assert quota["pct"] == 91
            assert "weekly limit" in (quota.get("banner_text") or "").lower()
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_ensure_cowork_auto_quota_near_at_96() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_COMPOSE_V2_QUOTA_96_HTML)
            result = await ensure_cowork_auto(page)
            assert result["quota_weekly_pct"] == 96
            assert result["ok"] is False
            assert result["step"] == "quota_near"
            assert result["failure_class"] == "quota_near"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_ensure_cowork_auto_compose_v2_success_91_pct() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_COMPOSE_V2_HTML)
            result = await ensure_cowork_auto(page)
            assert result["ok"] is True
            assert result["step"] == "compose_v2"
            assert result["quota_weekly_pct"] == 91
            assert result["permission"]["ok"] is True
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_ensure_compose_model_chip_readback_ok() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_COMPOSE_V2_HTML)
            result = await ensure_compose_model_chip(
                page, model_request="opus-5.5", effort="high"
            )
            assert result["ok"] is True
            assert result["step"] == "already_model"
            assert result["after"]["model_label"] == "Opus 5.5"
            assert result["after"]["effort"] == "High"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_ensure_compose_model_chip_mismatch_sets_via_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = _COMPOSE_V2_HTML.replace('aria-label="Opus 5.5"', 'aria-label="Sonnet 5"')
    html = html.replace(">Opus 5.5<", ">Sonnet 5<")

    async def _select_model(page, _model: str) -> dict:
        await page.evaluate(
            """() => {
              const b = document.querySelector('[data-testid="model-selector-dropdown"]');
              if (b) {
                b.innerText = 'Opus 5.5';
                b.setAttribute('aria-label', 'Opus 5.5');
              }
            }"""
        )
        return {"ok": True, "step": "selected_opus"}

    monkeypatch.setattr(
        "claude_bundles.chat_model_select.select_model",
        _select_model,
    )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(html)
            result = await ensure_compose_model_chip(
                page, model_request="opus-5.5", effort="high"
            )
            assert result["ok"] is True
            assert result["step"] == "model_chip_set"
            assert result["after"]["model_label"] == "Opus 5.5"
        finally:
            await browser.close()


_PERM_ANCHOR_HTML = """
<!doctype html><html><head><title>New session - Claude</title></head><body>
<header><button style="width:72px;height:28px">Manual</button></header>
<section class="compose-shell">
  <div data-testid="chat-input" contenteditable="true"></div>
  <button id="perm" style="width:72px;height:28px">Manual</button>
  <div id="menu" role="menu" style="display:none">
    <div role="menuitemradio" id="row-manual" aria-checked="true">Manual</div>
    <div role="menuitemradio" id="row-auto" aria-checked="false">Auto</div>
  </div>
</section>
<div role="radiogroup" aria-label="Mode">
  <div role="radio" aria-checked="true" aria-label="Claude">Claude</div>
</div>
<script>
document.getElementById('perm').addEventListener('click', () => {
  document.getElementById('menu').style.display = 'block';
});
document.getElementById('row-auto').addEventListener('click', () => {
  document.getElementById('perm').innerText = 'Auto';
  document.getElementById('menu').style.display = 'none';
});
</script>
</body></html>
"""


# Live /new census (opus smoke 295ffcc7): compose chip has PUA chevron, no
# data-testid="model-selector-dropdown"; Project chip sits beside Opus.
_COMPOSE_V2_LIVE_CHIP_HTML = """
<!doctype html><html><head><title>New session - Claude</title></head><body>
<div role="radiogroup" aria-label="Mode">
  <div role="radio" aria-checked="true" aria-label="Claude">Claude</div>
  <div role="radio" aria-checked="false" aria-label="Code">Code</div>
</div>
<section class="compose-shell">
  <div data-testid="chat-input" contenteditable="true"></div>
  <button id="project" style="width:72px;height:28px">Project</button>
  <button id="model" aria-label="Opus 5.5" style="width:90px;height:28px">Opus 5.5\ue003</button>
  <button id="perm" style="width:72px;height:28px">Auto</button>
  <div id="menu" role="menu" style="display:none">
    <div role="menuitemradio" id="row-opus" aria-checked="true" aria-label="Opus 5.5 High">
      Opus 5.5 High</div>
  </div>
</section>
<script>
document.getElementById('model').addEventListener('click', () => {
  document.getElementById('menu').style.display = 'block';
});
</script>
</body></html>
"""


@pytest.mark.asyncio
async def test_compose_model_chip_live_census_pua_no_testid() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_COMPOSE_V2_LIVE_CHIP_HTML)
            chip = await read_compose_model_chip(page)
            assert chip["model_label"] == "Opus 5.5"
            assert chip.get("model_chip_lookup") == "chat_input_region"
            result = await ensure_compose_model_chip(
                page, model_request="opus-5.5", effort="high"
            )
            assert result["ok"] is True
            assert result["step"] == "already_model"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_compose_model_chip_missing_attaches_candidates() -> None:
    html = _COMPOSE_V2_NO_MENU_HTML.replace(
        "<button style=", '<button id="decoy" style='
    )
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(html)
            result = await ensure_compose_model_chip(page, model_request="opus-5.5")
            assert result["ok"] is False
            assert result["step"] == "model_chip_missing"
            census = result.get("ui_census") or {}
            assert "model_chip_candidates" in census
            assert any(
                c.get("text") == "Manual" for c in census["model_chip_candidates"]
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_permission_chip_prefers_chat_input_region() -> None:
    from claude_bundles.chat_cowork_mode import _permission_mode_button_label

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_PERM_ANCHOR_HTML)
            btn = await _permission_mode_button_label(page)
            assert btn["found"] is True
            assert btn["permission_lookup"] == "chat_input_region"
            assert btn["text"] == "Manual"
        finally:
            await browser.close()
