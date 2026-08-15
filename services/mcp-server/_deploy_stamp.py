"""Source-sync deploy stamp — qualify the loaded-source identity."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from deploy_identity.code_version import resolve_code_version

_STAMP_PATH = Path("/app/.source_sync_stamp")


def read_source_sync_stamp() -> dict[str, str | None]:
    """Return source-sync fields for /health.

    ``code_version`` is a Git ancestry label used by propagation checks.  The
    routine MCP path copies the working tree, so that label is not an exact
    digest of the bytes loaded into ``/app`` when the checkout is dirty.  The
    extra stamp fields make that qualification explicit without breaking the
    two-line stamp reader in ``deploy_identity.code_version``.
    """
    if not _STAMP_PATH.is_file():
        return {
            "source_synced_at": None,
            "deploy_mode": "image_only",
            "source_sync_basis": None,
            "code_version_semantics": None,
            "source_sync_worktree_state": None,
        }
    lines = _STAMP_PATH.read_text(encoding="utf-8").strip().splitlines()
    line = lines[0].strip() if lines else ""
    metadata: dict[str, str] = {}
    for raw in lines[2:]:
        key, separator, value = raw.partition("=")
        if separator and key.strip() and value.strip():
            metadata[key.strip()] = value.strip()
    return {
        "source_synced_at": line or None,
        "deploy_mode": "source_synced",
        "source_sync_basis": metadata.get("source_basis", "unspecified_legacy"),
        "code_version_semantics": metadata.get(
            "code_version_semantics", "legacy_source_sync_commit_label"
        ),
        "source_sync_worktree_state": metadata.get(
            "working_tree_state", "unknown"
        ),
    }


def health_json() -> dict[str, Any]:
    """Base /health payload including deploy stamp, code version, and pid.

    ``pid`` is ``os.getpid()`` of the uvicorn process serving the request
    (AuthMiddleware short-circuits ``/health`` in-process). Same field GIW
    liveness uses for ``strong_process_identity``.
    """
    return {
        "status": "ok",
        "code_version": resolve_code_version(),
        "pid": os.getpid(),
        **read_source_sync_stamp(),
    }
