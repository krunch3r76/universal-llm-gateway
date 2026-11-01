"""Playwright DOM tests for /code model toolbar decoy button (friction:41151).

DOM fixture reconstructed from Stage Manager report (exec de7fea49, bus 21600#5),
not a live capture.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright")
from playwright.async_api import Page, async_playwright  # noqa: E402

from claude_bundles.project_ask import (
    _open_code_surface_model_menu,
    _read_code_surface_model_label,
)

pytestmark = pytest.mark.offline

_DECOY_LABEL = "More options for Model picker chip verification"
_VISIBLE_LABEL = "Model: Opus 5.5 Medium"

_CODE_SURFACE_FIXTURE_HTML = f"""<!doctype html><html><body>
<button
  aria-label="{_DECOY_LABEL}"
  aria-hidden="true"
  style="display:none"
  onclick="window.__decoy=1"
></button>
<button id="code-surface-model" aria-label="{_VISIBLE_LABEL}"></button>
<script>
document.getElementById('code-surface-model').addEventListener('click', function () {{
  var menu = document.createElement('div');
  menu.setAttribute('role', 'menu');
  menu.innerHTML =
    '<div role="menuitemradio" aria-label="Opus 5.5">Opus 5.5</div>' +
    '<div role="menuitemradio" aria-label="Fable 5.1">Fable 5.1</div>';
  document.body.appendChild(menu);
}});
</script>
</body></html>"""


async def _load_fixture() -> tuple[Page, object]:
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content(_CODE_SURFACE_FIXTURE_HTML)
    return page, (pw, browser)


async def _close_fixture(bundle: object) -> None:
    pw, browser = bundle  # type: ignore[misc]
    await browser.close()
    await pw.stop()


@pytest.mark.asyncio
async def test_old_model_selector_first_is_hidden_decoy() -> None:
    page, bundle = await _load_fixture()
    try:
        old = page.locator('button[aria-label*="Model"]')
        assert await old.count() >= 2
        first_label = await old.first.get_attribute("aria-label")
        assert first_label == _DECOY_LABEL
    finally:
        await _close_fixture(bundle)


@pytest.mark.asyncio
async def test_read_code_surface_model_label_skips_decoy() -> None:
    page, bundle = await _load_fixture()
    try:
        label = await _read_code_surface_model_label(page)
        assert label == _VISIBLE_LABEL
    finally:
        await _close_fixture(bundle)


@pytest.mark.asyncio
async def test_open_code_surface_model_menu_clicks_visible_not_decoy() -> None:
    page, bundle = await _load_fixture()
    try:
        opened = await _open_code_surface_model_menu(page)
        assert opened is True
        decoy = await page.evaluate("() => window.__decoy")
        assert decoy is None
        assert await page.locator('[role="menu"]').count() == 1
        assert await page.locator('[role="menuitemradio"]').count() == 2
    finally:
        await _close_fixture(bundle)
