"""Attach, probe, harvest, and paste a live grok.com chat tab on attended CDP.

Selectors are bound from a live probe — not guessed offline. Login-wall text
is an auth miss; credential fields are never filled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from claude_bundles.skills_ui_panel import connect_cdp

DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_GROK_CHAT_ID = "47794c69-9fcc-4481-b1a6-f6c9cbf8b768"
LOGIN_WALL_RE = re.compile(
    r"sign in to request access|this chat is private|log into your account"
    r"|email or phone|login with google",
    re.I,
)

PROBE_JS = """
() => {
  const body = (document.body && document.body.innerText) || "";
  const controls = [...document.querySelectorAll(
    "button, textarea, [contenteditable='true'], input"
  )].slice(0, 60).map((el) => ({
    tag: el.tagName,
    type: el.getAttribute("type"),
    aria: el.getAttribute("aria-label"),
    placeholder: el.getAttribute("placeholder"),
    testid: el.getAttribute("data-testid"),
    text: (el.innerText || "").trim().slice(0, 80),
    visible: !!(el.offsetWidth || el.offsetHeight),
  }));
  return {
    url: location.href,
    title: document.title,
    body_start: body.slice(0, 2000),
    login_wall: /sign in to request access|this chat is private|log into your account/i.test(body),
    composer_count: document.querySelectorAll("textarea, [contenteditable='true']").length,
    stop_buttons: [...document.querySelectorAll("button")].filter((b) =>
      /stop/i.test((b.innerText || "") + " " + (b.getAttribute("aria-label") || ""))
    ).map((b) => (b.getAttribute("aria-label") || b.innerText || "").trim().slice(0, 60)),
    controls,
  };
}
"""

# Bound 2026-08-14 on live grok.com/c/47794c69-… (signed-in :9222 probe):
# assistant-message / user-message / chat-input / Ask Grok anything.
HARVEST_JS = """
() => {
  const body = (document.body && document.body.innerText) || "";
  const url = location.href;
  const loginWall = /sign in to request access|this chat is private|log into your account/i.test(body)
    || /accounts\\.(x\\.ai|google\\.com)/.test(url);
  const stop = [...document.querySelectorAll("button")].some((b) => {
    const label = ((b.innerText || "") + " " + (b.getAttribute("aria-label") || "")).trim();
    return /^(stop|stop generating)$/i.test(label) || /stop generating/i.test(label);
  });
  const streaming = stop || !!document.querySelector("[aria-busy='true']");
  const asst = [...document.querySelectorAll("[data-testid='assistant-message']")];
  const last = asst.length ? (asst[asst.length - 1].innerText || "").trim() : "";
  const composer = document.querySelector(
    "[data-testid='chat-input'] [contenteditable='true'], [aria-label='Ask Grok anything']"
  ) || document.querySelector("[contenteditable='true']");
  return {
    url,
    login_wall: loginWall,
    streaming,
    stop,
    n: asst.length,
    last_assistant: last.slice(0, 20000),
    composer_tag: composer ? composer.tagName : null,
    signed_in: !loginWall && /\\/c\\//.test(url) && !!composer,
  };
}
"""


class GrokAuthError(RuntimeError):
    """Attended tab is not a signed-in grok.com chat."""


@dataclass(frozen=True)
class GrokHarvest:
    url: str
    login_wall: bool
    streaming: bool
    stop: bool
    n: int
    last_assistant: str
    signed_in: bool


def conversation_id_from_url(url: str) -> str:
    """Return the grok.com ``/c/<id>`` segment, or empty."""
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "c":
        return parts[1]
    return ""


def is_signed_in(probe: dict[str, Any]) -> bool:
    """True when the live page is the target chat and not a login wall."""
    url = str(probe.get("url") or "")
    host = urlparse(url).hostname or ""
    if host.endswith("accounts.google.com") or host.endswith("accounts.x.ai"):
        return False
    if conversation_id_from_url(url) == "":
        return False
    if probe.get("login_wall"):
        return False
    text = str(probe.get("body_start") or "")
    if LOGIN_WALL_RE.search(text):
        return False
    return bool(probe.get("composer_count", 0) > 0 or "/c/" in url)


_ATTACH_FALLBACK_HOSTS = (
    "grok.com",
    "accounts.x.ai",
    "accounts.google.com",
)


async def attach_grok_page(
    *,
    cdp_url: str = DEFAULT_CDP_URL,
    url_substr: str,
):
    """Connect to attended Chrome and return the existing grok tab.

    Never creates a tab and never navigates a claude.ai page. Mid-SSO pages
    (accounts.x.ai / Google identifier) are attached so ``--probe`` can report
    ``signed_in=false``; ``require_signed_in`` still fails closed.
    """
    pw, browser, context, _page0 = await connect_cdp(cdp_url)
    pages = list(context.pages)
    for page in pages:
        if url_substr and url_substr in (page.url or ""):
            return pw, browser, context, page
    for page in pages:
        url = page.url or ""
        if "claude.ai" in url:
            continue
        if any(host in url for host in _ATTACH_FALLBACK_HOSTS):
            return pw, browser, context, page
    await pw.stop()
    raise GrokAuthError(f"no open tab containing {url_substr!r} on {cdp_url}")


async def probe_dom(page) -> dict[str, Any]:
    """Dump visible composer / message / idle controls from the live SPA."""
    return await page.evaluate(PROBE_JS)


async def harvest(page) -> GrokHarvest:
    """Read last-assistant text and idle/streaming flags from the live tab."""
    raw = await page.evaluate(HARVEST_JS)
    return GrokHarvest(
        url=str(raw.get("url") or ""),
        login_wall=bool(raw.get("login_wall")),
        streaming=bool(raw.get("streaming")),
        stop=bool(raw.get("stop")),
        n=int(raw.get("n") or 0),
        last_assistant=str(raw.get("last_assistant") or ""),
        signed_in=bool(raw.get("signed_in")),
    )


async def require_signed_in(page, *, grok_url: str) -> GrokHarvest:
    """Fail closed when the tab is a login wall or off the chat URL."""
    shot = await harvest(page)
    if not shot.signed_in:
        raise GrokAuthError(
            f"grok tab not signed in url={shot.url!r} wall={shot.login_wall} "
            f"expected={grok_url!r}"
        )
    return shot


async def wait_idle(page, *, timeout_s: float = 180.0, poll_s: float = 1.0) -> GrokHarvest:
    """Poll until streaming/stop clears, then return the idle harvest."""
    import asyncio

    deadline = asyncio.get_event_loop().time() + timeout_s
    last = await harvest(page)
    stable = 0
    while asyncio.get_event_loop().time() < deadline:
        last = await harvest(page)
        if last.login_wall:
            raise GrokAuthError(f"login wall during wait_idle url={last.url!r}")
        if not last.streaming and not last.stop:
            stable += 1
            if stable >= 2:
                return last
        else:
            stable = 0
        await asyncio.sleep(poll_s)
    raise TimeoutError(f"grok turn still streaming after {timeout_s}s url={last.url!r}")


async def paste_and_send(page, text: str) -> None:
    """Fill the live ``chat-input`` composer (Tiptap) and submit."""
    locator = page.locator(
        "[data-testid='chat-input'] [contenteditable='true'], "
        "[aria-label='Ask Grok anything']"
    ).last
    await locator.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.insert_text(text)
    send = page.locator(
        "[data-testid='chat-input'] button[type='submit'], "
        "button[aria-label*='Send' i]"
    ).last
    if await send.count() > 0 and await send.is_enabled():
        await send.click()
        return
    await locator.press("Enter")
