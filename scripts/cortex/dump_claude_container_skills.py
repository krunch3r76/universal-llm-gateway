#!/usr/bin/env python3
"""Dispatch a cheap claude.ai chat to zip /mnt/skills and download the artifact.

Run on Jupiter (Chrome CDP). Remote seats: claude-ai-sync-jupiter dump-skills.

Chat is the standing path. CSE only if chat cannot see /mnt/skills.
/v1/chat/completions artifact grab is untested — not this script.
"""

from __future__ import annotations

import argparse
import asyncio
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
from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL  # noqa: E402


def parse_args() -> argparse.Namespace:
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    prompt = DUMP_PROMPT
    if args.prompt_file:
        prompt = args.prompt_file.read_text(encoding="utf-8")
    out = asyncio.run(
        dump_container_skills(
            out=args.out,
            cdp_url=args.cdp_url,
            chat_url=args.chat_url,
            download_only=args.download_only,
            prompt=prompt,
            timeout_s=args.timeout_s,
        )
    )
    inv = zip_tree_inventory(out)
    print(
        f"saved {out} bytes={out.stat().st_size} "
        f"public={len(inv['public'])} examples={len(inv['examples'])} "
        f"user={len(inv['user'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
