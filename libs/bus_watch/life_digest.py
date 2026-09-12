"""Life block composer for DIGEST bus delivery (F4/F5, Stops (b), C3, Amendment A1).

Composes scoreboard NOW, consent gates, stop evaluation, and policy knobs into
the life slice a LIFE root DIGEST turn carries; projection caps bus payload.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from bus_watch.consent_projection import project_gates
from bus_watch.life_scoreboard import project_now
from bus_watch.life_stops import evaluate_stops, life_knobs

LIFE_BLOCK_CAP = 1536
MAX_STOP_ROWS = 4
_PROJECTED_GATE_KEYS = ("class", "scope", "expiry")

__all__ = ["LIFE_BLOCK_CAP", "MAX_STOP_ROWS", "build_life_block", "project_life_block"]


def build_life_block(
    *,
    goals: list[dict[str, Any]],
    consents: list[dict[str, Any]],
    facts: dict[str, Any],
    policy: dict[str, Any],
    as_of: datetime | str,
) -> dict[str, Any]:
    """Compose full life block from entities and liaison tick facts (F4/F5 projection)."""
    knobs = life_knobs(policy, as_of=as_of)
    as_of_iso = knobs["wake_cron"]["as_of"]
    now = project_now(goals, as_of=as_of)
    gates_eval = project_gates(
        consents,
        now=as_of,
        let_drive_ttl_days=int(knobs["let_drive_ttl_days"]["value"]),
    )
    gates = {
        **gates_eval,
        "gates": [
            row
            for row in gates_eval.get("gates") or []
            if row.get("source") != "F3-default"
        ],
    }
    stop_facts = {**facts, "gates": gates_eval, "goals": goals}
    stops = evaluate_stops(stop_facts, knobs, now=as_of)
    return {
        "as_of": as_of_iso,
        "now": now,
        "gates": gates,
        "stops": stops,
        "knobs": knobs,
        "source": {"goals": len(goals), "consents": len(consents)},
    }


def _project_gate(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in _PROJECTED_GATE_KEYS if k in row}


def _project_stop(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in ("stop", "row", "trigger") if k in row}


def _project_now_row(now: dict[str, Any] | None) -> dict[str, Any] | None:
    if now is None:
        return None
    return {k: now[k] for k in ("goal_id", "rule", "as_of") if k in now}


def _encode(obj: dict[str, Any]) -> bytes:
    return json.dumps(
        obj, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def _byte_len(projected: dict[str, Any], truncated: list[str]) -> int:
    payload = dict(projected)
    if truncated:
        payload["truncated"] = list(truncated)
    return len(_encode(payload))


def project_life_block(
    block: dict[str, Any], *, cap_bytes: int = LIFE_BLOCK_CAP
) -> dict[str, Any]:
    """Cap-aware bus projection; drops knobs/stops/gates before NOW (Amendment A1)."""
    gates_in = block.get("gates") or {}
    projected: dict[str, Any] = {
        "as_of": block.get("as_of"),
        "now": _project_now_row(block.get("now")),
        "gates": {
            "gates": [_project_gate(g) for g in gates_in.get("gates") or []],
            "lifts": [_project_gate(lift) for lift in gates_in.get("lifts") or []],
            "last_steer": gates_in.get("last_steer"),
        },
        "stops": [
            _project_stop(row) for row in (block.get("stops") or [])[:MAX_STOP_ROWS]
        ],
        "knobs": {
            name: entry["value"] for name, entry in (block.get("knobs") or {}).items()
        },
    }
    truncated: list[str] = []
    if _byte_len(projected, truncated) <= cap_bytes:
        return projected

    if projected["knobs"]:
        projected["knobs"] = {}
        truncated.append("knobs")
        if _byte_len(projected, truncated) <= cap_bytes:
            projected["truncated"] = truncated
            return projected

    while projected["stops"]:
        projected["stops"].pop()
        truncated.append("stops")
        if _byte_len(projected, truncated) <= cap_bytes:
            projected["truncated"] = truncated
            return projected

    if projected["gates"]["lifts"]:
        projected["gates"]["lifts"] = []
        truncated.append("gates.lifts")
        if _byte_len(projected, truncated) <= cap_bytes:
            projected["truncated"] = truncated
            return projected

    if projected["gates"]["gates"]:
        projected["gates"]["gates"] = []
        truncated.append("gates.gates")
        if _byte_len(projected, truncated) <= cap_bytes:
            projected["truncated"] = truncated
            return projected

    size = _byte_len(projected, truncated)
    raise ValueError(f"life block exceeds {cap_bytes} bytes ({size})")
