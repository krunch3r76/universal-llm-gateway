"""Dump the claude.ai code-exec ``/mnt/skills`` tree via a cheap ordinary chat.

Chat is the standing path (every web session mounts ``/mnt/skills`` — confirmed
on ``agent_skill:lead-seat-boot`` a23741). CSE is a fallback only when chat
cannot see the tree. Stargate ``/v1/chat/completions`` artifact grab is
untested — do not treat it as live.

Never pick a ``/cowork/cse_`` tab unless the caller names that URL. Occupancy:
open a fresh ``/new`` chat so dump does not steal a live stream.
"""

from __future__ import annotations

import os
from pathlib import Path

from playwright.async_api import Page

from claude_bundles.chat_reply_wait import harvest_assistant, wait_assistant_reply
from claude_bundles.container_skills_zip import pick_download_label
from claude_bundles.project_ask import send_prompt
from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL, connect_cdp

NEW_CHAT_URL = "https://claude.ai/new"
DUMP_PROMPT = """You have a server-side skills directory at /mnt/skills with three trees: public, examples, and user.
Compress the entire /mnt/skills tree into one zip named claude-skills.zip and offer it as a downloadable artifact.
Keep the zip layout as skills/public, skills/examples, skills/user. Do not omit a tree. Do not list files instead of zipping.
"""


async def _wait_composer(page: Page) -> None:
    await page.locator('[data-testid="chat-input"]').wait_for(timeout=30_000)


async def _reuse_or_open(ctx, chat_url: str) -> Page:
    for page in ctx.pages:
        if chat_url in (page.url or ""):
            await page.bring_to_front()
            return page
    page = await ctx.new_page()
    await page.goto(chat_url, wait_until="domcontentloaded")
    await page.bring_to_front()
    return page


async def _fresh_chat(ctx) -> Page:
    page = await ctx.new_page()
    await page.goto(NEW_CHAT_URL, wait_until="domcontentloaded")
    await page.bring_to_front()
    await _wait_composer(page)
    return page


async def download_skills_zip(page: Page, out: Path) -> Path:
    """Click the in-chat skills-zip card and save the download."""
    buttons = page.get_by_role("button")
    labels: list[str] = []
    count = await buttons.count()
    for i in range(count):
        text = ((await buttons.nth(i).inner_text()) or "").strip()
        aria = (await buttons.nth(i).get_attribute("aria-label")) or ""
        labels.append(text or aria)
    chosen = pick_download_label(labels)
    if chosen:
        btn = page.get_by_role("button", name=chosen)
    else:
        btn = page.get_by_role("button", name="Download Claude skills")
        if not await btn.count():
            btn = page.get_by_role("button", name="Download")
    if not await btn.count():
        raise RuntimeError(
            "skills zip download control not found "
            f"(url={page.url!r} labels={labels[:12]!r})"
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    async with page.expect_download(timeout=60_000) as dl_info:
        await btn.first.click()
    download = await dl_info.value
    await download.save_as(str(out))
    return out


async def dump_container_skills(
    *,
    out: Path,
    cdp_url: str = DEFAULT_CDP_URL,
    chat_url: str | None = None,
    download_only: bool = False,
    prompt: str = DUMP_PROMPT,
    timeout_s: int = 360,
) -> Path:
    """Submit (optional) then download ``claude-skills.zip`` from the chat card."""
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
            before = await harvest_assistant(page, min_msg_chars=10)
            await send_prompt(page, prompt)
            await wait_assistant_reply(page, before=before, timeout_s=timeout_s)
        await download_skills_zip(page, out)
        return out
    finally:
        await pw.stop()


def default_dump_path(repo: Path) -> Path:
    override = os.environ.get("CLAUDE_AI_SKILLS_DUMP", "").strip()
    if override:
        return Path(override).expanduser()
    return repo / "tmp" / "reviews" / "claude-skills-latest.zip"
