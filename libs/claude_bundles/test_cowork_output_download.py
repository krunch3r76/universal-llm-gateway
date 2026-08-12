"""Offline tests for Cowork Output download and harvest-body resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_bundles.cowork_output_download import (
    _OUTPUT_DOWNLOAD_JS,
    THIN_CHAT_BODY_MAX_CHARS,
    OutputDownloadError,
    OutputDownloadResult,
    download_cowork_output,
    extract_cortex_uri,
    read_cortex_uri_content,
    resolve_harvest_body,
    should_attempt_output_download,
)
from claude_bundles.cowork_output_preview import (
    _OUTPUT_FILENAME_BUTTON_JS,
    _OUTPUT_PREVIEW_EXTRACT_JS,
    extract_cowork_output_preview,
    is_thin_or_chrome_preview,
    looks_like_output_filename,
)

pytestmark = pytest.mark.offline


def test_should_attempt_output_download_matrix() -> None:
    assert not should_attempt_output_download(harvest_source="chat")
    assert should_attempt_output_download(harvest_source="output-file")
    assert not should_attempt_output_download(
        harvest_source="auto", expected_size="small"
    )
    assert should_attempt_output_download(
        harvest_source="auto", expected_size="large"
    )
    assert should_attempt_output_download(
        harvest_source="auto", download_output=True
    )
    assert not should_attempt_output_download(
        harvest_source="auto", expected_size="auto", download_output=False
    )


def test_extract_and_read_cortex_uri(tmp_path: Path) -> None:
    deliverable = tmp_path / "notes/system/threads/big-review.md"
    deliverable.parent.mkdir(parents=True, exist_ok=True)
    deliverable.write_text("full deliverable body\n", encoding="utf-8")
    uri = "cortex://notes/system/threads/big-review.md"
    chat = f"Written to {uri} · sha256 abc"
    assert extract_cortex_uri(chat) == uri
    assert read_cortex_uri_content(uri, cortex_root=tmp_path) == "full deliverable body\n"


def _download_ctx(download: MagicMock):
    class DownloadInfo:
        def __init__(self, dl: MagicMock) -> None:
            self._dl = dl

        @property
        def value(self):
            async def coro():
                return self._dl

            return coro()

    class _Ctx:
        async def __aenter__(self):
            return DownloadInfo(download)

        async def __aexit__(self, *_args):
            return False

    return _Ctx()


@pytest.mark.asyncio
async def test_download_cowork_output_happy_path(tmp_path: Path) -> None:
    payload = tmp_path / "verdict.md"
    payload.write_bytes(b"downloaded output bytes")
    page = MagicMock()
    page.evaluate = AsyncMock(return_value={"tagged": True, "score": 50})
    locator = MagicMock()
    locator.count = AsyncMock(return_value=1)
    locator.click = AsyncMock()
    page.locator.return_value.first = locator

    download = MagicMock()
    download.suggested_filename = "verdict.md"
    download.path = AsyncMock(return_value=str(payload))
    page.expect_download.return_value = _download_ctx(download)
    result = await download_cowork_output(page)
    assert result is not None
    assert result.filename == "verdict.md"
    assert result.content == "downloaded output bytes"


@pytest.mark.asyncio
async def test_download_classic_wins_before_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Classic expect_download success must short-circuit the preview path."""
    classic = OutputDownloadResult(
        filename="via-download.md",
        content="classic download body " * 20,
        content_bytes=b"x",
    )
    classic_mock = AsyncMock(return_value=classic)
    preview_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "claude_bundles.cowork_output_download._download_via_classic_affordance",
        classic_mock,
    )
    monkeypatch.setattr(
        "claude_bundles.cowork_output_preview.extract_cowork_output_preview",
        preview_mock,
    )
    page = MagicMock()
    result = await download_cowork_output(page)
    assert result is classic
    classic_mock.assert_awaited_once()
    preview_mock.assert_not_called()


@pytest.mark.asyncio
async def test_download_filename_button_preview_on_classic_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview = OutputDownloadResult(
        filename="6386-band-order-verdict.md",
        content="# Verdict\n\n" + ("body line\n" * 40),
        content_bytes=b"y",
    )
    classic_mock = AsyncMock(return_value=None)
    preview_mock = AsyncMock(return_value=preview)
    monkeypatch.setattr(
        "claude_bundles.cowork_output_download._download_via_classic_affordance",
        classic_mock,
    )
    monkeypatch.setattr(
        "claude_bundles.cowork_output_preview.extract_cowork_output_preview",
        preview_mock,
    )
    page = MagicMock()
    result = await download_cowork_output(page)
    assert result is preview
    assert result.filename == "6386-band-order-verdict.md"
    classic_mock.assert_awaited_once()
    preview_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_both_paths_miss_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "claude_bundles.cowork_output_download._download_via_classic_affordance",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "claude_bundles.cowork_output_preview.extract_cowork_output_preview",
        AsyncMock(return_value=None),
    )
    assert await download_cowork_output(MagicMock()) is None


def test_looks_like_output_filename() -> None:
    assert looks_like_output_filename("6386-band-order-verdict.md")
    assert looks_like_output_filename("notes.json")
    assert not looks_like_output_filename("Download")
    assert not looks_like_output_filename("Write your prompt")
    assert not looks_like_output_filename("")


def test_is_thin_or_chrome_preview() -> None:
    assert is_thin_or_chrome_preview("Copy")
    assert is_thin_or_chrome_preview("Write your prompt")
    assert is_thin_or_chrome_preview("short")
    deliverable = "# Verdict\n\n" + (" substantive paragraph.\n" * 20)
    assert not is_thin_or_chrome_preview(deliverable)


@pytest.mark.asyncio
async def test_resolve_harvest_body_prefers_download(monkeypatch: pytest.MonkeyPatch) -> None:
    page = MagicMock()
    monkeypatch.setattr(
        "claude_bundles.cowork_output_download.download_cowork_output",
        AsyncMock(
            return_value=type(
                "R",
                (),
                {
                    "filename": "out.md",
                    "content": "x" * 500,
                    "content_bytes": b"x" * 500,
                },
            )()
        ),
    )
    body = await resolve_harvest_body(
        page,
        "thin chat card",
        harvest_source="auto",
        expected_size="large",
    )
    assert body.content == "x" * 500
    assert body.provenance == "output-file"


@pytest.mark.asyncio
async def test_resolve_harvest_body_auto_fallback_cortex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deliverable = tmp_path / "notes/system/threads/fallback.md"
    deliverable.parent.mkdir(parents=True, exist_ok=True)
    deliverable.write_text("fallback from cortex fs\n", encoding="utf-8")
    page = MagicMock()
    monkeypatch.setattr(
        "claude_bundles.cowork_output_download.download_cowork_output",
        AsyncMock(return_value=None),
    )
    chat = "pointer cortex://notes/system/threads/fallback.md"
    body = await resolve_harvest_body(
        page,
        chat,
        harvest_source="auto",
        expected_size="large",
        cortex_files_root=tmp_path,
    )
    assert body.content == "fallback from cortex fs\n"
    assert body.provenance == "cortex-uri"


@pytest.mark.asyncio
async def test_resolve_harvest_body_auto_large_miss_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = MagicMock()
    monkeypatch.setattr(
        "claude_bundles.cowork_output_download.download_cowork_output",
        AsyncMock(return_value=None),
    )
    with pytest.raises(OutputDownloadError) as excinfo:
        await resolve_harvest_body(
            page,
            "thin chat card only",
            harvest_source="auto",
            expected_size="large",
            cortex_files_root=None,
        )
    assert excinfo.value.chat_body == "thin chat card only"


@pytest.mark.asyncio
async def test_resolve_harvest_body_auto_large_keeps_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body past the thin bound is a transcript, not a completion card."""
    page = MagicMock()
    monkeypatch.setattr(
        "claude_bundles.cowork_output_download.download_cowork_output",
        AsyncMock(return_value=None),
    )
    transcript = "operator proxy turn\n" * 1000
    assert len(transcript) > THIN_CHAT_BODY_MAX_CHARS
    body = await resolve_harvest_body(
        page,
        transcript,
        harvest_source="auto",
        expected_size="large",
        cortex_files_root=None,
    )
    assert body.content == transcript
    assert body.provenance == "chat-large"


@pytest.mark.asyncio
async def test_resolve_harvest_body_output_file_has_no_size_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit Output demand still hard-fails, but surrenders the transcript."""
    page = MagicMock()
    monkeypatch.setattr(
        "claude_bundles.cowork_output_download.download_cowork_output",
        AsyncMock(return_value=None),
    )
    transcript = "operator proxy turn\n" * 1000
    with pytest.raises(OutputDownloadError) as excinfo:
        await resolve_harvest_body(
            page,
            transcript,
            harvest_source="output-file",
            expected_size="large",
        )
    assert excinfo.value.chat_body == transcript


@pytest.mark.asyncio
async def test_resolve_harvest_body_auto_non_large_fallback_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = MagicMock()
    monkeypatch.setattr(
        "claude_bundles.cowork_output_download.download_cowork_output",
        AsyncMock(return_value=None),
    )
    body = await resolve_harvest_body(
        page,
        "thin chat card only",
        harvest_source="auto",
        expected_size="auto",
        download_output=True,
        cortex_files_root=None,
    )
    assert body.content == "thin chat card only"
    assert body.provenance == "chat"


@pytest.mark.asyncio
async def test_resolve_harvest_body_chat_skips_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = MagicMock()
    mock_download = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "claude_bundles.cowork_output_download.download_cowork_output",
        mock_download,
    )
    body = await resolve_harvest_body(
        page,
        "legacy chat harvest",
        harvest_source="chat",
        expected_size="large",
        artifact_cards=[{"title": "ignored card", "kind": "MD"}],
    )
    assert body.content == "legacy chat harvest"
    assert body.provenance == "chat"
    mock_download.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_harvest_body_artifact_card_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claude_bundles.cowork_artifact_card import ArtifactCardResult

    page = MagicMock()
    monkeypatch.setattr(
        "claude_bundles.cowork_output_download.download_cowork_output",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "claude_bundles.cowork_artifact_card.extract_artifact_card_body",
        AsyncMock(
            return_value=ArtifactCardResult(
                title="Bind sidecar reasoning posture merge",
                content="# Sidecar\n\n" + ("deliverable line.\n" * 25),
            )
        ),
    )
    chat = "Bind complete. Summary prose here."
    cards = [{"title": "Bind sidecar reasoning posture merge", "kind": "MD"}]
    body = await resolve_harvest_body(
        page,
        chat,
        harvest_source="auto",
        expected_size="auto",
        artifact_cards=cards,
    )
    assert body.provenance == "artifact-card"
    assert chat in body.content
    assert "## Artifact card: Bind sidecar reasoning posture merge" in body.content


@pytest.mark.asyncio
async def test_resolve_harvest_body_artifact_card_fail_closed_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = MagicMock()
    monkeypatch.setattr(
        "claude_bundles.cowork_output_download.download_cowork_output",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "claude_bundles.cowork_artifact_card.extract_artifact_card_body",
        AsyncMock(return_value=None),
    )
    chat = "Bind complete with card chrome only."
    cards = [{"title": "Bind sidecar reasoning posture merge", "kind": "MD"}]
    with pytest.raises(OutputDownloadError) as excinfo:
        await resolve_harvest_body(
            page,
            chat,
            harvest_source="auto",
            expected_size="auto",
            artifact_cards=cards,
        )
    assert "artifact_card_without_body" in str(excinfo.value)
    assert excinfo.value.chat_body == chat


@pytest.mark.asyncio
async def test_resolve_harvest_body_artifact_card_trumps_chat_large(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = MagicMock()
    monkeypatch.setattr(
        "claude_bundles.cowork_output_download.download_cowork_output",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "claude_bundles.cowork_artifact_card.extract_artifact_card_body",
        AsyncMock(return_value=None),
    )
    transcript = "operator proxy turn\n" * 1000
    assert len(transcript) > THIN_CHAT_BODY_MAX_CHARS
    cards = [{"title": "Bind sidecar reasoning posture merge", "kind": "MD"}]
    with pytest.raises(OutputDownloadError) as excinfo:
        await resolve_harvest_body(
            page,
            transcript,
            harvest_source="auto",
            expected_size="large",
            artifact_cards=cards,
        )
    assert "artifact_card_without_body" in str(excinfo.value)
    assert excinfo.value.chat_body == transcript


@pytest.mark.asyncio
async def test_resolve_harvest_body_output_file_hard_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = MagicMock()
    monkeypatch.setattr(
        "claude_bundles.cowork_output_download.download_cowork_output",
        AsyncMock(return_value=None),
    )
    with pytest.raises(OutputDownloadError):
        await resolve_harvest_body(
            page,
            "thin chat card",
            harvest_source="output-file",
        )


# --- Playwright DOM fixtures (classic vs filename-button) -----------------

_CLASSIC_DOWNLOAD_HTML = """
<!doctype html><html><body>
<aside>
  <h2>Outputs</h2>
  <button>6386-band-order-verdict.md</button>
  <a download href="/out.md" aria-label="Download output">Download</a>
</aside>
<main><div>Write your prompt</div></main>
</body></html>
"""

_FILENAME_BUTTON_ONLY_HTML = """
<!doctype html><html><body>
<aside id="outputs">
  <h2>Outputs</h2>
  <button id="file-btn">6386-band-order-verdict.md</button>
</aside>
<main>
  <div>Write your prompt</div>
  <div id="preview" style="display:none"></div>
</main>
<script>
  document.getElementById('file-btn').addEventListener('click', () => {
    const p = document.getElementById('preview');
    p.style.display = 'block';
    p.innerText = [
      '# Arc 6386 band-order verdict',
      '',
      'Binder ruling on disposition_date ordering.',
      'Q1 verdict: disposition_date is the correct key.',
      'More ways to open',
      'Copy',
    ].concat(Array(30).fill('substantive paragraph of deliverable body.')).join('\\n');
  });
</script>
</body></html>
"""

_NO_AFFORDANCE_HTML = """
<!doctype html><html><body>
<main>
  <div>Write your prompt</div>
  <button>Send</button>
</main>
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
async def test_dom_classic_download_js_tags_download_affordance() -> None:
    pw, browser, page = await _with_page(_CLASSIC_DOWNLOAD_HTML)
    try:
        tagged = await page.evaluate(_OUTPUT_DOWNLOAD_JS)
        assert tagged is not None
        assert tagged["tagged"] is True
        assert await page.locator("[data-cdp-output-download='1']").count() == 1
        # Filename button must not be the classic tag target.
        classic = page.locator("[data-cdp-output-download='1']")
        assert "download" in (
            (await classic.get_attribute("aria-label") or "")
            + " "
            + (await classic.inner_text())
        ).lower() or await classic.get_attribute("download") is not None
    finally:
        await browser.close()
        await pw.stop()


@pytest.mark.asyncio
async def test_dom_filename_button_preview_extract_path() -> None:
    pw, browser, page = await _with_page(_FILENAME_BUTTON_ONLY_HTML)
    try:
        classic = await page.evaluate(_OUTPUT_DOWNLOAD_JS)
        assert classic is None
        tagged = await page.evaluate(_OUTPUT_FILENAME_BUTTON_JS)
        assert tagged is not None
        assert tagged["filename"] == "6386-band-order-verdict.md"
        result = await extract_cowork_output_preview(page, settle_ms=100)
        assert result is not None
        assert result.filename == "6386-band-order-verdict.md"
        assert "disposition_date" in result.content
        assert "Write your prompt" not in result.content
    finally:
        await browser.close()
        await pw.stop()


@pytest.mark.asyncio
async def test_dom_both_detectors_miss() -> None:
    pw, browser, page = await _with_page(_NO_AFFORDANCE_HTML)
    try:
        assert await page.evaluate(_OUTPUT_DOWNLOAD_JS) is None
        assert await page.evaluate(_OUTPUT_FILENAME_BUTTON_JS) is None
        assert await page.evaluate(_OUTPUT_PREVIEW_EXTRACT_JS) is None
        assert await extract_cowork_output_preview(page, settle_ms=0) is None
    finally:
        await browser.close()
        await pw.stop()
