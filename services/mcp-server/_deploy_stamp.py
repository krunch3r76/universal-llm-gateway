"""Source-sync deploy stamp — detect image-only vs synced MCP code."""

from __future__ import annotations

from pathlib import Path

_STAMP_PATH = Path("/app/.source_sync_stamp")


def read_source_sync_stamp() -> dict[str, str | None]:
    """Return stamp fields for /health; absent stamp ⇒ image-baked /app only."""
    if not _STAMP_PATH.is_file():
        return {"source_synced_at": None, "deploy_mode": "image_only"}
    line = _STAMP_PATH.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    return {"source_synced_at": line or None, "deploy_mode": "source_synced"}


def health_json() -> dict[str, str | None]:
    """Base /health payload including deploy stamp."""
    return {"status": "ok", **read_source_sync_stamp()}
