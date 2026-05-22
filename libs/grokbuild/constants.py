"""Shared constants for the grokbuild dispatch surface (review W8).

Single source of truth for values previously duplicated across
``grokbuild.validator``, ``grokbuild.runner``, ``grokbuild.dispatch``,
and ``grokbuild.fetch_result_decode``. Each consumer still re-binds the
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
# through `GROKBUILD_SIDECAR_DIR` so the lib reads from the operator-locked
# path (`/var/lib/grokbuild-worker/sidecars/` per decision:grokbuild-execution-
# tracker-shape A.2 override). The default value matches `WorkerConfig`'s
# default so the lib and worker land on the same dir even when the env var
# is missing in a bare-metal shell.
_SIDECAR_DIR: Final[Path] = Path(
    os.getenv("GROKBUILD_SIDECAR_DIR", "/var/lib/grokbuild-worker/sidecars")
)


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
# model=None (CLI default) is resolved to "grok-build" before lookup.
#
# --effort is the grok CLI agent-loop tier flag (independent of reasoning API).
# It is emitted for every model except those where the grok CLI rejects it
# (currently only "grok-build" legacy default).
#
# --reasoning-effort is the API passthrough for xAI reasoning_effort.
# grok-4.20-{reasoning,non-reasoning} silently swallow it at the CLI level
# (xAI confirmed CLI bug); the API rejects it with HTTP 400. Suppress for
# those. grok-4.20-multi-agent-0309 does honor reasoning.effort (verified
# 2026-05-20 direct against /v1/responses) — swarm size scales with effort.
MODEL_REGISTRY: Final[dict[str, _ModelCapabilities]] = {
    "grok-build": _ModelCapabilities(
        supports_reasoning_effort=False,
        supports_effort=False,
        supports_subagents=True,
        internal_multi_agent=False,
        default_reasoning_effort=None,
        notes="Legacy CLI default; rejects both effort flags. Deprecated.",
    ),
    "xai/grok-4.3": _ModelCapabilities(
        supports_reasoning_effort=True,
        supports_effort=True,
        supports_subagents=True,
        internal_multi_agent=False,
        default_reasoning_effort="high",
        notes="Primary reasoning model; effort injection via __effort_ stanzas.",
    ),
    "xai/grok-4.20-0309-reasoning": _ModelCapabilities(
        supports_reasoning_effort=False,
        supports_effort=True,
        supports_subagents=True,
        internal_multi_agent=False,
        default_reasoning_effort=None,
        notes="Built-in reasoning; --reasoning-effort silently swallowed (xAI CLI bug).",
    ),
    "xai/grok-4.20-0309-non-reasoning": _ModelCapabilities(
        supports_reasoning_effort=False,
        supports_effort=True,
        supports_subagents=True,
        internal_multi_agent=False,
        default_reasoning_effort=None,
        notes="Non-reasoning fast variant.",
    ),
    "xai/grok-4.20-multi-agent-0309": _ModelCapabilities(
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


# Tier → effort-encoded stanza name for xAI grok-4.3 (Responses API).
# The grok CLI ignores --reasoning-effort for custom stanzas; effort is
# encoded in the model-ID suffix (__effort_<value>) so the cloud-proxy
# _forward_native handler can inject reasoning.effort before forwarding.
# Stanza base_url must point at Stargate (http://localhost:9999/providers/xai).
# Wire values mirror xAI spec: low | medium | high | xhigh.
_XAI_GROK43_EFFORT_STANZA: Final[dict[str, str]] = {
    "quick": "xai/grok-4.3__effort_low",
    "balanced": "xai/grok-4.3__effort_medium",
    "thorough": "xai/grok-4.3__effort_high",
    "max": "xai/grok-4.3__effort_xhigh",
}

# xAI models that use effort-stanza routing instead of --reasoning-effort.
# ∀ m ∈ _XAI_EFFORT_INJECT_MODELS: _build_argv substitutes m with the
# tier-appropriate effort stanza before emitting --model.
_XAI_EFFORT_INJECT_MODELS: Final[frozenset[str]] = frozenset({"xai/grok-4.3"})
