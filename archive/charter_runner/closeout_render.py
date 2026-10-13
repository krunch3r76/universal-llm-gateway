"""Pure closeout markdown render for charter arc state-close."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from .friction_ledger import EnrollState, FrictionLedgerRow

_STATUS_LABEL: dict[EnrollState, str] = {
    "queued": "**queued**",
    "minted_only": "minted only",
    "filed_only": "filed only",
    "opted_out": "opted_out",
}


def render_frictions_table(rows: list[FrictionLedgerRow]) -> str:
    """Render the ``## Frictions`` table or the empty-ledger sentinel."""
    if not rows:
        return "_No frictions this arc._"
    lines = [
        "| # | What went wrong | Status |",
        "|---|---|---|",
    ]
    for row in rows:
        label = _STATUS_LABEL.get(row.enroll_state, row.enroll_state)
        if row.todo_slug and row.enroll_state in ("queued", "minted_only"):
            status = f"{label} — `{row.todo_slug}`"
        else:
            status = label
        note = row.note.replace("|", "\\|")[:120]
        lines.append(f"| {row.assertion_id} | {note} | {status} |")
    return "\n".join(lines)


def render_closeout(
    *,
    root_id: str,
    root_subject: str,
    window_count: int,
    reason: str,
    what_happened: str,
    where_left: str,
    ledger: list[FrictionLedgerRow],
    checkpoint_turn: int | None = None,
    concluded_at: datetime | None = None,
) -> str:
    """Render the durable closeout body (sidecar + bus close summary)."""
    concluded = (concluded_at or datetime.now(UTC)).isoformat()
    frictions = render_frictions_table(ledger)
    ledger_digest = hashlib.sha256(
        json.dumps(
            [
                {
                    "id": r.assertion_id,
                    "state": r.enroll_state,
                    "todo": r.todo_slug,
                }
                for r in ledger
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    machine = (
        f"reason={reason} checkpoint_turn={checkpoint_turn} "
        f"ledger_sha256={ledger_digest}"
    )
    subject = root_subject.strip() or f"agent-bus:{root_id}"
    return f"""# Charter closeout — {subject}

root: {root_id} · windows: {window_count} · concluded: {concluded} · reason: {reason}

## What happened

{what_happened.strip()}

## Where it left things

{where_left.strip()}

## Frictions

{frictions}

<!-- machine: {machine} -->
"""


__all__ = ["render_closeout", "render_frictions_table"]
