"""Paste pointer wakes into a pinned chatgpt.com conversation over CDP 9342.

Does not import ``orion_session`` or ``orion_cli`` (untracked helpers). Uses
``claude_bundles.skills_ui_panel.connect_cdp`` for live delivery only.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Protocol
from urllib.parse import urlparse

DEFAULT_ORION_CDP_URL = os.environ.get("ORION_CDP_URL", "http://127.0.0.1:9342")
PINNED_CONVERSATION_URL = (
    "https://chatgpt.com/c/6ae2e1bc-0c94-83e8-9f21-62467c4935d5"
)
PINNED_CONVERSATION_ID = "6ae2e1bc-0c94-83e8-9f21-62467c4935d5"

_STREAMING_JS = """
() => [...document.querySelectorAll('button')].some(
  b => (b.getAttribute('aria-label') || '').toLowerCase() === 'stop'
)
"""

_APPROVAL_DIALOG_JS = """
() => {
  const dlg = document.querySelector('[role=dialog],[role=alertdialog]');
  if (!dlg) return {present: false, buttons: []};
  const buttons = [...dlg.querySelectorAll('button,[role=button]')]
    .map(b => (b.innerText || '').trim())
    .filter(Boolean);
  return {present: true, buttons};
}
"""


def conversation_id_from_url(url: str) -> str:
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "c":
        return parts[1]
    return ""


def holder_conversation_url(sub: dict[str, Any] | None) -> str:
    """Resolve pinned /c/ URL from subscription holder fields."""
    if not sub:
        return PINNED_CONVERSATION_URL
    for key in ("agent_id", "title", "chat_url"):
        raw = str(sub.get(key) or "").strip()
        if not raw:
            continue
        if raw.startswith("http") and "/c/" in raw:
            return raw.split("?", 1)[0]
        if re.fullmatch(r"[0-9a-f-]{36}", raw, flags=re.I):
            return f"https://chatgpt.com/c/{raw}"
        cid = conversation_id_from_url(raw)
        if cid:
            return f"https://chatgpt.com/c/{cid}"
    return PINNED_CONVERSATION_URL


def build_wake_pointer(*, thread: str, turn: int | str, from_agent: str) -> str:
    sender = (from_agent or "unknown").strip()
    return (
        f"WAKE — agent-bus thread {thread} turn {turn} (from {sender}). "
        f"Use your ulg-code connector: agent_bus_read fetch thread {thread}, "
        f"read turn {turn} only. Reply on thread {thread} to=maestro with "
        f"subject ANSWER|… per that turn. Do not paste letter drafts."
    )


def _refuse(reason: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "reason": reason, "pasted": False, "delivered": False, **extra}


class OrionPage(Protocol):
    url: str

    async def evaluate(self, expression: str) -> Any: ...

    async def keyboard(self) -> Any: ...

    def locator(self, selector: str) -> Any: ...

    async def wait_for_timeout(self, ms: int) -> None: ...


async def _is_streaming(page: OrionPage) -> bool:
    return bool(await page.evaluate(_STREAMING_JS))


async def _paste_and_send(page: OrionPage, text: str) -> None:
    comp = page.locator('[contenteditable="true"]').last
    await comp.click()
    kb = page.keyboard
    await kb.press("Control+A")
    await kb.insert_text(text)
    send = page.locator('form button[aria-label="Send"]').first
    if await send.count() and await send.is_visible():
        await send.click()
        return
    await comp.press("Enter")


async def _handle_approval(page: OrionPage) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + 3.0
    last: dict[str, Any] = {"present": False, "buttons": []}
    while asyncio.get_event_loop().time() < deadline:
        raw = await page.evaluate(_APPROVAL_DIALOG_JS)
        last = raw if isinstance(raw, dict) else last
        if last.get("present"):
            break
        await page.wait_for_timeout(200)
    buttons = list(last.get("buttons") or [])
    if not last.get("present"):
        return {"outcome": "approval_absent", "buttons": buttons}
    if len(buttons) < 2:
        return {"outcome": "approval_unrecognized", "buttons": buttons}
    dlg = page.locator('[role=dialog], [role=alertdialog]').first
    opts = dlg.locator("button, [role=button]")
    await opts.nth(1).click()
    return {
        "outcome": "approval_clicked",
        "buttons": buttons,
        "clicked_index": 1,
        "clicked_text": buttons[1],
    }


async def deliver_orion_wake(
    *,
    thread: str,
    turn: int | str,
    from_agent: str,
    conversation_url: str | None = None,
    cdp_url: str = DEFAULT_ORION_CDP_URL,
    page: OrionPage | None = None,
    connect_cdp=None,
) -> dict[str, Any]:
    """Attach to pinned chat, paste a pointer wake, optional MCP approval click."""
    from agent_seat.registry import normalize_bus_address

    if normalize_bus_address(from_agent) == "orion":
        return _refuse("orion_echo_drop")

    thread_s = str(thread or "").strip()
    if not thread_s or turn in (None, "", 0, "0"):
        return _refuse("bad_options")

    target_url = (conversation_url or PINNED_CONVERSATION_URL).split("?", 1)[0]
    target_id = conversation_id_from_url(target_url)
    if not target_id:
        return _refuse("bad_conversation_url", conversation_url=target_url)

    owns_cdp = False
    pw = browser = None
    if page is None:
        connect = connect_cdp
        if connect is None:
            from claude_bundles.skills_ui_panel import connect_cdp as connect

        try:
            pw, _browser, ctx, _page0 = await connect(cdp_url)
        except Exception as exc:
            return _refuse("cdp_unreachable", error=type(exc).__name__)
        owns_cdp = True
        page = None
        for p in ctx.pages:
            if target_id in (p.url or ""):
                page = p
                break
        if page is None:
            if owns_cdp:
                await pw.stop()
            return _refuse("conversation_missing", conversation_id=target_id)

    assert page is not None
    if target_id not in (page.url or ""):
        if owns_cdp and pw is not None:
            await pw.stop()
        return _refuse("conversation_missing", conversation_id=target_id)

    if await _is_streaming(page):
        if owns_cdp and pw is not None:
            await pw.stop()
        return _refuse("deferred_streaming", pasted=False)

    line = build_wake_pointer(thread=thread_s, turn=turn, from_agent=from_agent)
    try:
        await _paste_and_send(page, line)
        await page.wait_for_timeout(400)
        approval = await _handle_approval(page)
    except Exception as exc:
        if owns_cdp and pw is not None:
            await pw.stop()
        return _refuse("paste_failed", error=type(exc).__name__)

    if owns_cdp and pw is not None:
        await pw.stop()

    if approval.get("outcome") == "approval_unrecognized":
        return {
            "ok": False,
            "reason": "approval_unrecognized",
            "pasted": True,
            "delivered": False,
            "approval": approval,
            "pointer_len": len(line),
        }

    return {
        "ok": True,
        "reason": "delivered",
        "pasted": True,
        "delivered": True,
        "approval": approval,
        "pointer_len": len(line),
        "conversation_id": target_id,
    }
