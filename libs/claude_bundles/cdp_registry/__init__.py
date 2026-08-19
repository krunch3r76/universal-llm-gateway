"""Explicit CDP Chrome-host registration (registry ports / profiles).

A registry host is a Chrome CDP port+profile row, not a bus thread or CSE
session. Persistence is cdp_registry_store; Event Service is observational.
"""
from __future__ import annotations

import importlib
from typing import Any

# CONSUMERS = import-nomination (GIW hop watch). INJECTORS = cdp_ask hygiene/drain.
CONSUMERS: tuple[str, ...] = ("git_integration_worker",)
INJECTORS: tuple[str, ...] = ("cdp_ask",)

_HELD_LOCKS: dict[str, int] = {}

_STORE_CONSTANTS = frozenset(
    {"REGISTRY_DIR", "REGISTRY_LOG", "ACTIVE_JSON", "PORTS_LOCK", "REGISTRATIONS_DIR"}
)

_SUBMODULE_ALIASES: dict[str, str] = {
    "_store": "claude_bundles.cdp_registry_store",
    "_events": "claude_bundles.cdp_registry_events",
    "cdp_lane": "claude_bundles.cdp_lane",
}

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    # driver_locks
    "_claim_driver_lock": (".driver_locks", "_claim_driver_lock"),
    "_release_driver_lock": (".driver_locks", "_release_driver_lock"),
    "is_driver_lock_held": (".driver_locks", "is_driver_lock_held"),
    "process_holds_driver_lock": (".driver_locks", "process_holds_driver_lock"),
    # hygiene
    "RECLAIM_TRASH_DIR": (".hygiene", "RECLAIM_TRASH_DIR"),
    "_profile_path_from_row": (".hygiene", "_profile_path_from_row"),
    "hygiene_reclaim_extended": (".hygiene", "hygiene_reclaim_extended"),
    "hygiene_reclaim_released": (".hygiene", "hygiene_reclaim_released"),
    "is_primary_profile": (".hygiene", "is_primary_profile"),
    "reclaim_best_effort": (".hygiene", "reclaim_best_effort"),
    # lifecycle
    "_kill_listener": (".lifecycle", "_kill_listener"),
    "activate_allocating_row": (".lifecycle", "activate_allocating_row"),
    "deregister_lane": (".lifecycle", "deregister_lane"),
    "reattach": (".lifecycle", "reattach"),
    "register_lane": (".lifecycle", "register_lane"),
    "ensure_driving_operator_seat": (".driving_seat", "ensure_driving_operator_seat"),
    "reserve_allocating_row": (".lifecycle", "reserve_allocating_row"),
    # models
    "DormantSeat": (".models", "DormantSeat"),
    "HygieneReclaimResult": (".models", "HygieneReclaimResult"),
    "Registration": (".models", "Registration"),
    "RegistryBusyError": (".models", "RegistryBusyError"),
    "RegistryError": (".models", "RegistryError"),
    "RegistryExhaustedError": (".models", "RegistryExhaustedError"),
    "STALE_ACTIVE_TTL_S": (".models", "STALE_ACTIVE_TTL_S"),
    "STATUS_DORMANT": (".models", "STATUS_DORMANT"),
    "_LISTABLE_STATUSES": (".models", "_LISTABLE_STATUSES"),
    "MISSION_KINDS": (".models", "MISSION_KINDS"),
    "_row_to_registration": (".models", "_row_to_registration"),
    "dormant_max_rows": (".models", "dormant_max_rows"),
    "dormant_ttl_s": (".models", "dormant_ttl_s"),
    # dormant
    "dormant_candidate_reason": (".dormant", "dormant_candidate_reason"),
    "dormant_for_chat_url": (".dormant", "dormant_for_chat_url"),
    "host_protection_reason": (".dormant", "host_protection_reason"),
    "list_dormant": (".dormant", "list_dormant"),
    "make_dormant": (".dormant", "make_dormant"),
    "reclaim_dormant_rows": (".dormant", "reclaim_dormant_rows"),
    "relaunch_dormant": (".dormant", "relaunch_dormant"),
    # dormant_drain
    "DrainResult": (".dormant_drain", "DrainResult"),
    "drain_live_hosts_to_dormant": (".dormant_drain", "drain_live_hosts_to_dormant"),
    "row_drain_protection": (".dormant_drain", "row_drain_protection"),
    # ports
    "PORT_RANGE": (".ports", "PORT_RANGE"),
    "select_free_registry_port": (".ports", "select_free_registry_port"),
    "used_ports_snapshot": (".ports", "used_ports_snapshot"),
    # session_address
    "_load_active": (".session_address", "_load_active"),
    "backfill_orphaned_retry_chat_urls": (
        ".session_address",
        "backfill_orphaned_retry_chat_urls",
    ),
    "bind_session_address": (".session_address", "bind_session_address"),
    "chat_url_for_registration": (".session_address", "chat_url_for_registration"),
    "count_capacity_lanes": (".session_address", "count_capacity_lanes"),
    "list_active": (".session_address", "list_active"),
    "list_capacity": (".session_address", "list_capacity"),
    "log_orphan_scan": (".session_address", "log_orphan_scan"),
}

__all__ = [
    *sorted(_LAZY_ATTRS),
    *sorted(_STORE_CONSTANTS),
    *sorted(_SUBMODULE_ALIASES),
    "CONSUMERS",
    "INJECTORS",
    "_HELD_LOCKS",
]


def __getattr__(name: str) -> Any:
    if name in _STORE_CONSTANTS:
        from claude_bundles import cdp_registry_store as store

        value = getattr(store, name)
        globals()[name] = value
        return value
    if name in _SUBMODULE_ALIASES:
        value = importlib.import_module(_SUBMODULE_ALIASES[name])
        globals()[name] = value
        return value
    if name in _LAZY_ATTRS:
        module_path, attr = _LAZY_ATTRS[name]
        if module_path.startswith("."):
            module_path = f"{__name__}{module_path}"
        value = getattr(importlib.import_module(module_path), attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
