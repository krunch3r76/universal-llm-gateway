"""CLI for ``scripts.local/grok-claude-relay`` (``python -m web_chat_relay.cli``)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from chat_harvest.grok_adapter import execute_grok_harvest, execute_grok_paste
from chat_harvest.models import ClassifyRefuse, classify_chat_url

from web_chat_relay import grok_session
from web_chat_relay.grok_session import GrokAuthError
from web_chat_relay.loop import RelayConfig, run_relay

DEFAULT_GROK_URL = "https://grok.com/c/47794c69-9fcc-4481-b1a6-f6c9cbf8b768"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Cursor one-shot grok.com send/harvest, or grok.com ↔ Cowork relay"
    )
    p.add_argument("--probe", action="store_true", help="Compact grok metadata JSON")
    p.add_argument(
        "--send",
        default="",
        help="Paste one message into grok.com, wait idle, print harvest pointer",
    )
    p.add_argument(
        "--send-file",
        default="",
        help="Read --send text from a file (overrides --send; avoids quoting)",
    )
    p.add_argument(
        "--harvest",
        action="store_true",
        help="Harvest full transcript to cortex sidecar; print pointer JSON",
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


def _print_harvest(shot: grok_session.GrokHarvest) -> None:
    """Stdout JSON for relay ``--run`` idle-detect (last-assistant shape unchanged)."""
    print(
        json.dumps(
            {
                "url": shot.url,
                "conversation_id": grok_session.conversation_id_from_url(shot.url),
                "signed_in": shot.signed_in,
                "n": shot.n,
                "streaming": shot.streaming,
                "last_assistant": grok_session.strip_chrome(shot.last_assistant)[
                    :20000
                ],
            },
            indent=2,
        )
    )


def _print_pointer_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2))


def _classify_refuse_response(refuse: ClassifyRefuse) -> dict:
    return {
        "outcome": "refused",
        "code": refuse.code,
        "reason": refuse.reason,
    }


async def _probe(grok_url: str, cdp_url: str, wait_auth_s: float = 0.0) -> int:
    deadline = asyncio.get_event_loop().time() + max(wait_auth_s, 0.0)
    while True:
        substr = grok_session.conversation_id_from_url(grok_url) or grok_url
        pw = None
        try:
            pw, _browser, _context, page = await grok_session.attach_grok_page(
                cdp_url=cdp_url, url_substr=substr
            )
            shot = await grok_session.harvest(page)
            meta = {
                "outcome": "probe",
                "site": "grok",
                "conversation_id": grok_session.conversation_id_from_url(shot.url),
                "url": shot.url,
                "signed_in": shot.signed_in,
                "streaming": shot.streaming,
                "stop": shot.stop,
                "login_wall": shot.login_wall,
                "n": shot.n,
            }
        except GrokAuthError as exc:
            meta = {
                "outcome": "no_tab",
                "site": "grok",
                "conversation_id": grok_session.conversation_id_from_url(grok_url),
                "url": grok_url,
                "signed_in": False,
                "code": "no_tab",
                "reason": str(exc),
            }
        finally:
            if pw is not None:
                await pw.stop()

        signed_in = bool(meta.get("signed_in"))
        if signed_in or wait_auth_s <= 0:
            _print_pointer_json(meta)
            return 0 if signed_in else 2
        if asyncio.get_event_loop().time() >= deadline:
            print("AUTH_MISSING wait-auth timeout", file=sys.stderr)
            return 2
        await asyncio.sleep(5.0)


async def _harvest(grok_url: str, cdp_url: str) -> int:
    classified = classify_chat_url(grok_url)
    if isinstance(classified, ClassifyRefuse):
        _print_pointer_json(_classify_refuse_response(classified))
        return 2

    if not classified.conversation_id:
        _print_pointer_json(
            {
                "outcome": "no_conversation",
                "site": classified.site,
                "conversation_id": classified.conversation_id,
                "url": classified.url,
            }
        )
        return 0

    if classified.site != "grok":
        refuse = ClassifyRefuse(
            code="unsupported_site",
            reason=f"CLI harvest supports grok only in G5a (got {classified.site!r})",
        )
        _print_pointer_json(_classify_refuse_response(refuse))
        return 2

    response = await execute_grok_harvest(
        url=classified.url,
        site=classified.site,
        conversation_id=classified.conversation_id,
        cdp_url=cdp_url,
    )
    payload = response.model_dump(exclude_none=True)
    _print_pointer_json(payload)
    if response.outcome in ("unauthenticated", "no_tab", "refused"):
        return 2
    return 0


async def _send(grok_url: str, cdp_url: str, text: str) -> int:
    classified = classify_chat_url(grok_url)
    if isinstance(classified, ClassifyRefuse):
        _print_pointer_json(_classify_refuse_response(classified))
        return 2

    if classified.site != "grok":
        refuse = ClassifyRefuse(
            code="unsupported_site",
            reason=f"CLI send supports grok only in G5a (got {classified.site!r})",
        )
        _print_pointer_json(_classify_refuse_response(refuse))
        return 2

    response = await execute_grok_paste(
        url=classified.url,
        prompt_text=text,
        cdp_url=cdp_url,
        grant="operator",
    )
    payload = {
        "outcome": "pasted" if response.ok else "refused",
        **response.model_dump(),
    }
    _print_pointer_json(payload)
    return 0 if response.ok else 2


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


def _read_text_arg(inline: str, file_path: str) -> str:
    """File content wins when both are given; empty when neither is set."""
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")
    return inline


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    send_text = _read_text_arg(args.send, args.send_file)
    modes = [args.probe, bool(send_text), args.harvest, args.run]
    if sum(bool(m) for m in modes) > 1:
        print(
            "Pick one of --probe, --send/--send-file, --harvest, --run", file=sys.stderr
        )
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
        return asyncio.run(_send(args.grok_url, args.cdp_url, send_text))
    if args.harvest:
        return asyncio.run(_harvest(args.grok_url, args.cdp_url))
    if args.run:
        return asyncio.run(_run(args))
    _parser().print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
