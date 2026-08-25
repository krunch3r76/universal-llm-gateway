"""Playwright orchestration for claude.ai Customize → Skills uploads.

Session entrypoints: preflight, diagnose-upload-menu, and multi-slug upload.
CDP must already be up on Jupiter; this module does not launch Chrome.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from playwright.async_api import Page
from universal_logging import get_logger

from claude_bundles.skills_api import (
    prepare_claude_ai_upload_md,
    prepare_ui_upload_artifact,
)
from claude_bundles.skills_ui_evidence import (
    ComposerPollutedError,
    RunReport,
    capture_failure_state,
    composer_has_attachments,
)
from claude_bundles.skills_ui_menu import (
    PreflightMenuError,
    diagnose_payload,
    empty_inventory,
    resolve_upload_selection,
    select_upload_item,
    stability_guarded_add_click,
    wait_menu_idle,
)
from claude_bundles.skills_ui_network import UploadNetworkOracle
from claude_bundles.skills_ui_panel import (
    DEFAULT_CDP_URL,
    NavigationGate,
    _dismiss_modals,
    _find_add_button,
    _skills_panel_visible,
    _skills_table_rows,
    chrome_start_hint,
    connect_cdp,
    debug_cdp,
    listed_skill_names,
    open_skills_panel,
    panel_state_summary,
    prepare_session,
    run_preflight,
)
from claude_bundles.skills_ui_upload import ReplaceBlockedError, upload_one_skill

logger = get_logger(__name__)

__all__ = [
    "DEFAULT_CDP_URL",
    "chrome_start_hint",
    "debug_cdp",
    "list_bundle_mds",
    "list_zip_dir",
    "prepare_session",
    "run_preflight_session",
    "diagnose_upload_menu_session",
    "upload_skills",
    "upload_zips",
]


def _default_run_dir() -> Path:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path.home() / ".gateway" / "claude-ai-sync" / "runs" / ts


def _network_status(oracle: UploadNetworkOracle | None, slug: str) -> dict | None:
    if oracle is None:
        return None
    result = oracle.result_for(slug)
    if result is None:
        return None
    return {
        "ok": result.ok,
        "status": result.status,
        "slug_echoed": result.slug_echoed,
        "skill_upload_url": result.skill_upload_url or oracle.skill_upload_url,
    }


async def _assert_composer_clean(page: Page, slug: str) -> None:
    if await composer_has_attachments(page):
        raise ComposerPollutedError(
            f"Composer polluted after {slug} upload — attachment chips detected; "
            "operator chat was not mutated"
        )


def _write_menu_json(run_dir: Path, name: str, payload: dict) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / name
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


async def run_preflight_session(cdp_url: str, *, run_dir: Path | None = None) -> None:
    """Connect CDP, assert Add → Upload is selectable, write ``preflight.json`` on fail."""
    effective_run_dir = run_dir or _default_run_dir()
    pw, _browser, context, page = await connect_cdp(cdp_url)
    try:
        await run_preflight(page, context)
        logger.info("preflight OK", extra={"cdp_url": cdp_url, "url": page.url})
        print(f"OK preflight — {page.url}", file=sys.stderr)
    except PreflightMenuError as exc:
        payload = diagnose_payload(
            exc.inventory,
            panel_visible=await _skills_panel_visible(page),
            rows=await _skills_table_rows(page),
            composer_chips=await composer_has_attachments(page),
            selection=select_upload_item(exc.inventory),
        )
        path = _write_menu_json(effective_run_dir, "preflight.json", payload)
        print(f"PREFLIGHT FAILED: {exc}\n{path}", file=sys.stderr)
        raise
    finally:
        await pw.stop()


async def diagnose_upload_menu_session(
    cdp_url: str, *, run_dir: Path | None = None
) -> tuple[Path, bool]:
    """Snapshot Add → menu inventory; never open the upload dialog.

    Returns ``(json_path, upload_item_found)``. Escape closes the menu before
    return so the composer and upload modal stay untouched.
    """
    effective_run_dir = run_dir or _default_run_dir()
    pw, _browser, context, page = await connect_cdp(cdp_url)
    try:
        page = await open_skills_panel(page, context)
        add = await _find_add_button(page)
        if add is None:
            inv = empty_inventory(page.url)
            sel = select_upload_item(inv)
        else:
            await stability_guarded_add_click(add)
            inv = await wait_menu_idle(page, add)
            sel, inv = await resolve_upload_selection(page, add, inv)
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
        png_path = effective_run_dir / "diagnose-upload-menu.png"
        effective_run_dir.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(png_path), full_page=True)
        payload = diagnose_payload(
            inv,
            panel_visible=await _skills_panel_visible(page),
            rows=await _skills_table_rows(page),
            composer_chips=await composer_has_attachments(page),
            selection=sel,
            evidence_paths={"screenshot": str(png_path)},
        )
        path = _write_menu_json(effective_run_dir, "diagnose-upload-menu.json", payload)
        found = sel.status in ("found", "drift")
        print(
            f"{'OK' if found else 'MISSING'} diagnose-upload-menu — {path}",
            file=sys.stderr,
        )
        print(await panel_state_summary(page, context), file=sys.stderr)
        return path, found
    finally:
        await pw.stop()


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
    run_dir: Path | None = None,
) -> list[str]:
    """Upload each skill via CDP-attached Chrome."""
    del truncate_descriptions
    effective_run_dir = run_dir or _default_run_dir()
    effective_run_dir.mkdir(parents=True, exist_ok=True)
    report = RunReport(started_at=datetime.now(UTC).isoformat(), cdp_url=cdp_url)
    nav_gate = NavigationGate()

    pw, _browser, context, page = await connect_cdp(cdp_url)
    uploaded: list[str] = []
    skipped = 0
    staging = Path(tempfile.mkdtemp(prefix="claude-ai-upload-"))
    try:
        page = await open_skills_panel(page, context, nav_gate=nav_gate)
        await run_preflight(page, context)
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
        report.skipped = skipped

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
            page = await open_skills_panel(page, context, nav_gate=nav_gate)
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

            evidence = None
            attempts = 0
            last_exc: Exception | None = None
            for attempt in range(2):
                attempts = attempt + 1
                oracle: UploadNetworkOracle | None = None
                try:
                    nav_gate.upload_in_flight = True
                    nav_gate.network_verified = False
                    oracle = UploadNetworkOracle(page)
                    oracle.expect_slug(slug)
                    page = await upload_one_skill(
                        page,
                        upload_path,
                        slug,
                        replacing=replacing,
                        screenshot_dir=screenshot_dir,
                        desc_len=desc_len or None,
                        context=context,
                        nav_gate=nav_gate,
                        oracle=oracle,
                    )
                    nav_gate.network_verified = True
                    await _assert_composer_clean(page, slug)
                    uploaded.append(slug)
                    table_before.add(slug.lower())
                    report.record_success(
                        slug,
                        mode=mode,
                        network_status=_network_status(oracle, slug),
                        skill_upload_url=oracle.skill_upload_url,
                    )
                    print(f"OK {slug}", file=sys.stderr)
                    last_exc = None
                    break
                except ReplaceBlockedError as exc:
                    print(f"SKIP {exc}", file=sys.stderr)
                    skipped += 1
                    report.skipped += 1
                    await _dismiss_modals(page)
                    if continue_on_error:
                        break
                    raise
                except ComposerPollutedError as exc:
                    last_exc = exc
                    print(f"ERROR {slug} (composer polluted): {exc}", file=sys.stderr)
                    evidence = await capture_failure_state(
                        page,
                        slug,
                        effective_run_dir,
                        oracle,
                        inventory=getattr(exc, "inventory", None),
                    )
                    report.composer_polluted = True
                    report.record_failure(
                        slug,
                        mode=mode,
                        error=str(exc),
                        attempts=attempts,
                        evidence=evidence,
                        network_status=_network_status(oracle, slug) if oracle else None,
                    )
                    await _dismiss_modals(page)
                    if continue_on_error:
                        continue
                    raise
                except Exception as exc:
                    last_exc = exc
                    print(f"ERROR {slug} (attempt {attempts}): {exc}", file=sys.stderr)
                    evidence = await capture_failure_state(
                        page,
                        slug,
                        effective_run_dir,
                        oracle,
                        inventory=getattr(exc, "inventory", None),
                    )
                    await _dismiss_modals(page)
                    if attempt == 0:
                        page = await open_skills_panel(page, context, nav_gate=nav_gate)
                        await run_preflight(page, context)
                        continue
                finally:
                    nav_gate.upload_in_flight = False
                    if oracle is not None:
                        oracle.detach()

            if last_exc is not None:
                report.record_failure(
                    slug,
                    mode=mode,
                    error=str(last_exc),
                    attempts=attempts,
                    evidence=evidence,
                    network_status=_network_status(oracle, slug) if oracle else None,
                )
                if continue_on_error:
                    continue
                raise last_exc

        print(
            f"Summary: uploaded {len(uploaded)}/{len(targets)}"
            + (f", {skipped} skipped" if skipped else "")
            + (f", {len(report.failed)} failed" if report.failed else ""),
            file=sys.stderr,
        )
    finally:
        try:
            report.composer_polluted = await composer_has_attachments(page)
        except Exception:
            report.composer_polluted = False
        report_path = report.write(effective_run_dir)
        logger.info("run report written", extra={"path": str(report_path)})
        print(f"Run report: {report_path}", file=sys.stderr)
        await pw.stop()
        if staging.is_dir():
            for child in staging.rglob("*"):
                if child.is_file():
                    child.unlink(missing_ok=True)
            for child in sorted(staging.rglob("*"), reverse=True):
                if child.is_dir():
                    child.rmdir()
            staging.rmdir()
        if report.composer_polluted:
            raise ComposerPollutedError(
                "Composer polluted at end of run — attachment chips detected"
            )
    return uploaded


upload_zips = upload_skills


def list_zip_dir(zip_dir: Path, *, slugs: Sequence[str] | None = None) -> list[tuple[str, Path]]:
    """Return ``(slug, zip_path)`` pairs from ``zip_dir``, optionally filtered."""
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
    """Return ``(slug, SKILL.md)`` pairs from staged bundle dirs, optionally filtered."""
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
