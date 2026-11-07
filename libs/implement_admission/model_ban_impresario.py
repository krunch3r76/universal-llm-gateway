"""Impresario argon role bar via ``model_bans`` (ARGON_ROLE_BARRED wire)."""

from __future__ import annotations

from implement_admission.model_ban_load import (
    ModelBanEntry,
    advisor_wire_from_policy,
    ban_slug_variants,
    find_matching_ban,
    load_model_bans,
    normalize_model_for_ban,
)

_ARGON_CODE = "ARGON_ROLE_BARRED"


def impresario_argon_ban_roles(
    bans: tuple[ModelBanEntry, ...] | None = None,
) -> frozenset[str]:
    entries = load_model_bans() if bans is None else bans
    try:
        advisor_slug = normalize_model_for_ban(advisor_wire_from_policy())
    except (TypeError, ValueError):
        return frozenset()
    roles: set[str] = set()
    for entry in entries:
        if entry.match_slug == advisor_slug and not entry.scope_all:
            roles.update(entry.roles)
    return frozenset(roles)


def argon_role_barred(
    impresario_role: str | None, *, bans: tuple[ModelBanEntry, ...] | None = None
) -> bool:
    if not impresario_role:
        return False
    return impresario_role.strip().lower() in impresario_argon_ban_roles(bans=bans)


def impresario_argon_refusal(
    *,
    model: str,
    impresario_role: str | None,
    bans: tuple[ModelBanEntry, ...] | None = None,
) -> tuple[str, str] | None:
    wire = advisor_wire_from_policy()
    try:
        if ban_slug_variants(model) != ban_slug_variants(wire):
            return None
    except (TypeError, ValueError):
        return None
    if (impresario_role or "").strip().lower() == "advisor":
        return None
    entry = find_matching_ban(model, role=impresario_role, bans=bans)
    if entry is None:
        return None
    return (_ARGON_CODE, entry.reason)
