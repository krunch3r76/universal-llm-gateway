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

from cdp_ask.attended_operator import (  # noqa: E402
    AttendedResolveRefused,
    refused_to_http_body,
    resolve_attended_operator,
    success_to_http_body,
)
from claude_bundles.chat_session_hygiene import pick_chat_page  # noqa: E402
from claude_bundles.project_ask_conversation import (  # noqa: E402
    project_followup_on_page,
    send_followup_paste_half,
)
from claude_bundles.skills_ui_panel import connect_cdp  # noqa: E402


def _resolve_target(
    cdp_url: str | None, chat_url: str | None
) -> tuple[str, str, str] | dict:
    """Return ``(cdp_url, chat_url, target_binding)`` or refusal dict."""
    if cdp_url and chat_url:
        return cdp_url, chat_url, "explicit"
    if cdp_url or chat_url:
        return {
            "ok": False,
            "error": "ambiguous_identity",
            "detail": "Provide both --cdp-url and --chat-url for explicit override",
        }
    outcome = resolve_attended_operator()
    if isinstance(outcome, AttendedResolveRefused):
        body = refused_to_http_body(outcome)
        return {"ok": False, "error": body["code"], **body}
    success = success_to_http_body(outcome)
    return success["cdp_url"], success["chat_url"], "resolver"


async def run(
    cdp_url: str,
    chat_url: str,
    prompt: str,
    *,
    model: str,
    timeout_s: int,
    paste_only: bool,
    target_binding: str,
) -> dict:
    pw, _browser, ctx, _page0 = await connect_cdp(cdp_url)
    try:
        page = await pick_chat_page(ctx)
        goto_ms = min(90_000, max(5_000, timeout_s * 1000))
        await page.goto(chat_url, wait_until="domcontentloaded", timeout=goto_ms)
        await page.wait_for_timeout(2500)
        if paste_only:
            paste = await send_followup_paste_half(
                page, prompt, target_binding=target_binding
            )
            return {
                "ok": bool(paste.get("send_verified")),
                "send_verified": bool(paste.get("send_verified")),
                "streaming_at_paste": paste.get("streaming_at_paste"),
                "verification_marker": paste.get("verification_marker"),
                "url": paste.get("url") or chat_url,
                "error": paste.get("error"),
                "paste_only": True,
                "target_binding": paste.get("target_binding", target_binding),
            }
        result = await project_followup_on_page(
            page,
            prompt,
            model=model,
            project_uuid="",
            timeout_s=timeout_s,
            min_growth=80,
            min_body=200,
        )
        base = result.as_dict() if hasattr(result, "as_dict") else {
            "ok": result.ok,
            "body_len": result.body_len,
            "url": result.url,
            "error": result.error,
            "body": result.body,
            "model": result.model,
        }
        base["target_binding"] = target_binding
        return base
    finally:
        await pw.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cdp-url",
        default="",
        help="Explicit CDP port override (requires --chat-url)",
    )
    parser.add_argument(
        "--chat-url",
        default="",
        help="Explicit CSE URL override (requires --cdp-url)",
    )
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument("--timeout-s", type=int, default=600)
    parser.add_argument(
        "--model",
        required=True,
        help="Picker model slug for attestation parity (e.g. opus-5, fable-5).",
    )
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
    resolved = _resolve_target(
        (args.cdp_url or "").strip() or None,
        (args.chat_url or "").strip() or None,
    )
    if isinstance(resolved, dict):
        print(json.dumps(resolved, indent=2))
        return 1
    cdp_url, chat_url, target_binding = resolved
    wall_s = args.timeout_s + (15 if args.paste_only else 30)
    try:
        summary = asyncio.run(
            asyncio.wait_for(
                run(
                    cdp_url,
                    chat_url,
                    prompt,
                    model=args.model,
                    timeout_s=args.timeout_s,
                    paste_only=args.paste_only,
                    target_binding=target_binding,
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
