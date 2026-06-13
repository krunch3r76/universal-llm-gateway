"""Shared helpers for dispatch op handlers — file I/O, validators, regex.

Centralizes constants and utilities used across dispatch_ops/ modules so the
FastAPI routes and dispatch handlers share one source of truth.
"""

from __future__ import annotations  # noqa: I001 — re-export pattern: _SESSION_ID_{RE,RE_SOURCE,EXAMPLES} are intentionally imported for session_close_validation.py

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from agent_seat.session_id import (
    SESSION_ID_EXAMPLES as _SESSION_ID_EXAMPLES,  # noqa: F401
    SESSION_ID_RE as _SESSION_ID_RE,  # noqa: F401
    SESSION_ID_RE_SOURCE as _SESSION_ID_RE_SOURCE,  # noqa: F401
    derive_session_id_from_timestamp,
)
from universal_logging import get_logger

from ..trait_vocabulary import NON_LIVE_LIFECYCLE

logger = get_logger(__name__)


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

# Confidence-axis status is DERIVED (Fork D, G1 thread 1173): frozen from
# hand-set writes at entity_create/update. Lifecycle-axis status stays settable.
# ``_LIFECYCLE_AXIS_STATUS`` sources the non-live set from the trait_vocabulary
# canonical SOT (leaf module — no import cycle). ``_VALID_STATUS`` is derived
# from both axis sets so the union can never drift (previously omitted 'reaped').
_CONFIDENCE_AXIS_STATUS = frozenset({"unsubstantiated", "confirmed", "provisional"})
_LIFECYCLE_AXIS_STATUS = NON_LIVE_LIFECYCLE
_VALID_STATUS = _CONFIDENCE_AXIS_STATUS | _LIFECYCLE_AXIS_STATUS
_VALID_CONFIDENCE = frozenset({"confirmed", "believed", "suspected", "hypothesized"})

# Agent slug: lowercase alnum + hyphens, must start with a letter.  Used as a
# permissive shape check (no allowlist — agent is a routing/metadata hint).
_AGENT_SLUG_RE_SOURCE = r"^[a-z][a-z0-9-]*$"
_AGENT_SLUG_RE = re.compile(_AGENT_SLUG_RE_SOURCE)
_AGENT_SLUG_EXAMPLES = ("cursor", "web", "claude-web", "api-claude", "gatherer")

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
        # Option-C trait write surface — explicit trait columns are settable on
        # entity_update only (NOT entity_create; birth traits come from
        # resolve_birth_traits). The legacy ``status`` mirror remains read-only
        # on the confidence axis (update_entity_impl ignores hand-set
        # confidence-axis status); the explicit columns below are the
        # intentional post-create write path.
        "confidence_band",
        "lifecycle",
        "adoption",
    }
)

_TRAIT_WRITE_KEYS = frozenset({"confidence_band", "lifecycle", "adoption"})


def reject_trait_writes_at_create(payload: dict[str, object]) -> dict[str, Any] | None:
    """Return an error dict when Option-C traits are supplied at create time."""
    keys = _TRAIT_WRITE_KEYS & payload.keys()
    if not keys:
        return None
    return {
        "error": (
            f"Option-C traits {sorted(keys)} are not settable at entity_create "
            "(birth traits are derived from entity type; use entity_update after create)."
        )
    }


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
        "doc_drift",
        "protocol",
    }
)


def normalize_service_slug(service: str) -> str:
    """Accept bare slug (mcp-server) or entity ID (service:mcp-server)."""
    return service.removeprefix("service:")


def service_entity_id(service: str) -> str:
    """Canonical service:* entity_id from either slug form."""
    return f"service:{normalize_service_slug(service)}"


try:
    from mcp_events import record as _record
except (
    ImportError
):  # pragma: no cover - mcp_events only available in mcp-server context
    try:
        from cortex_store.event_publisher import record as _record
    except ImportError:
        _record = None  # type: ignore[assignment]


def record(signal: str, **payload: Any) -> None:
    if _record is None:
        logger.debug("event publisher unavailable; skipping event %s", signal)
        return
    _record(signal, **payload)


def _compute_content_hash(source_uri: str) -> str | None:
    """SHA-256 of a local file under CORTEX_FILES_ROOT. None if not local or missing.

    Strips the ``cortex://`` scheme prefix so callers can pass either a bare
    relative path (e.g. ``agent-skills/foo.md``) or the canonical URI form
    (e.g. ``cortex://agent-skills/foo.md``). Other schemes (``workspaces://``,
    ``files://``, ``https://``) return None — those files are not under
    CORTEX_FILES_ROOT and this helper does not resolve them.

    Prior to the cortex:// strip, every entity_update that passed
    ``source_uri="cortex://..."`` (the corpus convention for skill files
    under ``agent-skills/``) silently produced None and skipped the
    auto-recompute — see ``test_compute_content_hash.py`` for the
    regression test that pins the bug at the corpus-convention URI shape.
    """
    if source_uri.startswith("cortex://"):
        source_uri = source_uri.removeprefix("cortex://")
    elif "://" in source_uri:
        # workspaces://, files://, https://, … — not under CORTEX_FILES_ROOT.
        return None
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
    return derive_session_id_from_timestamp(agent, timestamp)


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
