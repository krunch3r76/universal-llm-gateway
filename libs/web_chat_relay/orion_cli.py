"""CLI for Orion (chatgpt.com) on io CDP — probe and secrets-based login."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from web_chat_relay.orion_session import DEFAULT_CDP_URL, login_orion, probe_orion


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Orion / chatgpt.com house on io (:12 / CDP 9342)")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, help_text in (
        ("preflight", "CDP + chatgpt.com probe JSON"),
        ("probe", "Alias for preflight"),
        ("login", "Sign in using ~/.gateway/secrets/chatgpt.env (stops on email 2FA)"),
    ):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    sp = sub.add_parser(
        "refresh-connectors",
        help="Placeholder: MCP life+code connectors after paid plan (not automated yet)",
    )
    sp.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    return p


async def _run(args: argparse.Namespace) -> int:
    if args.cmd in ("preflight", "probe"):
        result = await probe_orion(cdp_url=args.cdp_url)
        print(json.dumps(result.as_dict(), indent=2))
        return 0 if result.signed_in or not result.login_wall else 1
    if args.cmd == "login":
        result = await login_orion(cdp_url=args.cdp_url)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    if args.cmd == "refresh-connectors":
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "not_implemented",
                    "hint": (
                        "After Plus/Pro/Business subscription: add custom MCP plugins "
                        "for /mcp/life and /mcp/code in chatgpt.com Settings → Apps. "
                        "Automation will mirror claude-ai refresh-operator-connectors."
                    ),
                },
                indent=2,
            )
        )
        return 2
    return 2


def main() -> None:
    args = _parser().parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
