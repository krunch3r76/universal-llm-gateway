"""Friction ledger — per-arc enroll_state for charter closeout rendering."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from cortex_store.dispatch_ops._friction_enqueue import (
    normalize_charter_root,
    todo_exists_for_friction,
    todo_open_for_friction,
)
from cortex_store.dispatch_ops.ops_assertions import _op_frictions

EnrollState = Literal[
    "queued",
    "minted_only",
    "filed_only",
    "opted_out",
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
    open_todo_slug: str | None,
    any_todo_slug: str | None,
) -> EnrollState | None:
    """Map friction + root context to a closeout enroll_state.

    Returns ``None`` when the row should be omitted from the ledger.
    """
    if attrs.get("defer_enqueue"):
        return "opted_out"
    if not root_has_charter_runner or root_conveyor_off:
        if any_todo_slug:
            return "minted_only"
        if _actionable(attrs):
            return "filed_only"
        return None
    if open_todo_slug:
        return "queued"
    if any_todo_slug:
        return "minted_only"
    if _actionable(attrs):
        return "filed_only"
    return None


def build_ledger(
    root_id: str,
    *,
    root_tags: list[str] | None = None,
    frictions_fn: Callable[..., dict[str, Any]] | None = None,
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
        open_slug = todo_open_for_friction(assertion_id)
        any_slug = open_slug or todo_exists_for_friction(assertion_id)
        state = derive_enroll_state(
            attrs=attrs,
            root_has_charter_runner=has_runner,
            root_conveyor_off=conveyor_off,
            open_todo_slug=open_slug,
            any_todo_slug=any_slug,
        )
        if state is None:
            continue
        rows.append(
            FrictionLedgerRow(
                assertion_id=assertion_id,
                note=_friction_note(item),
                enroll_state=state,
                todo_slug=any_slug,
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
