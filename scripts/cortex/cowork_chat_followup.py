#!/usr/bin/env python3
"""Follow up on an existing claude.ai Cowork/chat URL via CDP (no /new)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles.chat_session_hygiene import pick_chat_page  # noqa: E402
from claude_bundles.project_ask_conversation import (  # noqa: E402
    project_followup_on_page,
    send_followup_paste_half,
)
from claude_bundles.skills_ui_panel import connect_cdp  # noqa: E402


async def run(
    cdp_url: str,
    chat_url: str,
    prompt: str,
    *,
    timeout_s: int,
    paste_only: bool,
) -> dict:
    pw, _browser, ctx, _page0 = await connect_cdp(cdp_url)
    try:
        page = await pick_chat_page(ctx)
        goto_ms = min(90_000, max(5_000, timeout_s * 1000))
        await page.goto(chat_url, wait_until="domcontentloaded", timeout=goto_ms)
        await page.wait_for_timeout(2500)
        if paste_only:
            paste = await send_followup_paste_half(page, prompt)
            return {
                "ok": bool(paste.get("send_verified")),
                "send_verified": bool(paste.get("send_verified")),
                "streaming_at_paste": paste.get("streaming_at_paste"),
                "verification_marker": paste.get("verification_marker"),
                "url": paste.get("url") or chat_url,
                "error": paste.get("error"),
                "paste_only": True,
            }
        result = await project_followup_on_page(
            page,
            prompt,
            project_uuid="",
            timeout_s=timeout_s,
            min_growth=80,
            min_body=200,
        )
        return result.as_dict() if hasattr(result, "as_dict") else {
            "ok": result.ok,
            "body_len": result.body_len,
            "url": result.url,
            "error": result.error,
            "body": result.body,
            "model": result.model,
        }
    finally:
        await pw.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-url", required=True)
    parser.add_argument("--chat-url", required=True)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument("--timeout-s", type=int, default=600)
    parser.add_argument(
        "--paste-only",
        action="store_true",
        help=(
            "Send prompt and verify marker presence; do not wait for assistant "
            "reply (BREAK_IN / advisory pastes). Hard-walls at --timeout-s."
        ),
    )
    args = parser.parse_args()
    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    # Hard wall: idle wait inside project_followup_on_page can refresh past
    # timeout_s while streaming; paste-only still needs a process watchdog.
    wall_s = args.timeout_s + (15 if args.paste_only else 30)
    try:
        summary = asyncio.run(
            asyncio.wait_for(
                run(
                    args.cdp_url,
                    args.chat_url,
                    prompt,
                    timeout_s=args.timeout_s,
                    paste_only=args.paste_only,
                ),
                timeout=wall_s,
            )
        )
    except TimeoutError:
        summary = {
            "ok": False,
            "error": "wall_clock_exceeded",
            "timeout_s": args.timeout_s,
            "wall_s": wall_s,
            "paste_only": args.paste_only,
        }
    print(json.dumps({k: v for k, v in summary.items() if k != "body"}, indent=2))
    if args.out and summary.get("body"):
        Path(args.out).write_text(summary["body"] + "\n", encoding="utf-8")
        print(f"out={args.out}", flush=True)
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
