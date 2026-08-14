"""Skill-introspection block for the operator-proxy mission briefing.

The leading ``/<slug>`` lines are a request, not a receipt — composer
``+`` → Skills attach can silently no-op. This block tells the seat to
test whether it actually holds each required body and close gaps via
``Use the `<slug>` skill`` (probe-confirmed fetch on web-anthropic).

Kept out of ``operator_proxy_mission`` so the seat-map briefing stays
under SLOC budget. Callers pass ``slugs`` so this module never imports
the mission module (no cycle) and the required list cannot drift from
``MISSION_SKILL_SLUGS``.
"""

from __future__ import annotations

# CONSUMERS = import-nomination (GIW). INJECTORS = seat paste (cdp_ask).
CONSUMERS: tuple[str, ...] = ("git_integration_worker",)
INJECTORS: tuple[str, ...] = ("cdp_ask",)

_CURSOR_ONLY_NOT_HERE: tuple[str, ...] = (
    "operator-proxy-substrate",
    "claude-ai-cdp-navigation",
    "path-sim",
)


def skill_introspection_block(slugs: tuple[str, ...]) -> str:
    """Return the BINDING skill-introspection briefing paragraph.

    ``slugs`` is the required attachable set (mission ``shared_sync`` chips).
    """
    required = ", ".join(f"`{s}`" for s in slugs if str(s).strip())
    carve = ", ".join(f"`{s}`" for s in _CURSOR_ONLY_NOT_HERE)
    return f"""\
**Skill surface (BINDING):** the leading `/<slug>` lines are a **request**, not
a receipt. Composer `+` → Skills attach can silently no-op; a chip you did not
get produces no error. Required on this seat: {required}.

**Introspect (functional, one pass at birth):** for each required slug, before
the first act it governs, confirm you hold its body — if you cannot state its
invariants, you do not have it. Gap: emit `Use the `<slug>` skill` (verb `use`
fetches; bare mention does not). Invocation fail → `/mnt/skills/<slug>/SKILL.md`.
Close the gap at the act, ¬ at closeout. Self-fetch only slugs **not** already
delivered inline (`web-skill-body-activation` dual-channel ban).

**Provenance:** "skills loaded" is a claim. `completion-provenance-discipline`
is itself one of these chips — do not assert a loaded set you did not verify;
state what you actually hold.

**Not loadable here:** {carve} are `cursor_only` — not attachable or
self-fetchable on this seat by any channel. Commission a cursor seat via
`agent_bus.request`; ¬ chase them. Split: `decision:operator-proxy-skill-surface-split`.
"""
