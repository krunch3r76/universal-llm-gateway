"""Closeout audit: staging paths cited in durable docs and entity attributes."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from .._shared import _FILES_ROOT
from ...models._shared import STAGING_PREFIXES, uri_first_segment_is_staging
from ._shared import _finding

logger = get_logger(__name__)

_KIND = "provenance_cites_staging"
_SKIP_DIR_PARTS = frozenset({"tmp", "__pycache__", ".git"})
_SCAN_ROOTS = (
    _FILES_ROOT / "tasks" / "specs",
    _FILES_ROOT / "notes",
    _FILES_ROOT / "documents",
)
_STAGING_TOKEN_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(prefix) + r"(?:/|\b)" for prefix in STAGING_PREFIXES)
    + r")",
    re.IGNORECASE,
)


def _skip_dir(path: Path, root: Path) -> bool:
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(part in _SKIP_DIR_PARTS for part in rel_parts)


def _scan_file(path: Path) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("provenance_cites_staging: cannot read %s: %s", path, exc)
        return hits
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _STAGING_TOKEN_RE.search(line):
            hits.append((lineno, line.strip()[:160]))
    return hits


def detect_provenance_cites_staging(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Grep durable docs + entity provenance attrs for staging-prefix citations."""
    del subject
    findings: list[dict[str, Any]] = []

    for root in _SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or _skip_dir(path, root):
                continue
            if path.suffix.lower() not in {".md", ".txt", ".json", ".yaml", ".yml"}:
                continue
            for lineno, snippet in _scan_file(path):
                doc_ref = f"{path.relative_to(_FILES_ROOT)}:{lineno}"
                findings.append(
                    _finding(
                        _KIND,
                        doc_ref,
                        f"Staging path cited in durable doc at {doc_ref}: {snippet!r}",
                        audit_id=f"{_KIND}:{doc_ref}",
                    )
                )

    rows = conn.execute(
        "SELECT id, attributes FROM entities WHERE attributes IS NOT NULL"
    ).fetchall()
    for row in rows:
        entity_id = row["id"]
        raw = row["attributes"]
        try:
            attrs = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(attrs, dict):
            continue
        for key, value in attrs.items():
            if isinstance(value, str) and uri_first_segment_is_staging(value):
                findings.append(
                    _finding(
                        _KIND,
                        f"{entity_id}:{key}",
                        f"Entity attribute {entity_id}.{key} cites staging path {value!r}",
                        audit_id=f"{_KIND}:{entity_id}:{key}",
                    )
                )
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and uri_first_segment_is_staging(item):
                        findings.append(
                            _finding(
                                _KIND,
                                f"{entity_id}:{key}",
                                f"Entity attribute {entity_id}.{key} cites staging path {item!r}",
                                audit_id=f"{_KIND}:{entity_id}:{key}:{item}",
                            )
                        )

    return findings


__all__ = ["detect_provenance_cites_staging"]
