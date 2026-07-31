"""GIW/substrate probe for recoverable CapStore stop auto-clear (6486 Path B #2).

Recoverable stops (``admission_rejected``, ``admission_transport_error``) may
auto-clear once per root episode when substrate probes healthy — see
``CapStore.try_auto_clear_recoverable_stop``. This module owns the healthy probe
only; it does not mutate CapStore.
"""

from __future__ import annotations

from .giw_live_hold import _lease_is_held, fetch_giw_active_work_payload

RECOVERABLE_STOP_REASONS = frozenset(
    {"admission_rejected", "admission_transport_error"}
)


async def substrate_healthy_for_cap_clear() -> bool:
    """True when GIW is reachable, not draining, and no write lease is held.

    Reuses the same substrate signals as ``preflight_write_lease`` /
    ``gate_admission_defer`` — no new HTTP surfaces.
    """
    from ..restart_intent_store import RestartIntentStore

    intent = RestartIntentStore.instance().active_for_service("git_integration_worker")
    if intent is not None:
        return False

    payload = await fetch_giw_active_work_payload()
    if payload is None:
        return False

    if _lease_is_held(payload):
        return False

    return True


__all__ = ["RECOVERABLE_STOP_REASONS", "substrate_healthy_for_cap_clear"]
