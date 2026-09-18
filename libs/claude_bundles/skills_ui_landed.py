"""In-run Customize upload confirm — toast + Skills table, not a /mnt/skills dump.

The 2026-09-17 retrieval-before-authoring replace landed (network 200,
slug_echoed, toast ``Replaced {slug}``) and the harness still printed
``uploaded 0/1`` because a success toast matched a page-wide attachment
selector. Dumping the container zip is a content-hash audit, not the
upload OK line.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from playwright.async_api import Page

from claude_bundles.skills_ui_panel import snapshot_slug_row

_TOAST_VERB = re.compile(r"\b(replaced|uploaded|added|installed)\b", re.I)
_NAMED_TOAST = (
    r"(?:Replaced|Uploaded|Added|Installed)\s+{slug}"
)


@dataclass(frozen=True)
class LandedConfirm:
    """UI proof that Customize accepted the slug this run."""

    kind: str
    toast: str | None
    table_row: str | None

    def as_dict(self) -> dict[str, str | None]:
        return asdict(self)


def toast_names_slug(text: str, slug: str) -> bool:
    """True when toast copy names *slug* as replaced/uploaded/added."""
    if not text or slug.lower() not in text.lower():
        return False
    return bool(_TOAST_VERB.search(text))


async def read_skill_upload_toast(page: Page, slug: str) -> str | None:
    """Return visible Replaced/Uploaded toast text for *slug*, if any."""
    locators = (
        page.get_by_role("status"),
        page.get_by_role("alert"),
        page.locator("[class*='toast' i]"),
        page.locator("[data-testid*='toast' i]"),
        page.get_by_text(re.compile(_NAMED_TOAST.format(slug=re.escape(slug)), re.I)),
    )
    for loc in locators:
        try:
            n = await loc.count()
        except Exception:
            continue
        for i in range(min(n, 8)):
            el = loc.nth(i)
            try:
                if not await el.is_visible():
                    continue
                text = " ".join((await el.inner_text()).split())
            except Exception:
                continue
            if toast_names_slug(text, slug):
                return text
    return None


async def confirm_skill_upload_ui(
    page: Page, slug: str, *, replacing: bool
) -> LandedConfirm:
    """Fail closed when neither a success toast nor the Skills row is present.

    Replace of an already-listed slug needs the toast (or a changed row is
    still accepted as ``table``). A full container dump is out of scope.
    """
    del replacing
    toast = await read_skill_upload_toast(page, slug)
    row = await snapshot_slug_row(page, slug)
    if toast and row:
        return LandedConfirm("toast+table", toast, row)
    if toast:
        return LandedConfirm("toast", toast, row)
    if row:
        return LandedConfirm("table", None, row)
    raise RuntimeError(
        f"Upload UI confirm missing for {slug}: "
        "no Replaced/Uploaded toast and slug absent from Skills table"
    )
