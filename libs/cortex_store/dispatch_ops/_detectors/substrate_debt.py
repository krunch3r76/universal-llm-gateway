"""Closeout audit: named URI-fallback deferrals in DISPOSITIONS / SUBSTRATE_DEBT blocks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from .._shared import _FILES_ROOT
from ._shared import _finding

logger = get_logger(__name__)

_KIND = "substrate_debt_uri_fallback"
_SKIP_DIR_PARTS = frozenset({"tmp", "__pycache__", ".git"})
_SCAN_ROOTS = (
    _FILES_ROOT / "tasks" / "specs",
    _FILES_ROOT / "notes",
    _FILES_ROOT / "documents",
)
_DASH = r"[—–-]"
_DISPOSITIONS_HEADING_RE = re.compile(r"^##\s+DISPOSITIONS\b")
_ANY_HEADING_RE = re.compile(r"^##\s+")
_DISPOSITIONS_LINE_RE = re.compile(
    rf"^C(\d+):\s*substrate_debt\s*{_DASH}\s*uri_fallback\s*{_DASH}\s*"
    rf"(?P<uri>.+?)\s*{_DASH}\s*claim:\s*(?P<claim>.+?)"
    rf"(?:\s*{_DASH}\s*suggested_entity:\s*.+)?\s*$"
)
_SUBSTRATE_DEBT_LINE_RE = re.compile(
    r"^SUBSTRATE_DEBT:\s*uri_fallback\s*\|\s*(?P<uri>[^|]+?)\s*\|\s*(?P<claim>.+?)\s*$"
)


def _skip_dir(path: Path, root: Path) -> bool:
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(part in _SKIP_DIR_PARTS for part in rel_parts)


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return (lineno, uri, claim) for each valid deferral line in *path*."""
    hits: list[tuple[int, str, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("substrate_debt_uri_fallback: cannot read %s: %s", path, exc)
        return hits

    in_dispositions = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if _ANY_HEADING_RE.match(stripped):
            in_dispositions = bool(_DISPOSITIONS_HEADING_RE.match(stripped))
            continue

        standalone = _SUBSTRATE_DEBT_LINE_RE.match(stripped)
        if standalone:
            uri = standalone.group("uri").strip()
            claim = standalone.group("claim").strip()
            if uri and claim:
                hits.append((lineno, uri, claim))
            continue

        if in_dispositions:
            disposition = _DISPOSITIONS_LINE_RE.match(stripped)
            if disposition:
                uri = disposition.group("uri").strip()
                claim = disposition.group("claim").strip()
                if uri and claim:
                    hits.append((lineno, uri, claim))

    return hits


def detect_substrate_debt_uri_fallback(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Grep durable docs for named URI-fallback deferrals (r3 WWP arm)."""
    del conn, subject
    findings: list[dict[str, Any]] = []

    for root in _SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or _skip_dir(path, root):
                continue
            if path.suffix.lower() not in {".md", ".txt", ".json", ".yaml", ".yml"}:
                continue
            for lineno, uri, claim in _scan_file(path):
                doc_ref = f"{path.relative_to(_FILES_ROOT)}:{lineno}"
                findings.append(
                    _finding(
                        _KIND,
                        doc_ref,
                        f"Named URI-fallback deferral unpaid at {doc_ref}: {uri} — claim: {claim}",
                        audit_id=f"{_KIND}:{doc_ref}",
                    )
                )

    return findings


__all__ = ["detect_substrate_debt_uri_fallback"]
