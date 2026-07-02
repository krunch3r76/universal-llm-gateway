#!/usr/bin/env python3
"""Upload skills to claude.ai Customize → Skills via CDP-attached Chrome.

Workflow (saved runbook: docs/agent-guides/skills/claude-ai-bundle-sync.md):

  # 1. Regen local bundles from SOT
  python scripts/cortex/gen_claude_bundles.py

  # 2. Scan drift (Chrome CDP on Customize → Skills)
  python scripts/cortex/upload_claude_bundles_ui.py --status

  # 3. Upload NEW slugs only
  python scripts/cortex/upload_claude_bundles_ui.py --all --continue-on-error

  # 4. Refresh content for skills already on UI
  python scripts/cortex/upload_claude_bundles_ui.py --all --replace --continue-on-error

Skills API (--api on upload_claude_bundles.py) does **not** populate this UI.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles.resolver import CLAUDE_BUNDLE_SLUGS  # noqa: E402
from claude_bundles.skills_api import validate_bundle_dir  # noqa: E402
from claude_bundles.skills_ui import (  # noqa: E402
    DEFAULT_CDP_URL,
    chrome_start_hint,
    debug_cdp,
    list_bundle_mds,
    list_zip_dir,
    prepare_session,
    upload_skills,
)
from claude_bundles.skills_ui_status import print_parity_report, scan_ui_parity  # noqa: E402


def _parse_slugs(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [s.strip() for s in raw.split(",") if s.strip()]


def _load_gap_slugs() -> tuple[str, ...]:
    path = _REPO / "scripts" / "cortex" / "upload_claude_bundles.py"
    spec = importlib.util.spec_from_file_location("upload_claude_bundles", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return tuple(mod.GAP_SLUGS)


def _resolve_slugs(args: argparse.Namespace) -> list[str] | None:
    if args.slugs:
        return _parse_slugs(args.slugs)
    if args.all:
        return list(CLAUDE_BUNDLE_SLUGS)
    if args.gap:
        return list(_load_gap_slugs())
    return None


def _filter_valid(
    items: list[tuple[str, Path]], *, skip_invalid: bool
) -> list[tuple[str, Path]]:
    if not skip_invalid:
        return items
    kept: list[tuple[str, Path]] = []
    for slug, path in items:
        bundle_dir = path.parent
        try:
            _, desc = validate_bundle_dir(slug, bundle_dir)
            kept.append((slug, path))
        except ValueError as exc:
            print(f"SKIP-INVALID {slug}: {exc}", file=sys.stderr)
    return kept


def _resolve_items(args: argparse.Namespace) -> list[tuple[str, Path]]:
    slugs = _resolve_slugs(args)
    if args.zip_dir:
        items = list_zip_dir(Path(args.zip_dir), slugs=slugs)
    else:
        bundles = Path(args.bundles_dir or (_REPO / ".claude" / "skills"))
        items = list_bundle_mds(bundles, slugs=slugs)
    return _filter_valid(items, skip_invalid=args.skip_invalid)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--print-chrome-cmd", action="store_true")
    parser.add_argument("--bundles-dir", metavar="DIR")
    parser.add_argument("--zip-dir", metavar="DIR")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--all", action="store_true", help="All CLAUDE_BUNDLE_SLUGS (~90)")
    scope.add_argument("--gap", action="store_true", help="39-skill gap list")
    parser.add_argument("--slugs", help="Comma-separated slug subset")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sleep", type=float, default=6.0, help="Seconds between uploads (default 6)")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Re-upload skills already in the table (default: upload NEW slugs only)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Alias for default behavior (skip slugs already in table)",
    )
    parser.add_argument(
        "--skip-invalid",
        action="store_true",
        help="Skip bundles with description >200 chars (no upload attempt)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Same as --skip-invalid; do not auto-truncate descriptions for upload",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Log failures and continue bulk run",
    )
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--status",
        action="store_true",
        help="Scan local bundles vs claude.ai Skills table (no upload)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--screenshots", metavar="DIR")
    parser.add_argument(
        "--format",
        choices=("md", "zip"),
        default="zip",
        help="Upload artifact (default zip = {slug}/SKILL.md; md = full SKILL.md file)",
    )
    parser.add_argument(
        "--minimal-body",
        action="store_true",
        help="Frontmatter-only .md or zip (not the default full SKILL.md body)",
    )
    args = parser.parse_args()

    if args.print_chrome_cmd:
        print(chrome_start_hint())
        return 0
    if args.debug:
        asyncio.run(debug_cdp(args.cdp_url))
        return 0
    if args.prepare:
        asyncio.run(prepare_session(args.cdp_url))
        return 0
    if args.status:
        bundles = Path(args.bundles_dir or (_REPO / ".claude" / "skills"))
        report = asyncio.run(scan_ui_parity(cdp_url=args.cdp_url, bundles_dir=bundles))
        return print_parity_report(report)

    if args.zip_dir and args.bundles_dir:
        parser.error("Use --zip-dir OR --bundles-dir, not both")
    if not args.all and not args.gap and not args.slugs and not args.zip_dir:
        parser.error("Specify --all, --gap, --slugs, or --zip-dir")

    if args.strict:
        args.skip_invalid = True

    items = _resolve_items(args)
    if not items:
        print("No skill files matched", file=sys.stderr)
        return 1

    if args.dry_run:
        shown = items[: args.limit] if args.limit else items
        for slug, path in shown:
            print(f"DRY-RUN {slug}: {path}")
        print(f"OK dry-run ({len(shown)} file(s))")
        return 0

    skip_existing = not args.replace
    if args.skip_existing:
        skip_existing = True

    uploaded = asyncio.run(
        upload_skills(
            items,
            cdp_url=args.cdp_url,
            sleep_s=args.sleep,
            screenshot_dir=Path(args.screenshots) if args.screenshots else None,
            skip_existing=skip_existing,
            limit=args.limit,
            continue_on_error=args.continue_on_error,
            truncate_descriptions=not args.strict,
            upload_format=args.format,
            full_body=not args.minimal_body,
        )
    )
    print(f"Done: uploaded {len(uploaded)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
