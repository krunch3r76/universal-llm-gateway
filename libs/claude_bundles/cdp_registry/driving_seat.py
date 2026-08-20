"""Seat-axis-first driving operator seat: relaunch a dormant open seat; mint only when none exists.

``list_active()`` uniqueness is a host-allocation guard, not the seat census.
Hop satellites (``mission_kind == hop``) never take the driving seat.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from claude_bundles.what_is_running_view import OPERATOR_PURPOSES

from .models import STATUS_DORMANT, Registration, RegistryError, seat_open

_LaunchFn = Callable[[int, Path], int]
_ListenFn = Callable[[int], bool]

_HOP_KIND = "hop"
_ROOT_KIND = "root"


def _is_driving_kind(mission_kind: str | None) -> bool:
    kind = str(mission_kind or _ROOT_KIND).strip().lower()
    return kind != _HOP_KIND


def _row_registration(row: dict[str, Any]) -> Registration:
    from .models import _row_to_registration

    return _row_to_registration(row)


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
    """Return the open driving-operator seat for *parent_thread*.

    Order: open seat (relaunch if dormant) → unbound dormant operator row on
    the lane (bind + relaunch) → existing listable host (bind only) → mint
    via ``register_lane``. Two live driving Chromes on the lane is a host
    collision (``RegistryError``), not silent pick-one.
    """
    from claude_bundles import cdp_registry
    from claude_bundles import cdp_registry_store as store

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
    url = (chat_url or "").strip() or None

    active = store.load_active()
    open_seats = [
        (rid, row)
        for rid, row in active.items()
        if isinstance(row, dict) and seat_open(row, parent)
    ]
    if len(open_seats) > 1:
        ids = ", ".join(sorted(rid for rid, _row in open_seats))
        raise RegistryError(
            f"ambiguous open driving seats on parent_thread={parent}: {ids}"
        )
    if len(open_seats) == 1:
        rid, row = open_seats[0]
        if row.get("status") == STATUS_DORMANT:
            return cdp_registry.relaunch_dormant(
                rid,
                holder=holder,
                launch_chrome=launch_chrome,
                is_listening=is_listening,
            )
        if url:
            cdp_registry.bind_session_address(rid, chat_url=url)
        else:
            cdp_registry.bind_driving_seat(rid)
        return _row_registration(store.load_active()[rid])

    dormant_unbound = [
        (rid, row)
        for rid, row in active.items()
        if isinstance(row, dict)
        and row.get("status") == STATUS_DORMANT
        and str(row.get("parent_thread") or "").strip() == parent
        and str(row.get("purpose") or "").strip() in OPERATOR_PURPOSES
        and _is_driving_kind(row.get("mission_kind"))
        and not seat_open(row)
    ]
    if dormant_unbound:
        rid, _row = dormant_unbound[0]
        cdp_registry.bind_driving_seat(rid)
        return cdp_registry.relaunch_dormant(
            rid,
            holder=holder,
            launch_chrome=launch_chrome,
            is_listening=is_listening,
        )

    live = [
        lane
        for lane in cdp_registry.list_active()
        if (lane.purpose or "").strip() in OPERATOR_PURPOSES
        and str(lane.parent_thread or "").strip() == parent
        and _is_driving_kind(lane.mission_kind)
    ]
    if len(live) > 1:
        ids = ", ".join(sorted(lane.registration_id for lane in live))
        raise RegistryError(
            f"ambiguous listable driving operator hosts on parent_thread={parent}: {ids}"
        )
    if len(live) == 1:
        found = live[0]
        cdp_registry.bind_driving_seat(found.registration_id)
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
    cdp_registry.bind_driving_seat(reg.registration_id)
    if url:
        cdp_registry.bind_session_address(reg.registration_id, chat_url=url)
    return _row_registration(store.load_active()[reg.registration_id])
