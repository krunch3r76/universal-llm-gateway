"""Write packet-pinned deliverables under cortex ``_FILES_ROOT``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._shared import _FILES_ROOT
from ._thread_sidecar import content_sha256


def normalize_cortex_rel(raw: str) -> str | None:
    path = raw.strip()
    for prefix in ("cortex://", "cortex:"):
        if path.lower().startswith(prefix):
            path = path[len(prefix) :]
            break
    path = path.lstrip("/")
    if not path or ".." in Path(path).parts:
        return None
    return path


def pinned_deliverable_uri(rel: str) -> str:
    return f"cortex://{rel.lstrip('/')}"


def _resolved_target(rel_path: str) -> tuple[str, Path] | None:
    rel = normalize_cortex_rel(rel_path)
    if rel is None:
        return None
    target = (_FILES_ROOT / rel).resolve()
    try:
        target.relative_to(_FILES_ROOT.resolve())
    except ValueError:
        return None
    return rel, target


def write_pinned_deliverable_impl(
    rel_path: str,
    content: str,
    *,
    write_if_absent: bool = False,
) -> dict[str, Any]:
    resolved = _resolved_target(rel_path)
    if resolved is None:
        return {"error": "invalid or unsafe rel_path"}
    rel, target = resolved
    if write_if_absent and target.is_file():
        existing = target.read_text(encoding="utf-8")
        return {
            "uri": pinned_deliverable_uri(rel),
            "sha256": content_sha256(existing),
            "body_chars": len(existing),
            "skipped": True,
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {
        "uri": pinned_deliverable_uri(rel),
        "path": str(target),
        "sha256": content_sha256(content),
        "body_chars": len(content),
        "skipped": False,
    }
