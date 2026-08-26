"""Web boot inject-channel skill maps for injected-skill loaded-set accounting."""

from __future__ import annotations

# Channel 2 — orientation blocks (``render_orientation_blocks``).
ORIENTATION_BLOCK_SKILL_MAP: dict[str, tuple[str, ...]] = {
    # Inline doctrine only — operator-posture is cursor_only (¬ Customize chip).
    "operator-posture-block": (),
    "mcp-binding-block": ("lead-seat-boot",),
    "capability-verify-block": ("lead-seat-boot",),
    "dispatch-consult-block": ("consult-routing", "dispatch-shape"),
    "consult-routing-gate-block": ("consult-routing",),
    "liveness-block": ("git-posture",),
    "cursor-model-economics-block": ("cursor-model-economics",),
    "entity-hierarchy-block": ("entity-lifecycle-discipline",),
    "session-close-web-block": (
        "session-close",
        "session-close-audit",
        "web-transcript-preprocessing",
    ),
    "terminal-facts-pointer-block": ("cortex-orientation",),
    # No backing agent_skill (rendered inline only; never web skill-injected):
    #   mcp-server-primary-block — live tools/list manifest line
    #   rag-scope-awareness-block — no skill covers rag scope semantics
}

# Full doctrine set rendered inline on seats WITHOUT resident alwaysApply rules
# (web + api). Web adds _ORIENTATION_BLOCKS_WEB_ONLY on top; cursor renders the
# thinned _ORIENTATION_BLOCKS_CURSOR set instead (see orientation_block_keys_for_agent).
_ORIENTATION_CORE_BLOCKS: frozenset[str] = frozenset(
    {
        "operator-posture-block",
        "mcp-binding-block",
        "mcp-server-primary-block",
        "dispatch-consult-block",
        "consult-routing-gate-block",
        "rag-scope-awareness-block",
        "cursor-model-economics-block",
        "liveness-block",
        "entity-hierarchy-block",
    }
)

_ORIENTATION_BLOCKS_WEB_ONLY: frozenset[str] = frozenset(
    {
        "capability-verify-block",
        "session-close-web-block",
        "terminal-facts-pointer-block",
    }
)

# Cursor render set — friction 25727 follow-on. Cursor carries resident
# alwaysApply rules + native skill discovery, so a core block whose doctrine a
# resident rule (and/or a self-fetchable skill) already carries is DROPPED
# (verified against live rule bodies before cutting):
#   operator-posture-block    → operator-posture.mdc (resident)
#   dispatch-consult-block     → lean-context-dispatch-first_ulg ladder (resident) + GATES §2
#   consult-routing-gate-block → GATES §2 + phase-vocabulary_ulg (codified-bug 2-phase) + consult-routing skill
#   liveness-block             → commit-and-git-scope_ulg + shared-checkout-housekeeping_ulg (resident) + git-posture skill
#   entity-hierarchy-block     → phase-vocabulary_ulg (resident) + entity-lifecycle-discipline skill
#   mcp-binding-block          → web/claude.ai connector-shaped; cursor binds vortex natively (GATES §1 one-liner)
#   mcp-server-primary-block   → cursor sees tools/list natively via the IDE
# rag-scope-awareness-block has NO resident rule and NO backing skill → KEPT.
_ORIENTATION_BLOCKS_CURSOR: frozenset[str] = frozenset(
    {
        "rag-scope-awareness-block",
    }
)

# Channel 3 — operational-context sections (``render_operational_context``).
OPCONTEXT_SECTION_SKILL_MAP: dict[str, tuple[str, ...]] = {
    "ulg-for-llms": ("ulg-for-llms",),
    "reasoning-posture": ("reasoning-posture",),
    "prose-discipline": ("prose-discipline",),
    "team-consultation": ("consult-routing",),
}

_OPCONTEXT_SUBAGENT_EXCLUDED: frozenset[str] = frozenset(
    {"ulg-for-llms", "reasoning-posture", "team-consultation"}
)


def _is_web_agent(agent: str | None) -> bool:
    return bool(agent and agent.endswith("-web"))


def _is_cursor_agent(agent: str | None) -> bool:
    return bool(agent and agent.endswith("-cursor"))


def _is_subagent_seat(family: str, platform: str) -> bool:
    return family == "subagent" and platform == "subagent"


def orientation_block_keys_for_agent(agent: str | None) -> frozenset[str]:
    """Per-seat orientation block selection SOT (render + web skill-injection).

    One authoritative selector — the renderer emits exactly these keys, and the
    web skill-injection accounting derives its slugs from this set. Cursor gets
    the thinned resident-covered set; web gets full doctrine + web-only blocks;
    other platforms (api) get full doctrine without the web-only blocks.
    """
    if _is_cursor_agent(agent):
        return _ORIENTATION_BLOCKS_CURSOR
    keys = set(_ORIENTATION_CORE_BLOCKS)
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
        # Non-skill-backed render blocks (mcp-server-primary, rag-scope) carry no
        # inject slug — they render inline only, so they contribute nothing here.
        slugs.update(ORIENTATION_BLOCK_SKILL_MAP.get(key, ()))
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
