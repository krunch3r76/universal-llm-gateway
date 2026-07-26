"""Resolve CHECKPOINT bodies through agent-bus Sidecar: pointers.

Agent-bus soft-spill (8k) and sidecar-first worker posts leave a short briefing
plus ``Sidecar: cortex://…`` on the turn while the schema-complete CHECKPOINT
lives in the cortex file. Charter admit/residue/heal must read the durable
body — validating the stub alone freezes the tick at ``missing_sections``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from implement_admission.closeout_helpers import cortex_files_root
from universal_logging import get_logger

logger = get_logger(__name__)

_SIDECAR_LINE_RE = re.compile(
    r"(?im)^\s*Sidecar:\s*(cortex://notes/system/threads/\S+)"
)
_CHECKPOINT_MARK_RE = re.compile(r"(?im)^#\s*CHECKPOINT\b|^##\s+Steps\b|^##\s+Next pickup\b")


def extract_sidecar_uri(
    body: str,
    *,
    sidecar_uri: str | None = None,
) -> str | None:
    """Prefer the turn's ``sidecar_uri`` field; else parse a trailing Sidecar: line."""
    field = (sidecar_uri or "").strip()
    if field.startswith("cortex://"):
        return field
    match = _SIDECAR_LINE_RE.search(body or "")
    return match.group(1).rstrip(".,);]") if match else None


def strip_sidecar_frontmatter(raw: str) -> str:
    """Drop the thread-sidecar YAML frontmatter wrapper; return CHECKPOINT body."""
    text = raw or ""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    return text[end + 4 :].lstrip("\n")


def _resolve_cortex_path(uri: str, cortex_root: Path) -> Path | None:
    raw = uri.strip()
    if not raw.startswith("cortex://"):
        return None
    rel = raw[len("cortex://") :]
    root = cortex_root.resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def _looks_like_checkpoint(body: str) -> bool:
    return _CHECKPOINT_MARK_RE.search(body or "") is not None


def resolve_checkpoint_body(
    body: str,
    *,
    sidecar_uri: str | None = None,
    cortex_root: Path | None = None,
) -> str:
    """Return schema-bearing CHECKPOINT text, following Sidecar: when needed.

    If the inline body already looks like a CHECKPOINT, it wins (no I/O).
    Otherwise load the pointed cortex sidecar (frontmatter stripped). Unreadable
    or non-CHECKPOINT sidecars fall through to the original body.
    """
    inline = body or ""
    if _looks_like_checkpoint(inline):
        return inline
    uri = extract_sidecar_uri(inline, sidecar_uri=sidecar_uri)
    if uri is None:
        return inline
    root = cortex_root if cortex_root is not None else cortex_files_root()
    path = _resolve_cortex_path(uri, root)
    if path is None or not path.is_file():
        logger.warning(
            "charter-runner checkpoint sidecar unreadable uri=%s path=%s",
            uri,
            path,
        )
        return inline
    try:
        loaded = strip_sidecar_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        logger.exception("charter-runner failed reading checkpoint sidecar %s", path)
        return inline
    if not _looks_like_checkpoint(loaded):
        logger.warning(
            "charter-runner checkpoint sidecar lacks CHECKPOINT schema uri=%s",
            uri,
        )
        return inline
    return loaded


def materialize_checkpoint_turn(
    turn: dict[str, Any],
    *,
    cortex_root: Path | None = None,
) -> dict[str, Any]:
    """Shallow-copy ``turn`` with ``body`` resolved through any Sidecar pointer."""
    resolved = resolve_checkpoint_body(
        str(turn.get("body") or ""),
        sidecar_uri=turn.get("sidecar_uri") if isinstance(turn.get("sidecar_uri"), str) else None,
        cortex_root=cortex_root,
    )
    if resolved == turn.get("body"):
        return turn
    out = dict(turn)
    out["body"] = resolved
    return out


__all__ = [
    "extract_sidecar_uri",
    "materialize_checkpoint_turn",
    "resolve_checkpoint_body",
    "strip_sidecar_frontmatter",
]
