"""Failure evidence capture and end-of-run JSON report."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from playwright.async_api import Page

from claude_bundles.skills_ui_panel import (
    _skills_panel_visible,
)

if TYPE_CHECKING:
    from claude_bundles.skills_ui_network import UploadNetworkOracle

_UPLOAD_TITLE = __import__("re").compile(r"upload\s+skill", __import__("re").I)


class ComposerPollutedError(RuntimeError):
    """Chat composer has attachment chips — upload path must not mutate operator chat."""


async def composer_has_attachments(page: Page) -> bool:
    """Return True when visible attachment-semantic chips pollute the composer.

    Page-chrome badges (e.g. Labs/Beta feature chips matching generic ``chip``
    classes or ``data-testid*='chip'``) are intentionally excluded — only
    attachment-semantic selectors are consulted. Fail-closed pollution detection
    rests on those primaries; if a future attachment variant uses only a bare
    ``chip`` class with no attachment token, repair via a composer-scoped
    fallback (P2), not a page-wide chip scan.
    """
    selectors = (
        "[data-testid='file-attachment']",
        "[data-testid='attachment']",
        ".attachment-chip",
        "[class*='attachment']",
        "[class*='Attachment']",
    )
    for sel in selectors:
        loc = page.locator(sel)
        if await loc.count():
            for i in range(min(await loc.count(), 5)):
                if await loc.nth(i).is_visible():
                    return True
    return False


async def _table_row_texts(page: Page) -> list[str]:
    rows = page.locator("table tbody tr")
    n = await rows.count()
    out: list[str] = []
    for i in range(n):
        out.append((await rows.nth(i).inner_text()).strip())
    return out


async def _upload_modal_open(page: Page) -> bool:
    overlays = page.locator('[data-state="open"].fixed, [role="dialog"]')
    for i in range(await overlays.count()):
        ov = overlays.nth(i)
        if not await ov.is_visible():
            continue
        text = await ov.inner_text()
        if _UPLOAD_TITLE.search(text):
            return True
    return False


async def _overlay_html(page: Page) -> str:
    overlays = page.locator('[data-state="open"]')
    parts: list[str] = []
    for i in range(min(await overlays.count(), 4)):
        try:
            parts.append(await overlays.nth(i).evaluate("el => el.outerHTML"))
        except Exception:
            pass
    return "\n".join(parts)


async def capture_failure_state(
    page: Page,
    slug: str,
    run_dir: Path,
    oracle: UploadNetworkOracle | None = None,
) -> dict[str, Any]:
    """Write png + state JSON + overlay HTML + network log for a failed slug."""
    fail_dir = run_dir / "failures" / slug
    fail_dir.mkdir(parents=True, exist_ok=True)

    from claude_bundles.skills_ui_confirm import replace_confirm_open

    table_rows = await _table_row_texts(page)
    slug_row = next((r for r in table_rows if slug.lower() in r.lower()), None)
    state: dict[str, Any] = {
        "slug": slug,
        "url": page.url,
        "panel_visible": await _skills_panel_visible(page),
        "upload_modal_open": await _upload_modal_open(page),
        "replace_confirm_open": await replace_confirm_open(page),
        "table_rows": table_rows,
        "slug_row_text": slug_row,
        "composer_chips": await composer_has_attachments(page),
        "network_log": oracle.captured_log() if oracle else [],
        "captured_at": datetime.now(UTC).isoformat(),
    }

    png_path = fail_dir / "screenshot.png"
    await page.screenshot(path=str(png_path), full_page=True)
    state_path = fail_dir / "state.json"
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    html_path = fail_dir / "overlays.html"
    html_path.write_text(await _overlay_html(page), encoding="utf-8")
    if oracle and oracle.captured_log():
        net_path = fail_dir / "network.json"
        net_path.write_text(json.dumps(oracle.captured_log(), indent=2), encoding="utf-8")

    state["evidence_paths"] = {
        "screenshot": str(png_path),
        "state": str(state_path),
        "overlays": str(html_path),
    }
    return state


@dataclass
class SlugOutcome:
    slug: str
    ok: bool
    attempts: int = 1
    mode: str = "NEW"
    error: str | None = None
    evidence_paths: dict[str, str] = field(default_factory=dict)
    network_status: dict[str, Any] | None = None
    skill_upload_url: str | None = None
    composer_polluted: bool = False


@dataclass
class RunReport:
    started_at: str
    cdp_url: str = ""
    finished_at: str | None = None
    outcomes: list[SlugOutcome] = field(default_factory=list)
    composer_polluted: bool = False
    skill_upload_url: str | None = None
    uploaded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: int = 0

    def record_success(
        self,
        slug: str,
        *,
        mode: str,
        network_status: dict | None,
        skill_upload_url: str | None = None,
    ) -> None:
        self.uploaded.append(slug)
        if skill_upload_url:
            self.skill_upload_url = skill_upload_url
        self.outcomes.append(
            SlugOutcome(
                slug=slug,
                ok=True,
                mode=mode,
                network_status=network_status,
                skill_upload_url=skill_upload_url,
            )
        )

    def record_failure(
        self,
        slug: str,
        *,
        mode: str,
        error: str,
        attempts: int,
        evidence: dict[str, Any] | None,
        network_status: dict | None,
    ) -> None:
        self.failed.append(slug)
        paths = (evidence or {}).get("evidence_paths", {})
        polluted = bool((evidence or {}).get("composer_chips"))
        self.outcomes.append(
            SlugOutcome(
                slug=slug,
                ok=False,
                attempts=attempts,
                mode=mode,
                error=error,
                evidence_paths=paths,
                network_status=network_status,
                composer_polluted=polluted,
            )
        )

    def write(self, run_dir: Path) -> Path:
        self.finished_at = datetime.now(UTC).isoformat()
        out = run_dir / "run_report.json"
        payload = asdict(self)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return out
