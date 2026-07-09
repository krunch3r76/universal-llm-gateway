"""Render + persist recon sidecar markdown under cortex ``_FILES_ROOT``."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from ._shared import _FILES_ROOT

_RECON_SUBDIR = ("notes", "system", "recon")
_SLUG_MAXLEN = 60
_SKIP_TAG_RE = re.compile(r"\[SKIP\]")
_DISCARDS_HEADING_RE = re.compile(r"^## Discards\s*$", re.MULTILINE)


def slugify(value: str, *, default: str = "recon") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (value or default).lower()).strip("-")
    return (s[:_SLUG_MAXLEN].rstrip("-")) or default


def recon_sidecar_uri(label_slug: str, theme_slug: str) -> str:
    return f"cortex://notes/system/recon/{label_slug}/{theme_slug}.md"


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def discards_advisory(body: str) -> str | None:
    """Return advisory when body has SKIP tags but no ## Discards heading."""
    if _DISCARDS_HEADING_RE.search(body):
        return None
    if not _SKIP_TAG_RE.search(body):
        return None
    return (
        "Body contains [SKIP]-tagged content but no ## Discards section; "
        "add ## Discards (one line per SKIP anchor) for escalation veto surface."
    )


def _recon_root() -> Path:
    return _FILES_ROOT.joinpath(*_RECON_SUBDIR)


def resolve_recon_target(label: str, theme: str) -> tuple[str, str, Path] | None:
    """Slugify label/theme and return confined target path, or None if unsafe."""
    label_slug = slugify(label, default="recon")
    theme_slug = slugify(theme, default="theme")
    root = _recon_root().resolve()
    target = (root / label_slug / f"{theme_slug}.md").resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    if ".." in Path(label_slug).parts or ".." in Path(theme_slug).parts:
        return None
    return label_slug, theme_slug, target


def render_recon_sidecar_markdown(
    *,
    label: str,
    theme: str,
    body: str,
    scopes: list[str] | None,
    queries: list[str] | None,
    sink_backend: str,
    sha256: str,
) -> str:
    scopes_json = json.dumps(scopes or [], ensure_ascii=False)
    queries_json = json.dumps(queries or [], ensure_ascii=False)
    fm = [
        "---",
        f"label: {label}",
        f"theme: {theme}",
        f"scopes: {scopes_json}",
        f"queries: {queries_json}",
        f"created_at: {datetime.now(UTC).isoformat()}",
        f"sink_backend: {sink_backend}",
        f"sha256: {sha256}",
        "---",
        "",
    ]
    return "\n".join(fm) + body


def write_recon_sidecar_file(label: str, theme: str, file_content: str) -> str:
    resolved = resolve_recon_target(label, theme)
    if resolved is None:
        raise ValueError("unsafe recon sidecar path")
    _label_slug, _theme_slug, path = resolved
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(file_content, encoding="utf-8")
    return str(path)
