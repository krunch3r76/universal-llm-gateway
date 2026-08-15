"""Track reserved ports and select free ports for the CDP host registry pool during allocation."""

from __future__ import annotations

from typing import Any

from claude_bundles import cdp_registry_store as _store

from .models import _RESERVED_STATUSES, RegistryExhaustedError, _ListenFn
from .registry_module import registry_package

PORT_RANGE = range(9223, 9350)


def _used_ports(active: dict[str, dict[str, Any]]) -> set[int]:
    used: set[int] = set()
    for row in active.values():
        if row.get("status") in _RESERVED_STATUSES:
            port = row.get("port")
            if isinstance(port, int):
                used.add(port)
    return used


def used_ports_snapshot() -> set[int]:
    """Return registry-reserved ports so cdp_lane can exclude them during allocation."""
    return _used_ports(_store.load_active())


def _used_suffixes(active: dict[str, dict[str, Any]]) -> set[str]:
    return {
        str(row["profile_suffix"])
        for row in active.values()
        if row.get("profile_suffix")
    }


def select_free_registry_port(
    is_listening: _ListenFn,
    *,
    exclude: set[int],
    port_range: range | None = None,
) -> int:
    """Return the first port in *port_range* that is not excluded and not listening."""
    port_range = registry_package().PORT_RANGE if port_range is None else port_range
    for port in port_range:
        if port in exclude:
            continue
        if not is_listening(port):
            return port
    raise RegistryExhaustedError(
        f"no free CDP port in {port_range.start}-{port_range.stop - 1} "
        f"(active+released+allocating excluded; run hygiene to reclaim released)"
    )
