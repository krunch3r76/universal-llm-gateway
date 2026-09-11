#!/usr/bin/env python3
"""Poll Jupiter DoorDash orders until Thu breakfast scheduled order confirms.

Arm:
  scripts/watch-supervise.sh start --label 9758-breakfast -- \\
    $HOME/.venvs/universal/bin/python scripts/watch-9758-doordash-breakfast.py \\
    --label 9758-breakfast --thread 9758 --max-hours 7
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml
from bus_watch.state import write_state

_REPO = Path(__file__).resolve().parents[1]
_CDP = os.environ.get("DOORDASH_CDP_URL", "http://127.0.0.1:19270")
_AGENT_BUS_SOCK = os.environ.get("AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock")
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"
# 6:15–6:45 AM and early-7 fallback if vendor opens at 7:00
_WINDOW_RE = re.compile(
    r"(6:\s*(?:1[5-9]|[2-3]\d|4[0-5])|7:\s*(?:0\d|1[0-5]))\s*(?:AM|am)?",
    re.I,
)


def _token() -> str:
    with open(_MCP_YAML) as f:
        cfg = yaml.safe_load(f)
    token = str(cfg.get("AGENT_BUS_TOKEN") or "").strip()
    if not token:
        raise SystemExit(f"AGENT_BUS_TOKEN missing in {_MCP_YAML}")
    return token


def _bus_post(token: str, thread: str, body: str) -> None:
    payload = {
        "thread": thread,
        "from": "cursor",
        "to": "cursor",
        "subject": "BREAKFAST PLACED — DoorDash confirmation",
        "body": body,
    }
    with httpx.Client(
        transport=httpx.HTTPTransport(uds=_AGENT_BUS_SOCK),
        timeout=30.0,
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        r = client.post("http://localhost/threads/send", json=payload)
        r.raise_for_status()


async def _read_orders_text() -> str:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(_CDP)
        page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if pg.url.startswith("https://www.doordash.com/orders"):
                    page = pg
                    break
        if page is None:
            ctx = browser.contexts[0]
            page = await ctx.new_page()
            await page.goto("https://www.doordash.com/orders", wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)
        else:
            await page.reload(wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)
        return await page.evaluate("() => document.body.innerText")


def _looks_confirmed(text: str) -> dict[str, Any] | None:
    lower = text.lower()
    if "sign in" in lower and "flintridge" not in lower:
        return {"reject": "sign_in_page"}
    breakfast_hints = (
        "bill",
        "mcdonald",
        "breakfast club",
        "starbucks",
        "omelette",
        "egg",
        "hash brown",
        "skillet",
    )
    if not any(h in lower for h in breakfast_hints):
        return None
    if (
        "scheduled" not in lower
        and "sep 10" not in lower
        and "september 10" not in lower
        and "today" not in lower
        and "tomorrow" not in lower
    ):
        return None
    if not _WINDOW_RE.search(text):
        return None
    return {"snippet": text[:2500]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="9758-breakfast")
    parser.add_argument("--thread", default="9758")
    parser.add_argument("--poll-seconds", type=int, default=120)
    parser.add_argument("--max-hours", type=float, default=7.0)
    parser.add_argument(
        "--state-file",
        default="",
        help="durable JSON status path (watch-supervise sets this)",
    )
    args = parser.parse_args()
    token = _token()
    deadline = time.time() + args.max_hours * 3600
    state_path = (
        Path(args.state_file)
        if str(args.state_file).strip()
        else _REPO / "tmp" / "watchers" / f"{args.label}.state.json"
    )

    write_state(
        state_path,
        label=args.label,
        thread=args.thread,
        status="polling",
        cdp=_CDP,
    )

    print(
        f"watching doordash orders thread={args.thread} label={args.label!r} "
        f"poll_s={args.poll_seconds} max_hours={args.max_hours}",
        flush=True,
    )

    while time.time() < deadline:
        hit: dict[str, Any] | None = None
        try:
            text = asyncio.run(_read_orders_text())
            hit = _looks_confirmed(text)
        except Exception as exc:
            print(f"poll error: {exc}", flush=True)
        if isinstance(hit, dict) and hit.get("reject") == "sign_in_page":
            print("poll: sign-in page (session lost?)", flush=True)
            write_state(
                state_path,
                label=args.label,
                thread=args.thread,
                status="polling",
                last_status="sign_in_page",
            )
            time.sleep(args.poll_seconds)
            continue
        if hit:
            body = (
                "DoorDash breakfast order confirmation observed on /orders while operator slept.\n\n"
                f"```\n{hit['snippet']}\n```"
            )
            bus_err = ""
            try:
                _bus_post(token, args.thread, body)
            except Exception as exc:
                bus_err = str(exc)
                print(f"bus post error: {bus_err}", flush=True)
            write_state(
                state_path,
                label=args.label,
                thread=args.thread,
                status="complete",
                last_status="matched",
                bus_post_error=bus_err or None,
            )
            print("consult complete: breakfast confirmation observed", flush=True)
            return 0 if not bus_err else 1
        print("poll: no_match", flush=True)
        write_state(
            state_path,
            label=args.label,
            thread=args.thread,
            status="polling",
            last_status="no_match",
        )
        time.sleep(args.poll_seconds)

    write_state(
        state_path,
        label=args.label,
        thread=args.thread,
        status="complete",
        verdict="timeout",
    )
    print("stall-pop: breakfast watch timed out without confirmation", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
