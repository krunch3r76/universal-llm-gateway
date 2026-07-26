"""Friction ledger — per-arc enroll_state for charter closeout rendering."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from cortex_store.dispatch_ops._friction_enqueue import (
    normalize_charter_root,
    todo_exists_for_friction,
)
from cortex_store.dispatch_ops.ops_assertions import _op_frictions

EnrollState = Literal[
    "on_tick",
    "minted_only",
    "filed_only",
    "opted_out",
    "stale_unenrolled",
]

CONVEYOR_OFF_TAG = "conveyor-off"


@dataclass(frozen=True)
class FrictionLedgerRow:
    assertion_id: int
    note: str
    enroll_state: EnrollState
    todo_slug: str | None = None


def _parse_attributes(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return {}


def _friction_note(row: dict[str, Any]) -> str:
    claim = str(row.get("claim") or "")
    m = re.match(r"^\[[^\]]+\]\s*(.+)", claim)
    if m:
        return m.group(1).strip()
    attrs = _parse_attributes(row.get("attributes"))
    return str(attrs.get("note") or claim or f"friction #{row.get('id')}").strip()


def _actionable(attrs: dict[str, Any]) -> bool:
    if attrs.get("defer_enqueue"):
        return False
    return attrs.get("actionable", True) is not False


def derive_enroll_state(
    *,
    attrs: dict[str, Any],
    root_has_charter_runner: bool,
    root_conveyor_off: bool,
    todo_slug: str | None,
    on_conveyor: bool,
    stale: bool,
) -> EnrollState:
    """Map friction + root context to a closeout enroll_state."""
    if attrs.get("defer_enqueue"):
        return "opted_out"
    if stale and todo_slug:
        return "stale_unenrolled"
    if not root_has_charter_runner:
        return "minted_only" if todo_slug else "filed_only"
    if root_conveyor_off:
        return "minted_only" if todo_slug else "filed_only"
    if on_conveyor and todo_slug:
        return "on_tick"
    if todo_slug:
        return "minted_only"
    return "filed_only"


def build_ledger(
    root_id: str,
    *,
    root_tags: list[str] | None = None,
    frictions_fn: Callable[..., dict[str, Any]] | None = None,
    on_conveyor_fn: Callable[[int, str | None], bool] | None = None,
    stale_fn: Callable[[int], bool] | None = None,
) -> list[FrictionLedgerRow]:
    """Build the arc friction ledger for one charter root."""
    root = normalize_charter_root(root_id)
    tags = list(root_tags or [])
    has_runner = "charter-runner" in tags
    conveyor_off = CONVEYOR_OFF_TAG in tags
    list_fn = frictions_fn or _op_frictions
    resp = list_fn(charter_root=root, superseded=False, limit=200, intent="full")
    rows: list[FrictionLedgerRow] = []
    for item in resp.get("items") or []:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        assertion_id = int(item["id"])
        attrs = _parse_attributes(item.get("attributes"))
        todo_slug = todo_exists_for_friction(assertion_id)
        on_conveyor = (
            on_conveyor_fn(assertion_id, todo_slug)
            if on_conveyor_fn is not None
            else False
        )
        stale = stale_fn(assertion_id) if stale_fn is not None else False
        state = derive_enroll_state(
            attrs=attrs,
            root_has_charter_runner=has_runner,
            root_conveyor_off=conveyor_off,
            todo_slug=todo_slug,
            on_conveyor=on_conveyor,
            stale=stale,
        )
        if not todo_slug and not _actionable(attrs) and state == "filed_only":
            pass
        rows.append(
            FrictionLedgerRow(
                assertion_id=assertion_id,
                note=_friction_note(item),
                enroll_state=state,
                todo_slug=todo_slug,
            )
        )
    rows.sort(key=lambda r: r.assertion_id)
    return rows


__all__ = [
    "CONVEYOR_OFF_TAG",
    "EnrollState",
    "FrictionLedgerRow",
    "build_ledger",
    "derive_enroll_state",
]
