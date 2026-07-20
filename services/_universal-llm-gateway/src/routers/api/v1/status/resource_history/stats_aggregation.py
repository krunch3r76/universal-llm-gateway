"""Aggregate resource statistics from normalized snapshot records.

Computes worker/system VRAM and RAM min/max/avg summaries plus GPU utilization
metrics returned by the model resource-stats API endpoint.
"""

from typing import Any


def _metric_values(snapshots: list[dict[str, Any]], key: str) -> list[Any]:
    return [
        snapshot.get(key, 0) for snapshot in snapshots if snapshot.get(key) is not None
    ]


def _usage_summary(
    values: list[Any], max_config_key: str, snapshots: list[dict[str, Any]]
) -> dict[str, Any]:
    max_config = max(
        (
            snapshot.get(max_config_key, 0)
            for snapshot in snapshots
            if snapshot.get(max_config_key) is not None
        ),
        default=0,
    )
    return {
        "current_mb": values[-1] if values else 0,
        "max_mb": max_config,
        "avg_mb": sum(values) / len(values) if values else 0,
        "min_mb": min(values) if values else 0,
        "max_observed_mb": max(values) if values else 0,
    }


def build_resource_stats_response(
    model_id: str, snapshots: list[dict[str, Any]]
) -> dict[str, Any]:
    """Return the full resource-stats payload for a model from snapshot rows."""
    worker_vram_values = _metric_values(snapshots, "worker_vram_used_mb")
    worker_ram_values = _metric_values(snapshots, "worker_ram_used_mb")
    system_vram_values = _metric_values(snapshots, "system_vram_used_mb")
    system_ram_values = _metric_values(snapshots, "system_ram_used_mb")
    gpu_util_values = _metric_values(snapshots, "gpu_utilization")

    worker_config = snapshots[0].get("worker_config", {}) if snapshots else {}

    return {
        "model_id": model_id,
        "total_snapshots": len(snapshots),
        "time_range": {
            "first_snapshot": min(
                snapshot.get("timestamp", 0) for snapshot in snapshots
            ),
            "last_snapshot": max(
                snapshot.get("timestamp", 0) for snapshot in snapshots
            ),
        },
        "worker_resources": {
            "vram_usage": _usage_summary(
                worker_vram_values, "worker_vram_max_mb", snapshots
            ),
            "ram_usage": _usage_summary(
                worker_ram_values, "worker_ram_max_mb", snapshots
            ),
        },
        "system_resources": {
            "vram_usage": _usage_summary(
                system_vram_values, "system_vram_max_mb", snapshots
            ),
            "ram_usage": _usage_summary(
                system_ram_values, "system_ram_max_mb", snapshots
            ),
        },
        "gpu_utilization": {
            "current_percent": gpu_util_values[-1] if gpu_util_values else 0,
            "avg_percent": sum(gpu_util_values) / len(gpu_util_values)
            if gpu_util_values
            else 0,
            "max_percent": max(gpu_util_values) if gpu_util_values else 0,
        },
        "worker_config": worker_config,
    }
