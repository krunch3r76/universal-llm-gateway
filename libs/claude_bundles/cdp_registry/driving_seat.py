"""Ensure a watched lane has exactly one listable driving-operator registry row.

Census identity for request admission is listable operator-purpose rows on
``parent_thread``. Hop ``register_lane`` births go dormant when Chrome
releases; bus ``TYPE: SEAT_REGISTRATION`` is a projection of an already
observed row and never creates one. The driving operator CSE (the Cowork/life
window that actually holds the lane) must therefore call ``register_lane``
with ``purpose=operator-proxy``, ``mission_kind=root``, and the lane as
``parent_thread``.

This module is that birth+reuse: mint once, reuse while listable, never
promote dormant hops into listable, never pick-one when two listable
driving rows already exist.

Listable still means a Chrome process may hold the CSE
(``active`` / ``orphaned_alive`` / ``retained``). Dormant stays excluded.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from claude_bundles.what_is_running_view import OPERATOR_PURPOSES

from .models import Registration, RegistryError

_LaunchFn = Callable[[int, Path], int]
_ListenFn = Callable[[int], bool]

_HOP_KIND = "hop"
_ROOT_KIND = "root"


def _is_driving_kind(mission_kind: str | None) -> bool:
    """True when the row is a driving operator host, not a hop satellite."""
    kind = str(mission_kind or _ROOT_KIND).strip().lower()
    return kind != _HOP_KIND


def ensure_driving_operator_seat(
    *,
    holder: str,
    parent_thread: str,
    purpose: str = "operator-proxy",
    mission_kind: str = _ROOT_KIND,
    chat_url: str | None = None,
    launch: bool = True,
    launch_chrome: _LaunchFn | None = None,
    is_listening: _ListenFn | None = None,
) -> Registration:
    """Return the single listable driving-operator row for *parent_thread*.

    Reuses an existing listable operator-purpose non-hop row bound to the
    lane. Mints via ``register_lane`` (emits ``cdp.port.registered``) when
    none exists. Raises ``RegistryError`` when two listable driving rows
    already share the lane — census must see N=2, not a silent pick-one.

    Hop satellites on the same ``parent_thread`` are not reused: they go
    dormant when Chrome releases, which is the hole this helper closes.
    """
    from claude_bundles import cdp_registry

    parent = str(parent_thread or "").strip()
    if not parent:
        raise RegistryError("parent_thread is required for a driving operator seat")
    purpose_norm = str(purpose or "operator-proxy").strip() or "operator-proxy"
    if purpose_norm not in OPERATOR_PURPOSES:
        raise RegistryError(
            f"driving operator purpose must be one of {sorted(OPERATOR_PURPOSES)}; "
            f"got {purpose_norm!r}"
        )
    kind = str(mission_kind or _ROOT_KIND).strip().lower() or _ROOT_KIND
    if kind == _HOP_KIND:
        raise RegistryError("driving operator seat cannot be mission_kind=hop")

    matches = [
        lane
        for lane in cdp_registry.list_active()
        if (lane.purpose or "").strip() in OPERATOR_PURPOSES
        and str(lane.parent_thread or "").strip() == parent
        and _is_driving_kind(lane.mission_kind)
    ]
    if len(matches) > 1:
        ids = ", ".join(sorted(lane.registration_id for lane in matches))
        raise RegistryError(
            f"ambiguous listable driving operator seats on parent_thread={parent}: {ids}"
        )
    if len(matches) == 1:
        found = matches[0]
        url = (chat_url or "").strip()
        if url:
            cdp_registry.bind_session_address(found.registration_id, chat_url=url)
        return found

    reg = cdp_registry.register_lane(
        holder=holder,
        purpose=purpose_norm,
        mission_kind=kind,
        parent_thread=parent,
        launch=launch,
        launch_chrome=launch_chrome,
        is_listening=is_listening,
    )
    url = (chat_url or "").strip()
    if url:
        cdp_registry.bind_session_address(reg.registration_id, chat_url=url)
    return reg
