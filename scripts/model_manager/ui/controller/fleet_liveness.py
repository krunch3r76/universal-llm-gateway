"""Assemble a load-surface-aware fleet liveness snapshot for manage JSON-RPC.

``build_snapshot`` is called by manage tools and MCP; it joins checkout porcelain,
per-service health checkers, and import-sealed code labels without mutating fleet state.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from charter_runner_store.propagation_validation import current_validation
from deploy_identity.code_version import (
    is_valid_sha40,
    process_age_s,
    resolve_code_version,
)

from ..model.service_state import ServiceInfo, ServiceState, ServiceStatus
from .fleet_liveness_filter import normalize_service_filter
from .fleet_liveness_probe import (
    BIND_MOUNT_SERVICES,
    CONTAINER_MARKERS,
    CONTAINER_SERVICES,
    HOST_CLOCK_GRANULARITY_S,
    SERVICE_SLUGS,
)
from .fleet_liveness_probe import (
    code_ref_changed_paths as _code_ref_changed_paths,
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
    manage_loaded_code_evidence as _manage_loaded_code_evidence,
)
from .fleet_liveness_probe import (
    mcp_reported_version as _mcp_reported_version,
)
from .fleet_liveness_probe import (
    paths_index_behind_head as _paths_index_behind_head,
)
from .fleet_liveness_probe import (
    process_start as _process_start,
)
from .fleet_liveness_probe import (
    services_for_path as _services_for_path,
)
from .fleet_liveness_probe import (
    tree_probe as _tree_probe,
)
from .fleet_liveness_probe import (
    utc as _utc,
)
from .fleet_liveness_remote import (
    _health_url_is_remote,
    _remote_pid_unmeasured,
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
        blob_sha = _git_blob_sha(root, str(reported.get("value") or head_sha), path)
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


def _manage_self_info() -> ServiceInfo:
    """Identity of this process, which is the manage row.

    ``ServiceState`` has no ``check_manage``. ``getattr(state, "check_manage")``
    raises AttributeError, and the row then reports status unknown with a null
    pid. The process building the snapshot is manage, so the pid and age are
    ``os.getpid`` and ``process_age_s``, the same pair ``_whoami`` reports.
    """
    pid = os.getpid()
    age = process_age_s()
    if age is None:
        detail = f"PID {pid}, process age unavailable"
    else:
        detail = f"PID {pid}, age {age:.0f}s"
    return ServiceInfo(
        name="manage",
        status=ServiceStatus.RUNNING,
        pid=pid,
        detail=detail,
    )


def _untracked_off_load_only(paths: dict[str, Any]) -> bool:
    """True when every porcelain row is untracked and off the load surface.

    An empty porcelain is not this case. A tracked edit, or any untracked file
    a service loads, keeps the source-sync stamp's dirty bit.
    """
    rows = list(paths.values())
    if not rows:
        return False
    for row in rows:
        if str(row.get("status", "")).strip() != "??":
            return False
        if row.get("on_load_surface"):
            return False
    return True


def _reported_sha(row: dict[str, Any]) -> str | None:
    value = (row.get("reported_version") or {}).get("value")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _mark_not_loaded_by(row: dict[str, Any], changed: list[str] | None) -> None:
    """Replace an ancestry yes when this service imports none of the commit.

    A missing or empty path list is left alone: git could not name the change,
    so the ancestry answer stays. Paths the ownership map does not serve do not
    count as loaded by this service.
    """
    if not changed:
        return
    validation = row.get("code_ref_validation")
    if not isinstance(validation, dict):
        return
    liveness = validation.get("liveness")
    if not isinstance(liveness, dict) or liveness.get("answer") != "yes":
        return
    service = str(row.get("service") or "")
    if any(service in _services_for_path(path) for path in changed):
        return
    liveness["answer"] = "not_loaded_by"
    liveness["reason"] = (
        "service imports none of the paths changed by code_ref; "
        "an ancestry yes is not load evidence"
    )


def _apply_answers_equal(row: dict[str, Any]) -> None:
    """Set answers_equal only when the import seal and observed version compare.

    Temporal evidence has no seal, and a seal without an observed code version
    has nothing to compare. Those rows omit the field instead of storing null.
    """
    evidence = row.get("loaded_code_evidence")
    if not isinstance(evidence, dict):
        return
    seal = evidence.get("sha")
    observed = (row.get("code_ref_validation") or {}).get("liveness", {})
    observed_sha = (
        observed.get("observed_code_version") if isinstance(observed, dict) else None
    )
    if (
        evidence.get("kind") == "imported_code"
        and isinstance(seal, str)
        and isinstance(observed_sha, str)
        and is_valid_sha40(seal)
        and is_valid_sha40(observed_sha)
    ):
        evidence["answers_equal"] = seal.strip().lower() == observed_sha.strip().lower()
        return
    evidence.pop("answers_equal", None)


def _fill_pid_from_observation(row: dict[str, Any]) -> None:
    """Copy a reachable liveness pid when the service checker left the row null.

    A checker pid is kept. An unreachable probe's pid is not a row pid: the
    probe did not confirm the process it named.
    """
    if row.get("pid") is not None:
        return
    liveness = (row.get("code_ref_validation") or {}).get("liveness") or {}
    observation = liveness.get("observation") if isinstance(liveness, dict) else None
    if not isinstance(observation, dict):
        return
    if observation.get("probe_reachable") is False:
        return
    pid = observation.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return
    row["pid"] = pid


_STAMP_SOURCE = "docker:/app/.source_sync_stamp"


def _version_evidence(
    reported: dict[str, Any], validation: dict[str, Any]
) -> dict[str, Any] | None:
    """Label the live sync stamp against the process-start code_version seal.

    ``reported_version`` cats ``/app/.source_sync_stamp`` line 2 on each probe.
    ``code_ref_validation`` reads ``/health`` ``code_version``, sealed once at
    process import. Source sync writes that stamp before the serving process
    recycles, so the SHAs differ until the new process seals the new stamp.
    The process seal answers whether a code_ref is live. The stamp answers
    what the last source sync wrote.
    """
    if reported.get("source") != _STAMP_SOURCE:
        return None
    stamp = reported.get("value")
    sealed = (validation.get("liveness") or {}).get("observed_code_version")
    if not isinstance(stamp, str) or not stamp.strip():
        return None
    if not isinstance(sealed, str) or not sealed.strip():
        return None
    agree = stamp == sealed
    evidence: dict[str, Any] = {
        "stamp_sha": stamp,
        "stamp_source": _STAMP_SOURCE,
        "stamp_authority": "last_source_sync_write",
        "process_seal_sha": sealed,
        "process_seal_authority": "process_start_code_version",
        "authoritative_for_liveness": "process_start_code_version",
        "agree": agree,
    }
    if not agree:
        evidence["disagreement"] = (
            "source_sync_stamp line 2 is the last sync write; "
            "observed_code_version is the process-start seal. "
            "Liveness uses the process seal."
        )
    return evidence


def _code_ref_validation_with_provenance(
    service: str,
    code_ref: str,
    *,
    authoritative_status: str,
    health_url: str | None,
    activation_validation_id: str | None = None,
) -> dict[str, Any]:
    """Attach code-ref liveness as a derived plane under manage health authority."""
    validation = current_validation(
        service, code_ref, activation_validation_id=activation_validation_id
    )
    liveness = validation.get("liveness") or {}
    observation = liveness.get("observation") or {}
    provenance: dict[str, Any] = {
        "authority": "services[].status",
        "derived_plane": "code_ref_validation",
        "basis": "derived",
        "manage_health_checker": {
            "status": authoritative_status,
            "health_url": health_url,
            "probe": "GET /health via ServiceState.check_*",
        },
        "code_ref_probe": {
            "endpoint": "GET /api/v1/git/admin/liveness",
            "observation": observation,
        },
        "note": (
            "code_ref liveness answers whether a code_ref is live on the service; "
            "it does not override services[].status from the manage health checker."
        ),
    }
    manage_healthy = authoritative_status == "running"
    obs_live = observation.get("live") is True
    if obs_live and not manage_healthy:
        provenance["contradiction"] = {
            "manage_status": authoritative_status,
            "code_ref_live": obs_live,
            "resolution": "trust services[].status; cursor-auto liveness is orthogonal",
        }
    validation["provenance"] = provenance
    return validation


def _compact_code_ref_validation(full: dict[str, Any]) -> dict[str, Any]:
    """Shrink code_ref_validation to liveness answer/relation plus failure evidence."""
    liveness = full.get("liveness") or {}
    observation = liveness.get("observation") or {}
    answer = liveness.get("answer")
    relation = liveness.get("relation")
    reason = liveness.get("reason")
    probe_error = observation.get("probe_error")
    probe_unreachable = observation.get("probe_reachable") is False
    failed_live = probe_unreachable or probe_error is not None

    compact: dict[str, Any] = {}
    if probe_error:
        compact["probe_errors"] = [str(probe_error)]
    liveness_out: dict[str, Any] = {}
    if reason:
        liveness_out["reason"] = reason
    if answer is not None and not (failed_live and answer == "yes"):
        liveness_out["answer"] = answer
    if relation is not None:
        liveness_out["relation"] = relation
    if liveness_out:
        compact["liveness"] = liveness_out
    return compact


def _compact_service_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project one service row for ``projection=compact`` consumers."""
    compact: dict[str, Any] = {
        "service": row["service"],
        "pid": row.get("pid"),
        "reported_version": row.get("reported_version"),
    }
    if "status" in row:
        compact["status"] = row["status"]
    if row.get("probe_errors"):
        compact["probe_errors"] = list(row["probe_errors"])
    if "code_ref_validation" in row:
        compact["code_ref_validation"] = _compact_code_ref_validation(
            row["code_ref_validation"]
        )
    if "version_evidence" in row:
        compact["version_evidence"] = row["version_evidence"]
    if "loaded_code_evidence" in row:
        compact["loaded_code_evidence"] = row["loaded_code_evidence"]
    marker = row.get("load_marker")
    if isinstance(marker, dict):
        compact["load_marker"] = dict(marker)
    return compact


def _compact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Drop porcelain and per-service heavy fields; keep manage health signals."""
    checkout = dict(snapshot.get("checkout") or {})
    checkout.pop("porcelain_raw_open", None)
    checkout.pop("porcelain_raw_close", None)
    return {
        **snapshot,
        "checkout": checkout,
        "services": [_compact_service_row(row) for row in snapshot.get("services", [])],
    }


def build_snapshot(
    root: Path,
    service_state: ServiceState,
    *,
    code_ref: str | None = None,
    activation_validation_id: str | None = None,
    service: str | None = None,
    services: list[str] | None = None,
    projection: str | None = None,
) -> dict[str, Any]:
    """Build a fresh evidence snapshot without mutating checkout or services.

    ``status`` is copied from the manage ``ServiceInfo`` checker for that slug.
    For ``rag`` in UDS mode that checker is a fail-closed 2.0s GET /stats
    (HTTP 200) plus PID+socket — not process-liveness. ``detail``, ``pid``,
    ``health_url``, and ``fail_class`` (when the checker sets it) are copied so
    a reader can distinguish the checker's already-known fail classes (socket
    not ready vs probe failed vs exception) without collapsing instrument
    misses into stopped. A host-process service whose health URL is another
    machine does not have that pid read from this host's ``/proc``; the load
    marker keeps ``value_utc`` null and states that in ``error``.

    ``service`` / ``services`` select a subset of rows. Omitted services are
    absent from the payload — never implied healthy. Checkout porcelain stays
    full-fleet. ``service_filter`` echoes the selected slugs (``null`` when
    unfiltered).

    ``manage`` has no ``check_manage``. Its row uses this process's pid and
    ``process_age_s``. An ancestry yes becomes ``not_loaded_by`` when the
    service imports none of the code_ref's first-parent paths. Untracked
    off-load-surface porcelain does not keep a source-sync stamp dirty, and
    it does not leave ``matches_reported_sha`` indeterminate when a reported
    sha is present.
    """
    selected = normalize_service_filter(service=service, services=services)
    started = time.time()
    before = _tree_probe(root)
    services_out: list[dict[str, Any]] = []
    for service_slug in selected if selected is not None else SERVICE_SLUGS:
        if service_slug == "manage":
            info = _manage_self_info()
            status = info.status.value
            errors = []
        else:
            try:
                info = _service_info(service_state, service_slug)
                status = info.status.value
                errors = []
            except Exception as exc:
                info = None
                status = "unknown"
                errors = [f"service_probe:{type(exc).__name__}"]

        if service_slug in CONTAINER_SERVICES:
            marker = _container_start(CONTAINER_SERVICES[service_slug][0])
            reported = _mcp_reported_version(CONTAINER_SERVICES[service_slug][0])
            surface = "container_copy"
        elif service_slug in BIND_MOUNT_SERVICES:
            marker = _container_start(CONTAINER_MARKERS[service_slug])
            reported = {
                "field": None,
                "value": None,
                "source": "unavailable",
                "denotes": "unavailable",
                "error": "reported_version_unavailable",
            }
            surface = "bind_mount"
        else:
            if info is not None and _health_url_is_remote(info.health_url):
                marker = _remote_pid_unmeasured(info)
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

        row: dict[str, Any] = {
            "service": service_slug,
            "status": status,
            "detail": info.detail if info is not None else "",
            "pid": info.pid if info is not None else None,
            "health_url": info.health_url if info is not None else None,
            "fail_class": info.fail_class if info is not None else None,
            "load_surface_kind": surface,
            "load_marker": marker,
            "reported_version": reported,
            "probe_errors": errors,
            "paths": [],
        }
        if service_slug == "manage":
            loaded = _manage_loaded_code_evidence(resolve_code_version())
            row["loaded_code_evidence"] = loaded
            if loaded["kind"] == "imported_code":
                row["reported_version"] = {
                    "field": "code_version",
                    "value": loaded["sha"],
                    "source": "deploy_identity.resolve_code_version",
                    "denotes": "import_sealed_git_ancestry_label",
                    "error": None,
                }
        services_out.append(row)

    after = _tree_probe(root)
    tree_moved = before["raw"] != after["raw"] or before["paths"] != after["paths"]
    off_load_untracked_only = _untracked_off_load_only(before.get("paths") or {})
    by_service = {row["service"]: row for row in services_out}
    for path_row in before["paths"].values():
        for serving in path_row.get("serving_services", []):
            row = by_service.get(serving)
            if row is not None:
                row["paths"].append(
                    _path_comparison(
                        root,
                        service=serving,
                        path_row=path_row,
                        head_sha=before.get("head_sha"),
                        marker=row["load_marker"],
                        reported=row["reported_version"],
                        tree_moved=tree_moved,
                    )
                )

    changed_paths = _code_ref_changed_paths(root, code_ref) if code_ref else None
    for row in services_out:
        reported = row.get("reported_version")
        if (
            off_load_untracked_only
            and isinstance(reported, dict)
            and reported.get("source_sync_worktree_state") == "dirty"
        ):
            reported["source_sync_worktree_state"] = "clean"
            reported["worktree_state_qualification"] = "untracked_off_load_surface_only"
        results = row["paths"]
        off_only_match = (
            off_load_untracked_only and not results and bool(_reported_sha(row))
        )
        if not results:
            if off_only_match:
                row["live_sha_claim"] = {
                    "sound": True,
                    "reason": "untracked_off_load_surface_only",
                }
            else:
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
            if results
            and all(item["running_bytes_determinable"] == "yes" for item in results)
            else "no"
        )
        if off_only_match:
            row["matches_reported_sha"] = "yes"
        else:
            row["matches_reported_sha"] = (
                "no"
                if any(item["matches_reported_sha"] == "no" for item in results)
                else (
                    "yes"
                    if results
                    and all(item["matches_reported_sha"] == "yes" for item in results)
                    else "indeterminate"
                )
            )
        if code_ref:
            row["code_ref_validation"] = _code_ref_validation_with_provenance(
                row["service"],
                code_ref,
                authoritative_status=row["status"],
                health_url=row.get("health_url"),
                activation_validation_id=activation_validation_id,
            )
            evidence = _version_evidence(
                row["reported_version"], row["code_ref_validation"]
            )
            if evidence is not None:
                row["version_evidence"] = evidence
            _mark_not_loaded_by(row, changed_paths)
        _apply_answers_equal(row)
        _fill_pid_from_observation(row)

    behind = _paths_index_behind_head(
        root, before.get("head_sha"), before.get("paths", {})
    )
    behind_set = set(behind)
    for path_row in before.get("paths", {}).values():
        if path_row.get("path") in behind_set:
            path_row["verdict"] = "index_behind_head"
    checkout_verdict = "index_behind_head" if behind else None

    finished = time.time()
    snapshot = {
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
            "verdict": checkout_verdict,
            "index_behind_head_paths": behind,
        },
        "service_filter": (None if selected is None else {"services": list(selected)}),
        "services": services_out,
        "probe_errors": before.get("errors", []) + after.get("errors", []),
    }
    if projection == "compact":
        return _compact_snapshot(snapshot)
    return snapshot


__all__ = ["SERVICE_SLUGS", "build_snapshot", "normalize_service_filter"]
