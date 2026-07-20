"""Snapshot builders for model resource history and statistics API responses.

Fetches current and peak usage from the worker controller and normalizes
bytes, percentages, and timestamps into dict payloads for route handlers.
"""

from typing import Any

from fastapi import HTTPException

_BYTES_PER_MB = 1024 * 1024


async def fetch_model_usage(
    worker_controller, model_id: str, logger: Any
) -> tuple[Any, Any]:
    """Return current and peak resource usage, mapping worker errors to HTTP 500."""
    try:
        current_usage = await worker_controller.get_resource_usage(model_id)
        peak_usage = worker_controller.get_peak_usage(model_id)
        return current_usage, peak_usage
    except Exception as exc:
        logger.error(f"Failed to get resource data for {model_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _bytes_to_mb(value: int | float | None) -> int:
    return int((value or 0) / _BYTES_PER_MB)


def build_history_snapshots(current_usage, peak_usage) -> list[dict[str, Any]]:
    """Build typed current/peak snapshots for the resource-history endpoint."""
    snapshots: list[dict[str, Any]] = []
    if current_usage:
        timestamp = current_usage.timestamp
        if hasattr(timestamp, "isoformat"):
            timestamp = timestamp.isoformat()
        else:
            timestamp = str(timestamp)
        snapshots.append(
            {
                "timestamp": timestamp,
                "ram_used_mb": _bytes_to_mb(current_usage.ram_used),
                "vram_used_mb": _bytes_to_mb(current_usage.vram_used),
                "ram_percent": current_usage.ram_percent,
                "vram_percent": current_usage.vram_percent or 0,
                "cpu_percent": current_usage.cpu_percent or 0,
                "type": "current",
            }
        )

    if peak_usage:
        snapshots.append(
            {
                "timestamp": peak_usage.get("peak_timestamp", "unknown"),
                "ram_used_mb": _bytes_to_mb(peak_usage.get("peak_ram_bytes", 0)),
                "vram_used_mb": _bytes_to_mb(peak_usage.get("peak_vram_bytes", 0)),
                "ram_percent": 0,
                "vram_percent": 0,
                "cpu_percent": 0,
                "type": "peak",
            }
        )
    return snapshots


def build_stats_snapshots(current_usage, peak_usage) -> list[dict[str, Any]]:
    """Build simplified snapshots used as input for resource statistics aggregation."""
    snapshots: list[dict[str, Any]] = []
    if current_usage:
        snapshots.append(
            {
                "ram_used_mb": _bytes_to_mb(current_usage.ram_used),
                "vram_used_mb": _bytes_to_mb(current_usage.vram_used),
                "ram_percent": current_usage.ram_percent,
                "vram_percent": current_usage.vram_percent or 0,
                "timestamp": current_usage.timestamp,
            }
        )

    if peak_usage:
        snapshots.append(
            {
                "ram_used_mb": _bytes_to_mb(peak_usage.get("peak_ram_bytes", 0)),
                "vram_used_mb": _bytes_to_mb(peak_usage.get("peak_vram_bytes", 0)),
                "ram_percent": 0,
                "vram_percent": 0,
                "timestamp": peak_usage.get("peak_timestamp", "unknown"),
            }
        )
    return snapshots
