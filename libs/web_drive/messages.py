"""Messages interface on the paired Google Messages Chrome.

``list_conversations`` is the since-window: each row's timestamp is when that
conversation was last active. ``read_messages`` opens one conversation and
returns the bubbles the page has rendered. ``send_message`` is on the
interface and does not touch the page unless the harness grant passes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime
from typing import Any

from playwright.async_api import Page, async_playwright

from web_drive.harness import SendAuthorization, require_send_authorization
from web_drive.messages_time import label_text, parse_list_time, parse_since

_INBOX = "https://messages.google.com/web/conversations"
_DEFAULT_CDP = "http://127.0.0.1:19250"

_LIST_JS = """
() => [...document.querySelectorAll('mws-conversation-list-item')].map(el => {
  const link = el.querySelector('a.list-item');
  const href = link ? (link.getAttribute('href') || '') : '';
  const name = el.querySelector('h2.name span');
  const stamp = el.querySelector('mws-relative-timestamp');
  const id = href.split('/').filter(Boolean).pop() || '';
  return {
    id,
    href,
    title: name ? (name.textContent || '').trim() : '',
    active_label: stamp ? (stamp.innerText || '').trim() : '',
  };
}).filter(row => row.id)
"""

_BUBBLES_JS = """
() => [...document.querySelectorAll('mws-message-wrapper')].map(el => ({
  id: el.getAttribute('msg-id') || '',
  outgoing: el.classList.contains('is-outgoing'),
  text: (el.innerText || '').trim(),
}))
"""


def _cdp(explicit: str | None) -> str:
    return explicit or os.environ.get("MESSAGES_CDP") or _DEFAULT_CDP


def _paired(page: Page) -> bool:
    url = (page.url or "").lower()
    return (
        "messages.google.com/web/conversations" in url
        and "welcome" not in url
        and "accounts.google.com" not in url
    )


async def _inbox(cdp: str) -> tuple[Any, Page]:
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(cdp)
    page = next((p for p in browser.contexts[0].pages if _paired(p)), None)
    if page is None:
        await pw.stop()
        raise RuntimeError(
            "no live Messages conversations tab — paired inbox is /web/conversations"
        )
    return pw, page


def _row(raw: dict[str, str], now: datetime) -> dict[str, Any]:
    active_at = parse_list_time(raw.get("active_label") or "", now)
    return {
        "id": raw["id"],
        "title": raw.get("title") or "",
        "active_label": label_text(raw.get("active_label") or ""),
        "active_at": active_at.isoformat() if active_at else None,
    }


async def list_conversations(
    *,
    since: datetime | None = None,
    cdp: str | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Conversations on the paired inbox, optionally active at or after ``since``."""
    clock = now or datetime.now().astimezone()
    pw, page = await _inbox(_cdp(cdp))
    try:
        rows = await page.evaluate(_LIST_JS)
    finally:
        await pw.stop()
    out = [_row(row, clock) for row in rows]
    if since is None:
        return out
    if since.tzinfo is None:
        since = since.replace(tzinfo=clock.tzinfo)
    return [
        row
        for row in out
        if row["active_at"] is not None
        and datetime.fromisoformat(row["active_at"]) >= since
    ]


async def read_messages(
    conversation_id: str,
    *,
    since: datetime | None = None,
    cdp: str | None = None,
) -> dict[str, Any]:
    """Open one conversation and return the bubbles rendered on the page."""
    clock = datetime.now().astimezone()
    listed = await list_conversations(cdp=cdp, now=clock)
    match = next((row for row in listed if row["id"] == conversation_id), None)
    if match is None:
        raise RuntimeError(
            f"conversation {conversation_id} is not on the Messages list"
        )
    if since is not None and match["active_at"] is not None:
        bound = since if since.tzinfo else since.replace(tzinfo=clock.tzinfo)
        if datetime.fromisoformat(match["active_at"]) < bound:
            return {"conversation": match, "messages": []}

    pw, page = await _inbox(_cdp(cdp))
    try:
        link = page.locator(f'a.list-item[href$="{conversation_id}"]')
        await link.first.click()
        await page.wait_for_selector("mws-message-wrapper", timeout=12_000)
        bubbles = await page.evaluate(_BUBBLES_JS)
    finally:
        await pw.stop()
    return {"conversation": match, "messages": bubbles}


async def send_message(
    conversation_id: str,
    body: str,
    *,
    agent: str,
    authorization: SendAuthorization | None = None,
    cdp: str | None = None,
) -> dict[str, Any]:
    """Send ``body`` in one conversation. The harness runs before the page."""
    grant = require_send_authorization(authorization, agent)
    text = body.strip()
    if not text:
        raise ValueError("send body is empty")

    pw, page = await _inbox(_cdp(cdp))
    try:
        link = page.locator(f'a.list-item[href$="{conversation_id}"]')
        await link.first.click()
        box = page.locator(
            "[data-e2e-message-input-box], [contenteditable='true']"
        ).last
        await box.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await page.keyboard.type(text)
        send = page.locator(
            "button[data-e2e-send-button], button[aria-label='Send']"
        ).last
        await send.click()
    finally:
        await pw.stop()
    return {
        "ok": True,
        "conversation_id": conversation_id,
        "agent": grant.agent,
        "scope": grant.scope,
    }


def _print_counts(rows: list[dict[str, Any]]) -> None:
    parsed = sum(1 for row in rows if row["active_at"])
    print(json.dumps({"conversations": len(rows), "with_active_at": parsed}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("list", "read", "send"))
    parser.add_argument("--since", default=None, help="ISO time, or 24h / 7d")
    parser.add_argument("--conversation", default=None)
    parser.add_argument("--body", default=None)
    parser.add_argument("--agent", default=None)
    parser.add_argument("--scope", choices=("all", "subsequent"), default=None)
    parser.add_argument("--cdp", default=None)
    parser.add_argument(
        "--counts",
        action="store_true",
        help="list: print counts only, not titles or message text",
    )
    args = parser.parse_args()
    now = datetime.now().astimezone()
    since = parse_since(args.since, now) if args.since else None

    if args.action == "list":
        rows = asyncio.run(list_conversations(since=since, cdp=args.cdp, now=now))
        if args.counts:
            _print_counts(rows)
        else:
            print(json.dumps(rows, indent=2))
        return 0

    if not args.conversation:
        parser.error("--conversation is required")
    if args.action == "read":
        payload = asyncio.run(
            read_messages(args.conversation, since=since, cdp=args.cdp)
        )
        if args.counts:
            print(json.dumps({"messages": len(payload["messages"])}))
        else:
            print(json.dumps(payload, indent=2))
        return 0

    if args.scope is None or not args.agent:
        raise SystemExit(
            "harness: no send until this agent is authorized for all "
            "messages, or for subsequent messages"
        )
    auth = SendAuthorization(agent=args.agent, scope=args.scope)
    payload = asyncio.run(
        send_message(
            args.conversation,
            args.body or "",
            agent=args.agent,
            authorization=auth,
            cdp=args.cdp,
        )
    )
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
