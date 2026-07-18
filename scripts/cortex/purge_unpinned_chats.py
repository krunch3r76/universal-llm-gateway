#!/usr/bin/env python3
"""Purge unpinned claude.ai chats via Jupiter CDP. Best-effort; continues on errors.

Automated path: --registration-id or auto --register for the run duration.
Raw --cdp-url requires --no-register (attended primary only).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles import cdp_registry  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

CHATS_URL = "https://claude.ai/chats"

LIST_JS = """() => {
  const recentsEl = [...document.querySelectorAll('*')].find(
    (el) => el.childElementCount === 0 && (el.textContent || '').trim() === 'Recents'
  );
  const recentsY = recentsEl ? recentsEl.getBoundingClientRect().y : Infinity;
  const rows = [];
  for (const b of document.querySelectorAll('button[aria-label*="More options"]')) {
    const y = b.getBoundingClientRect().y;
    if (y < recentsY) continue;
    rows.push({
      aria: b.getAttribute('aria-label') || '',
      y: Math.round(y),
    });
  }
  rows.sort((a, b) => a.y - b.y);
  return { recentsY, count: rows.length, rows };
}"""

CLICK_MORE_JS = """(aria) => {
  const b = [...document.querySelectorAll('button[aria-label*="More options"]')]
    .find((x) => x.getAttribute('aria-label') === aria);
  if (!b) return { ok: false, step: 'find-more' };
  b.scrollIntoView({ block: 'center' });
  b.click();
  return { ok: true, step: 'more-clicked', aria };
}"""

DELETE_MENU_JS = """() => {
  const del = document.querySelector('[data-testid="delete-session-trigger"]')
    || document.querySelector('[data-testid="delete-chat-trigger"]');
  if (!del) return { ok: false, step: 'delete-trigger' };
  del.click();
  return { ok: true, step: 'delete-trigger', testid: del.getAttribute('data-testid') };
}"""

CONFIRM_JS = """() => {
  const buttons = [...document.querySelectorAll('button,[role=button]')];
  const del = buttons.find(
    (b) => /^delete$/i.test((b.innerText || '').trim()) && b.offsetParent
  );
  if (!del) return { ok: false, step: 'confirm' };
  del.click();
  return { ok: true, step: 'confirm' };
}"""


async def delete_one(page, aria: str) -> dict:
    r1 = await page.evaluate(CLICK_MORE_JS, aria)
    if not r1.get("ok"):
        return r1
    await page.wait_for_timeout(700)
    r2 = await page.evaluate(DELETE_MENU_JS)
    if not r2.get("ok"):
        await page.keyboard.press("Escape")
        return r2
    await page.wait_for_timeout(700)
    r3 = await page.evaluate(CONFIRM_JS)
    await page.wait_for_timeout(1200)
    return {
        "ok": bool(r3.get("ok")),
        "aria": aria,
        "steps": {"more": r1, "trigger": r2, "confirm": r3},
    }


async def run(cdp_url: str, *, dry_run: bool, max_delete: int) -> int:
    pw = await async_playwright().start()
    deleted = 0
    failed = 0
    log: list[dict] = []
    try:
        browser = await pw.chromium.connect_over_cdp(cdp_url)
        page = browser.contexts[0].pages[0]
        await page.goto(CHATS_URL, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(2500)

        listing = await page.evaluate(LIST_JS)
        print(
            json.dumps(
                {
                    "phase": "survey",
                    "recentsY": listing["recentsY"],
                    "unpinned_count": listing["count"],
                    "dry_run": dry_run,
                },
                indent=2,
            ),
            flush=True,
        )

        if dry_run:
            for row in listing["rows"][:20]:
                print(f"  would delete: {row['aria'][:100]}", flush=True)
            if listing["count"] > 20:
                print(f"  ... and {listing['count'] - 20} more", flush=True)
            return 0

        while deleted + failed < max_delete:
            listing = await page.evaluate(LIST_JS)
            if listing["count"] == 0:
                break
            target = listing["rows"][0]
            title = target["aria"].replace("More options for ", "", 1)[:80]
            result = await delete_one(page, target["aria"])
            result["title"] = title
            log.append(result)
            if result.get("ok"):
                deleted += 1
                print(f"deleted ({deleted}): {title}", flush=True)
            else:
                failed += 1
                print(
                    f"failed ({failed}): {title} -> {result.get('steps', result)}",
                    flush=True,
                )
                await page.goto(CHATS_URL, wait_until="domcontentloaded", timeout=90000)
                await page.wait_for_timeout(1500)

        remaining = await page.evaluate(LIST_JS)
        print(
            json.dumps(
                {
                    "phase": "done",
                    "deleted": deleted,
                    "failed": failed,
                    "remaining_unpinned": remaining["count"],
                },
                indent=2,
            ),
            flush=True,
        )
        return 0
    finally:
        await pw.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cdp-url",
        default=None,
        help="Raw CDP URL — only with --no-register (attended primary).",
    )
    parser.add_argument(
        "--register",
        dest="register",
        action="store_true",
        default=True,
        help="Auto-register a lane for this run (default).",
    )
    parser.add_argument(
        "--no-register",
        dest="register",
        action="store_false",
        help="Opt out; requires --cdp-url or --registration-id.",
    )
    parser.add_argument("--registration-id", default="")
    parser.add_argument(
        "--holder",
        default="",
        help="Registry holder (default: purge-<pid>).",
    )
    parser.add_argument("--deregister-on-exit", action="store_true", default=True)
    parser.add_argument(
        "--keep-registration",
        dest="deregister_on_exit",
        action="store_false",
        help="Leave the registration active after exit.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max", type=int, default=500, help="Max delete attempts")
    args = parser.parse_args(argv)

    holder = args.holder.strip() or f"purge-{os.getpid()}"
    reg_id: str | None = None
    cdp_url = args.cdp_url

    try:
        if args.registration_id:
            reg = cdp_registry.reattach(args.registration_id, holder=holder)
            cdp_url = reg.cdp_url
            reg_id = reg.registration_id
            print(f"registration_id={reg.registration_id}", flush=True)
            print(f"cdp_url={cdp_url}", flush=True)
        elif args.register:
            if args.cdp_url:
                parser.error("raw --cdp-url forbidden with --register")
            reg = cdp_registry.register_lane(holder=holder, purpose="purge")
            cdp_url = reg.cdp_url
            reg_id = reg.registration_id
            print(f"registration_id={reg.registration_id}", flush=True)
            print(f"cdp_url={cdp_url}", flush=True)
        else:
            if not cdp_url:
                parser.error("--no-register requires --cdp-url or --registration-id")

        assert cdp_url is not None
        return asyncio.run(run(cdp_url, dry_run=args.dry_run, max_delete=args.max))
    finally:
        if reg_id and args.deregister_on_exit:
            with contextlib.suppress(Exception):
                cdp_registry.deregister_lane(reg_id)


# late import for finally suppress
import contextlib  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
