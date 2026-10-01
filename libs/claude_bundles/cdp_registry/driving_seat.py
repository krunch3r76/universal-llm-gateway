"""Seat-axis-first driving operator seat: relaunch a dormant open seat; mint only when none exists.

``list_active()`` uniqueness is a host-allocation guard, not the seat census.
The driving seat is the attachment that most recently proved a turn on the lane,
regardless of ``mission_kind`` (hops bind via ``register_lane``, not this entry).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from claude_bundles import cdp_registry_events as _events
from claude_bundles.what_is_running_view import OPERATOR_PURPOSES

from .models import (
    _HOST_LISTABLE_STATUSES,
    STATUS_DORMANT,
    Registration,
    RegistryError,
    SeatContended,
    seat_open,
)

_LaunchFn = Callable[[int, Path], int]
_ListenFn = Callable[[int], bool]

_HOP_KIND = "hop"
_ROOT_KIND = "root"


def _is_driving_kind(mission_kind: str | None) -> bool:
    """Host-listable driving rows. Hop Chromes are a separate mint, not a reuse target."""
    return str(mission_kind or "").strip().lower() != "hop"


def _row_registration(row: dict[str, Any]) -> Registration:
    from .models import _row_to_registration

    return _row_to_registration(row)


def driving_lane_census(
    active: Mapping[str, Any], parent: str
) -> dict[str, list[dict[str, Any]]]:
    """Partition driving-operator rows for *parent* from *active* alone.

    Pure: the mapping and the parent string are the only inputs. Does not
    take the ports lock, list hosts, probe sockets, or read SSH.
    ``allocating`` membership uses ``parent_thread`` only and ignores
    ``seat_lane``.
    """
    parent_norm = str(parent or "").strip()
    live: list[dict[str, Any]] = []
    open_seats: list[dict[str, Any]] = []
    dormant_unbound: list[dict[str, Any]] = []
    allocating: list[dict[str, Any]] = []
    for row in active.values():
        if not isinstance(row, dict):
            continue
        if seat_open(row, parent_norm):
            open_seats.append(row)
        purpose = str(row.get("purpose") or "").strip()
        same_parent = str(row.get("parent_thread") or "").strip() == parent_norm
        driving = _is_driving_kind(row.get("mission_kind"))
        operator = purpose in OPERATOR_PURPOSES
        status = row.get("status")
        if (
            status in _HOST_LISTABLE_STATUSES
            and operator
            and same_parent
            and driving
            and row.get("seat_closed_at") is None
        ):
            live.append(row)
        if (
            status == STATUS_DORMANT
            and same_parent
            and operator
            and driving
            and not seat_open(row)
        ):
            dormant_unbound.append(row)
        if status == "allocating" and same_parent and operator and driving:
            allocating.append(row)
    return {
        "live": live,
        "open_seats": open_seats,
        "dormant_unbound": dormant_unbound,
        "allocating": allocating,
    }


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
    joined: list | None = None,
) -> Registration:
    """Return the open driving-operator seat for *parent_thread*.

    When more than one listable host is already on the lane, keep the
    newest ``seat_bound_at`` and retire the others (``seat_closed_at``)
    so the census collapses to one successor. Rows that already have
    ``seat_closed_at`` are not candidates: handing one to
    ``retire_predecessor_identity`` would reopen it and close the hop
    successor. Then seat-axis: open seat (relaunch if dormant) → unbound
    dormant operator row (bind + relaunch) → existing single listable
    host (bind only) → mint via ``register_lane``.
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

    def _census_and_branch() -> tuple[Registration, str]:
        active = store.load_active()
        census = driving_lane_census(active, parent)
        live = list(census["live"])
        if len(live) > 1:
            active_now = store.load_active()
            winner = max(
                live,
                key=lambda row: float(
                    (active_now.get(str(row.get("registration_id") or "")) or {}).get(
                        "seat_bound_at"
                    )
                    or 0.0
                ),
            )
            from claude_bundles.cdp_registry.session_address import (
                retire_predecessor_identity,
            )

            retire_predecessor_identity(
                str(winner["registration_id"]), parent_thread=parent
            )
            live = [winner]

        active = store.load_active()
        census = driving_lane_census(active, parent)
        open_seats = census["open_seats"]
        if len(open_seats) > 1:
            winner_id = str(
                max(
                    open_seats,
                    key=lambda row: float(row.get("seat_bound_at") or 0.0),
                )["registration_id"]
            )
            cdp_registry.bind_driving_seat(winner_id)
            active = store.load_active()
            census = driving_lane_census(active, parent)
            open_seats = census["open_seats"]
        if len(open_seats) == 1:
            row = open_seats[0]
            rid = str(row["registration_id"])
            if row.get("status") == STATUS_DORMANT:
                reg = cdp_registry.relaunch_dormant(
                    rid,
                    holder=holder,
                    launch_chrome=launch_chrome,
                    is_listening=is_listening,
                )
                return reg, "open_seat_relaunch"
            if url:
                cdp_registry.bind_session_address(rid, chat_url=url)
            else:
                cdp_registry.bind_driving_seat(rid)
            return _row_registration(store.load_active()[rid]), "open_seat_bind"

        dormant_unbound = census["dormant_unbound"]
        if dormant_unbound:
            rid = str(dormant_unbound[0]["registration_id"])
            cdp_registry.bind_driving_seat(rid)
            reg = cdp_registry.relaunch_dormant(
                rid,
                holder=holder,
                launch_chrome=launch_chrome,
                is_listening=is_listening,
            )
            return reg, "dormant_unbound_relaunch"

        if len(live) == 1:
            found = live[0]
            found_id = str(found["registration_id"])
            cdp_registry.bind_driving_seat(found_id)
            if url:
                cdp_registry.bind_session_address(found_id, chat_url=url)
            return _row_registration(found), "live_bind"

        joined_len_before = len(joined) if joined is not None else 0
        reg = cdp_registry.register_lane(
            holder=holder,
            purpose=purpose_norm,
            mission_kind=kind,
            parent_thread=parent,
            launch=launch,
            launch_chrome=launch_chrome,
            is_listening=is_listening,
            joined=joined,
        )
        fresh = store.load_active().get(reg.registration_id) or {}
        if joined or fresh.get("status") == "allocating":
            branch = (
                "register_lane_join"
                if joined is not None and len(joined) > joined_len_before
                else "register_lane_mint"
            )
            return reg, branch
        cdp_registry.bind_driving_seat(reg.registration_id)
        if url:
            cdp_registry.bind_session_address(reg.registration_id, chat_url=url)
        return (
            _row_registration(store.load_active()[reg.registration_id]),
            "register_lane_mint",
        )

    contended: SeatContended | None = None
    for attempt in range(2):
        try:
            reg, branch = _census_and_branch()
        except SeatContended as exc:
            if not exc.retryable or attempt == 1:
                raise
            contended = exc
            continue
        if contended is not None:
            depth = str(contended.data.get("depth") or "")
            with contextlib.suppress(Exception):
                _events.emit(
                    _events.cdp_seat_recensus_joined(
                        parent_thread=parent,
                        depth=depth,
                        contended_registration_id=str(
                            contended.data.get("registration_id") or ""
                        ),
                        observed_status=(
                            contended.data.get("observed_status")
                            if isinstance(
                                contended.data.get("observed_status"), (str, type(None))
                            )
                            else str(contended.data.get("observed_status"))
                        ),
                        registration_id=reg.registration_id,
                        branch=branch,
                    )
                )
        return reg
    raise AssertionError("unreachable recensus loop")
