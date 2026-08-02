"""Source-sync deploy stamp — detect image-only vs synced MCP code."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from deploy_identity.code_version import resolve_code_version

_STAMP_PATH = Path("/app/.source_sync_stamp")


def read_source_sync_stamp() -> dict[str, str | None]:
    """Return stamp fields for /health; absent stamp ⇒ image-baked /app only."""
    if not _STAMP_PATH.is_file():
        return {"source_synced_at": None, "deploy_mode": "image_only"}
    lines = _STAMP_PATH.read_text(encoding="utf-8").strip().splitlines()
    line = lines[0].strip() if lines else ""
    return {"source_synced_at": line or None, "deploy_mode": "source_synced"}


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
