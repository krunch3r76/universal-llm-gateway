"""Write packet-pinned deliverables under cortex ``_FILES_ROOT``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from durable_io.atomic import durable_write_text, path_flock
from implement_admission.scheme_resolve import resolve_fs_ingress
from implement_admission.share_uri_emit import to_share_uri

from ._shared import _FILES_ROOT
from ._thread_sidecar import content_sha256


def normalize_share_rel(raw: str, *, sandbox: str = "cortex") -> str | None:
    path = raw.strip()
    try:
        ingress = resolve_fs_ingress(path, sandbox=sandbox)
    except ValueError:
        for prefix in ("cortex://", "cortex:", "workspaces://", "workspaces:"):
            if path.lower().startswith(prefix):
                path = path.split(":", 2)[-1].lstrip("/")
                break
        path = path.lstrip("/")
        if not path or ".." in Path(path).parts:
            return None
        return path
    rel = ingress.rel_path.lstrip("/")
    if not rel or ".." in Path(rel).parts:
        return None
    return rel


def normalize_cortex_rel(raw: str) -> str | None:
    return normalize_share_rel(raw, sandbox="cortex")


def pinned_deliverable_uri(rel: str, *, sandbox: str = "cortex") -> str:
    return to_share_uri(sandbox, rel.lstrip("/"))


def _resolved_target(
    rel_path: str, *, sandbox: str = "cortex"
) -> tuple[str, Path] | None:
    rel = normalize_share_rel(rel_path, sandbox=sandbox)
    if rel is None:
        return None
    root = _FILES_ROOT if sandbox == "cortex" else _FILES_ROOT
    target = (root / rel).resolve()
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
    if target.is_dir():
        return {"error": "rel_path resolves to a directory"}
    with path_flock(target):
        if write_if_absent and target.is_file():
            existing = target.read_text(encoding="utf-8")
            return {
                "uri": pinned_deliverable_uri(rel),
                "path": rel,
                "sha256": content_sha256(existing),
                "body_chars": len(existing),
                "skipped": True,
            }
        durable_write_text(
            target,
            content,
            already_locked=True,
            retain_store_root=_FILES_ROOT,
        )
    return {
        "uri": pinned_deliverable_uri(rel),
        "path": rel,
        "sha256": content_sha256(content),
        "body_chars": len(content),
        "skipped": False,
    }
