"""Offline tests for in-chat Cowork artifact card body extraction (AC2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_bundles.cowork_artifact_card import (
    ArtifactCardResult,
    extract_artifact_card_body,
    is_chrome_only_card_extract,
)

pytestmark = pytest.mark.offline


def test_is_chrome_only_rejects_title_echo() -> None:
    assert is_chrome_only_card_extract(
        "Bind sidecar reasoning posture merge",
        "Bind sidecar reasoning posture merge\nDocument · MD\nGoogle Drive",
    )


def test_is_chrome_only_accepts_substantive_body() -> None:
    body = "# Verdict\n\n" + ("substantive paragraph.\n" * 20)
    assert not is_chrome_only_card_extract("Bind sidecar reasoning posture merge", body)


@pytest.mark.asyncio
async def test_extract_artifact_card_body_success(monkeypatch: pytest.MonkeyPatch) -> None:
    page = MagicMock()
    page.evaluate = AsyncMock(
        side_effect=[
            {"tagged": True, "title": "Bind sidecar reasoning posture merge"},
            {
                "content": "# Bind sidecar\n\n" + ("body line.\n" * 30),
                "length": 400,
            },
        ]
    )
    locator = MagicMock()
    locator.count = AsyncMock(return_value=1)
    locator.click = AsyncMock()
    page.locator.return_value.first = locator
    page.wait_for_timeout = AsyncMock()

    result = await extract_artifact_card_body(page, "Bind sidecar reasoning posture merge")
    assert result is not None
    assert isinstance(result, ArtifactCardResult)
    assert result.title == "Bind sidecar reasoning posture merge"
    assert "Bind sidecar" in result.content


@pytest.mark.asyncio
async def test_extract_artifact_card_body_miss_returns_none() -> None:
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=None)
    assert await extract_artifact_card_body(page, "missing card") is None


@pytest.mark.asyncio
async def test_extract_artifact_card_body_chrome_only_returns_none() -> None:
    page = MagicMock()
    page.evaluate = AsyncMock(
        side_effect=[
            {"tagged": True, "title": "Bind sidecar reasoning posture merge"},
            {
                "content": "Bind sidecar reasoning posture merge\nDocument · MD\nGoogle Drive",
                "length": 80,
            },
        ]
    )
    locator = MagicMock()
    locator.count = AsyncMock(return_value=1)
    locator.click = AsyncMock()
    page.locator.return_value.first = locator
    page.wait_for_timeout = AsyncMock()

    assert await extract_artifact_card_body(page, "Bind sidecar reasoning posture merge") is None


_CARD_FIXTURE_HTML = """
<!doctype html><html><body>
<main>
  <div data-testid="assistant-message">
    <p>Bind complete. Here is the summary of the ruling.</p>
    <button id="card-btn">
      Bind sidecar reasoning posture merge
      Document · MD
      Google Drive
    </button>
  </div>
</main>
<div id="preview" style="display:none"></div>
<script>
  document.getElementById('card-btn').addEventListener('click', () => {
    const p = document.getElementById('preview');
    p.style.display = 'block';
    p.innerText = [
      '# Bind sidecar reasoning posture merge',
      '',
      'Substantive deliverable body for the sidecar.',
    ].concat(Array(30).fill('More paragraph content for the card body.')).join('\\n');
  });
</script>
</body></html>
"""

_CHROME_ONLY_CARD_HTML = """
<!doctype html><html><body>
<main>
  <div data-testid="assistant-message">
    <p>Summary prose only.</p>
    <button id="card-btn">
      Bind sidecar reasoning posture merge
      Document · MD
      Google Drive
    </button>
  </div>
</main>
<div id="preview" style="display:none"></div>
<script>
  document.getElementById('card-btn').addEventListener('click', () => {
    document.getElementById('preview').innerText =
      'Bind sidecar reasoning posture merge\\nDocument · MD\\nGoogle Drive';
  });
</script>
</body></html>
"""


async def _with_page(html: str):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.set_content(html)
    return pw, browser, page


@pytest.mark.asyncio
async def test_dom_extract_artifact_card_body_success() -> None:
    pw, browser, page = await _with_page(_CARD_FIXTURE_HTML)
    try:
        result = await extract_artifact_card_body(
            page, "Bind sidecar reasoning posture merge", settle_ms=100
        )
        assert result is not None
        assert "Substantive deliverable body" in result.content
    finally:
        await browser.close()
        await pw.stop()


@pytest.mark.asyncio
async def test_dom_extract_artifact_card_body_chrome_only() -> None:
    pw, browser, page = await _with_page(_CHROME_ONLY_CARD_HTML)
    try:
        assert (
            await extract_artifact_card_body(
                page, "Bind sidecar reasoning posture merge", settle_ms=100
            )
            is None
        )
    finally:
        await browser.close()
        await pw.stop()
