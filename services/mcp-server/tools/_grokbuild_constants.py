"""Shared constants for the grokbuild dispatch surface (review W8).

Single source of truth for values previously duplicated across
``_grokbuild_validator``, ``_grokbuild_runner``, ``_grokbuild_dispatch``,
and ``_grokbuild_fetch_result_decode``. Each consumer still re-binds the
name at module scope (so test fixtures that monkeypatch per-module
``_SIDECAR_DIR`` continue to work — Python's ``from X import Y`` creates
a per-module binding, not a live alias).

Derived sets (``_VALID_TIERS``, ``_MODE_BY_PERMISSION``) are computed
from their canonical maps so the two cannot diverge.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

# Sidecar location — both validator preflight (mkdir) and runner
# (append NDJSON) hit this directory; fetch_result reads from it.
_SIDECAR_DIR: Final[Path] = Path("/tmp/logs/grokbuild")


# mode ↔ grok --permission-mode mapping. Canonical direction is
# mode → permission_mode (the validator emits, the runner consumes via
# grok argv). The reverse map is derived so adding a new mode cannot
# silently leave the decode path with a stale reverse lookup.
_PERMISSION_BY_MODE: Final[dict[str, str]] = {
    "read_only": "acceptEdits",
    "edit": "acceptEdits",
}
_MODE_BY_PERMISSION: Final[dict[str, str]] = {
    v: k for k, v in _PERMISSION_BY_MODE.items()
}


@dataclass(frozen=True, slots=True)
class _TierPreset:
    reasoning_effort: str
    effort: str
    timeout_seconds: int


# Tier presets — canonical home so the validator's accepted-tier set
# (``_VALID_TIERS``) is derived from these keys directly, not
# hand-mirrored. Adding a tier here automatically updates the
# validator's accept-set.
_TIER_PRESETS: Final[dict[str, _TierPreset]] = {
    "quick": _TierPreset("minimal", "low", 300),
    "balanced": _TierPreset("medium", "medium", 600),
    "thorough": _TierPreset("high", "high", 1200),
    "max": _TierPreset("xhigh", "max", 1800),
}
_VALID_TIERS: Final[frozenset[str]] = frozenset(_TIER_PRESETS.keys())

# Models that do not accept reasoning controls. `grok` exposes `--effort`
# (agent-loop tier: turn budget / subagent depth / retry behavior) and
# `--reasoning-effort` (passthrough to the API `reasoning_effort` parameter)
# as two distinct flags with distinct value spaces. Both are suppressed in
# _build_argv for these models and for model=None (the CLI default,
# currently grok-build) because `grok-build` rejects both flags entirely
# and the API rejects `reasoning_effort` with HTTP 400 on the
# grok-4.20-0309-reasoning / grok-4.20-0309-non-reasoning models despite
# the CLI swallowing it with exit 0. Restoring either control requires
# routing through a reasoning-capable model (e.g. grok-4.3).
_NON_REASONING_MODELS: Final[frozenset[str]] = frozenset({"grok-build"})
