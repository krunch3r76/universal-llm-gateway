"""Shared helpers for dispatch op handlers — file I/O, validators, regex.

Centralizes constants and utilities used across dispatch_ops/ modules so the
FastAPI routes and dispatch handlers share one source of truth.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("cortex-api.dispatch_ops")


_CORTEX_FILES_ROOT_ENV = os.environ.get("CORTEX_FILES_ROOT")
if _CORTEX_FILES_ROOT_ENV:
    _FILES_ROOT = Path(_CORTEX_FILES_ROOT_ENV)
else:
    # Per workspace defaults-policy: log ERROR when relying on a default for a
    # resource path. The default must match the MCP container's data_dir bind
    # mount (~/.gateway/mcp.yaml -> data_dir) — when they diverge, fs(cortex)
    # cannot read what cortex-api wrote (mcp-data path alias gap).
    _FILES_ROOT = Path.home() / "mcp-data" / "files"
    logger.error(
        "CORTEX_FILES_ROOT is unset — falling back to %s. This MUST match the "
        "MCP container's data_dir/files bind mount or fs(cortex) writes/reads "
        "will diverge. Set CORTEX_FILES_ROOT explicitly (manage TUI does this "
        "from ~/.gateway/mcp.yaml).",
        _FILES_ROOT,
    )
_DEFAULT_USER_ENTITY = os.getenv("CORTEX_DEFAULT_USER_ENTITY", "")

_VALID_STATUS = frozenset({"confirmed", "provisional", "merged", "deprecated"})
_VALID_CONFIDENCE = frozenset({"confirmed", "believed", "suspected", "hypothesized"})
_SESSION_ID_RE = re.compile(r"^[a-z]+-\d{4}-\d{2}-\d{2}-\d{4}$")

_ENTITY_MUTABLE = frozenset(
    {
        "name",
        "aliases",
        "attributes",
        "notes",
        "source_uri",
        "description",
        "status",
        "workflow_state",
        "content_hash",
    }
)

_FRICTION_CATEGORIES = frozenset(
    {
        "tool_mismatch",
        "tool_absent",
        "tool_error",
        "schema_gap",
        "boot_drift",
        "lesson_gap",
        "lesson_conflict",
        "stale_context",
    }
)

try:
    from mcp_events import record as _record
except Exception:  # pragma: no cover - import-path dependent
    _record = None


def record(signal: str, **payload: Any) -> None:
    if _record is None:
        logger.debug("mcp_events unavailable; skipping event %s", signal)
        return
    _record(signal, **payload)


def _compute_content_hash(source_uri: str) -> str | None:
    """SHA-256 of a local file under CORTEX_FILES_ROOT. None if not local or missing."""
    local_path = _FILES_ROOT / source_uri
    if not local_path.is_file():
        return None
    h = hashlib.sha256()
    with open(local_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _derive_session_id_local(agent: str, timestamp: str) -> str:
    """Derive a session ID from agent + timestamp (mirrors cortex-api session_journals logic)."""
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):?(\d{2})", timestamp)
    if match:
        year, mon, day, hour, minute = match.groups()
        return f"{agent}-{year}-{mon}-{day}-{hour}{minute}"
    now = datetime.now(UTC).strftime("%Y-%m-%d-%H%M")
    return f"{agent}-{now}"


def _validate_canonical_sandbox_path(
    candidate: str,
    *,
    canonical_subdir: str,  # e.g. "agent-skills" for agent_skill:
    must_be_file: bool = True,
) -> Path:
    """Resolve candidate strictly inside _FILES_ROOT/<canonical_subdir>.

    Rejects:
      - paths whose resolved realpath leaves the canonical subdir
      - .. traversal that escapes _FILES_ROOT
      - absolute paths under workspaces/ or any other sandbox alias

    Returns the resolved Path on success; raises ValueError otherwise.
    Used by register_skill_substrate, register_evidence, etc. (W5 from
    cortex-primitives v2 plan).
    """
    canonical_root = (_FILES_ROOT / canonical_subdir).resolve()
    if Path(candidate).is_absolute():
        resolved = Path(candidate).resolve()
    else:
        resolved = (_FILES_ROOT / candidate).resolve()
    canonical_str = str(canonical_root) + os.sep
    if not (str(resolved) + os.sep).startswith(canonical_str):
        raise ValueError(
            f"path {candidate!r} resolves to {resolved} — "
            f"outside canonical sandbox {canonical_root}"
        )
    if must_be_file and not resolved.is_file():
        raise ValueError(f"path {candidate!r} does not resolve to a file")
    return resolved
