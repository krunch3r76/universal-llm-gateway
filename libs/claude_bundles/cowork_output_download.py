"""Cowork task Output download and harvest-body resolution for CDP project-ask.

Playwright ``expect_download`` on the held page — sibling to chat DOM harvest,
not a rewrite of ``HARVEST_JS``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

ExpectedSize = Literal["small", "large", "auto"]
HarvestSource = Literal["chat", "output-file", "auto"]
HarvestProvenance = Literal["output-file", "cortex-uri", "chat", "chat-large"]

# A Cowork completion card ("written the verdict to the output file") runs a few
# hundred chars; a real transcript runs tens of thousands. Above this bound the
# chat body cannot be the stub the fail-closed guard exists to reject.
THIN_CHAT_BODY_MAX_CHARS = 8000


def cortex_files_root_from_env() -> Path | None:
    """Return ``CORTEX_FILES_ROOT`` when configured, else the default mount if present."""
    import os

    raw = os.environ.get("CORTEX_FILES_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    default = Path.home() / "mcp-data" / "files"
    return default if default.is_dir() else None

_CORTEX_URI = re.compile(r"cortex://[^\s`'\"<>]+")
_OUTPUT_DOWNLOAD_JS = """
() => {
  const root = document.body;
  if (!root) return null;
  const candidates = [];
  const push = (el, score) => {
    if (!el || el.disabled) return;
    candidates.push({ el, score });
  };
  for (const el of root.querySelectorAll(
    'a[download], button[download], [data-testid*="output" i] a, '
    + '[data-testid*="output" i] button, [aria-label*="download" i]'
  )) {
    const label = (
      (el.getAttribute('aria-label') || '') + ' ' + (el.textContent || '')
    ).toLowerCase();
    let score = 10;
    if (label.includes('output')) score += 40;
    if (label.includes('download')) score += 30;
    if (el.hasAttribute('download')) score += 20;
    push(el, score);
  }
  for (const block of root.querySelectorAll('section, article, div')) {
    const text = (block.textContent || '').trim();
    if (!/\\boutput\\b/i.test(text) || text.length > 4000) continue;
    for (const el of block.querySelectorAll('a[href], button')) {
      const label = (
        (el.getAttribute('aria-label') || '') + ' ' + (el.textContent || '')
      ).toLowerCase();
      if (!label.includes('download') && !el.hasAttribute('download')) continue;
      push(el, 35);
    }
  }
  candidates.sort((a, b) => b.score - a.score);
  if (!candidates.length) return null;
  const best = candidates[0].el;
  best.setAttribute('data-cdp-output-download', '1');
  return { tagged: true, score: candidates[0].score };
}
"""


class OutputDownloadError(RuntimeError):
    """Raised when Output bytes are required and resolution misses.

    Raised for ``harvest_source=output-file`` on download miss/empty, and for
    ``harvest_source=auto`` with ``expected_size=large`` when both Cowork Output
    download and a readable chat ``cortex://`` pointer miss (fail-closed — no
    thin chat archive).

    Carries ``chat_body`` so callers can surface what was scraped. The guard
    blocks the *archive write*, not the harvest — discarding the transcript
    leaves no copy anywhere and reports ``body_len=0``, which reads as "the
    model produced nothing" (agent-bus 5911, execution 23f94ae8).
    """

    def __init__(self, message: str, *, chat_body: str = "") -> None:
        super().__init__(message)
        self.chat_body = chat_body


@dataclass(frozen=True)
class OutputDownloadResult:
    """Downloaded Cowork Output payload."""

    filename: str
    content: str
    content_bytes: bytes


@dataclass(frozen=True)
class HarvestBody:
    """Resolved harvest payload and its provenance (distinct from submit ``harvest_source``)."""

    content: str
    provenance: HarvestProvenance


def should_attempt_output_download(
    *,
    harvest_source: HarvestSource = "auto",
    expected_size: ExpectedSize = "auto",
    download_output: bool = False,
) -> bool:
    """Return whether the held page should attempt a Cowork Output download.

    ``expected_size=small`` and ``harvest_source=chat`` never force download.
    ``harvest_source=output-file`` always attempts. ``harvest_source=auto``
    attempts when ``expected_size=large`` or ``download_output`` is true.
    """
    if harvest_source == "chat":
        return False
    if harvest_source == "output-file":
        return True
    if expected_size == "small":
        return False
    if download_output or expected_size == "large":
        return True
    return False


def extract_cortex_uri(body: str) -> str | None:
    """Return the first ``cortex://`` deliverable URI embedded in chat text."""
    match = _CORTEX_URI.search(body or "")
    if not match:
        return None
    return match.group(0).rstrip(".,;)")


def read_cortex_uri_content(uri: str, *, cortex_root: Path) -> str | None:
    """Read UTF-8 text for a resolvable ``cortex://`` path under *cortex_root*."""
    if not uri.startswith("cortex://"):
        return None
    rel = uri.removeprefix("cortex://").lstrip("/")
    path = (cortex_root / rel).resolve()
    try:
        path.relative_to(cortex_root.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


async def _download_via_classic_affordance(
    page: Page,
    *,
    timeout_ms: int = 15000,
) -> OutputDownloadResult | None:
    """First-pass: tag classic download affordance and ``expect_download``.

    Preserves the original ``_OUTPUT_DOWNLOAD_JS`` selectors/scoring. Returns
    ``None`` on tag miss, download timeout, or empty bytes.
    """
    tagged = await page.evaluate(_OUTPUT_DOWNLOAD_JS)
    if not tagged:
        return None
    locator = page.locator("[data-cdp-output-download='1']").first
    if not await locator.count():
        return None
    try:
        async with page.expect_download(timeout=timeout_ms) as download_info:
            await locator.click(force=True)
        download = await download_info.value
    except PlaywrightTimeoutError:
        return None
    suggested = download.suggested_filename or "cowork-output"
    path = await download.path()
    if not path:
        return None
    raw = Path(path).read_bytes()
    if not raw:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return OutputDownloadResult(
        filename=suggested,
        content=text,
        content_bytes=raw,
    )


async def download_cowork_output(
    page: Page,
    *,
    timeout_ms: int = 15000,
) -> OutputDownloadResult | None:
    """Resolve Cowork Output bytes: classic download, then filename-button preview.

    Order (fail-closed callers unchanged):
    1. Classic download affordance (``_OUTPUT_DOWNLOAD_JS`` + ``expect_download``).
    2. Filename-button → preview-panel ``innerText`` extract (sibling module).
    3. ``None`` — callers decide soft fallback (``auto``) vs hard fail
       (``output-file``).

    Provenance for both successful paths remains ``output-file`` at
    ``resolve_harvest_body`` (Outputs-panel deliverable body).
    """
    classic = await _download_via_classic_affordance(page, timeout_ms=timeout_ms)
    if classic is not None and classic.content.strip():
        return classic
    from claude_bundles.cowork_output_preview import extract_cowork_output_preview

    preview = await extract_cowork_output_preview(page)
    if preview is not None and preview.content.strip():
        return preview
    return None


async def resolve_harvest_body(
    page: Page,
    chat_body: str,
    *,
    harvest_source: HarvestSource = "auto",
    expected_size: ExpectedSize = "auto",
    download_output: bool = False,
    cortex_files_root: Path | None = None,
) -> HarvestBody:
    """Resolve archive body from Output download, cortex-fs fallback, or chat scrape.

    Returns ``HarvestBody`` with ``provenance`` distinct from the submit-time
    ``harvest_source`` knob (``auto`` | ``output-file`` | ``chat``).

    ``harvest_source=output-file`` raises ``OutputDownloadError`` on miss/empty.
    ``harvest_source=auto`` with ``expected_size=large`` tries Output download
    (classic affordance, then filename-button preview extract), then a chat
    ``cortex://`` pointer; on both miss it measures the scraped body — over
    ``THIN_CHAT_BODY_MAX_CHARS`` yields ``provenance=chat-large``, at or under
    it raises ``OutputDownloadError`` (fail-closed — no thin chat archive).
    Non-large ``auto`` soft-falls to chat. ``harvest_source=chat`` returns
    scraped chat with ``provenance=chat``.

    Measuring the body replaces "an Output affordance existed" as the thin-stub
    discriminator. A ``converse`` operator-proxy session never mints a Cowork
    Output — its work lands on the agent bus — so the affordance proxy rejected
    full transcripts as stubs (agent-bus 5911).
    """
    if not should_attempt_output_download(
        harvest_source=harvest_source,
        expected_size=expected_size,
        download_output=download_output,
    ):
        return HarvestBody(content=chat_body, provenance="chat")

    downloaded = await download_cowork_output(page)
    if downloaded is not None and downloaded.content.strip():
        return HarvestBody(content=downloaded.content, provenance="output-file")

    if harvest_source == "output-file":
        # Explicit caller demand for Output bytes — no size escape.
        raise OutputDownloadError(
            "Cowork Output download required (harvest_source=output-file) but "
            "affordance missing or download empty",
            chat_body=chat_body,
        )

    root = cortex_files_root
    if root is not None:
        uri = extract_cortex_uri(chat_body)
        if uri:
            copied = read_cortex_uri_content(uri, cortex_root=root)
            if copied and copied.strip():
                return HarvestBody(content=copied, provenance="cortex-uri")

    if harvest_source == "auto" and expected_size == "large":
        if len(chat_body.strip()) > THIN_CHAT_BODY_MAX_CHARS:
            return HarvestBody(content=chat_body, provenance="chat-large")
        raise OutputDownloadError(
            "Cowork Output download and cortex-uri both missed for "
            "harvest_source=auto with expected_size=large — refusing thin chat archive",
            chat_body=chat_body,
        )

    return HarvestBody(content=chat_body, provenance="chat")
