"""Pure planner + apply for boot lane re-adoption (arc 7119).

Lane re-adoption preserves Chrome host reservation across a ``cdp_ask`` process
boundary; it does not resurrect in-memory ``ExecutionRecord`` rows.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from claude_bundles import cdp_lane, cdp_orphans, cdp_registry
from claude_bundles.cdp_orphans import LivePort, is_primary_profile
from claude_bundles.cse_url import normalize_cse_url

# File-level serving map — package CONSUMERS on cdp_registry/__init__ do not
# cover this sibling module (unmapped_serving otherwise).
CONSUMERS: tuple[str, ...] = ("cdp_ask",)
INJECTORS: tuple[str, ...] = ("cdp_ask",)

CseAffinity = Literal["bound_present", "bound_missing", "unbound_present", "none"]
BootVerdict = Literal["adopt", "orphan", "refuse"]

BOOT_HOLDER = "cdp-ask-satellite"
ADOPT_STATUS = "retained"
_REACHABLE_CSE_AFFINITIES = frozenset(
    {"bound_present", "unbound_present", "bound_missing"}
)
_CSE_URL_MARKER = "claude.ai/cowork/cse_"
_REG_PROFILE_PREFIX = "claude-ai-chrome-profile-reg-"


class HostObservation(StrEnum):
    """How a registry row's Chrome host looks to the boot planner.

    Distinguishes a matching live port from TCP-only, profile mismatch,
    dead, and invalid-port cases so adopt/orphan verdicts stay explicit.
    """

    LIVE_OK = "live_ok"
    LIVE_PROFILE_MISMATCH = "live_profile_mismatch"
    TCP_ONLY = "tcp_only"
    DEAD = "dead"
    INVALID_PORT = "invalid_port"


@dataclass(frozen=True)
class BootReadoptionPlan:
    would_adopt: tuple[dict[str, Any], ...]
    would_orphan: tuple[dict[str, Any], ...]
    would_refuse: tuple[dict[str, Any], ...]
    inputs_digest: dict[str, int] = field(default_factory=dict)


def _is_gateway_reg_profile(profile: Path | None) -> bool:
    if profile is None:
        return False
    return profile.name.startswith(_REG_PROFILE_PREFIX)


def _wake_debt_check(
    registration_id: str,
    wake_debt: Callable[[str], bool] | set[str] | frozenset[str],
) -> bool:
    if callable(wake_debt):
        return bool(wake_debt(registration_id))
    return registration_id in wake_debt


def _profile_path_from_row(row: Mapping[str, Any]) -> Path | None:
    return cdp_registry._profile_path_from_row(dict(row))


def _observe_host(
    row: Mapping[str, Any],
    live_by_port: Mapping[int, LivePort],
    *,
    is_listening: Callable[[int], bool],
) -> HostObservation:
    port = row.get("port")
    if not isinstance(port, int) or port <= 0:
        return HostObservation.INVALID_PORT

    live = live_by_port.get(port)
    if live is not None:
        expected = _profile_path_from_row(row)
        if live.profile is not None and expected is not None:
            if live.profile.resolve() != expected.resolve():
                return HostObservation.LIVE_PROFILE_MISMATCH
        return HostObservation.LIVE_OK

    if is_listening(port):
        return HostObservation.TCP_ONLY
    return HostObservation.DEAD


def _cse_affinity(row: Mapping[str, Any], live: LivePort | None) -> CseAffinity:
    if live is None:
        return "none"
    normalized_pages = {
        normalize_cse_url(url)
        for url in live.page_urls
        if _CSE_URL_MARKER in url
    }
    chat_url = str(row.get("chat_url") or "").strip()
    if chat_url:
        norm = normalize_cse_url(chat_url)
        if norm and norm in normalized_pages:
            return "bound_present"
        if live.has_live_cse or normalized_pages:
            return "bound_missing"
        return "none"
    if live.has_live_cse or normalized_pages:
        return "unbound_present"
    return "none"


def _verdict_for_row(
    registration_id: str,
    row: Mapping[str, Any],
    *,
    running_registration_ids: set[str],
    wake_debt: Callable[[str], bool] | set[str] | frozenset[str],
    observation: HostObservation,
    live: LivePort | None,
) -> tuple[BootVerdict, str]:
    status = str(row.get("status") or "")
    in_memory = registration_id in running_registration_ids
    has_debt = _wake_debt_check(registration_id, wake_debt)

    if status == "active":
        if in_memory:
            return "refuse", "already_live_execution"
        if observation is HostObservation.LIVE_OK:
            return "adopt", "live_host_match"
        if observation is HostObservation.TCP_ONLY:
            return "orphan", "cdp_unresponsive"
        if observation is HostObservation.LIVE_PROFILE_MISMATCH:
            return "orphan", "profile_mismatch"
        if observation is HostObservation.INVALID_PORT:
            return "orphan", "invalid_port"
        if has_debt:
            return "refuse", "wake_debt_no_host"
        return "orphan", "no_live_host"

    if status == "orphaned_alive":
        if in_memory:
            return "refuse", "already_live_execution"
        if observation is HostObservation.LIVE_OK:
            return "adopt", "reverse_false_demote"
        return "refuse", "orphan_no_live_match"

    if status in {"retained", "released", "orphaned_retry"}:
        return "refuse", f"status_{status}"

    if status == "allocating":
        if has_debt:
            return "refuse", "allocating_wake_debt"
        return "orphan", "incomplete_registration"

    return "refuse", f"status_{status or 'unknown'}"


def _plan_entry(
    *,
    verdict: BootVerdict,
    registration_id: str | None,
    row: Mapping[str, Any] | None,
    live: LivePort | None,
    reason: str,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"reason": reason}
    if registration_id is not None:
        entry["registration_id"] = registration_id
    port = row.get("port") if row is not None else (live.port if live is not None else None)
    if isinstance(port, int):
        entry["port"] = port
    if row is not None:
        entry["prior_status"] = row.get("status")
        if verdict == "adopt":
            entry["cse_affinity"] = _cse_affinity(row, live)
    return entry


def plan_boot_lane_readoption(
    active_rows: Mapping[str, Mapping[str, Any]],
    live_ports: Sequence[LivePort],
    *,
    running_registration_ids: set[str] | frozenset[str],
    wake_debt: Callable[[str], bool] | set[str] | frozenset[str],
    is_listening: Callable[[int], bool] | None = None,
) -> BootReadoptionPlan:
    """Classify registry rows for boot lane re-adoption without mutating state."""
    listen = is_listening or cdp_lane.is_listening
    live_by_port = {live.port: live for live in live_ports}

    would_adopt: list[dict[str, Any]] = []
    would_orphan: list[dict[str, Any]] = []
    would_refuse: list[dict[str, Any]] = []

    wake_debt_hits = 0
    for registration_id, row in active_rows.items():
        if _wake_debt_check(registration_id, wake_debt):
            wake_debt_hits += 1
        observation = _observe_host(row, live_by_port, is_listening=listen)
        live = live_by_port.get(row.get("port")) if isinstance(row.get("port"), int) else None
        verdict, reason = _verdict_for_row(
            registration_id,
            row,
            running_registration_ids=set(running_registration_ids),
            wake_debt=wake_debt,
            observation=observation,
            live=live,
        )
        entry = _plan_entry(
            verdict=verdict,
            registration_id=registration_id,
            row=row,
            live=live,
            reason=reason,
        )
        if verdict == "adopt":
            would_adopt.append(entry)
        elif verdict == "orphan":
            would_orphan.append(entry)
        else:
            would_refuse.append(entry)

    registered_ports = {
        int(row["port"])
        for row in active_rows.values()
        if isinstance(row.get("port"), int)
    }
    for live in live_ports:
        if live.port in registered_ports:
            continue
        if live.profile is None:
            would_refuse.append(
                _plan_entry(
                    verdict="refuse",
                    registration_id=None,
                    row=None,
                    live=live,
                    reason="profile_unresolved",
                )
            )
            continue
        if is_primary_profile(live.profile):
            would_refuse.append(
                _plan_entry(
                    verdict="refuse",
                    registration_id=None,
                    row=None,
                    live=live,
                    reason="primary_profile",
                )
            )
            continue
        if _is_gateway_reg_profile(live.profile) and live.has_live_cse:
            would_refuse.append(
                _plan_entry(
                    verdict="refuse",
                    registration_id=None,
                    row=None,
                    live=live,
                    reason="unregistered_orphan_port",
                )
            )

    digest = {
        "active_rows": len(active_rows),
        "live_ports": len(live_ports),
        "running_ids": len(running_registration_ids),
        "wake_debt_hits": wake_debt_hits,
    }
    return BootReadoptionPlan(
        would_adopt=tuple(would_adopt),
        would_orphan=tuple(would_orphan),
        would_refuse=tuple(would_refuse),
        inputs_digest=digest,
    )


def _adopted_status(row: Mapping[str, Any], cse_affinity: CseAffinity) -> str:
    """Choose boot-adopt status without restoring a driver lock.

    Operator-proxy / mission / blank-purpose hosts with a reachable CSE page
    keep ``active`` so a ``cdp_ask`` restart does not demote a live seat into
    the drainable set. Ask hosts and hosts with no CSE stay ``retained`` —
    drain parking and leaked-Chrome release depend on that demotion.
    ``active`` here is reservation, not a claimed driver: the lock stays off.
    """
    from claude_bundles.cdp_registry.dormant_drain import _idle_reachable_protects

    if cse_affinity in _REACHABLE_CSE_AFFINITIES and _idle_reachable_protects(
        dict(row)
    ):
        return "active"
    return ADOPT_STATUS


def boot_adopt_lane(
    registration_id: str,
    *,
    prior_status: str | None,
    cse_affinity: CseAffinity,
    holder: str | None = None,
) -> None:
    """Reserve a surviving host after process death (boot-only).

    Adoption keeps the port and profile reserved. The turn that owned the row
    died with the previous process — the planner already refuses rows with a
    live execution. Restoring ``active`` *plus* a driver lock claimed a busy
    seat nobody was driving, which no drain could reclaim. This path never
    restores the lock. Operator seats with a reachable CSE keep ``active``
    (not drainable); ask hosts and CSE-less rows become ``retained`` so the
    dormant drain can still park or release them.
    """
    from claude_bundles import cdp_registry_store as _store

    adopt_holder = (holder or BOOT_HOLDER).strip()
    if not adopt_holder:
        raise cdp_registry.RegistryError("holder is required for boot adopt")

    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(registration_id)
        if row is None:
            raise cdp_registry.RegistryError(
                f"unknown registration_id: {registration_id!r}"
            )
        adopted = _adopted_status(row, cse_affinity)
        updated = dict(row)
        updated["status"] = adopted
        updated["holder"] = adopt_holder
        updated["holder_pid"] = os.getpid()
        updated.pop("orphaned_at", None)
        updated.pop("orphan_reason", None)
        active[registration_id] = updated
        _store.write_active(active)
        _store.append_log(
            "boot_lane_readoption",
            {
                "registration_id": registration_id,
                "port": updated.get("port"),
                "cse_affinity": cse_affinity,
                "prior_status": prior_status,
                "adopted_status": adopted,
                "reason": "boot_lane_readoption",
            },
        )


def apply_boot_readoption_plan(
    plan: BootReadoptionPlan,
    *,
    adopt_fn: Callable[..., None] | None = None,
    orphan_fn: Callable[[str], None] | None = None,
) -> tuple[list[str], list[str]]:
    """Apply adopt/orphan actions from *plan*; refuse entries are no-ops."""
    adopted: list[str] = []
    orphaned: list[str] = []
    do_adopt = adopt_fn or (
        lambda rid, **kw: boot_adopt_lane(
            rid,
            prior_status=kw.get("prior_status"),
            cse_affinity=kw.get("cse_affinity", "none"),
        )
    )
    do_orphan = orphan_fn or (
        lambda rid: cdp_registry.deregister_lane(rid, reason="probe_failed")
    )

    for item in plan.would_adopt:
        rid = str(item["registration_id"])
        do_adopt(
            rid,
            prior_status=item.get("prior_status"),
            cse_affinity=item.get("cse_affinity", "none"),
        )
        adopted.append(rid)

    for item in plan.would_orphan:
        rid = str(item["registration_id"])
        do_orphan(rid)
        orphaned.append(rid)

    return adopted, orphaned


def gather_rehearsal_inputs(
    *,
    assume_empty_store: bool = True,
) -> tuple[dict[str, dict[str, Any]], list[LivePort], set[str], Callable[[str], bool]]:
    """Read live registry + probe inputs for rehearsal (read-only)."""
    from claude_bundles.cse_wake_retain import registration_has_wake_debt

    active_rows = cdp_registry._load_active()
    live_ports = cdp_orphans.probe_live_ports()
    running_ids: set[str] = set() if assume_empty_store else set()
    return active_rows, live_ports, running_ids, registration_has_wake_debt


def rehearsal_boot_readoption_plan(
    *,
    assume_empty_store: bool = True,
    is_listening: Callable[[int], bool] | None = None,
) -> BootReadoptionPlan:
    """Dry-run planner against live inputs; performs zero registry mutations."""
    active_rows, live_ports, running_ids, wake_debt = gather_rehearsal_inputs(
        assume_empty_store=assume_empty_store
    )
    return plan_boot_lane_readoption(
        active_rows,
        live_ports,
        running_registration_ids=running_ids,
        wake_debt=wake_debt,
        is_listening=is_listening,
    )


def plan_as_dict(plan: BootReadoptionPlan) -> dict[str, Any]:
    """Render a boot-readoption plan as JSON-ready adopt/orphan/refuse lists."""
    return {
        "would_adopt": list(plan.would_adopt),
        "would_orphan": list(plan.would_orphan),
        "would_refuse": list(plan.would_refuse),
        "inputs_digest": dict(plan.inputs_digest),
    }


__all__ = [
    "ADOPT_STATUS",
    "BOOT_HOLDER",
    "BootReadoptionPlan",
    "apply_boot_readoption_plan",
    "boot_adopt_lane",
    "gather_rehearsal_inputs",
    "plan_as_dict",
    "plan_boot_lane_readoption",
    "rehearsal_boot_readoption_plan",
]
