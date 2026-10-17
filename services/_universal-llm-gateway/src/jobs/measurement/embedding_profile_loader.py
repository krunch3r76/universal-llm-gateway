"""Resolve per-profile loader config for embedding measurement."""

from __future__ import annotations

from typing import Any


def merged_profile_loader(
    entry: dict[str, Any],
    *,
    ctx: int,
    mode: str,
) -> dict[str, Any]:
    """Merge top-level loader with devices.<gpu|cpu>.profiles.<ctx>.loader."""
    base_loader = dict(entry.get("loader") or {})
    device_key = "cpu" if mode == "cpu" else "gpu"
    devices = entry.get("devices") or {}
    device_block = devices.get(device_key) or {}
    profiles = device_block.get("profiles") or {}
    profile = profiles.get(str(ctx)) or {}
    profile_loader = profile.get("loader") or {}
    return {**base_loader, **profile_loader}
