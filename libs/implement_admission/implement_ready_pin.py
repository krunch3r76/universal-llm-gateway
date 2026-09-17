"""Readiness-row resolution for implement admission: requested id, pin, predicate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from implement_admission.implement_ready import assertion_active
from implement_admission.implement_ready_gate_resolve import (
    ImplementReadyCortex,
    coerce_assertion_id,
    implement_ready_predicate,
    pin_needs_resolution,
    resolve_fresh_implement_ready,
)

REQUESTED = "requested"
PINNED = "pinned"
RESOLVED_PREDICATE = "resolved_predicate"
UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class ImplementReadyPin:
    """Which readiness row admission evaluated, and on what basis it was chosen.

    ``basis`` is the provenance of ``assertion_id``: a caller-supplied
    ``requested`` id that validated, the entity's own ``pinned`` attribute, a
    ``resolved_predicate`` row found by predicate scan when the pin was stale,
    or ``unresolved`` when no active readiness row exists. A bare id without
    its basis cannot tell a caller whether the pin was honoured or bypassed.
    """

    assertion_id: int | None
    assertion: dict[str, Any] | None
    basis: str
    rejected_requested_id: int | None = None


def _load(cortex: ImplementReadyCortex, assertion_id: int) -> dict[str, Any] | None:
    loaded = cortex.assertion_get(assertion_id)
    if isinstance(loaded, dict) and "error" not in loaded:
        return loaded
    return None


def _requested_usable(
    assertion: dict[str, Any] | None,
    *,
    todo_id: str,
    now_iso: str,
) -> bool:
    """A requested id is honoured only when confirmed, active, and on-entity."""
    if assertion is None:
        return False
    if assertion.get("entity_id") != todo_id:
        return False
    if str(assertion.get("confidence") or "") != "confirmed":
        return False
    return assertion_active(assertion, now_iso=now_iso)


def resolve_implement_ready_pin(
    *,
    todo_id: str,
    cortex: ImplementReadyCortex,
    now_iso: str,
    pinned_id: int | None,
    pinned_assertion: dict[str, Any] | None,
    requested_id: Any = None,
) -> ImplementReadyPin:
    """Resolve the readiness row: requested id first, then pin, then predicate.

    Ordering is deliberate. An explicit ``requested_id`` that is confirmed,
    active and bound to this todo wins, because the caller naming a row is
    more specific than a stale entity attribute. A pin that is still active
    is used as-is. Otherwise the predicate scan
    (``status({todo_id}, implement_ready, current)``) picks the newest active
    row, so a superseded pin cannot latch admission shut while a valid
    declaration sits unread on the same entity.
    """
    requested = coerce_assertion_id(requested_id)
    rejected: int | None = None
    if requested is not None:
        candidate = _load(cortex, requested)
        if _requested_usable(candidate, todo_id=todo_id, now_iso=now_iso):
            return ImplementReadyPin(requested, candidate, REQUESTED)
        rejected = requested

    if pinned_id is not None and not pin_needs_resolution(
        pinned_assertion, todo_id=todo_id, now_iso=now_iso
    ):
        return ImplementReadyPin(pinned_id, pinned_assertion, PINNED, rejected)

    resolved = resolve_fresh_implement_ready(
        todo_id=todo_id, cortex=cortex, now_iso=now_iso
    )
    if resolved is not None:
        return ImplementReadyPin(resolved[0], resolved[1], RESOLVED_PREDICATE, rejected)
    return ImplementReadyPin(pinned_id, pinned_assertion, UNRESOLVED, rejected)


def implement_ready_pin_reason(todo_id: str, *, pin: ImplementReadyPin) -> str:
    """Name the exact predicate a fresh declaration must carry to be resolvable."""
    reason = (
        f"no confirmed active assertion with predicate_form "
        f"{implement_ready_predicate(todo_id)} was found on {todo_id} — record "
        "one citing the dense spec and its spec_sha256:<hex>; a "
        f"has_attribute({todo_id}, implement_ready) form is not resolvable"
    )
    if pin.rejected_requested_id is not None:
        reason += (
            f" (requested implement_ready_assertion_id="
            f"{pin.rejected_requested_id} was not honoured: it must be "
            "confirmed, active, and bound to this todo)"
        )
    return reason


__all__ = [
    "ImplementReadyPin",
    "PINNED",
    "REQUESTED",
    "RESOLVED_PREDICATE",
    "UNRESOLVED",
    "implement_ready_pin_reason",
    "resolve_implement_ready_pin",
]
