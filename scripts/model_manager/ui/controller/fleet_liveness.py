"""Assemble a load-surface-aware liveness snapshot for manage JSON-RPC."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from charter_runner_store.propagation_validation import current_validation

from ..model.service_state import ServiceInfo, ServiceState
from .fleet_liveness_probe import (
    BIND_MOUNT_SERVICES,
    CONTAINER_MARKERS,
    CONTAINER_SERVICES,
    HOST_CLOCK_GRANULARITY_S,
    SERVICE_SLUGS,
)
from .fleet_liveness_probe import (
    container_sha as _container_sha,
)
from .fleet_liveness_probe import (
    container_start as _container_start,
)
from .fleet_liveness_probe import (
    git_blob_sha as _git_blob_sha,
)
from .fleet_liveness_probe import (
    mcp_reported_version as _mcp_reported_version,
)
from .fleet_liveness_probe import (
    process_start as _process_start,
)
from .fleet_liveness_probe import (
    tree_probe as _tree_probe,
)
from .fleet_liveness_probe import (
    utc as _utc,
)


def _path_comparison(
    root: Path,
    *,
    service: str,
    path_row: dict[str, Any],
    head_sha: str | None,
    marker: dict[str, Any],
    reported: dict[str, Any],
    tree_moved: bool,
) -> dict[str, Any]:
    """Compare one dirty path using the service's honest measurement method."""
    path = str(path_row["path"])
    result = {
        "path": path,
        "on_load_surface": service in path_row.get("serving_services", []),
        "import_reachable": "unknown",
        "comparison_method": "none",
        "running_bytes_determinable": "no",
        "matches_reported_sha": "indeterminate",
        "indeterminate_reason": None,
        "evidence": {},
    }
    if not result["on_load_surface"]:
        return result

    if service in CONTAINER_SERVICES:
        result["comparison_method"] = "content_hash_in_load_location"
        running_sha = _container_sha(CONTAINER_SERVICES[service][0], path)
        blob_sha = _git_blob_sha(
            root, str(reported.get("value") or head_sha), path
        )
        result["evidence"] = {
            "load_surface_sha256": running_sha,
            "git_blob_sha256": blob_sha,
        }
        result["running_bytes_determinable"] = "yes" if running_sha else "no"
        if running_sha and blob_sha:
            result["matches_reported_sha"] = "yes" if running_sha == blob_sha else "no"
        elif path_row.get("status", "").strip() == "??":
            result["matches_reported_sha"] = "no"
            result["indeterminate_reason"] = "untracked_no_blob"
        else:
            result["indeterminate_reason"] = "probe_error"
        return result

    if service in BIND_MOUNT_SERVICES:
        result["comparison_method"] = "bind_mount_module_import_unmeasured"
        result["indeterminate_reason"] = "bind_mount_per_module_import"
        return result

    result["comparison_method"] = "mtime_vs_load_marker"
    mtime_ns = path_row.get("mtime_ns")
    marker_value = marker.get("value_utc")
    if tree_moved:
        result["indeterminate_reason"] = "tree_moved_during_probe"
        return result
    if not mtime_ns or not marker_value:
        result["indeterminate_reason"] = "load_marker_or_mtime_missing"
        return result
    try:
        mtime_s = int(mtime_ns) / 1_000_000_000
        marker_s = datetime.fromisoformat(
            str(marker_value).replace("Z", "+00:00")
        ).timestamp()
    except (TypeError, ValueError, OverflowError):
        result["indeterminate_reason"] = "timestamp_parse_error"
        return result
    granularity = float(marker.get("granularity_s") or 1.0)
    delta = marker_s - mtime_s
    if abs(delta) < granularity:
        relation = "within_granularity"
        reason = "clock_granularity_overlap"
    elif delta > 0:
        relation = "marker_after_mtime"
        reason = "host_process_import_not_observable"
    else:
        relation = "marker_before_mtime"
        reason = "load_marker_precedes_mtime"
    result["temporal_relation"] = relation
    result["indeterminate_reason"] = reason
    return result


def _service_info(service_state: ServiceState, service: str) -> ServiceInfo:
    """Read one service status through the existing manage health checker."""
    checker = getattr(service_state, f"check_{service}")
    return checker()


def build_snapshot(
    root: Path, service_state: ServiceState, *, code_ref: str | None = None
) -> dict[str, Any]:
    """Build a fresh evidence snapshot without mutating checkout or services.

    ``status`` is copied from the manage ``ServiceInfo`` checker for that slug.
    For ``rag`` in UDS mode that checker is a fail-closed 2.0s GET /stats
    (HTTP 200) plus PID+socket — not process-liveness. ``detail``, ``pid``,
    and ``health_url`` are copied so a reader can distinguish the checker's
    already-known fail classes (socket not ready vs probe failed vs exception).
    """
    started = time.time()
    before = _tree_probe(root)
    services: list[dict[str, Any]] = []
    for service in SERVICE_SLUGS:
        try:
            info = _service_info(service_state, service)
            status = info.status.value
            errors: list[str] = []
        except Exception as exc:
            info = None
            status = "unknown"
            errors = [f"service_probe:{type(exc).__name__}"]

        if service in CONTAINER_SERVICES:
            marker = _container_start(CONTAINER_SERVICES[service][0])
            reported = _mcp_reported_version(CONTAINER_SERVICES[service][0])
            surface = "container_copy"
        elif service in BIND_MOUNT_SERVICES:
            marker = _container_start(CONTAINER_MARKERS[service])
            reported = {
                "field": None,
                "value": None,
                "source": "unavailable",
                "denotes": "unavailable",
                "error": "reported_version_unavailable",
            }
            surface = "bind_mount"
        else:
            marker = _process_start(info.pid if info else None)
            reported = {
                "field": None,
                "value": None,
                "source": "unavailable",
                "denotes": "unavailable",
                "error": "reported_version_unavailable",
            }
            surface = "host_process"

        services.append(
            {
                "service": service,
                "status": status,
                "detail": info.detail if info is not None else "",
                "pid": info.pid if info is not None else None,
                "health_url": info.health_url if info is not None else None,
                "load_surface_kind": surface,
                "load_marker": marker,
                "reported_version": reported,
                "probe_errors": errors,
                "paths": [],
            }
        )

    after = _tree_probe(root)
    tree_moved = before["raw"] != after["raw"] or before["paths"] != after["paths"]
    by_service = {row["service"]: row for row in services}
    for path_row in before["paths"].values():
        for service in path_row.get("serving_services", []):
            row = by_service.get(service)
            if row is not None:
                row["paths"].append(
                    _path_comparison(
                        root,
                        service=service,
                        path_row=path_row,
                        head_sha=before.get("head_sha"),
                        marker=row["load_marker"],
                        reported=row["reported_version"],
                        tree_moved=tree_moved,
                    )
                )

    for row in services:
        results = row["paths"]
        if not results:
            row["live_sha_claim"] = {
                "sound": None,
                "reason": "no_relevant_dirty_paths_or_version",
            }
        elif any(item["matches_reported_sha"] == "no" for item in results):
            row["live_sha_claim"] = {"sound": False, "reason": "path_mismatch"}
        elif all(item["matches_reported_sha"] == "yes" for item in results):
            row["live_sha_claim"] = {"sound": True, "reason": "all_paths_hash_match"}
        else:
            row["live_sha_claim"] = {"sound": None, "reason": "evidence_indeterminate"}
        row["running_bytes_determinable"] = (
            "yes"
            if results and all(item["running_bytes_determinable"] == "yes" for item in results)
            else "no"
        )
        row["matches_reported_sha"] = (
            "no"
            if any(item["matches_reported_sha"] == "no" for item in results)
            else (
                "yes"
                if results and all(item["matches_reported_sha"] == "yes" for item in results)
                else "indeterminate"
            )
        )
        if code_ref:
            row["code_ref_validation"] = current_validation(
                row["service"], code_ref
            )

    finished = time.time()
    return {
        "schema_version": 1,
        "observed_at_utc": _utc(finished),
        "observation_window": {
            "started_utc": _utc(started),
            "ended_utc": _utc(finished),
        },
        "tree_moved_during_probe": tree_moved,
        "depth": "content_hash_for_containers_timestamps_for_hosts",
        "code_ref": code_ref,
        "clock": {
            "domain": before.get("clock", {}).get("domain", "host_wall_clock"),
            "boot_utc": before.get("clock", {}).get("boot_utc"),
            "granularity_s": before.get("clock", {}).get(
                "granularity_s", HOST_CLOCK_GRANULARITY_S
            ),
            "sample_open_ns": before.get("clock", {}).get("sample_open_ns"),
            "sample_close_ns": after.get("clock", {}).get("sample_close_ns"),
            "step_ns": (
                after.get("clock", {}).get("sample_close_ns", 0)
                - before.get("clock", {}).get("sample_open_ns", 0)
            ),
            "error": before.get("clock", {}).get("error")
            or after.get("clock", {}).get("error"),
        },
        "checkout": {
            "branch": before.get("branch"),
            "head_sha": before.get("head_sha"),
            "porcelain_raw_open": before.get("raw", ""),
            "porcelain_raw_close": after.get("raw", ""),
            "paths": list(before.get("paths", {}).values()),
        },
        "services": services,
        "probe_errors": before.get("errors", []) + after.get("errors", []),
    }


__all__ = ["SERVICE_SLUGS", "build_snapshot"]
