"""Revision archive write and as-of lookup for derived views."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

_REV_DIR = "notes/system/views/revisions"
_STAMP_TIME_RE = re.compile(r'"time"\s*:\s*"([^"]+)"')


def _doc_slug(document_id: str) -> str:
    return document_id.split(":", 1)[-1]


def archive_revision(
    files_root: Path,
    *,
    document_id: str,
    view_rev: int,
    body: str,
    archived_at: str | None = None,
) -> str:
    slug = _doc_slug(document_id)
    rel = f"{_REV_DIR}/{slug}/rev-{view_rev}.md"
    path = files_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    header = f"<!-- archived_at: {archived_at or datetime.now(UTC).isoformat()} -->\n"
    path.write_text(header + body, encoding="utf-8")
    return rel


def _stamp_time(body: str) -> str | None:
    for line in body.splitlines()[:5]:
        if "view-stamp:" in line:
            match = _STAMP_TIME_RE.search(line)
            if match:
                return match.group(1)
    return None


def read_asof_instance(
    files_root: Path,
    *,
    document_id: str,
    as_of_system: str,
) -> str | None:
    slug = _doc_slug(document_id)
    rev_dir = files_root / _REV_DIR / slug
    if not rev_dir.is_dir():
        return None
    target = datetime.fromisoformat(as_of_system.replace("Z", "+00:00"))
    best_path: Path | None = None
    best_time: datetime | None = None
    for path in sorted(rev_dir.glob("rev-*.md")):
        body = path.read_text(encoding="utf-8")
        stamp_time = _stamp_time(body)
        if not stamp_time:
            continue
        try:
            inst_time = datetime.fromisoformat(stamp_time.replace("Z", "+00:00"))
        except ValueError:
            continue
        if inst_time <= target and (best_time is None or inst_time > best_time):
            best_time = inst_time
            best_path = path
    if best_path is None:
        return None
    return best_path.read_text(encoding="utf-8")


def list_revision_uris(files_root: Path, document_id: str) -> list[str]:
    slug = _doc_slug(document_id)
    rev_dir = files_root / _REV_DIR / slug
    if not rev_dir.is_dir():
        return []
    return [
        f"{_REV_DIR}/{slug}/{p.name}"
        for p in sorted(rev_dir.glob("rev-*.md"))
    ]


__all__ = ["archive_revision", "list_revision_uris", "read_asof_instance"]
