"""Shared constants for the grokbuild dispatch surface (review W8).

Single source of truth for values previously duplicated across
``grokbuild.validator``, ``grokbuild.runner``, ``grokbuild.dispatch``,
``grokbuild.api_dispatch``, and ``grokbuild.fetch_result_decode``. Each
consumer still re-binds the
name at module scope (so test fixtures that monkeypatch per-module
``_SIDECAR_DIR`` continue to work — Python's ``from X import Y`` creates
a per-module binding, not a live alias).

Derived sets (``_VALID_TIERS``, ``_MODE_BY_PERMISSION``) are computed
from their canonical maps so the two cannot diverge.

The ``MODEL_REGISTRY`` replaces the former ``_NON_REASONING_MODELS``
frozenset with per-flag capability booleans. Unknown models (not in
registry) are treated as supporting both flags — pass-through, not
admission-blocked.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# Sidecar location — both validator preflight (mkdir) and runner
# (append NDJSON) hit this directory; fetch_result reads from it.
#
# Env-driven (V2): the grokbuild-worker `WorkerConfig.sidecar_dir` propagates
# through `GROKBUILD_SIDECAR_DIR` so the lib reads from the same path as the
# worker.  Default is XDG-compliant (~/.local/share/grokbuild-worker/sidecars);
# expanduser() handles the tilde so the default works even when the env var is
# absent.  Docker deployments set GROKBUILD_SIDECAR_DIR explicitly to keep the
# bind-mounted /var/lib path; env var override always takes precedence.
_SIDECAR_DIR: Final[Path] = Path(
    os.getenv("GROKBUILD_SIDECAR_DIR", "~/.local/share/grokbuild-worker/sidecars")
).expanduser()


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


# Caller-facing dispatch wall-clock limit when ``timeout_seconds`` is omitted.
# Orthogonal to tier (reasoning/effort); tier must not hard-kill mid-edit.
DEFAULT_TIMEOUT_SECONDS: Final[int] = 3600

# Sole admitted model for grokbuild dispatches (CLI subprocess path).
# Host ~/.grok/config.toml [model.*] pipeline aliases are stripped from
# dispatch-scoped config so subagents cannot route to Stargate API models.
DISPATCH_MODEL_ID: Final[str] = "grok-build"


@dataclass(frozen=True, slots=True)
class _TierPreset:
    reasoning_effort: str
    effort: str
    default_model: str


# Tier presets — canonical home so the validator's accepted-tier set
# (``_VALID_TIERS``) is derived from these keys directly, not
# hand-mirrored. Adding a tier here automatically updates the
# validator's accept-set.
_TIER_PRESETS: Final[dict[str, _TierPreset]] = {
    "quick": _TierPreset("minimal", "low", DISPATCH_MODEL_ID),
    "balanced": _TierPreset("medium", "medium", DISPATCH_MODEL_ID),
    "thorough": _TierPreset("high", "high", DISPATCH_MODEL_ID),
    "max": _TierPreset("xhigh", "max", DISPATCH_MODEL_ID),
}
_VALID_TIERS: Final[frozenset[str]] = frozenset(_TIER_PRESETS.keys())


@dataclass(frozen=True, slots=True)
class _ModelCapabilities:
    supports_reasoning_effort: bool  # grok --reasoning-effort (API passthrough)
    supports_effort: bool  # grok --effort (agent-loop tier: budget/depth/retry)
    supports_subagents: bool  # --no-subagents meaningful for this model?
    internal_multi_agent: bool  # model coordinates its own subagent swarm
    default_reasoning_effort: str | None
    notes: str


# Per-model capability registry. Flag-gating source for _build_argv; also
# the backing store for op="models".
#
# ∀ model ∉ MODEL_REGISTRY: both effort flags treated as supported (pass-
# through — caller may still hit a CLI/API failure, but admission won't block).
# dispatch_op resolves omitted model to each tier preset's default_model
# (grok-build) before RunnerSpec; explicit model overrides are rejected
# at admission unless they equal DISPATCH_MODEL_ID.
#
# --effort is the grok CLI agent-loop tier flag (independent of reasoning API).
# It is emitted for every model except those where the grok CLI rejects it
# (currently only "grok-build" legacy default).
#
# --reasoning-effort is the API passthrough for xAI reasoning_effort.
# Subscription plan: grok-4.3 supports both --reasoning-effort and --effort
# directly via CLI (no stanza workaround needed). grok-4.20-{reasoning,non-
# reasoning} silently swallow --reasoning-effort at the CLI level (xAI CLI bug);
# the API rejects it with HTTP 400. Suppress for those.
# grok-4.20-multi-agent-0309 does honor reasoning.effort (verified 2026-05-20
# direct against /v1/responses) — swarm size scales with effort.
MODEL_REGISTRY: Final[dict[str, _ModelCapabilities]] = {
    "grok-build": _ModelCapabilities(
        supports_reasoning_effort=False,
        supports_effort=False,
        supports_subagents=True,
        internal_multi_agent=False,
        default_reasoning_effort=None,
        notes="Legacy CLI default; rejects both effort flags. Deprecated.",
    ),
    "grok-4.3": _ModelCapabilities(
        supports_reasoning_effort=True,
        supports_effort=True,
        supports_subagents=True,
        internal_multi_agent=False,
        default_reasoning_effort="high",
        notes="Primary reasoning model; subscription plan supports --reasoning-effort and --effort natively.",
    ),
    "grok-build-0.1": _ModelCapabilities(
        supports_reasoning_effort=False,
        supports_effort=False,
        supports_subagents=True,
        internal_multi_agent=False,
        default_reasoning_effort=None,
        notes=(
            "CLI lists this subscription alias, but probes on 2026-05-24 "
            "showed grok -m grok-build-0.1 resolves upstream as "
            "model_id=grok-build and 404s; JSON success likely came from "
            "CLI fallback. Keep registered to suppress effort flags until "
            "targeting is verified."
        ),
    ),
    "grok-4.20-0309-reasoning": _ModelCapabilities(
        supports_reasoning_effort=False,
        supports_effort=True,
        supports_subagents=True,
        internal_multi_agent=False,
        default_reasoning_effort=None,
        notes="Built-in reasoning; --reasoning-effort silently swallowed (xAI CLI bug).",
    ),
    "grok-4.20-0309-non-reasoning": _ModelCapabilities(
        supports_reasoning_effort=False,
        supports_effort=True,
        supports_subagents=True,
        internal_multi_agent=False,
        default_reasoning_effort=None,
        notes="Non-reasoning fast variant.",
    ),
    "grok-4.20-multi-agent-0309": _ModelCapabilities(
        supports_reasoning_effort=True,
        supports_effort=True,
        supports_subagents=False,
        internal_multi_agent=True,
        default_reasoning_effort=None,
        notes=(
            "Native swarm with specialized roles (search/analysis/synthesis/"
            "conflict-resolution); 2M context. --no-subagents is a no-op "
            "(model coordinates internally). reasoning.effort scales swarm "
            "size via xAI Responses API: low/medium → ~4 agents, high/xhigh "
            "→ ~16. Empirical 2026-05-20 (cortex 10603): low→high "
            "reasoning_tokens 1081→10736 (~9.9x), input_tokens 3631→49658 "
            "(~13.7x). Sibling grok-4.20-{reasoning,non-reasoning} reject "
            "reasoningEffort with HTTP 400."
        ),
    ),
}


def default_model_for_tier(tier: str) -> str:
    """Resolve tier preset → effective model id.

    Caller MUST validate ``tier ∈ _VALID_TIERS`` before calling; this helper
    indexes ``_TIER_PRESETS`` directly.
    """
    return _TIER_PRESETS[tier].default_model


def envelope_metadata_model(*, model: str | None, tier: str) -> str:
    """Resolve envelope / sidecar ``model`` field.

    Caller MUST validate ``tier ∈ _VALID_TIERS`` when ``model`` is None.
    """
    if model is None:
        return _TIER_PRESETS[tier].default_model
    return model
