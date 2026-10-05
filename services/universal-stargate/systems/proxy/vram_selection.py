"""Shared VRAM gateway selection for /health and gateways/status/full."""

from __future__ import annotations

from typing import Any


def select_best_vram_from_status(
    status: dict[str, dict[str, Any]],
) -> tuple[str | None, int]:
    """
    Pick the gateway with the largest total_vram_mb among enabled, connected rows.

    Same rule as ``GET /api/v1/gateways/status/full`` summary fields
    ``best_vram_gateway`` / ``best_vram_mb``.
    """
    best_vram_gateway: str | None = None
    best_vram = 0
    for url, gw in status.items():
        if gw["enabled"] and gw["is_connected"] and gw["total_vram_mb"] > best_vram:
            best_vram = gw["total_vram_mb"]
            best_vram_gateway = url
    return best_vram_gateway, best_vram


def vram_mb_for_health_reachable_gateways(
    live_gateways: list[Any],
) -> tuple[int, int]:
    """
    VRAM pair for router-only master /health from reachable federated gateways.

    Uses ``select_best_vram_from_status`` when any reachable gateway reports
    VRAM; otherwise falls back to the first reachable gateway (registration order).
    """
    if not live_gateways:
        raise ValueError("live_gateways must be non-empty")

    status = {
        gw.gateway_id: {
            "enabled": True,
            "is_connected": True,
            "total_vram_mb": gw.vram_total_mb,
        }
        for gw in live_gateways
    }
    best_id, best_vram = select_best_vram_from_status(status)
    if best_vram > 0 and best_id is not None:
        chosen = next(gw for gw in live_gateways if gw.gateway_id == best_id)
        return chosen.vram_free_mb, chosen.vram_total_mb

    fallback = live_gateways[0]
    return fallback.vram_free_mb, fallback.vram_total_mb
