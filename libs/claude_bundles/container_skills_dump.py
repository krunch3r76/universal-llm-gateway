"""Dump the claude.ai code-exec ``/mnt/skills`` tree via a cheap ordinary chat.

Chat is the standing path (every web session mounts ``/mnt/skills`` — confirmed
on ``agent_skill:lead-seat-boot`` a23741). CSE is a fallback only when chat
cannot see the tree. Stargate ``/v1/chat/completions`` artifact grab is
untested — do not treat it as live.

Never pick a ``/cowork/cse_`` tab unless the caller names that URL. Occupancy:
open a fresh ``/new`` chat so dump does not steal a live stream. ``/new``
defaults to Cowork (friction 25051); harvest the zip from the chat card **or**
the Cowork Output download — do not treat the landing CSE as a live stream.
"""

from __future__ import annotations

import asyncio
import io
import os
import zipfile
from pathlib import Path

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from claude_bundles.chat_cowork_mode import ensure_chat_compose
from claude_bundles.chat_reply_wait import harvest_assistant, wait_assistant_reply
from claude_bundles.container_skills_zip import pick_download_label
from claude_bundles.cowork_output_download import download_cowork_output
from claude_bundles.liveness_wait import liveness_wait
from claude_bundles.project_ask import send_prompt
from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL, connect_cdp

_COMPOSER = '[data-testid="chat-input"]'
_PROBE_S = 2.0
# Bounds one click or Cowork attempt so the other path can run. Not a failure bound.
_ATTEMPT_MS = 15_000
_LIFE_JS = """() => {
  const ready = document.readyState || "";
  const dom = document.documentElement ? document.documentElement.outerHTML.length : 0;
  let resources = 0;
  try { resources = performance.getEntriesByType("resource").length; } catch (e) {}
  return { ready, dom, resources, url: location.href || "" };
}"""
_READY_RANK = {"loading": 1, "interactive": 2, "complete": 3}

NEW_CHAT_URL = "https://claude.ai/new"
DUMP_PROMPT = """You have a server-side skills directory at /mnt/skills with three trees: public, examples, and user.
First run: find /mnt/skills -maxdepth 2 -type d
Then compress the entire /mnt/skills tree into one zip named claude-skills.zip and offer it as a downloadable artifact.
Keep the zip layout as skills/public, skills/examples, skills/user. Include skills/user even if empty. Do not omit a tree. Do not list files instead of zipping.
After the zip, say USER_SKILL_COUNT=<n> for the number of skill directories under /mnt/skills/user.
"""


class _PageLife:
    """Monotonic navigation signature for one page.

    Tracks the high-water mark of load rank, DOM length, finished resource
    entries, and distinct URLs. A stuck readyState or an open socket does not
    advance the tuple, so a wedged document stalls instead of resetting forever.
    """

    def __init__(self, page: Page) -> None:
        self.page = page
        self.max_rank = 0
        self.max_dom = 0
        self.max_resources = 0
        self.seen_urls: set[str] = set()

    async def signature(self) -> tuple[int, int, int, int]:
        raw = None
        try:
            raw = await asyncio.wait_for(self.page.evaluate(_LIFE_JS), timeout=_PROBE_S)
        except Exception:
            raw = None
        if isinstance(raw, dict):
            rank = _READY_RANK.get(str(raw.get("ready") or ""), 0)
            self.max_rank = max(self.max_rank, rank)
            try:
                dom = int(raw.get("dom") or 0)
            except (TypeError, ValueError):
                dom = 0
            self.max_dom = max(self.max_dom, dom)
            try:
                resources = int(raw.get("resources") or 0)
            except (TypeError, ValueError):
                resources = 0
            self.max_resources = max(self.max_resources, resources)
            url = str(raw.get("url") or "")
            if url:
                self.seen_urls.add(url)
        return (self.max_rank, self.max_dom, self.max_resources, len(self.seen_urls))


async def _composer_ready(page: Page) -> bool:
    try:
        count = await asyncio.wait_for(
            page.locator(_COMPOSER).count(),
            timeout=_PROBE_S,
        )
    except Exception:
        return False
    return count > 0


async def _navigate(page: Page, url: str) -> None:
    """Open ``url`` under the liveness loop.

    ``page.goto`` uses ``wait_until='commit'`` and ``timeout=0`` so Playwright
    does not turn a slow commit into a failure. Completion is the chat composer
    or ``document.readyState === 'complete'``. The inspect loop cancels a goto
    that is still in flight when the wait ends.
    """
    nav = asyncio.create_task(page.goto(url, wait_until="commit", timeout=0))
    life = _PageLife(page)

    async def _error() -> BaseException | None:
        if nav.done() and not nav.cancelled():
            return nav.exception()
        return None

    async def _complete() -> bool:
        if await _composer_ready(page):
            return True
        if nav.done() and not nav.cancelled() and nav.exception() is None:
            rank, _dom, _resources, _urls = await life.signature()
            return rank >= _READY_RANK["complete"]
        return False

    try:
        await liveness_wait(
            progress=life.signature,
            complete=_complete,
            error=_error,
            phase="navigate",
        )
    finally:
        if not nav.done():
            nav.cancel()
            try:
                await nav
            except asyncio.CancelledError:
                pass


async def _wait_composer(page: Page) -> None:
    """Wait until the chat composer is mounted.

    Uses the same inspect loop as navigation. DOM growth, a new URL, load-state
    advance, or a finished resource resets the window. A quiet page stalls.
    There is no ``wait_for(timeout=30_000)`` failure bound.
    """
    life = _PageLife(page)
    await liveness_wait(
        progress=life.signature,
        complete=lambda: _composer_ready(page),
        phase="composer",
    )


async def _reuse_or_open(ctx, chat_url: str) -> Page:
    for page in ctx.pages:
        if chat_url in (page.url or ""):
            await page.bring_to_front()
            return page
    page = await ctx.new_page()
    await _navigate(page, chat_url)
    await page.bring_to_front()
    return page


async def _fresh_chat(ctx) -> Page:
    page = await ctx.new_page()
    await _navigate(page, NEW_CHAT_URL)
    await page.bring_to_front()
    await _wait_composer(page)
    return page


async def _zip_button(page: Page):
    """Return the in-chat skills-zip button, or None when it is not mounted."""
    root = page.locator("main")
    if not await root.count():
        root = page.locator("body")
    buttons = root.get_by_role("button")
    labels: list[str] = []
    count = await buttons.count()
    for i in range(count):
        text = ((await buttons.nth(i).inner_text()) or "").strip()
        aria = ((await buttons.nth(i).get_attribute("aria-label")) or "").strip()
        if aria:
            labels.append(aria)
        elif text:
            labels.append(text)
    chosen = pick_download_label(labels)
    if chosen:
        btn = root.get_by_role("button", name=chosen, exact=True)
        if not await btn.count():
            btn = root.get_by_role("button", name=chosen)
    else:
        btn = root.get_by_role("button", name="Download Claude skills", exact=True)
        if not await btn.count():
            btn = root.get_by_role("button", name="Download", exact=True)
    if not await btn.count():
        return None
    return btn


async def _click_chat_zip_card(page: Page, out: Path, *, timeout_ms: int = 0) -> Path:
    """Click the in-chat skills-zip Download control and save the file.

    The card's visible text is often just ``Download`` while the accessible
    name is ``Download Claude skills``. A chat-title More options button
    shares the title words and sits under the transcript, so a coordinate
    click times out (``subtree intercepts pointer events``). Scope to
    ``main`` and invoke ``HTMLElement.click`` so the row overlay does not
    take the hit.
    """
    btn = await _zip_button(page)
    if btn is None:
        raise RuntimeError(f"skills zip download control not found (url={page.url!r})")
    out.parent.mkdir(parents=True, exist_ok=True)
    async with page.expect_download(timeout=timeout_ms) as dl_info:
        await btn.first.evaluate("el => el.click()")
    download = await dl_info.value
    await download.save_as(str(out))
    return out


async def _save_cowork_zip(
    page: Page, out: Path, *, timeout_ms: int = 0
) -> Path | None:
    """Write Cowork Output bytes when they are a zip (``/new`` → CSE).

    ``timeout_ms=0`` disables Playwright's download deadline. The caller runs
    this under ``liveness_wait``, which stalls only when no progress is visible.
    """
    result = await download_cowork_output(page, timeout_ms=timeout_ms)
    if result is None or not result.content_bytes:
        return None
    raw = result.content_bytes
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    return out


class _ZipHarvest:
    """One zip download driven by the inspect loop, not a fixed retry count.

    A running attempt is cancelled when the wait stalls. Button-not-found is
    not an error event: the loop keeps watching for new buttons, a growing DOM,
    or a growing zip. Each attempt has a short slice so a quiet click falls
    through to Cowork Output. Completion is the attempt's returned zip, never
    a file that was already at the destination. Any other exception fails
    immediately as an error.
    """

    def __init__(self, page: Page, out: Path) -> None:
        self.page = page
        self.out = out
        self.life = _PageLife(page)
        self.task: asyncio.Task | None = None
        self.error: BaseException | None = None
        self.max_file = 0
        self.max_buttons = 0

    def _file_size(self) -> int:
        try:
            if self.out.is_file():
                return self.out.stat().st_size
        except OSError:
            return self.max_file
        return 0

    async def _button_count(self) -> int:
        try:
            root = self.page.locator("main")
            if not await root.count():
                root = self.page.locator("body")
            return int(await root.get_by_role("button").count())
        except Exception:
            return self.max_buttons

    def _capture_task_error(self) -> None:
        if self.error is not None or self.task is None:
            return
        if not self.task.done() or self.task.cancelled():
            return
        exc = self.task.exception()
        if isinstance(exc, (PlaywrightTimeoutError, asyncio.CancelledError)):
            return
        if isinstance(exc, RuntimeError) and "not found" in str(exc):
            return
        if exc is not None:
            self.error = exc

    def _kick(self) -> None:
        self._capture_task_error()
        if self.error is not None:
            return
        if self.task is not None and not self.task.done():
            return
        self.task = asyncio.create_task(self._once())

    async def _once(self) -> Path | None:
        try:
            return await _click_chat_zip_card(
                self.page, self.out, timeout_ms=_ATTEMPT_MS
            )
        except RuntimeError as exc:
            if "not found" not in str(exc):
                raise
        except PlaywrightTimeoutError:
            pass
        return await _save_cowork_zip(self.page, self.out, timeout_ms=_ATTEMPT_MS)

    async def signature(self) -> tuple[int, int, int, int, int, int]:
        self._kick()
        try:
            page_sig = await asyncio.wait_for(self.life.signature(), timeout=_PROBE_S)
        except Exception:
            page_sig = (
                self.life.max_rank,
                self.life.max_dom,
                self.life.max_resources,
                len(self.life.seen_urls),
            )
        try:
            buttons = await asyncio.wait_for(self._button_count(), timeout=_PROBE_S)
        except Exception:
            buttons = self.max_buttons
        self.max_buttons = max(self.max_buttons, buttons)
        self.max_file = max(self.max_file, self._file_size())
        return (*page_sig, self.max_buttons, self.max_file)

    async def complete(self) -> bool:
        task = self.task
        if task is None or not task.done() or task.cancelled():
            return False
        if task.exception() is not None:
            return False
        result = task.result()
        if not isinstance(result, Path):
            return False
        try:
            return result.is_file() and zipfile.is_zipfile(result)
        except (OSError, zipfile.BadZipFile):
            return False

    async def error_check(self) -> BaseException | None:
        self._capture_task_error()
        return self.error

    async def cancel(self) -> None:
        task = self.task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, PlaywrightTimeoutError, RuntimeError):
            return


async def download_skills_zip(page: Page, out: Path) -> Path:
    """Harvest ``claude-skills.zip`` from the chat card or Cowork Output.

    Replaces the fixed six-by-ten-second retry and the 60s ``expect_download``
    deadline. Streaming DOM growth, new buttons, or a growing zip resets the
    inspect window. No progress across one full window stalls. An explicit
    download exception fails immediately.
    """
    partial = out.with_name(out.name + ".partial")
    partial.unlink(missing_ok=True)
    harvest = _ZipHarvest(page, partial)
    try:
        await liveness_wait(
            progress=harvest.signature,
            complete=harvest.complete,
            error=harvest.error_check,
            phase="download",
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial, out)
        return out
    finally:
        await harvest.cancel()
        partial.unlink(missing_ok=True)


async def dump_container_skills(
    *,
    out: Path,
    cdp_url: str = DEFAULT_CDP_URL,
    chat_url: str | None = None,
    download_only: bool = False,
    prompt: str = DUMP_PROMPT,
    timeout_s: int = 360,
    chat_compose: bool = False,
) -> Path:
    """Submit (optional) then download ``claude-skills.zip`` from the chat card.

    Navigation and zip harvest use the inspect-window loop. The assistant reply
    wait is unchanged: ``timeout_s`` stays an idle budget.
    """
    if download_only and not chat_url:
        raise ValueError("download_only requires chat_url")
    if chat_url and "/cowork/cse_" in chat_url and not download_only:
        raise ValueError(
            "refusing to submit on a CSE URL — pass --download-only or omit --chat-url"
        )
    pw, _browser, ctx, _ignored = await connect_cdp(cdp_url)
    try:
        page = await (_reuse_or_open(ctx, chat_url) if chat_url else _fresh_chat(ctx))
        if not download_only:
            await _wait_composer(page)
            if chat_compose:
                # Operator-gated: /new defaults to Cowork; Chat is the standing
                # /mnt/skills dump transport (a23741). Cowork dumps omit user/.
                mode = await ensure_chat_compose(page)
                if not mode.get("ok"):
                    raise RuntimeError(f"ensure_chat_compose failed: {mode}")
                print(f"compose chat url={page.url}", flush=True)
            before = await harvest_assistant(page, min_msg_chars=10)
            await send_prompt(page, prompt)
            await wait_assistant_reply(page, before=before, timeout_s=timeout_s)
            print(f"after send url={page.url}", flush=True)
        await download_skills_zip(page, out)
        return out
    finally:
        await pw.stop()


def default_dump_path(repo: Path) -> Path:
    """Resolve the dump zip path for ``repo``.

    ``CLAUDE_AI_SKILLS_DUMP`` overrides the default
    ``tmp/reviews/claude-skills-latest.zip`` under the checkout. Callers pass
    the result as ``dump_container_skills(out=...)``.
    """
    override = os.environ.get("CLAUDE_AI_SKILLS_DUMP", "").strip()
    if override:
        return Path(override).expanduser()
    return repo / "tmp" / "reviews" / "claude-skills-latest.zip"
