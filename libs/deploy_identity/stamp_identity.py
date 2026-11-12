"""Deploy-gate identity fields read from a source-sync stamp file."""

from __future__ import annotations

import subprocess
import time
from datetime import UTC, datetime
from typing import Any

from deploy_identity.mcp_restart_gate import CONTAINER_NAME

_DEFAULT_STAMP_PATH = "/app/.source_sync_stamp"
_DOCKER_TIMEOUT_S = 4.0
_MANAGE_HEALTH_BUDGET_S = 4.5


def _parse_ts(raw: str) -> datetime | None:
    text = raw.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def deploy_identity_from_stamp_text(text: str) -> dict[str, str]:
    """Parse stamp body into deploy-gate identity fields (mode + sync timestamp only)."""
    lines = text.strip().splitlines()
    if not lines:
        return {}
    synced_at = lines[0].strip()
    if not synced_at:
        return {}
    return {
        "deploy_mode": "source_synced",
        "source_synced_at": synced_at,
    }


def _remaining_budget(deadline: float, *, cap: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return 0.0
    return min(cap, remaining)


def _read_container_started_at(
    container: str,
    *,
    deadline: float,
) -> tuple[datetime | None, str | None]:
    """Read docker State.StartedAt; return (parsed, reason) when unknown."""
    timeout = _remaining_budget(deadline, cap=_DOCKER_TIMEOUT_S)
    if timeout <= 0:
        return None, "stamp_budget_exhausted"
    try:
        proc = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                "{{.State.StartedAt}}",
                container,
            ],
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"stamp_exec:{type(exc).__name__}"
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        suffix = f":{detail}" if detail else ""
        return None, f"stamp_inspect:exit_{proc.returncode}{suffix}"
    raw = proc.stdout.decode("utf-8", errors="replace").strip()
    if not raw or raw == "0001-01-01T00:00:00Z":
        return None, "stamp_started_at_unknown"
    parsed = _parse_ts(raw)
    if parsed is None:
        return None, "stamp_started_at_unknown"
    return parsed, None


def read_mcp_container_deploy_identity(
    container: str = CONTAINER_NAME,
    *,
    stamp_path: str = _DEFAULT_STAMP_PATH,
    budget_s: float = _MANAGE_HEALTH_BUDGET_S,
) -> tuple[dict[str, Any], str | None]:
    """Read MCP container stamp via docker exec; never invent field values."""
    deadline = time.monotonic() + budget_s
    timeout = _remaining_budget(deadline, cap=_DOCKER_TIMEOUT_S)
    if timeout <= 0:
        return {}, "stamp_budget_exhausted"
    try:
        proc = subprocess.run(
            ["docker", "exec", container, "cat", stamp_path],
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {}, f"stamp_exec:{type(exc).__name__}"
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        suffix = f":{detail}" if detail else ""
        return {}, f"stamp_exec:exit_{proc.returncode}{suffix}"
    text = proc.stdout.decode("utf-8", errors="replace")
    synced_raw = text.strip().splitlines()[0].strip() if text.strip() else ""
    synced_at = _parse_ts(synced_raw) if synced_raw else None
    if synced_at is None:
        return {}, "stamp_unreadable"

    started_at, started_reason = _read_container_started_at(container, deadline=deadline)
    if started_reason:
        return {}, started_reason
    if started_at is not None and started_at < synced_at:
        return {}, "stamp_newer_than_process"

    fields = deploy_identity_from_stamp_text(text)
    if not fields:
        return {}, "stamp_unreadable"
    return fields, None


__all__ = [
    "deploy_identity_from_stamp_text",
    "read_mcp_container_deploy_identity",
]
