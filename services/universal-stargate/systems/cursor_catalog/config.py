"""Cursor SDK catalog poller configuration."""

from __future__ import annotations

from dataclasses import dataclass

from giw_endpoint import git_integration_worker_base_url


@dataclass(slots=True, kw_only=True)
class CursorSdkCatalogConfig:
    """Configuration for polling git_integration_worker's cursor catalog."""

    worker_url: str


def parse_cursor_catalog_config() -> CursorSdkCatalogConfig:
    """Build worker catalog URL from git_integration_worker env defaults."""
    return CursorSdkCatalogConfig(worker_url=git_integration_worker_base_url())
