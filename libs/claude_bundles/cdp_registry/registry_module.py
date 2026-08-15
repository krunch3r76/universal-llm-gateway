"""Provide a call-time accessor for monkeypatch-safe public registry attribute lookups during tests and lifecycle operations."""

from __future__ import annotations


def registry_package():
    """Return the public package whose mutable registry attributes tests replace at runtime."""
    from claude_bundles import cdp_registry

    return cdp_registry
