"""Read-only parity scan: local bundles vs claude.ai Skills table."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from claude_bundles.resolver import claude_ai_target_slugs
from claude_bundles.skills_api import validate_bundle_dir
from claude_bundles.skills_ui_panel import (
    connect_cdp,
    listed_skill_names,
    open_skills_panel,
)


@dataclass(frozen=True)
class ParityReport:
    target_count: int
    on_ui: frozenset[str]
    missing_on_ui: tuple[str, ...]
    extra_on_ui: tuple[str, ...]
    invalid_local: tuple[tuple[str, str], ...]
    stale_local: tuple[str, ...]

    @property
    def in_sync(self) -> bool:
        return not self.missing_on_ui and not self.invalid_local


async def scan_ui_parity(
    *,
    cdp_url: str,
    bundles_dir: Path,
) -> ParityReport:
    """Compare ``claude_ai_target_slugs()`` to Customize → Skills table + local bundles."""
    target = {s.lower() for s in claude_ai_target_slugs()}

    invalid: list[tuple[str, str]] = []
    stale: list[str] = []
    for slug in claude_ai_target_slugs():
        bundle_dir = bundles_dir / slug
        md = bundle_dir / "SKILL.md"
        if not md.is_file():
            invalid.append((slug, "missing .claude/skills bundle — run gen_claude_bundles"))
            continue
        try:
            validate_bundle_dir(slug, bundle_dir)
        except ValueError as exc:
            invalid.append((slug, str(exc)))

    pw, _browser, context, page = await connect_cdp(cdp_url)
    try:
        page = await open_skills_panel(page, context)
        on_ui = frozenset(await listed_skill_names(page))
    finally:
        await pw.stop()

    missing = tuple(sorted(s for s in target if s not in on_ui))
    extra = tuple(sorted(s for s in on_ui if s not in target))
    return ParityReport(
        target_count=len(target),
        on_ui=on_ui,
        missing_on_ui=missing,
        extra_on_ui=extra,
        invalid_local=tuple(invalid),
        stale_local=tuple(stale),
    )


def _uninstall_command(extra: tuple[str, ...]) -> str:
    slugs = ",".join(extra)
    return f"claude-ai-sync-jupiter uninstall --slugs {slugs} --continue-on-error"


def print_parity_report(report: ParityReport) -> int:
    """Emit human-readable status; return exit code (0 = in sync)."""
    print(f"target={report.target_count} on_ui={len(report.on_ui)}", file=sys.stderr)
    if report.missing_on_ui:
        print(f"missing_on_ui ({len(report.missing_on_ui)}):", file=sys.stderr)
        for slug in report.missing_on_ui:
            print(f"  {slug}", file=sys.stderr)
    if report.extra_on_ui:
        print(f"extra_on_ui ({len(report.extra_on_ui)}):", file=sys.stderr)
        for slug in report.extra_on_ui:
            print(f"  {slug}", file=sys.stderr)
        print(_uninstall_command(report.extra_on_ui), file=sys.stderr)
    if report.invalid_local:
        print(f"invalid_local ({len(report.invalid_local)}):", file=sys.stderr)
        for slug, err in report.invalid_local:
            print(f"  {slug}: {err}", file=sys.stderr)
    if report.in_sync and not report.extra_on_ui:
        print("OK parity in sync", file=sys.stderr)
        return 0
    if report.in_sync:
        print(
            "DRIFT detected — run uninstall command above, then re-scan",
            file=sys.stderr,
        )
        return 1
    print("DRIFT detected — regen then upload missing slugs", file=sys.stderr)
    return 1
