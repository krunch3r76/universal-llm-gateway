"""Low-level read-only probes used by the fleet liveness manage projection."""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from implement_admission.service_lib_ownership import (
    serving_services_for_lib_path,
    slug_for_service_path,
)

SERVICE_SLUGS = (
    "gateway",
    "stargate",
    "rag",
    "cloud_proxy",
    "mcp",
    "event_service",
    "cortex_api",
    "agent_bus",
    "email_bridge",
    "git_integration_worker",
    "cdp_ask",
)
CONTAINER_SERVICES = {"mcp": ("mcp-server", "/app")}
CONTAINER_MARKERS = {
    "mcp": "mcp-server",
    "gateway": os.environ.get("GATEWAY_CONTAINER", "edge-localhost"),
}
BIND_MOUNT_SERVICES = {"gateway"}
HOST_CLOCK_GRANULARITY_S = 1.0 / os.sysconf("SC_CLK_TCK")


def utc(ts: float) -> str:
    """Render a Unix timestamp as a UTC ISO string."""
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


def run(
    args: list[str], *, cwd: Path | None = None, timeout: float = 5.0
) -> subprocess.CompletedProcess[str]:
    """Run a bounded read-only probe and return its raw process result."""
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def git(root: Path, *args: str) -> str | None:
    """Return trimmed git output, or None when the read-only probe fails."""
    try:
        result = run(["git", "-C", str(root), *args], timeout=5.0)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def classify_path(path: str) -> str:
    """Classify a dirty path without claiming that classification means import."""
    lower = path.lower()
    name = Path(path).name.lower()
    if "/test" in lower or name.startswith("test_") or name.endswith("_test.py"):
        return "test"
    if lower.startswith("docs/") or lower.endswith((".md", ".mdc", ".txt")):
        return "doc"
    if lower.startswith("config/") or lower.endswith((".yaml", ".yml", ".toml")):
        return "config"
    if lower.endswith((".py", ".js", ".ts", ".tsx")):
        return "runtime"
    return "unmapped"


def services_for_path(path: str) -> tuple[str, ...]:
    """Map a path to serving services while preserving shared-library fan-out."""
    direct = slug_for_service_path(path)
    if direct:
        return (direct,)
    services = serving_services_for_lib_path(path)
    if services:
        return services
    if path.startswith("libs/") or path.startswith("config/"):
        return ("mcp",) if path.startswith("config/") else ()
    return ()


def clock_observation() -> dict[str, Any]:
    """Capture host clock boot, granularity, and sampling-step evidence."""
    sample_open_ns = time.time_ns()
    boot_epoch: float | None = None
    error: str | None = None
    try:
        for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
            if line.startswith("btime "):
                boot_epoch = float(line.split()[1])
                break
        if boot_epoch is None:
            error = "clock_boot_time_unavailable"
    except (OSError, ValueError, IndexError):
        error = "clock_boot_time_probe_error"
    sample_close_ns = time.time_ns()
    return {
        "domain": "host_wall_clock",
        "boot_utc": utc(boot_epoch) if boot_epoch is not None else None,
        "granularity_s": HOST_CLOCK_GRANULARITY_S,
        "sample_open_ns": sample_open_ns,
        "sample_close_ns": sample_close_ns,
        "step_ns": sample_close_ns - sample_open_ns,
        "error": error,
    }


def tree_probe(root: Path) -> dict[str, Any]:
    """Read branch, HEAD, porcelain, and filesystem mtimes for the checkout."""
    branch = git(root, "symbolic-ref", "--short", "HEAD")
    head = git(root, "rev-parse", "HEAD")
    try:
        result = run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "-z"], timeout=5.0
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "raw": "",
            "paths": {},
            "errors": [f"git_status_probe:{type(exc).__name__}"],
            "branch": branch,
            "head_sha": head,
            "clock": clock_observation(),
        }
    if result.returncode != 0:
        return {
            "raw": result.stdout,
            "paths": {},
            "errors": [f"git_status_probe:exit_{result.returncode}"],
            "branch": branch,
            "head_sha": head,
            "clock": clock_observation(),
        }

    raw = result.stdout
    paths: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for item in raw.split("\0"):
        if not item:
            continue
        status = item[:2]
        path = item[3:] if len(item) >= 4 else ""
        if not path:
            continue
        try:
            stat = (root / path).stat()
            mtime_ns = stat.st_mtime_ns
            mtime_utc = utc(stat.st_mtime)
        except OSError as exc:
            mtime_ns = None
            mtime_utc = None
            probe_error = (
                "deleted_path" if "D" in status else f"stat_probe:{type(exc).__name__}"
            )
            errors.append(f"{path}:{probe_error}")
        else:
            probe_error = None
        services = services_for_path(path)
        paths[path] = {
            "path": path,
            "status": status,
            "mtime_ns": mtime_ns,
            "mtime_utc": mtime_utc,
            "classification": classify_path(path),
            "serving_services": list(services),
            "on_load_surface": bool(services),
            "import_reachable": "unknown",
            "probe_error": probe_error,
        }
    clock = clock_observation()
    if clock["error"]:
        errors.append(str(clock["error"]))
    return {
        "raw": raw,
        "paths": paths,
        "errors": errors,
        "branch": branch,
        "head_sha": head,
        "clock": clock,
    }


def process_start(pid: int | None) -> dict[str, Any]:
    """Read a host process start marker with its clock granularity."""
    if not pid:
        return {
            "kind": "host_proc_start",
            "value_utc": None,
            "granularity_s": HOST_CLOCK_GRANULARITY_S,
            "clock_domain": "host_proc",
            "error": "pid_unavailable",
        }
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        uptime_text = Path("/proc/uptime").read_text(encoding="utf-8")
        fields = stat_text[stat_text.rindex(")") + 1 :].split()
        start_ticks = float(fields[19])
        uptime_s = float(uptime_text.split()[0])
        started_at = time.time() - uptime_s + start_ticks / os.sysconf(
            "SC_CLK_TCK"
        )
    except (OSError, ValueError, IndexError):
        return {
            "kind": "host_proc_start",
            "value_utc": None,
            "granularity_s": HOST_CLOCK_GRANULARITY_S,
            "clock_domain": "host_proc",
            "error": "proc_start_unavailable",
        }
    return {
        "kind": "host_proc_start",
        "value_utc": utc(started_at),
        "granularity_s": HOST_CLOCK_GRANULARITY_S,
        "clock_domain": "host_proc",
        "error": None,
    }


def container_start(container: str) -> dict[str, Any]:
    """Read a container StartedAt marker from Docker."""
    try:
        result = run(
            ["docker", "inspect", container, "--format", "{{.State.StartedAt}}"],
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "kind": "container_started_at",
            "value_utc": None,
            "granularity_s": 0.001,
            "clock_domain": "docker_host",
            "error": f"docker_inspect:{type(exc).__name__}",
        }
    value = result.stdout.strip() if result.returncode == 0 else ""
    return {
        "kind": "container_started_at",
        "value_utc": value or None,
        "granularity_s": 0.001,
        "clock_domain": "docker_host",
        "error": None if value else f"docker_inspect:exit_{result.returncode}",
    }


def mcp_reported_version(container: str) -> dict[str, Any]:
    """Read the MCP checkout label and its source-sync qualification.

    The routine MCP restart copies the working tree into ``/app``.  The stamp's
    SHA remains useful for Git ancestry checks, but it is not an exact loaded
    byte identity when the checkout was dirty.
    """
    try:
        result = run(["docker", "exec", container, "cat", "/app/.source_sync_stamp"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "field": "code_version",
            "value": None,
            "source": "docker:/app/.source_sync_stamp",
            "denotes": "checkout_head_label_at_working_tree_sync",
            "error": f"stamp_read:{type(exc).__name__}",
        }
    lines = result.stdout.splitlines()
    value = lines[1].strip() if result.returncode == 0 and len(lines) > 1 else None
    metadata: dict[str, str] = {}
    for raw in lines[2:]:
        key, separator, item = raw.partition("=")
        if separator and key.strip() and item.strip():
            metadata[key.strip()] = item.strip()
    return {
        "field": "code_version",
        "value": value,
        "source": "docker:/app/.source_sync_stamp",
        "denotes": metadata.get(
            "code_version_semantics", "legacy_source_sync_commit_label"
        ),
        "source_sync_basis": metadata.get("source_basis", "unspecified_legacy"),
        "source_sync_worktree_state": metadata.get(
            "working_tree_state", "unknown"
        ),
        "error": None if value else "stamp_value_unavailable",
    }


def git_blob_sha(root: Path, commit: str | None, path: str) -> str | None:
    """Hash the committed blob bytes for one path, or None for untracked paths."""
    if not commit:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "show", f"{commit}:{path}"],
            capture_output=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return hashlib.sha256(result.stdout).hexdigest()


def container_sha(container: str, path: str) -> str | None:
    """Hash one file at its running container load surface."""
    try:
        result = subprocess.run(
            ["docker", "exec", container, "sha256sum", f"/app/{path}"],
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.split(maxsplit=1)
    return value[0] if value else None


__all__ = [
    "BIND_MOUNT_SERVICES",
    "CONTAINER_MARKERS",
    "CONTAINER_SERVICES",
    "HOST_CLOCK_GRANULARITY_S",
    "SERVICE_SLUGS",
    "container_sha",
    "container_start",
    "clock_observation",
    "git_blob_sha",
    "mcp_reported_version",
    "process_start",
    "tree_probe",
    "utc",
]
