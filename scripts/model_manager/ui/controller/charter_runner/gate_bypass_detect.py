"""Second, independent detector for an ungated charter implement window.

``dispatch_client._warn_on_ungated_implement`` is the first detector: it inspects
the *dispatch response* for a server-stamped ``implement_spec_hash``. That makes
it blind to anything the response itself gets wrong or omits.

This detector reads the worker's own closeout turn on the worker thread — the
surface ``harvest`` already fetches — and looks for the deviation token the
worker echoes when ``contract=implement`` closed out with no resolvable
``source_ref``. Same finding, wholly independent signal path, and no event-service
query is introduced into the tick loop (review §2 isolation invariant).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Wire token produced by
# ``services/git_integration_worker/cursor_sdk_implement_gate.py``. Restated
# rather than imported: the worker is a separate service domain.
GATE_BYPASS_DEVIATION = "gate:implement_source_ref_unresolved"


@dataclass(frozen=True)
class GateBypassFinding:
    """One closeout turn that reported an ungated implement run."""

    turn_number: int
    dispatch_id: str
    source_ref: str


def _closeout_payload(turn: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a turn body as closeout JSON; ``None`` for any non-closeout turn."""
    try:
        payload = json.loads(str(turn.get("body") or ""))
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _first_dispatch_id(payload: dict[str, Any]) -> str:
    evidence = payload.get("evidence_uris")
    if not isinstance(evidence, dict):
        return ""
    ids = evidence.get("dispatch_ids")
    if isinstance(ids, list) and ids:
        return str(ids[0])
    return ""


def detect_gate_bypass(worker_turns: list[dict[str, Any]]) -> list[GateBypassFinding]:
    """Findings for every closeout turn carrying the gate-bypass deviation token."""
    findings: list[GateBypassFinding] = []
    for turn in worker_turns:
        payload = _closeout_payload(turn)
        if payload is None:
            continue
        deviations = payload.get("deviations")
        if not isinstance(deviations, list):
            continue
        if GATE_BYPASS_DEVIATION not in deviations:
            continue
        try:
            turn_number = int(turn.get("turn_number") or 0)
        except (TypeError, ValueError):
            turn_number = 0
        findings.append(
            GateBypassFinding(
                turn_number=turn_number,
                dispatch_id=_first_dispatch_id(payload),
                source_ref=str(payload.get("source_ref") or ""),
            )
        )
    return findings
