#!/usr/bin/env python3
"""CLI — sealed ask / n-turn consult against claude.ai via Jupiter CDP.

Run ON Jupiter (CDP host). From Cursor / remote seats use:
  scripts/cortex/claude-ai-sync-jupiter project-ask …

Examples:
  # Automated seat — registry allocates a fresh (port, profile) lane
  scripts/cortex/claude-ai-sync-jupiter project-ask --register --purpose ask \\
    --uuid 019f6917-… --prompt-file sealed.md --out body.md

  # Reattach a held registration (same holder)
  scripts/cortex/claude-ai-sync-jupiter project-ask \\
    --registration-id <id> --holder seat-x --uuid … --prompt-file …

  # Primary attended :9222 only — explicit opt-out of registry
  scripts/cortex/claude-ai-sync-jupiter project-ask --no-register \\
    --cdp-url http://127.0.0.1:9222 --uuid … --prompt-file …

  # N-turn Fable consult on /new (no Project) — MUST --converse with --no-uuid
  scripts/cortex/claude-ai-sync-jupiter project-ask --register --purpose fable \\
    --converse --no-uuid --model fable-5 \\
    --prompt-file t1.md --prompt-file t2.md --prompt-file t3.md \\
    --out-dir /mnt/torus/mcp-data/files/notes/system/threads/4917-fable-review/

On converse success the CLI prints JSON plus `turn_N_chat_url=…` per turn.
When `--out-dir` is omitted, bodies live in stdout JSON only — see
agent_skill:claude-ai-cdp-navigation § Post-converse recovery.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles import cdp_lane, cdp_registry  # noqa: E402
from claude_bundles.project_ask import run_project_ask  # noqa: E402
from claude_bundles.project_ask_abort import (  # noqa: E402
    abort_cleanup_registration_id,
    deregister_on_exit,
    install_abort_handlers,
)
from claude_bundles.project_ask_conversation import (  # noqa: E402
    run_project_conversation,
)
from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cdp-url",
        default=None,
        help=(
            "Raw CDP URL — forbidden for automated paths unless --no-register "
            f"(primary {DEFAULT_CDP_URL} only). Prefer --register."
        ),
    )
    parser.add_argument(
        "--register",
        dest="register",
        action="store_true",
        default=True,
        help="Acquire a never-used (port, profile) via cdp_registry (default).",
    )
    parser.add_argument(
        "--no-register",
        dest="register",
        action="store_false",
        help="Opt out of registry (attended :9222 / explicit --cdp-url only).",
    )
    parser.add_argument(
        "--registration-id",
        default="",
        help="Reattach an existing active registration (same --holder).",
    )
    parser.add_argument(
        "--holder",
        default="",
        help="Registry holder id (default: env CDP_HOLDER or project-ask-<pid>).",
    )
    parser.add_argument(
        "--purpose",
        default=None,
        help="Registry purpose tag (e.g. ask, fable, purge).",
    )
    parser.add_argument(
        "--deregister-on-exit",
        action="store_true",
        help="With --register: deregister the lane when this process exits.",
    )
    parser.add_argument(
        "--abort-cleanup-registration-id",
        default="",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--intent",
        default="",
        help=(
            "Legacy profile-keyed lane (cdp_lane). Prefer --register. "
            "Mutually exclusive with --register / --registration-id."
        ),
    )
    parser.add_argument(
        "--fresh-profile",
        action="store_true",
        help="With --intent: mint a fresh clone profile (escape hatch) rather "
        "than queueing on the canonical one.",
    )
    parser.add_argument(
        "--queue-timeout-s",
        type=float,
        default=120.0,
        help="With --intent: seconds to wait for an actively-leased profile.",
    )
    parser.add_argument(
        "--uuid",
        default="",
        help="Cowork Project UUID (omit with --no-uuid for /new consult)",
    )
    parser.add_argument(
        "--no-uuid",
        action="store_true",
        help="Compose on https://claude.ai/new (n-turn consult)",
    )
    parser.add_argument("--prompt", default="", help="Inline sealed prompt (ask mode)")
    parser.add_argument(
        "--prompt-file",
        action="append",
        default=[],
        help="Prompt file (repeatable for --converse)",
    )
    parser.add_argument(
        "--converse",
        action="store_true",
        help="N-turn conversation (keep chat until end)",
    )
    parser.add_argument(
        "--model",
        default="opus-4.8",
        help="live picker name/pattern (e.g. opus-4.8, sonnet-5, fable-5) | leave",
    )
    parser.add_argument(
        "--no-cowork-auto",
        action="store_true",
        help=(
            "On /new compose: skip Cowork + Automatically approve "
            "(default is ensure_cowork_auto=True)"
        ),
    )
    parser.add_argument(
        "--keep-chat",
        action="store_true",
        help="Ask mode: harvest without delete (still archives if --archive set)",
    )
    parser.add_argument(
        "--close",
        action="store_true",
        help="Converse mode only: delete chat after final turn (default: keep)",
    )
    parser.add_argument(
        "--archive",
        default="",
        help="Archive path before delete (ask mode; auto-default when deleting)",
    )
    parser.add_argument("--out", default="", help="Write assistant body (ask mode)")
    parser.add_argument(
        "--out-dir",
        default="",
        help="Write turn-N.md bodies (converse mode)",
    )
    parser.add_argument("--ledger", default="", help="Write JSON ledger to path")
    parser.add_argument(
        "--timeout-s",
        type=int,
        default=360,
        help=(
            "Idle completion budget in seconds (default 360; converse floors "
            "at 600). While Stop/streaming/tool_pause is present the idle "
            "clock pauses — no wall ceiling for long Cowork tool-runs (24666)."
        ),
    )
    parser.add_argument("--min-body", type=int, default=40)
    parser.add_argument("--min-growth", type=int, default=50)
    args = parser.parse_args(argv)

    cleanup_id = args.abort_cleanup_registration_id.strip()
    if cleanup_id:
        return abort_cleanup_registration_id(cleanup_id)

    holder = (
        args.holder.strip()
        or os.environ.get("CDP_HOLDER", "").strip()
        or f"project-ask-{os.getpid()}"
    )

    if args.intent and args.registration_id:
        parser.error("--intent is mutually exclusive with --registration-id")
    # --intent opts into the legacy profile-keyed allocator (implies ¬register).
    if args.intent:
        args.register = False

    if args.registration_id:
        reg = cdp_registry.reattach(args.registration_id, holder=holder)
        args.cdp_url = reg.cdp_url
        _print_registration(reg)
        if args.deregister_on_exit:
            install_abort_handlers(reg, purpose=reg.purpose)
        try:
            return _dispatch(args, parser)
        finally:
            if args.deregister_on_exit:
                deregister_on_exit(reg, purpose=reg.purpose)

    if args.intent:
        with cdp_lane.acquire_lane(
            args.intent,
            fresh=args.fresh_profile,
            queue_timeout_s=args.queue_timeout_s,
        ) as lane:
            args.cdp_url = lane.cdp_url
            print(
                f"lane: intent={lane.intent} suffix={lane.suffix} "
                f"port={lane.port} reused={lane.reused}",
                flush=True,
            )
            return _dispatch(args, parser)

    if args.register:
        if args.cdp_url:
            parser.error(
                "raw --cdp-url is forbidden with --register; "
                "omit --cdp-url, or use --no-register for primary :9222"
            )
        purpose = args.purpose or args.intent or "ask"
        reg = cdp_registry.register_lane(holder=holder, purpose=purpose)
        args.cdp_url = reg.cdp_url
        _print_registration(reg)
        if args.deregister_on_exit:
            install_abort_handlers(reg, purpose=purpose)
        try:
            return _dispatch(args, parser)
        finally:
            if args.deregister_on_exit:
                deregister_on_exit(reg, purpose=purpose)

    # --no-register: attended primary or explicit URL
    if not args.cdp_url:
        args.cdp_url = DEFAULT_CDP_URL
    return _dispatch(args, parser)


def _print_registration(reg: cdp_registry.Registration) -> None:
    print(f"registration_id={reg.registration_id}", flush=True)
    print(f"cdp_url={reg.cdp_url}", flush=True)
    print(
        f"registry: port={reg.port} suffix={reg.profile_suffix} "
        f"holder={reg.holder} purpose={reg.purpose}",
        flush=True,
    )


def _dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    prompts: list[str] = []
    for path in args.prompt_file:
        prompts.append(Path(path).read_text(encoding="utf-8"))
    if args.prompt.strip():
        prompts.append(args.prompt)
    if not prompts:
        parser.error("provide --prompt or --prompt-file")

    if args.converse:
        if not args.no_uuid and not args.uuid:
            parser.error("--converse needs --uuid or --no-uuid")
        # Fable bind: converse never auto-deletes; only --close
        results = asyncio.run(
            run_project_conversation(
                prompts,
                project_uuid="" if args.no_uuid else args.uuid,
                model=args.model,
                delete_after=bool(args.close),
                cdp_url=args.cdp_url,
                timeout_s=max(args.timeout_s, 600),
                min_growth=max(args.min_growth, 80),
                min_body=max(args.min_body, 200),
                ensure_cowork_auto=not args.no_cowork_auto,
            )
        )
        summary = {
            "ok": all(r.ok for r in results),
            "turns": len(results),
            "results": [
                {
                    "ok": r.ok,
                    "body_len": r.body_len,
                    "url": r.url,
                    "model": r.model,
                    "error": r.error,
                    "delete_after": r.delete_after,
                    "body_preview": (r.body or "")[:300],
                }
                for r in results
            ],
        }
        print(json.dumps(summary, indent=2))
        for i, r in enumerate(results, start=1):
            if r.ok and r.url:
                print(f"turn_{i}_chat_url={r.url}", flush=True)
        if args.out_dir:
            out_dir = Path(args.out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            for i, r in enumerate(results, start=1):
                (out_dir / f"turn-{i}.md").write_text(
                    (r.body or "") + "\n", encoding="utf-8"
                )
        elif summary["ok"]:
            print(
                "hint: converse bodies are in stdout JSON "
                "(results[].body_preview); pass --out-dir or --ledger for "
                "durable files (agent_skill:claude-ai-cdp-navigation "
                "§ Post-converse recovery)",
                flush=True,
            )
        if args.ledger:
            led = Path(args.ledger)
            led.parent.mkdir(parents=True, exist_ok=True)
            led.write_text(
                json.dumps([r.as_dict() for r in results], indent=2) + "\n",
                encoding="utf-8",
            )
        return 0 if summary["ok"] else 1

    # Single ask — always archives then deletes (unless --keep-chat via empty archive+keep)
    if args.no_uuid and not args.converse:
        parser.error(
            "--no-uuid alone is invalid: use --converse --no-uuid for /new "
            "(Cowork consult), or --uuid for a Project sealed ask (24611)"
        )
    if not args.uuid:
        parser.error("ask mode requires --uuid (or --converse --no-uuid for /new)")
    prompt = prompts[0]
    archive = args.archive
    if not archive and not args.keep_chat:
        # Default archive next to out or under /tmp
        archive = args.out or (
            f"/mnt/torus/mcp-data/files/notes/system/threads/"
            f"cdp-ask-archive-{args.uuid[:8].replace('/', '_')}.md"
        )
        if args.out:
            archive = str(Path(args.out).with_suffix("")) + "-archive.md"
    result = asyncio.run(
        run_project_ask(
            prompt,
            project_uuid=args.uuid,
            model=args.model,
            delete_after=not args.keep_chat,
            cdp_url=args.cdp_url,
            timeout_s=args.timeout_s,
            min_growth=args.min_growth,
            min_body=args.min_body,
            archive_path=archive or None,
        )
    )
    summary = {
        "ok": result.ok,
        "body_len": result.body_len,
        "url": result.url,
        "project_uuid": result.project_uuid,
        "model": result.model,
        "attested_model": result.attested_model,
        "archive_uri": result.archive_uri,
        "error": result.error,
        "delete_after": result.delete_after,
        "body_preview": (result.body or "")[:400],
    }
    print(json.dumps(summary, indent=2))
    if result.archive_uri:
        print(f"archive_uri={result.archive_uri}", flush=True)
    if args.out and result.body:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result.body + "\n", encoding="utf-8")
    if args.ledger:
        led = Path(args.ledger)
        led.parent.mkdir(parents=True, exist_ok=True)
        led.write_text(json.dumps(result.as_dict(), indent=2) + "\n", encoding="utf-8")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
