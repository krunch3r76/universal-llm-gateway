"""Web boot inject-channel skill maps for injected-skill loaded-set accounting."""

from __future__ import annotations

# Channel 2 — orientation blocks (``render_orientation_blocks``).
ORIENTATION_BLOCK_SKILL_MAP: dict[str, tuple[str, ...]] = {
    "operator-posture-block": ("operator-posture",),
    "mcp-binding-block": ("lead-seat-boot",),
    "capability-verify-block": ("lead-seat-boot",),
    "dispatch-consult-block": ("consult-routing", "dispatch-shape"),
    "consult-routing-gate-block": ("consult-routing",),
    "liveness-block": ("git-posture",),
    "entity-hierarchy-block": ("entity-lifecycle-discipline",),
    "session-close-web-block": (
        "session-close",
        "session-close-audit",
        "web-transcript-preprocessing",
    ),
    "tier-selection-block": ("model-tier-awareness-web",),
    # rag-scope-awareness-block: no backing agent_skill
}

_ORIENTATION_BLOCKS_ALL_SEATS: frozenset[str] = frozenset(
    {
        "operator-posture-block",
        "mcp-binding-block",
        "dispatch-consult-block",
        "consult-routing-gate-block",
        "liveness-block",
        "entity-hierarchy-block",
    }
)

_ORIENTATION_BLOCKS_WEB_ONLY: frozenset[str] = frozenset(
    {
        "capability-verify-block",
        "session-close-web-block",
        "tier-selection-block",
    }
)

# Channel 3 — operational-context sections (``render_operational_context``).
OPCONTEXT_SECTION_SKILL_MAP: dict[str, tuple[str, ...]] = {
    "frontier-reasoning": ("frontier-reasoning-discipline",),
    "prose-discipline": ("prose-discipline",),
    "team-consultation": ("consult-routing",),
}

_OPCONTEXT_SUBAGENT_EXCLUDED: frozenset[str] = frozenset(
    {"frontier-reasoning", "team-consultation"}
)


def _is_web_agent(agent: str | None) -> bool:
    return bool(agent and agent.endswith("-web"))


def _is_subagent_seat(family: str, platform: str) -> bool:
    return family == "subagent" and platform == "subagent"


def orientation_block_keys_for_agent(agent: str | None) -> frozenset[str]:
    keys = set(_ORIENTATION_BLOCKS_ALL_SEATS)
    if _is_web_agent(agent):
        keys.update(_ORIENTATION_BLOCKS_WEB_ONLY)
    return frozenset(keys)


def opcontext_section_keys_for_agent(family: str, platform: str) -> frozenset[str]:
    keys = set(OPCONTEXT_SECTION_SKILL_MAP)
    if _is_subagent_seat(family, platform):
        keys -= _OPCONTEXT_SUBAGENT_EXCLUDED
    return frozenset(keys)


def web_orientation_inject_skill_slugs(agent: str | None) -> tuple[str, ...]:
    slugs: set[str] = set()
    for key in orientation_block_keys_for_agent(agent):
        slugs.update(ORIENTATION_BLOCK_SKILL_MAP[key])
    return tuple(sorted(slugs))


def web_opcontext_inject_skill_slugs(
    agent: str | None,
    family: str,
    platform: str,
) -> tuple[str, ...]:
    slugs: set[str] = set()
    for key in opcontext_section_keys_for_agent(family, platform):
        slugs.update(OPCONTEXT_SECTION_SKILL_MAP[key])
    return tuple(sorted(slugs))


def web_seat_injected_skill_slugs(
    agent: str,
    *,
    family: str | None = None,
    platform: str | None = None,
) -> tuple[str, ...]:
    """Boot-card channel slugs for injected-skill loaded-set accounting."""
    parts = agent.split("-", 1)
    resolved_family = family if family is not None else (parts[0] if parts else agent)
    resolved_platform = platform
    if resolved_platform is None:
        resolved_platform = parts[1] if len(parts) == 2 else "web"
    slugs: set[str] = set()
    slugs.update(web_orientation_inject_skill_slugs(agent))
    slugs.update(
        web_opcontext_inject_skill_slugs(
            agent, resolved_family, resolved_platform or "web"
        )
    )
    return tuple(sorted(slugs))
