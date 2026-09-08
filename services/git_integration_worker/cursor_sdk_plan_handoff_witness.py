"""Ledger reader for plan-mode nest_implement_hint closeouts (G3 / hop witness)."""

from __future__ import annotations

import json
from typing import Any

from implement_admission.plan_implement_handoff import (
    parse_nest_implement_hint,
    plan_implement_handoff_eligible,
)

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _parse_record_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _hint_from_child_row(
    *,
    contract: str | None,
    status: str | None,
    record_json: str | None,
) -> dict[str, Any] | None:
    if str(status or "") not in _TERMINAL_STATUSES:
        return None
    rec = _parse_record_json(record_json)
    if str(rec.get("sdk_mode") or "") != "plan":
        return None
    closeout_body = str(rec.get("closeout_body") or "")
    for blob in (closeout_body, record_json or ""):
        hint = parse_nest_implement_hint(blob)
        if plan_implement_handoff_eligible(hint):
            return hint
    return None


def plan_handoff_for_conductor(*, nest_under_dispatch_id: str) -> dict[str, Any] | None:
    """Return the first eligible plan closeout hint nested under a conductor row."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    ledger = CursorDispatchLedger.instance()
    child_ids = ledger.list_nested_children(parent_dispatch_id=nest_under_dispatch_id)
    if not child_ids:
        return None
    with ledger._connect() as conn:
        for child_id in child_ids:
            row = conn.execute(
                "SELECT contract, status, record_json FROM cursor_sdk_dispatches "
                "WHERE dispatch_id=?",
                (child_id,),
            ).fetchone()
            if row is None:
                continue
            hint = _hint_from_child_row(
                contract=row["contract"],
                status=row["status"],
                record_json=row["record_json"],
            )
            if hint is not None:
                return hint
    return None


class LedgerPlanHandoffWitness:
    """FoldDeps adapter — wires GIW ledger reads at the production boundary."""

    def plan_handoff_for_conductor(
        self, *, nest_under_dispatch_id: str
    ) -> dict[str, Any] | None:
        return plan_handoff_for_conductor(nest_under_dispatch_id=nest_under_dispatch_id)
