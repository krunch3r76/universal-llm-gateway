"""Cursor SDK catalog poller configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True, kw_only=True)
class CursorSdkCatalogConfig:
    """Configuration for polling git_integration_worker's cursor catalog."""

    worker_url: str


def parse_cursor_catalog_config() -> CursorSdkCatalogConfig:
    """Build worker catalog URL from git_integration_worker env defaults."""
    host = os.environ.get("GIT_INTEGRATION_WORKER_HOST", "127.0.0.1")
    port = os.environ.get("GIT_INTEGRATION_WORKER_PORT", "8091")
    return CursorSdkCatalogConfig(worker_url=f"http://{host}:{port}")
