#!/usr/bin/env python3
"""Harvest ESS My Schedule weeks from a standing Jupiter Chrome CDP session.

Reattach only — does not launch Chrome or enter credentials.
Skips PingFederate SSO tabs even when RelayState contains reflexisinc.com.
Opens ESS My Schedule by rail text (not COMPASS ``active-submodule``).

    HOME=$HOME PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright \\
      $HOME/.venvs/universal/bin/python scripts/walgreens/ess_my_schedule_harvest.py \\
      --cdp http://127.0.0.1:9260 --weeks 6 --out /tmp/ess-my-schedule.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from playwright.async_api import async_playwright

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from ess_schedule_parse import parse_day, parse_week_label  # noqa: E402

_FRAME_NEEDLE = "ess_emp_schedule.jsp"
_EXTRACT_JS = """() => {
  const week = document.querySelector('.weekDateLabel')?.innerText || '';
  const dates = [...document.querySelectorAll('.dateContainer.empShiftRow')];
  return {
    week,
    rows: dates.map((el) => ({
      date: (el.innerText || '').trim(),
      text: (el.parentElement && el.parentElement.innerText) || '',
    })),
  };
}"""


def _is_sso(url: str) -> bool:
    """True when the tab is PingFederate Sign On, not a live Reflexis roster."""
    return "sso.walgreens.com" in url or "/idp/SSO.saml2" in url


def _pick_reflexis_page(ctx):
    """Prefer a live Reflexis tab. Skip SSO RelayState URLs that also contain reflexisinc.com."""
    return next(
        (
            pg
            for pg in ctx.pages
            if "reflexisinc.com" in (pg.url or "") and not _is_sso(pg.url or "")
        ),
        None,
    )


def _pick_wconnect_page(ctx):
    """W Connect My Schedule shell — used to re-launch Workforce Scheduler."""
    return next(
        (pg for pg in ctx.pages if "wconnect.walgreens.com" in (pg.url or "")),
        None,
    )


async def _launch_workforce_scheduler(page) -> None:
    """Click Launch Workforce Scheduler on W Connect so Reflexis My Work opens."""
    clicked = await page.evaluate(
        """() => {
          const el = [...document.querySelectorAll('a,button,span')]
            .find((n) => (n.innerText || '').trim() === 'Launch Workforce Scheduler');
          if (!el) return false;
          el.click();
          return true;
        }"""
    )
    if not clicked:
        raise RuntimeError("Launch Workforce Scheduler not found on W Connect")


async def _wait_live_reflexis(ctx, timeout_s: float = 20.0):
    """Wait until a Reflexis tab is stable (not an SSO hop that briefly looks live)."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        page = _pick_reflexis_page(ctx)
        if page is not None:
            try:
                await page.wait_for_timeout(400)
                url = page.url or ""
                if "reflexisinc.com" in url and not _is_sso(url):
                    await page.evaluate("() => document.readyState")
                    return page
            except Exception:
                pass
        await asyncio.sleep(0.25)
    raise RuntimeError(
        "Workforce Scheduler did not open a live Reflexis tab; Sign On may have lapsed"
    )


async def _week_label(frame) -> str:
    raw = await frame.locator(".weekDateLabel").first.inner_text()
    return raw.replace("\xa0", " ").strip()


async def _ensure_my_schedule(page):
    frame = next((f for f in page.frames if _FRAME_NEEDLE in (f.url or "")), None)
    if frame is not None:
        return frame
    await page.evaluate(
        """() => {
          const byText = (label) => [...document.querySelectorAll('li')]
            .find((el) => (el.innerText || '').trim() === label);
          let ess = byText('ESS');
          if (!ess) {
            const more = byText('More');
            if (more) more.click();
          }
        }"""
    )
    await page.wait_for_timeout(400)
    await page.evaluate(
        """() => {
          const ess = [...document.querySelectorAll('li')]
            .find((el) => (el.innerText || '').trim() === 'ESS');
          if (ess) ess.click();
        }"""
    )
    await page.wait_for_timeout(1200)
    await page.evaluate(
        """() => {
          const header = [...document.querySelectorAll('mat-expansion-panel-header')]
            .find((el) => (el.innerText || '').trim() === 'My Schedule');
          if (header) header.click();
        }"""
    )
    for _ in range(16):
        await page.wait_for_timeout(250)
        frame = next((f for f in page.frames if _FRAME_NEEDLE in (f.url or "")), None)
        if frame is not None:
            return frame
    raise RuntimeError("My Schedule iframe not found; stay on signed-in My Work")


async def _wait_rows(frame, minimum: int = 7) -> int:
    for _ in range(24):
        count = await frame.locator(".dateContainer.empShiftRow").count()
        if count >= minimum:
            return count
        await frame.page.wait_for_timeout(250)
    return await frame.locator(".dateContainer.empShiftRow").count()


async def _extract_week(frame) -> dict:
    await _wait_rows(frame)
    payload = await frame.evaluate(_EXTRACT_JS)
    week_of = parse_week_label(payload["week"])
    days = [parse_day(row["date"], row["text"], week_of) for row in payload["rows"]]
    return {
        "week_of": week_of.isoformat(),
        "week_label": payload["week"].strip(),
        "days": days,
    }


async def _click_week(frame, title: str, css: str) -> bool:
    return await frame.evaluate(
        """({title, css}) => {
          const el = document.querySelector(`span[title="${title}"]`)
            || document.querySelector(css);
          if (!el) return false;
          el.click();
          return true;
        }""",
        {"title": title, "css": css},
    )


async def _next_week(frame, before: str) -> str:
    clicked = await _click_week(frame, "Next Week", ".ws-iconArrowDateRight")
    if not clicked:
        raise RuntimeError("Next Week control not found")
    for _ in range(20):
        await frame.page.wait_for_timeout(250)
        label = await _week_label(frame)
        if label and label != before:
            return label
    return before


async def _prev_week(frame, before: str) -> str:
    clicked = await _click_week(frame, "Previous Week", ".ws-iconArrowDateLeft")
    if not clicked:
        raise RuntimeError("Previous Week control not found")
    for _ in range(20):
        await frame.page.wait_for_timeout(250)
        label = await _week_label(frame)
        if label and label != before:
            return label
    return before


async def _rewind_to_today(frame) -> None:
    today = date.today()
    for _ in range(16):
        label = await _week_label(frame)
        week_of = parse_week_label(label)
        week_end = week_of + timedelta(days=6)
        if week_of <= today <= week_end:
            return
        if week_of > today:
            nxt = await _prev_week(frame, label)
            if nxt == label:
                return
            continue
        return


async def harvest(cdp: str, weeks: int) -> dict:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.connect_over_cdp(cdp)
        ctx = browser.contexts[0]
        page = _pick_reflexis_page(ctx)
        if page is None:
            wconnect = _pick_wconnect_page(ctx)
            if wconnect is None:
                raise RuntimeError(
                    "no Reflexis or W Connect tab on CDP; open Workforce Scheduler first"
                )
            await wconnect.bring_to_front()
            await _launch_workforce_scheduler(wconnect)
            page = await _wait_live_reflexis(ctx)
        await page.bring_to_front()
        frame = await _ensure_my_schedule(page)
        await _rewind_to_today(frame)
        collected = []
        seen = set()
        label = await _week_label(frame)
        for _ in range(weeks):
            week = await _extract_week(frame)
            if week["week_of"] in seen:
                break
            seen.add(week["week_of"])
            if not week["days"]:
                break
            collected.append(week)
            nxt = await _next_week(frame, label)
            if nxt == label:
                break
            label = nxt
            await frame.page.wait_for_timeout(400)
        await browser.close()
    return {
        "harvested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cdp": cdp,
        "frame_url": f"https://knlwalgreens.reflexisinc.com/RWS4/ess/{_FRAME_NEEDLE}",
        "weeks": collected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp", default="http://127.0.0.1:9260")
    parser.add_argument("--weeks", type=int, default=6)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    payload = asyncio.run(harvest(args.cdp, args.weeks))
    out = Path(args.out)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    scheduled = sum(
        1 for week in payload["weeks"] for day in week["days"] if day["scheduled"]
    )
    print(
        json.dumps(
            {
                "out": str(out),
                "weeks": len(payload["weeks"]),
                "scheduled_days": scheduled,
                "window": [
                    payload["weeks"][0]["week_of"] if payload["weeks"] else None,
                    payload["weeks"][-1]["week_of"] if payload["weeks"] else None,
                ],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
