"""Impresario argon role bar via ``model_bans`` (ARGON_ROLE_BARRED wire)."""

from __future__ import annotations

from implement_admission.model_ban_load import (
    ModelBanEntry,
    find_matching_ban,
    load_model_bans,
    normalize_model_for_ban,
)

_ARGON_CODE = "ARGON_ROLE_BARRED"


def impresario_argon_ban_roles(
    bans: tuple[ModelBanEntry, ...] | None = None,
) -> frozenset[str]:
    from implement_admission.impresario_policy import ADVISOR_MODEL

    entries = load_model_bans() if bans is None else bans
    try:
        advisor_slug = normalize_model_for_ban(ADVISOR_MODEL)
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
    from implement_admission.impresario_policy import ADVISOR_MODEL

    normalized = (model or "").strip()
    if normalized != ADVISOR_MODEL:
        return None
    if (impresario_role or "").strip().lower() == "advisor":
        return None
    if find_matching_ban(normalized, role=impresario_role, bans=bans) is None:
        return None
    return (
        _ARGON_CODE,
        f"cursor/gemini-4-argon is barred for impresario role {impresario_role!r}",
    )
