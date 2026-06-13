"""Capability and role profile registry — single source of truth for
(family, platform) routing data and role roster.

Three registries loaded from config/agents.yaml:
(family, platform) cells via CapabilityProfile, role slugs via RoleProfile,
and operator lead seats via load_lead_agent_slugs(). Accessor functions
(get_profile, get_role, is_lead_agent, resolve_seat) are the primary call sites.
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

    Admission flags (replace legacy ``dispatchable``):
      api_dispatchable — cloud/API generate-peer (CapabilityDispatch path)
      auto_dispatchable — unattended local substrate (e.g. cursor/sdk bridge)
      manual_handoff — human seat reachable only via op=handoff

    Invariant: at most one of api_dispatchable/auto_dispatchable is True;
    manual_handoff profiles have delivery=manual.
    """

    family: Literal["claude", "gpt", "grok", "gemini", "subagent", "cursor"]
    platform: Literal["api", "api-multi", "web", "cursor", "subagent", "sdk"]
    provider: Literal["anthropic", "openai", "xai", "google", "cursor"]
    default_model: str | None
    tool_surface: Literal["mcp", "inline-only", "sdk"]
    delivery: Literal["auto", "manual"]
    include_deadlines: bool
    include_review_queue: bool
    confirm_and_proceed: bool
    addenda: tuple[str, ...]
    allowed_models: tuple[str, ...] = field(default_factory=tuple)
    model_requirement: str | None = None
    capability_tier: Literal["inline-only"] | None = None
    api_dispatchable: bool = False
    auto_dispatchable: bool = False
    manual_handoff: bool = False
    session_limit: int = 3
    self_reflections_limit: int = 5

    def admits_generate(self) -> bool:
        return self.api_dispatchable or self.auto_dispatchable

    def admits_handoff(self) -> bool:
        return self.manual_handoff


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
    # Handoff work-intent when ``role`` is the handoff selector.
    # None ⟹ resolves to ``consult``.
    default_contract: Literal["consult", "implement"] | None = None


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
            api_dispatchable=entry.get("api_dispatchable", False),
            auto_dispatchable=entry.get("auto_dispatchable", False),
            manual_handoff=entry.get("manual_handoff", False),
            session_limit=entry.get("session_limit", 3),
            self_reflections_limit=entry.get("self_reflections_limit", 5),
        )
        _validate_profile_admission_flags(profiles[(family, platform)], key)
    return profiles


def _validate_profile_admission_flags(profile: CapabilityProfile, key: str) -> None:
    if profile.api_dispatchable and profile.auto_dispatchable:
        raise ValueError(
            f"agents.yaml profile {key!r}: api_dispatchable and auto_dispatchable "
            "are mutually exclusive"
        )
    if profile.manual_handoff and profile.delivery != "manual":
        raise ValueError(
            f"agents.yaml profile {key!r}: manual_handoff requires delivery=manual"
        )
    if profile.auto_dispatchable and profile.delivery != "auto":
        raise ValueError(
            f"agents.yaml profile {key!r}: auto_dispatchable requires delivery=auto"
        )


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
            default_contract=entry.get("default_contract"),
        )
    return roles


@functools.cache
def load_lead_agent_slugs() -> frozenset[str]:
    """Return operator lead seat slugs from config/agents.yaml ``lead_seats``."""
    raw = _load_agents_yaml().get("lead_seats")
    if not raw:
        raise ValueError(
            "agents.yaml: lead_seats must be a non-empty list of seat slugs"
        )
    slugs = frozenset(str(s).strip() for s in raw if str(s).strip())
    if not slugs:
        raise ValueError("agents.yaml: lead_seats resolved to an empty set")
    return slugs


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
    """True iff the effective model's family resolves to an inline-only profile.

    Looks up the (family, "api") CapabilityProfile for the model's provider.
    Returns True only if that profile has capability_tier="inline-only" or
    tool_surface="inline-only". Falls through to False for unknown providers
    or families without an explicit profile restriction.
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
    (via ``_mcp_base_admitted``). False for profiles with capability_tier/tool_surface
    inline-only, and for xAI multi-agent models (API rejects client-side function
    tools; server-side built-ins injected via provider_options instead).
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


@functools.cache
def known_families() -> frozenset[str]:
    """Families present in the agents.yaml profile registry (e.g. claude, gpt, grok, gemini)."""
    return frozenset(family for family, _platform in load_profiles())


def seat_capabilities(profile: CapabilityProfile) -> frozenset[str]:
    """Closed capability-token set derived from the profile axes (no second table)."""
    toks: set[str] = set()
    if profile.tool_surface == "mcp":
        toks.add("mcp_fs")
    if profile.tool_surface in ("mcp", "sdk"):
        toks.add("local_fs_write")
    if profile.tool_surface == "sdk":
        toks.add("git_worktree")
    return frozenset(toks)


CAPABILITY_TOKENS: frozenset[str] = frozenset(
    {"mcp_fs", "local_fs_write", "git_worktree"}
)


@functools.cache
def seat_capability_map() -> dict[str, frozenset[str]]:
    return {
        f"{family}-{platform}": seat_capabilities(prof)
        for (family, platform), prof in load_profiles().items()
    }


@functools.cache
def known_seats() -> frozenset[str]:
    """Canonical seat slugs ({family}-{platform}) from the agents.yaml profile cells.

    The closed seat vocabulary for applicability gating. Derived from the profile
    registry — no second hardcoded list. '*' (universal) is NOT a seat; callers add
    it explicitly where universal is permitted.
    """
    return frozenset(f"{family}-{platform}" for family, platform in load_profiles())


def seat_to_family(slug: str) -> str | None:
    """Project a seat slug to its model family (provenance granularity).

    ``claude-cursor`` → ``claude``; ``grok-cursor`` → ``grok``. Returns the
    input unchanged when it is already a bare family. Returns None when the
    leading token is not a registered family — caller decides reject vs pass.
    Routing/operational identity keeps the full seat; this projection is for
    knowledge provenance (``seeded_by``) only.
    """
    if not slug:
        return None
    families = known_families()
    if slug in families:
        return slug
    head = slug.split("-", 1)[0]
    return head if head in families else None
