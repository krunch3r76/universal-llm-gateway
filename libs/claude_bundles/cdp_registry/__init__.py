"""Explicit CDP Chrome-host registration (registry ports / profiles).

A registry host is a Chrome CDP port+profile row, not a bus thread or CSE
session. Persistence is cdp_registry_store; Event Service is observational.
"""
# ruff: noqa: F401, I001
from claude_bundles import cdp_lane  # noqa: F401 — test attr reg.cdp_lane
from claude_bundles import cdp_registry_events as _events  # noqa: F401
from claude_bundles import cdp_registry_store as _store

from .driver_locks import (
    _claim_driver_lock, _release_driver_lock, is_driver_lock_held, process_holds_driver_lock,
)
from .hygiene import (
    RECLAIM_TRASH_DIR, _profile_path_from_row, hygiene_reclaim_extended,
    hygiene_reclaim_released, is_primary_profile, reclaim_best_effort,
)
from .lifecycle import (
    _kill_listener, activate_allocating_row, deregister_lane, reattach, register_lane,
    reserve_allocating_row,
)
from .models import (
    DormantSeat, HygieneReclaimResult, Registration, RegistryBusyError, RegistryError,
    RegistryExhaustedError, STALE_ACTIVE_TTL_S, STATUS_DORMANT, _LISTABLE_STATUSES,
    MISSION_KINDS, _row_to_registration, dormant_max_rows, dormant_ttl_s,
)
from .dormant import (
    dormant_candidate_reason, dormant_for_chat_url, host_protection_reason,
    list_dormant, make_dormant, reclaim_dormant_rows, relaunch_dormant,
)
from .dormant_drain import DrainResult, drain_live_hosts_to_dormant, row_drain_protection
from .ports import PORT_RANGE, select_free_registry_port, used_ports_snapshot
from .session_address import (
    _load_active, backfill_orphaned_retry_chat_urls,
    bind_session_address, chat_url_for_registration, count_capacity_lanes,
    list_active, list_capacity, log_orphan_scan,
)

REGISTRY_DIR = _store.REGISTRY_DIR
REGISTRY_LOG = _store.REGISTRY_LOG
ACTIVE_JSON = _store.ACTIVE_JSON
PORTS_LOCK = _store.PORTS_LOCK
REGISTRATIONS_DIR = _store.REGISTRATIONS_DIR
_HELD_LOCKS: dict[str, int] = {}
