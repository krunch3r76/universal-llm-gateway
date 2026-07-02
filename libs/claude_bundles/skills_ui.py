"""Playwright orchestration for claude.ai Customize → Skills uploads."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from claude_bundles.skills_api import prepare_claude_ai_upload_md, prepare_ui_upload_artifact
from claude_bundles.skills_ui_panel import (
    DEFAULT_CDP_URL,
    _dismiss_modals,
    chrome_start_hint,
    connect_cdp,
    debug_cdp,
    listed_skill_names,
    open_skills_panel,
    prepare_session,
)
from claude_bundles.skills_ui_upload import ReplaceBlockedError, upload_one_skill

__all__ = [
    "DEFAULT_CDP_URL",
    "chrome_start_hint",
    "debug_cdp",
    "list_bundle_mds",
    "list_zip_dir",
    "prepare_session",
    "upload_skills",
    "upload_zips",
]


async def upload_skills(
    items: Sequence[tuple[str, Path]],
    *,
    cdp_url: str,
    sleep_s: float = 6.0,
    screenshot_dir: Path | None = None,
    skip_existing: bool = False,
    limit: int | None = None,
    continue_on_error: bool = False,
    truncate_descriptions: bool = True,
    upload_format: str = "md",
    full_body: bool = True,
) -> list[str]:
    """Upload each skill via CDP-attached Chrome."""
    del truncate_descriptions
    pw, _browser, context, page = await connect_cdp(cdp_url)
    uploaded: list[str] = []
    skipped = 0
    failed: list[str] = []
    staging = Path(tempfile.mkdtemp(prefix="claude-ai-upload-"))
    try:
        page = await open_skills_panel(page, context)
        table_before = await listed_skill_names(page)

        targets: list[tuple[str, Path]] = []
        pending_new: list[tuple[str, Path]] = []
        pending_replace: list[tuple[str, Path]] = []
        for slug, path in items:
            if skip_existing and slug.lower() in table_before:
                skipped += 1
                continue
            if slug.lower() in table_before:
                pending_replace.append((slug, path))
            else:
                pending_new.append((slug, path))
        targets = pending_new + pending_replace
        if limit:
            targets = targets[:limit]

        if skip_existing and skipped:
            print(f"SKIP {skipped} already in Skills table (use --replace to re-upload)", file=sys.stderr)
        if pending_new:
            print(f"NEW {len(pending_new)} to upload", file=sys.stderr)
        if pending_replace:
            print(f"REPLACE {len(pending_replace)} queued (confirm dialog)", file=sys.stderr)

        if not targets:
            print(f"Nothing to upload ({skipped} skipped, 0 pending)", file=sys.stderr)
            return []

        for i, (slug, path) in enumerate(targets):
            if i:
                await asyncio.sleep(sleep_s)
            page = await open_skills_panel(page, context)
            await _dismiss_modals(page)
            replacing = slug.lower() in table_before
            mode = "REPLACE" if replacing else "NEW"
            print(f"{mode} {slug}: uploading", file=sys.stderr)
            if path.suffix.lower() == ".zip":
                upload_path, desc_len = path, 0
            elif upload_format == "md":
                upload_path, truncated, desc_len = prepare_claude_ai_upload_md(
                    path, staging, slug=slug
                )
                if truncated:
                    print(
                        f"TRUNC-DESC {slug}: description staged ≤200 for claude.ai",
                        file=sys.stderr,
                    )
            else:
                upload_path, desc_len = prepare_ui_upload_artifact(
                    path,
                    staging,
                    slug=slug,
                    fmt=upload_format,
                    full_body=full_body,
                )
            try:
                page = await upload_one_skill(
                    page,
                    upload_path,
                    slug,
                    replacing=replacing,
                    screenshot_dir=screenshot_dir,
                    desc_len=desc_len or None,
                )
                uploaded.append(slug)
                table_before.add(slug.lower())
                print(f"OK {slug}", file=sys.stderr)
            except ReplaceBlockedError as exc:
                print(f"SKIP {exc}", file=sys.stderr)
                skipped += 1
                await _dismiss_modals(page)
                if continue_on_error:
                    continue
                raise
            except Exception as exc:
                print(f"ERROR {slug}: {exc}", file=sys.stderr)
                if screenshot_dir:
                    screenshot_dir.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(
                        path=str(screenshot_dir / f"{slug}-error.png"), full_page=True
                    )
                await _dismiss_modals(page)
                if continue_on_error:
                    failed.append(slug)
                    continue
                raise
        print(
            f"Summary: uploaded {len(uploaded)}/{len(targets)}"
            + (f", {skipped} skipped" if skipped else "")
            + (f", {len(failed)} failed" if failed else ""),
            file=sys.stderr,
        )
    finally:
        await pw.stop()
        if staging.is_dir():
            for child in staging.rglob("*"):
                if child.is_file():
                    child.unlink(missing_ok=True)
            for child in sorted(staging.rglob("*"), reverse=True):
                if child.is_dir():
                    child.rmdir()
            staging.rmdir()
    return uploaded


upload_zips = upload_skills


def list_zip_dir(zip_dir: Path, *, slugs: Sequence[str] | None = None) -> list[tuple[str, Path]]:
    if not zip_dir.is_dir():
        raise FileNotFoundError(f"Zip dir not found: {zip_dir}")
    paths = sorted(zip_dir.glob("*.zip"))
    if slugs:
        wanted = {s.strip() for s in slugs if s.strip()}
        paths = [p for p in paths if p.stem in wanted]
    return [(p.stem, p) for p in paths]


def list_bundle_mds(
    bundles_dir: Path, *, slugs: Sequence[str] | None = None
) -> list[tuple[str, Path]]:
    if not bundles_dir.is_dir():
        raise FileNotFoundError(f"Bundles dir not found: {bundles_dir}")
    if slugs:
        names = [s.strip() for s in slugs if s.strip()]
    else:
        names = sorted(
            d.name for d in bundles_dir.iterdir() if (d / "SKILL.md").is_file()
        )
    out: list[tuple[str, Path]] = []
    for slug in names:
        md = bundles_dir / slug / "SKILL.md"
        if md.is_file():
            out.append((slug, md))
    return out
