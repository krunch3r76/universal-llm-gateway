"""Capability and role profile registry — single source of truth for
(family, platform) routing data and role roster.

Two tables keyed by the orthogonal axes the system operates on:
(family, platform) cells via CapabilityProfile, and role slugs via
RoleProfile. Data is loaded from config/agents.yaml at startup via
load_profiles() and load_roles(). Accessor functions (get_profile,
get_role, resolve_seat) are the primary call sites.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

_DEFAULT_FAMILY = "claude"
_DEFAULT_PLATFORM = "cursor"

# config/agents.yaml lives at the project root, two levels above libs/.
_AGENTS_YAML = Path(__file__).parent.parent.parent / "config" / "agents.yaml"


@dataclass(frozen=True, slots=True)
class CapabilityProfile:
    """Per-(family, platform) routing + boot profile.

    Mandatory:
      family, platform, provider, default_model, tool_surface, delivery,
      include_deadlines, include_review_queue, confirm_and_proceed,
      addenda

    Optional:
      allowed_models, model_requirement, capability_tier, dispatchable
    """

    family: Literal["claude", "gpt", "grok", "gemini", "subagent"]
    platform: Literal["api", "api-multi", "web", "cursor", "subagent"]
    provider: Literal["anthropic", "openai", "xai", "google"]
    default_model: str | None  # None → not API-reachable (web platforms)
    tool_surface: Literal["mcp", "inline-only"]
    delivery: Literal["auto", "manual"]
    include_deadlines: bool
    include_review_queue: bool
    confirm_and_proceed: bool
    addenda: tuple[str, ...]  # keys into _ADDENDA_BLOCKS dict
    allowed_models: tuple[str, ...] = field(default_factory=tuple)
    model_requirement: str | None = None  # e.g. "multi-agent" substring requirement
    capability_tier: Literal["inline-only"] | None = None
    dispatchable: bool = True  # False for web-only seats (web-claude, etc.)
    session_limit: int = 3
    self_reflections_limit: int = 5


@dataclass(frozen=True, slots=True)
class RoleProfile:
    """Per-role functional seat. Model-agnostic with default assignment.

    The role is the team seat (function); the default (family, platform,
    model) is the conventional assignment but is swappable at dispatch
    time.
    """

    role: str
    description: str
    default_family: Literal["claude", "gpt", "grok", "gemini"]
    default_platform: Literal["api", "api-multi", "web", "cursor"]
    default_model: str | None  # None ⟹ operator picks (web roles)
    allowed_models: tuple[str, ...] = field(default_factory=tuple)
    allowed_options: tuple[str, ...] | None = None  # None ⟹ no restriction


def _load_agents_yaml() -> dict[str, Any]:
    """Read and parse config/agents.yaml; raises FileNotFoundError if absent."""
    if not _AGENTS_YAML.exists():
        raise FileNotFoundError(
            f"agents.yaml not found at {_AGENTS_YAML}. "
            "Ensure config/agents.yaml is present in the project root."
        )
    with _AGENTS_YAML.open() as fh:
        return yaml.safe_load(fh) or {}


@functools.cache
def load_profiles() -> dict[tuple[str, str], CapabilityProfile]:
    """Return the full (family, platform) → CapabilityProfile registry."""
    raw = _load_agents_yaml().get("profiles") or {}
    profiles: dict[tuple[str, str], CapabilityProfile] = {}
    for key, entry in raw.items():
        family, platform = key.split("/", 1)
        profiles[(family, platform)] = CapabilityProfile(
            family=family,
            platform=platform,
            provider=entry["provider"],
            default_model=entry.get("default_model"),
            tool_surface=entry["tool_surface"],
            delivery=entry["delivery"],
            include_deadlines=entry["include_deadlines"],
            include_review_queue=entry["include_review_queue"],
            confirm_and_proceed=entry["confirm_and_proceed"],
            addenda=tuple(entry.get("addenda") or []),
            allowed_models=tuple(entry.get("allowed_models") or []),
            model_requirement=entry.get("model_requirement"),
            capability_tier=entry.get("capability_tier"),
            dispatchable=entry.get("dispatchable", True),
            session_limit=entry.get("session_limit", 3),
            self_reflections_limit=entry.get("self_reflections_limit", 5),
        )
    return profiles


@functools.cache
def load_roles() -> dict[str, RoleProfile]:
    """Return the full role-slug → RoleProfile registry."""
    raw = _load_agents_yaml().get("roles") or {}
    roles: dict[str, RoleProfile] = {}
    for slug, entry in raw.items():
        allowed_opts_raw = entry.get("allowed_options")
        roles[slug] = RoleProfile(
            role=slug,
            description=entry["description"],
            default_family=entry["default_family"],
            default_platform=entry["default_platform"],
            default_model=entry.get("default_model"),
            allowed_models=tuple(entry.get("allowed_models") or []),
            allowed_options=(
                tuple(allowed_opts_raw) if allowed_opts_raw is not None else None
            ),
        )
    return roles


# ── Accessor functions ───────────────────────────────────────────────────────


def get_profile(family: str, platform: str) -> CapabilityProfile:
    """Look up profile by (family, platform); raises KeyError if not registered."""
    profiles = load_profiles()
    key = (family, platform)
    if key not in profiles:
        raise KeyError(
            f"No CapabilityProfile registered for ({family!r}, {platform!r}). "
            f"Known cells: {sorted(profiles)}"
        )
    return profiles[key]


def get_role(role: str) -> RoleProfile:
    """Look up role profile by slug; raises KeyError if not registered."""
    roles = load_roles()
    if role not in roles:
        raise KeyError(
            f"No RoleProfile registered for {role!r}. Known roles: {sorted(roles)}"
        )
    return roles[role]


def family_anchor(family: str) -> str:
    """Return the Cortex entity ID for a model family's memory anchor."""
    return f"family:{family}"


def role_anchor(role: str) -> str:
    """Return the Cortex entity ID for a role's memory anchor."""
    return f"role:{role}"


def derive_inline_only(profile: CapabilityProfile) -> bool:
    """Pure function of the profile — no Cortex round-trip.

    Returns True when the profile's capability_tier or tool_surface restricts
    the seat to inline-only operation (no client-side tool loop, no MCP).
    """
    return (
        profile.capability_tier == "inline-only"
        or profile.tool_surface == "inline-only"
    )


_PROVIDER_TO_FAMILY: dict[str, str] = {
    "anthropic": "claude",
    "openai": "gpt",
    "xai": "grok",
    "google": "gemini",
}


def inline_only_for_model(model: str) -> bool:
    """True iff the effective model's family is policy-restricted to inline-only.

    Capability binds to the EFFECTIVE model, not the role label. A model whose
    ``(family, "api")`` profile is inline-only — e.g. gemini, which hallucinates
    MCP calls and is barred from write surfaces on any role — must never receive
    a client-side tool loop, even when an explicit ``model=`` override assigns it
    to a write-capable role (reviewer/lead). Closes the explicit-override gap
    where ``capability_tier`` was derived only from the role's default profile.
    """
    from model_id import ModelId

    family = _PROVIDER_TO_FAMILY.get(ModelId.parse(model).provider)
    if family is None:
        return False
    profile = load_profiles().get((family, "api"))
    return profile is not None and derive_inline_only(profile)


def client_side_mcp_tool_loop_admitted(model: str) -> bool:
    """True iff dispatch admission may enable a client-side MCP function-tool loop.

    Shared by ``mcp_enabled_for_team_dispatch`` and ``mcp_enabled_for_frontier_dispatch``
    (via ``_mcp_base_admitted``). False for inline-only families (gemini) and for
    xAI multi-agent models (API rejects client-side function tools; server builtins only).
    """
    if inline_only_for_model(model):
        return False
    from model_id import ModelId

    mid = ModelId.parse(model)
    return not (mid.provider == "xai" and "multi-agent" in mid.base_id)


def resolve_seat(
    family: str | None = None,
    platform: str | None = None,
) -> tuple[str, str]:
    """Return canonical (family, platform), default-filling missing axes.

    Default: family → claude, platform → cursor.
    """
    return family or _DEFAULT_FAMILY, platform or _DEFAULT_PLATFORM
