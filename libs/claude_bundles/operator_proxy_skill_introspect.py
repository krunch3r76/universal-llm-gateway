"""Skill-introspection block for the operator-proxy mission briefing.

The leading ``/<slug>`` lines are a request, not a receipt — composer
``+`` → Skills attach can silently no-op. This block tells the seat to
test whether it actually holds each required body and close gaps via
``Use the `<slug>` skill`` (probe-confirmed fetch on web-anthropic).

A Skill-tool ``download failed`` on a required slug is not ``not_found``.
This briefing owns the mis-attribution class a booting CDP seat can act
on; it does not own the container writer's remaining-work fact.

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

# Cowork Customize mirror (hop-readable). Distinct from /mnt/skills examples.
_COWORK_SYNCED_SKILL = "/root/.claude/skills/synced/<slug>/SKILL.md"
_MNT_SKILLS_FALLBACK = "/mnt/skills/<slug>/SKILL.md"


def skill_introspection_block(slugs: tuple[str, ...]) -> str:
    """Return the BINDING skill-introspection briefing paragraph.

    ``slugs`` is the required attachable set (mission ``shared_sync`` chips).
    The block also classifies Skill-tool download-failed on those slugs as
    ``not_yet_synced`` rather than permanent ``not_found``, and names the
    writer-die residual where both predicates are true.
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
fetches; bare mention does not). Invocation fail → `{_MNT_SKILLS_FALLBACK}`
(examples/public mount only — not the Customize mirror). Close the gap at the
act, ¬ at closeout. Self-fetch only slugs **not** already delivered inline
(`web-skill-body-activation` dual-channel ban).

**Mid-flight honesty (BINDING):** Skill-tool `could not be downloaded (download failed). Proceed without it.` on a required/advertised slug is **not** `not_found`. Classify before acting:
- required/advertised AND body absent → `not_yet_synced` (writer may still have remaining work). Retry `Use the `<slug>` skill` once; if still miss, proceed degraded and **name the gap**. Snapshot greens (well-formed manifest, `lastUpdated` set, empty `.staging`) are not "sync completed."
- slug not advertised this generation AND body absent → `not_found`. Do not chase.
- writer-die / no further generation: `not_yet_synced` and `not_found` are both true. Last consistent snapshot is observationally done. Proceed degraded; do not invent a remaining-work bit this briefing cannot see.
Cowork synced bodies (when that tree is readable) live at `{_COWORK_SYNCED_SKILL}`. A miss on `{_MNT_SKILLS_FALLBACK}` does not prove the slug is absent from the synced mirror.

**Provenance:** "skills loaded" is a claim. `completion-provenance-discipline`
is itself one of these chips — do not assert a loaded set you did not verify;
state what you actually hold.

**Not loadable here:** {carve} are `cursor_only` — not attachable or
self-fetchable on this seat by any channel. Commission a cursor seat via
`agent_bus.request`; ¬ chase them. Split: `decision:operator-proxy-skill-surface-split`.
"""
