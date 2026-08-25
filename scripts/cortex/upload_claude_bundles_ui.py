#!/usr/bin/env python3
"""Upload skills to claude.ai Customize → Skills via CDP-attached Chrome.

Workflow (SOT): .cursor/skills/claude-ai-bundle-sync/SKILL.md

**Run on Jupiter** where Chrome CDP lives. From Cursor / remote seats use:
  scripts/cortex/claude-ai-sync-jupiter status|upload …

Local-only steps (any host with repo mount):
  python scripts/cortex/gen_claude_bundles.py
  python scripts/cortex/gen_claude_bundles.py --check

On Jupiter (or via claude-ai-sync-jupiter wrapper):
  python scripts/cortex/upload_claude_bundles_ui.py --status
  # Session-loaded skills (Context frame DOM — not model self-report):
  python scripts/cortex/upload_claude_bundles_ui.py --loaded-skills \\
    --chat-url 'https://claude.ai/cowork/cse_…' \\
    --require-loaded reasoning-posture
  python scripts/cortex/upload_claude_bundles_ui.py --preflight
  python scripts/cortex/upload_claude_bundles_ui.py --diagnose-upload-menu
  python scripts/cortex/upload_claude_bundles_ui.py --slugs SLUG --continue-on-error
  python scripts/cortex/upload_claude_bundles_ui.py --slugs SLUG --replace --continue-on-error
  python scripts/cortex/upload_claude_bundles_ui.py --all --continue-on-error
  # Fleet-wide re-upload is NEVER default — requires --force-replace-all:
  # python scripts/cortex/upload_claude_bundles_ui.py --all --replace --force-replace-all

Skills API (--api on upload_claude_bundles.py) does **not** populate this UI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from universal_logging import get_logger

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles.chat_context_skills import (  # noqa: E402
    emit_loaded_skills_json,
    print_loaded_skills_report,
    scrape_loaded_skills_cdp,
)
from claude_bundles.resolver import claude_ai_target_slugs  # noqa: E402
from claude_bundles.skills_api import validate_bundle_dir  # noqa: E402
from claude_bundles.skills_ui import (  # noqa: E402
    DEFAULT_CDP_URL,
    chrome_start_hint,
    debug_cdp,
    diagnose_upload_menu_session,
    list_bundle_mds,
    list_zip_dir,
    prepare_session,
    run_preflight_session,
    upload_skills,
)
from claude_bundles.skills_ui_status import (  # noqa: E402
    print_parity_report,
    scan_ui_parity,
)
from claude_bundles.skills_ui_uninstall import uninstall_skills  # noqa: E402
from claude_bundles.staging_paths import claude_ai_bundle_dir  # noqa: E402
from claude_bundles.upload_safety import reject_unsafe_replace_all  # noqa: E402

logger = get_logger(__name__)


def _parse_slugs(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [s.strip() for s in raw.split(",") if s.strip()]


def _resolve_slugs(args: argparse.Namespace) -> list[str] | None:
    if args.slugs:
        return _parse_slugs(args.slugs)
    if args.all:
        return list(claude_ai_target_slugs())
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
            validate_bundle_dir(slug, bundle_dir)
            kept.append((slug, path))
        except ValueError as exc:
            print(f"SKIP-INVALID {slug}: {exc}", file=sys.stderr)
    return kept


def _resolve_items(args: argparse.Namespace) -> list[tuple[str, Path]]:
    slugs = _resolve_slugs(args)
    if args.zip_dir:
        items = list_zip_dir(Path(args.zip_dir), slugs=slugs)
    elif args.bundles_dir:
        items = list_bundle_mds(Path(args.bundles_dir), slugs=slugs)
    else:
        targets = slugs if slugs is not None else list(claude_ai_target_slugs())
        items = [
            (slug, claude_ai_bundle_dir(_REPO, slug) / "SKILL.md") for slug in targets
        ]
    return _filter_valid(items, skip_invalid=args.skip_invalid)


def _emit_status_json(report) -> int:
    payload = asdict(report)
    payload["on_ui"] = sorted(report.on_ui)
    payload["in_sync"] = report.in_sync
    print(json.dumps(payload, indent=2))
    if report.in_sync and not report.extra_on_ui:
        return 0
    if report.in_sync:
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Validate CDP, Skills panel, and Add → Upload menuitem",
    )
    parser.add_argument(
        "--diagnose-upload-menu",
        action="store_true",
        help="Snapshot Add → menu inventory JSON; never open the upload dialog",
    )
    parser.add_argument("--print-chrome-cmd", action="store_true")
    parser.add_argument("--bundles-dir", metavar="DIR")
    parser.add_argument("--zip-dir", metavar="DIR")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--all", action="store_true", help="All catalog Claude.ai targets"
    )
    parser.add_argument("--slugs", help="Comma-separated slug subset")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--sleep", type=float, default=6.0, help="Seconds between uploads (default 6)"
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help=(
            "Re-upload skills already in the table (default: upload NEW slugs only). "
            "Prefer --slugs SLUG --replace. Combined with --all requires --force-replace-all."
        ),
    )
    parser.add_argument(
        "--force-replace-all",
        action="store_true",
        help=(
            "Required confirmation when pairing --all with --replace "
            "(fleet-wide re-upload). Without this flag, --all --replace is refused."
        ),
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
    parser.add_argument(
        "--loaded-skills",
        action="store_true",
        help=(
            "Scrape chat UI Context→Skills (DOM, non-LLM). Requires --chat-url. "
            "Use this for post-sync session verification — not SKILLS_PROBE_OK."
        ),
    )
    parser.add_argument(
        "--chat-url",
        help="Cowork/chat URL for --loaded-skills (e.g. https://claude.ai/cowork/cse_…)",
    )
    parser.add_argument(
        "--require-loaded",
        help="Comma-separated slugs that must appear in Context→Skills (with --loaded-skills)",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Uninstall --slugs from Customize → Skills (retired extras)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Machine-readable JSON output (with --status or --loaded-skills)",
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
    if args.preflight:
        try:
            asyncio.run(run_preflight_session(args.cdp_url))
            return 0
        except Exception as exc:
            logger.error("preflight failed", extra={"error": str(exc)})
            print(f"PREFLIGHT FAILED: {exc}", file=sys.stderr)
            return 1
    if args.diagnose_upload_menu:
        try:
            path, found = asyncio.run(diagnose_upload_menu_session(args.cdp_url))
            print(path)
            return 0 if found else 1
        except Exception as exc:
            logger.error("diagnose-upload-menu failed", extra={"error": str(exc)})
            print(f"DIAGNOSE FAILED: {exc}", file=sys.stderr)
            return 1
    if args.status:
        if args.bundles_dir:
            report = asyncio.run(
                scan_ui_parity(cdp_url=args.cdp_url, bundles_dir=Path(args.bundles_dir))
            )
        else:
            report = asyncio.run(scan_ui_parity(cdp_url=args.cdp_url, repo_root=_REPO))
        if args.json:
            return _emit_status_json(report)
        return print_parity_report(report)
    if args.loaded_skills:
        if not args.chat_url:
            parser.error("--loaded-skills requires --chat-url")
        required = _parse_slugs(args.require_loaded) or None
        loaded = asyncio.run(
            scrape_loaded_skills_cdp(cdp_url=args.cdp_url, chat_url=args.chat_url)
        )
        if args.json:
            return emit_loaded_skills_json(loaded, required=required)
        return print_loaded_skills_report(loaded, required=required)
    if args.uninstall:
        slugs = _parse_slugs(args.slugs)
        if not slugs:
            parser.error("--uninstall requires --slugs")
        results = asyncio.run(
            uninstall_skills(
                cdp_url=args.cdp_url,
                slugs=slugs,
                continue_on_error=args.continue_on_error,
            )
        )
        failed = 0
        for result in results:
            print(
                f"{result.status.upper()} {result.slug}"
                + (f": {result.detail}" if result.detail else "")
            )
            if result.status == "failed":
                failed += 1
        return 1 if failed else 0

    if args.zip_dir and args.bundles_dir:
        parser.error("Use --zip-dir OR --bundles-dir, not both")
    if not args.all and not args.slugs and not args.zip_dir:
        parser.error("Specify --all, --slugs, or --zip-dir")
    reject_unsafe_replace_all(
        all_=args.all,
        replace=args.replace,
        force=args.force_replace_all,
        error=parser.error,
    )
    if args.force_replace_all and not (args.all and args.replace):
        parser.error("--force-replace-all is only valid with --all --replace")

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

    screenshot_dir = Path(args.screenshots) if args.screenshots else None
    uploaded = asyncio.run(
        upload_skills(
            items,
            cdp_url=args.cdp_url,
            sleep_s=args.sleep,
            screenshot_dir=screenshot_dir,
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
