#!/usr/bin/env python3
"""CLI — advisory Cowork Project chrome via Jupiter CDP.

Run ON Jupiter (CDP host). From Cursor / remote seats use:
  scripts/cortex/claude-ai-sync-jupiter project-chrome …

Examples:
  python scripts/cortex/cowork_project_chrome.py render-prompt \\
    --name "dogfood" --host-id dogfood-4917 \\
    --charter-uri cortex://notes/system/threads/4917-charter-scoreboard.md \\
    --ring-thread 4917

  python scripts/cortex/cowork_project_chrome.py ensure \\
    --name "dogfood · harness" --host-id dogfood-4917 \\
    --charter-uri cortex://notes/system/threads/4917-charter-scoreboard.md \\
    --ring-thread 4917

  python scripts/cortex/cowork_project_chrome.py refresh --uuid UUID \\
    --name "…" --host-id … --charter-uri … --ring-thread …

  python scripts/cortex/cowork_project_chrome.py destroy --uuid UUID
  python scripts/cortex/cowork_project_chrome.py get --uuid UUID
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles.project_chrome import (  # noqa: E402
    DEFAULT_ORG_ID,
    run_destroy,
    run_ensure,
    run_get,
    run_refresh,
)
from claude_bundles.project_chrome_prompt import (  # noqa: E402
    ProjectChromeSpec,
    build_prompt_template,
)
from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL  # noqa: E402


def _spec_from_args(args: argparse.Namespace) -> ProjectChromeSpec:
    extras = tuple(
        p.strip() for p in (args.extra_pointer or []) if p and str(p).strip()
    )
    workflow = ""
    if args.workflow_file:
        workflow = Path(args.workflow_file).read_text(encoding="utf-8")
    elif args.workflow_md:
        workflow = args.workflow_md
    return ProjectChromeSpec(
        name=args.name,
        host_id=args.host_id,
        charter_uri=args.charter_uri,
        ring_thread=str(args.ring_thread),
        description=args.description or "",
        deliverables_uri=args.deliverables_uri or "",
        scoreboard_uri=args.scoreboard_uri or "",
        workflow_md=workflow,
        extra_pointers=extras,
    )


def _add_spec_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--name", required=True)
    p.add_argument("--host-id", required=True)
    p.add_argument("--charter-uri", required=True)
    p.add_argument("--ring-thread", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--deliverables-uri", default="")
    p.add_argument("--scoreboard-uri", default="")
    p.add_argument("--workflow-md", default="", help="Inline workflow section markdown")
    p.add_argument(
        "--workflow-file", default="", help="Path to workflow section markdown"
    )
    p.add_argument(
        "--extra-pointer",
        action="append",
        default=[],
        help="Extra SoT pointer line (repeatable)",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--org-id", default=DEFAULT_ORG_ID)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_render = sub.add_parser("render-prompt", help="Print prompt_template only")
    _add_spec_args(p_render)

    p_ensure = sub.add_parser("ensure", help="Create Project + set instructions")
    _add_spec_args(p_ensure)

    p_refresh = sub.add_parser(
        "refresh", help="Re-PUT instructions on existing Project (no create)"
    )
    _add_spec_args(p_refresh)
    p_refresh.add_argument("--uuid", required=True)

    p_get = sub.add_parser("get", help="GET project JSON")
    p_get.add_argument("--uuid", required=True)

    p_destroy = sub.add_parser("destroy", help="Archive + delete Project")
    p_destroy.add_argument("--uuid", required=True)

    args = parser.parse_args(argv)

    if args.cmd == "render-prompt":
        print(build_prompt_template(_spec_from_args(args)))
        return 0

    if args.cmd == "ensure":
        result = asyncio.run(
            run_ensure(
                _spec_from_args(args),
                cdp_url=args.cdp_url,
                org_id=args.org_id,
            )
        )
        print(json.dumps(asdict(result), indent=2))
        return 0

    if args.cmd == "refresh":
        result = asyncio.run(
            run_refresh(
                _spec_from_args(args),
                uuid=args.uuid,
                cdp_url=args.cdp_url,
                org_id=args.org_id,
            )
        )
        print(json.dumps(asdict(result), indent=2))
        return 0

    if args.cmd == "get":
        body = asyncio.run(
            run_get(uuid=args.uuid, cdp_url=args.cdp_url, org_id=args.org_id)
        )
        slim = {
            "uuid": body.get("uuid"),
            "name": body.get("name"),
            "description": body.get("description"),
            "prompt_len": len(body.get("prompt_template") or ""),
            "prompt_preview": (body.get("prompt_template") or "")[:240],
            "archived_at": body.get("archived_at"),
        }
        print(json.dumps(slim, indent=2))
        return 0

    if args.cmd == "destroy":
        asyncio.run(
            run_destroy(uuid=args.uuid, cdp_url=args.cdp_url, org_id=args.org_id)
        )
        print(json.dumps({"destroyed": args.uuid}, indent=2))
        return 0

    parser.error(f"unknown cmd {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
