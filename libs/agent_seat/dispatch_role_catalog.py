"""Op-scoped dispatch-role catalog — single SOT derived from config/agents.yaml.

Resolves each role's default ``(family, platform)`` to its ``CapabilityProfile``
and partitions the roster by dispatch op:

- ``generate_roles()`` — roles whose resolved profile is ``api_dispatchable``
  (valid for ``team_dispatch(op="generate"|"to_thread")`` via cloud/API path).
- ``handoff_roles()`` — roles whose resolved profile is ``manual_handoff``
  (valid for ``team_dispatch(op="handoff")`` only).

The render helpers produce the exact agent-facing clause strings embedded in
``config/mcp/canonical.yaml``, ``services/mcp-server/tools/frontier.py``,
``_oc_surface_templates.py``, and ``_orientation_blocks.py``. ``scripts/gen-mcp-dispatch-role-docs``
regenerates those clauses from here so docs stay in lockstep with the role roster.
"""

from __future__ import annotations

from .profiles import CapabilityProfile, get_profile, load_roles

# Roles documented as legacy in agent-facing docs. Currently empty — machinery
# retained so future legacy roles can render with a "(legacy)" suffix.
_LEGACY_ROLES: frozenset[str] = frozenset()


def is_legacy_role(role: str) -> bool:
    """True iff the role is documented as legacy (suffixed "(legacy)" in docs)."""
    return role in _LEGACY_ROLES


def _resolved_profile(role: str) -> CapabilityProfile:
    rp = load_roles()[role]
    return get_profile(rp.default_family, rp.default_platform)


def generate_roles() -> list[str]:
    """Roles valid on ``op=generate``/``to_thread`` (api_dispatchable profiles)."""
    return [r for r in load_roles() if _resolved_profile(r).api_dispatchable]


def handoff_roles() -> list[str]:
    """Roles valid on ``op=handoff`` only (manual_handoff profiles)."""
    return [r for r in load_roles() if _resolved_profile(r).manual_handoff]


def handoff_role_seat(role: str) -> str:
    """Resolved ``{family}-{platform}`` seat slug for a handoff role."""
    rp = load_roles()[role]
    return f"{rp.default_family}-{rp.default_platform}"


# ── Render helpers (canonical agent-facing clause strings) ────────────────────


def generate_slash_clause() -> str:
    """Slash-joined generate roster, e.g. ``reviewer/gatherer/synthesizer/...``."""
    return "/".join(generate_roles())


def generate_comma_clause() -> str:
    """Comma-joined generate roster, e.g. ``reviewer, gatherer, synthesizer, ...``."""
    return ", ".join(generate_roles())


def handoff_comma_clause() -> str:
    """Comma-joined handoff roster with legacy suffixes, e.g.
    ``web-consult, web-implement, cursor-consult, cursor-implement``."""
    return ", ".join(
        f"{r} (legacy)" if is_legacy_role(r) else r for r in handoff_roles()
    )


def handoff_seat_map_clause() -> str:
    """Seat-grouped handoff map, e.g.
    ``web-consult, web-implement → claude-web; cursor-consult, cursor-implement → claude-cursor``.

    Roles are grouped by resolved seat (first-seen order); a group is marked
    ``(legacy)`` when every role in it is legacy.
    """
    groups: list[tuple[str, list[str]]] = []
    seat_index: dict[str, int] = {}
    for role in handoff_roles():
        seat = handoff_role_seat(role)
        if seat not in seat_index:
            seat_index[seat] = len(groups)
            groups.append((seat, []))
        groups[seat_index[seat]][1].append(role)

    parts: list[str] = []
    for seat, roles in groups:
        clause = f"{', '.join(roles)} → {seat}"
        if all(is_legacy_role(r) for r in roles):
            clause += " (legacy)"
        parts.append(clause)
    return "; ".join(parts)


def admitted_handoff_roles() -> list[str]:
    """Handoff roles admission accepts (non-legacy). Advertised roster everywhere."""
    return [r for r in handoff_roles() if not is_legacy_role(r)]


def handoff_slash_clause() -> str:
    """Slash-joined admitted handoff roster, e.g. ``web-consult/web-implement/...``."""
    return "/".join(admitted_handoff_roles())


def handoff_bare_comma_clause() -> str:
    """Comma-joined admitted handoff roster, no legacy suffix (enums, shorthand lists)."""
    return ", ".join(admitted_handoff_roles())


def handoff_seats() -> list[str]:
    """Distinct resolved handoff seats, first-seen roster order."""
    seats: list[str] = []
    for role in handoff_roles():
        seat = handoff_role_seat(role)
        if seat not in seats:
            seats.append(seat)
    return seats
