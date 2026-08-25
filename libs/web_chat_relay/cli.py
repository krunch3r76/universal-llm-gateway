"""CLI for ``scripts.local/grok-claude-relay`` (``python -m web_chat_relay.cli``)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from web_chat_relay import grok_session
from web_chat_relay.grok_session import GrokAuthError
from web_chat_relay.loop import RelayConfig, run_relay

DEFAULT_GROK_URL = (
    "https://grok.com/c/47794c69-9fcc-4481-b1a6-f6c9cbf8b768"
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Cursor one-shot grok.com send/harvest, or grok.com ↔ Cowork relay"
    )
    p.add_argument("--probe", action="store_true", help="Dump grok DOM; do not relay")
    p.add_argument(
        "--send",
        default="",
        help="Paste one message into grok.com, wait idle, print last assistant",
    )
    p.add_argument(
        "--send-file",
        default="",
        help="Read --send text from a file (overrides --send; avoids quoting)",
    )
    p.add_argument(
        "--harvest",
        action="store_true",
        help="Print last assistant from the signed-in tab; do not paste",
    )
    p.add_argument("--run", action="store_true", help="Open Cowork and poll/relay")
    p.add_argument(
        "--wait-auth",
        type=float,
        default=0.0,
        help="Seconds to poll until grok tab is signed in (0 = once)",
    )
    p.add_argument("--grok-url", default=DEFAULT_GROK_URL)
    p.add_argument("--cdp-url", default=grok_session.DEFAULT_CDP_URL)
    p.add_argument("--project-ask-url", default="http://127.0.0.1:8770")
    p.add_argument("--poll-s", type=float, default=5.0)
    p.add_argument("--max-relays", type=int, default=None)
    p.add_argument(
        "--seed-grok",
        default="",
        help="After Cowork opener, paste this into grok so the first assistant turn fires",
    )
    p.add_argument(
        "--seed-grok-file",
        default="",
        help="Read --seed-grok text from a file (overrides --seed-grok; avoids SSH quoting)",
    )
    p.add_argument(
        "--claude-opener-file",
        default="",
        help="Read the Cowork opener text from a file, replacing the generic "
        "wait-for-grok opener (task framing, entity ids, etc.)",
    )
    p.add_argument("--stop-file", default="/tmp/grok-claude-relay.stop")
    p.add_argument(
        "--state-file",
        default="/tmp/grok-claude-relay.state.json",
        help="Where to write the most recent claude.ai chat_url + relay count",
    )
    return p


async def _probe(grok_url: str, cdp_url: str, wait_auth_s: float = 0.0) -> int:
    substr = grok_session.conversation_id_from_url(grok_url) or grok_url
    deadline = asyncio.get_event_loop().time() + max(wait_auth_s, 0.0)
    while True:
        pw, _b, _c, page = await grok_session.attach_grok_page(
            cdp_url=cdp_url, url_substr=substr
        )
        try:
            dump = await grok_session.probe_dom(page)
            dump["signed_in"] = grok_session.is_signed_in(dump)
            if dump["signed_in"] or wait_auth_s <= 0:
                print(json.dumps(dump, indent=2)[:12000])
                return 0 if dump["signed_in"] else 2
        except GrokAuthError as exc:
            if wait_auth_s <= 0:
                print(f"AUTH_MISSING {exc}", file=sys.stderr)
                return 2
        finally:
            await pw.stop()
        if asyncio.get_event_loop().time() >= deadline:
            print("AUTH_MISSING wait-auth timeout", file=sys.stderr)
            return 2
        await asyncio.sleep(5.0)


def _print_harvest(shot: grok_session.GrokHarvest) -> None:
    """Stdout JSON for a one-shot Cursor send/harvest."""
    print(
        json.dumps(
            {
                "url": shot.url,
                "conversation_id": grok_session.conversation_id_from_url(shot.url),
                "signed_in": shot.signed_in,
                "n": shot.n,
                "streaming": shot.streaming,
                "last_assistant": grok_session.strip_chrome(shot.last_assistant)[:20000],
            },
            indent=2,
        )
    )


async def _one_shot(
    grok_url: str,
    cdp_url: str,
    *,
    text: str = "",
    harvest_only: bool = False,
    wait_auth_s: float = 0.0,
) -> int:
    """Attach the attended grok tab, optionally paste, then print last assistant."""
    substr = grok_session.conversation_id_from_url(grok_url) or grok_url
    deadline = asyncio.get_event_loop().time() + max(wait_auth_s, 0.0)
    pw = page = None
    while True:
        try:
            pw, _b, _c, page = await grok_session.attach_grok_page(
                cdp_url=cdp_url, url_substr=substr
            )
            await grok_session.require_signed_in(page, grok_url=grok_url)
            break
        except GrokAuthError as exc:
            if pw is not None:
                await pw.stop()
                pw = page = None
            if wait_auth_s <= 0:
                print(f"AUTH_MISSING {exc}", file=sys.stderr)
                return 2
            if asyncio.get_event_loop().time() >= deadline:
                print("AUTH_MISSING wait-auth timeout", file=sys.stderr)
                return 2
            await asyncio.sleep(5.0)
    try:
        assert page is not None
        if text.strip() and not harvest_only:
            await grok_session.paste_and_send(page, text.strip())
            shot = await grok_session.wait_idle(page)
        else:
            shot = await grok_session.harvest(page)
            if shot.streaming or shot.stop:
                shot = await grok_session.wait_idle(page)
        _print_harvest(shot)
        return 0
    except GrokAuthError as exc:
        print(f"AUTH_MISSING {exc}", file=sys.stderr)
        return 2
    finally:
        if pw is not None:
            await pw.stop()


def _read_text_arg(inline: str, file_path: str) -> str:
    """File content wins when both are given; empty when neither is set."""
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")
    return inline


async def _run(args: argparse.Namespace) -> int:
    if args.wait_auth > 0:
        rc = await _probe(args.grok_url, args.cdp_url, args.wait_auth)
        if rc != 0:
            return rc
    substr = grok_session.conversation_id_from_url(args.grok_url) or args.grok_url
    cfg = RelayConfig(
        grok_url=args.grok_url,
        grok_cdp_url=args.cdp_url,
        project_ask_url=args.project_ask_url,
        poll_s=args.poll_s,
        max_relays=args.max_relays,
        seed_grok=_read_text_arg(args.seed_grok, args.seed_grok_file),
        claude_opener=_read_text_arg("", args.claude_opener_file),
        stop_file=Path(args.stop_file),
        state_file=Path(args.state_file),
        url_substr=substr,
    )
    state = await run_relay(cfg)
    print(json.dumps({"relays": state.relays, "stop_reason": state.stop_reason}))
    return 0 if state.stop_reason != "auth_missing" else 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    send_text = _read_text_arg(args.send, args.send_file)
    modes = [args.probe, bool(send_text), args.harvest, args.run]
    if sum(bool(m) for m in modes) > 1:
        print("Pick one of --probe, --send/--send-file, --harvest, --run", file=sys.stderr)
        return 2
    if args.probe:
        return asyncio.run(_probe(args.grok_url, args.cdp_url, args.wait_auth))
    if send_text or args.harvest:
        if args.grok_url == DEFAULT_GROK_URL:
            print(
                "WARN: --grok-url is the CLI default chat id — pass the live "
                "/c/<uuid> from the operator or from --probe",
                file=sys.stderr,
            )
    if send_text:
        return asyncio.run(
            _one_shot(
                args.grok_url,
                args.cdp_url,
                text=send_text,
                wait_auth_s=args.wait_auth,
            )
        )
    if args.harvest:
        return asyncio.run(
            _one_shot(
                args.grok_url,
                args.cdp_url,
                harvest_only=True,
                wait_auth_s=args.wait_auth,
            )
        )
    if args.run:
        return asyncio.run(_run(args))
    _parser().print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
