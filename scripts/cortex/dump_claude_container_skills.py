#!/usr/bin/env python3
"""Dispatch a cheap claude.ai chat to zip /mnt/skills and download the artifact.

Run on Jupiter (Chrome CDP). Remote seats: claude-ai-sync-jupiter dump-skills.

Chat is the standing path. CSE only if chat cannot see /mnt/skills.
/v1/chat/completions artifact grab is untested — not this script.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles.container_skills_dump import (  # noqa: E402
    DUMP_PROMPT,
    default_dump_path,
    dump_container_skills,
)
from claude_bundles.container_skills_zip import zip_tree_inventory  # noqa: E402
from claude_bundles.liveness_wait import (  # noqa: E402
    LivenessError,
    LivenessStalledError,
)
from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse dump CLI flags.

    ``--download-only`` skips the prompt. ``--cowork`` keeps the ``/new``
    Cowork default. ``--timeout-s`` is the reply idle budget, not a navigation
    failure cap.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=default_dump_path(_REPO),
        help="Destination zip (default tmp/reviews/claude-skills-latest.zip)",
    )
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument(
        "--chat-url",
        help="Reuse this /chat/ URL (download-only, or follow-up). CSE submit refused.",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Do not send a prompt — click the existing Download card",
    )
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--timeout-s", type=int, default=360)
    parser.add_argument(
        "--chat",
        action="store_true",
        default=True,
        help="Force Chat compose on /new (standing dump; Cowork omits user/).",
    )
    parser.add_argument(
        "--cowork",
        action="store_true",
        help="Dump via /new Cowork default (omits skills/user/).",
    )
    return parser.parse_args()


def _nested() -> bool:
    return os.environ.get("CLAUDE_SYNC_NESTED", "").strip() in {"1", "true", "yes"}


def _emit(line: str) -> None:
    print(line, flush=True)


def main() -> int:
    """Run one dump and emit phase plus a terminal harvest line.

    ``CLAUDE_SYNC_NESTED=1`` (set by ``claude-ai-sync-jupiter mirror``) skips
    the terminal ``MIRROR_DONE`` so the wrapper can print it after reconcile.
    ``STALLED`` and ``MIRROR_ERROR`` still print here: those end the wait.
    Reply wait stays on ``wait_assistant_reply`` (idle budget, friction 24666).
    """
    try:
        args = parse_args()
        prompt = DUMP_PROMPT
        if args.prompt_file:
            prompt = args.prompt_file.read_text(encoding="utf-8")
        nested = _nested()
        _emit("PHASE dump start")
        out = asyncio.run(
            dump_container_skills(
                out=args.out,
                cdp_url=args.cdp_url,
                chat_url=args.chat_url,
                download_only=args.download_only,
                prompt=prompt,
                timeout_s=args.timeout_s,
                chat_compose=not args.cowork,
            )
        )
        _emit("PHASE dump done")
        inv = zip_tree_inventory(out)
        _emit(
            f"saved {out} bytes={out.stat().st_size} "
            f"public={len(inv['public'])} examples={len(inv['examples'])} "
            f"user={len(inv['user'])}"
        )
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        _emit(f"MIRROR_ERROR argparse rc={code}")
        return code if isinstance(code, int) else 2
    except LivenessStalledError as exc:
        _emit(f"STALLED {exc.phase}")
        return 3
    except LivenessError as exc:
        _emit(f"MIRROR_ERROR {exc}")
        return 1
    except Exception as exc:
        _emit(f"MIRROR_ERROR {type(exc).__name__}: {exc}")
        return 1
    if not nested:
        _emit("MIRROR_DONE rc=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
