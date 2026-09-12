"""Consent → gates projection (F3, C3).

Pure rules from life-orchestrator-navigator phase-2 bind § Consent grammar (c) and
F3 posture: default gate classes, let-drive lifts, steer tracking, echo strings.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

DEFAULT_GATE_CLASSES = (
    "outbound_correspondence",
    "money",
    "venue_calendar_write_others",
    "credentials",
    "irreversible_act",
)
"""F3 gate classes. Non-gates (drafts, research, self-only holds, re-confirms) are
ruled out per 10479#88 and are never listed here."""


def _as_dt(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def project_gates(
    consents: list[dict[str, Any]],
    *,
    now: datetime | str,
    let_drive_ttl_days: int,
) -> dict[str, Any]:
    """Project assertion-shaped consents into gates block for tick state."""
    as_of = _as_dt(now)
    gates: list[dict[str, Any]] = [
        {
            "class": gate_class,
            "scope": None,
            "source": "F3-default",
            "since": _iso(as_of),
        }
        for gate_class in DEFAULT_GATE_CLASSES
    ]
    lifts: list[dict[str, Any]] = []
    source_ids: list[str] = []
    steers: list[tuple[datetime, dict[str, Any]]] = []

    for consent in consents:
        cid = str(consent.get("id") or "")
        if cid:
            source_ids.append(cid)
        kind = str(consent.get("class") or "")
        gate_class = consent.get("gate_class") or consent.get("move_class")
        scope = consent.get("scope")
        valid_from = consent.get("valid_from")
        since = _iso(_as_dt(valid_from)) if valid_from else _iso(as_of)

        if kind == "gate":
            move_class = str(gate_class or scope or "")
            gate_scope = None if gate_class else scope
            if gate_class:
                gate_scope = scope
            if move_class:
                expiry = consent.get("expiry") or consent.get("valid_until")
                entry: dict[str, Any] = {
                    "class": move_class,
                    "scope": gate_scope,
                    "source": cid or "unknown",
                    "since": since,
                }
                if expiry:
                    entry["expiry"] = _iso(_as_dt(expiry))
                gates.append(entry)
        elif kind == "let-drive":
            move_class = str(gate_class or "")
            if not move_class:
                continue
            stated = consent.get("expiry") or consent.get("valid_until")
            if stated:
                expiry_dt = _as_dt(stated)
            else:
                expiry_dt = as_of + timedelta(days=let_drive_ttl_days)
            if expiry_dt <= as_of:
                continue
            lifts.append(
                {
                    "class": move_class,
                    "scope": scope,
                    "expiry": _iso(expiry_dt),
                    "source": cid or "unknown",
                }
            )
        elif kind == "steer":
            vf = _as_dt(valid_from) if valid_from else as_of
            steers.append((vf, consent))
        elif kind == "restate":
            pass

    last_steer: dict[str, Any] | None = None
    if steers:
        steers.sort(key=lambda item: item[0])
        last_steer = steers[-1][1]

    return {
        "gates": gates,
        "lifts": lifts,
        "last_steer": last_steer,
        "source_ids": source_ids,
        "as_of": _iso(as_of),
    }


def is_gated(move_class: str, scope: str | None, gates_block: dict[str, Any]) -> bool:
    """True when move_class is gated and no matching lift applies."""
    gates = gates_block.get("gates") or []
    lifts = gates_block.get("lifts") or []
    gated = any(str(g.get("class")) == move_class for g in gates)
    if not gated:
        return False
    for lift in lifts:
        if str(lift.get("class")) != move_class:
            continue
        lift_scope = lift.get("scope")
        if lift_scope is None or lift_scope == scope:
            return False
    return True


def echo(
    kind: str,
    *,
    target: str | None = None,
    scope: str | None = None,
    expiry: str | None = None,
) -> str:
    """Return the C3 echo string for a consent kind."""
    if kind == "steer":
        return f"Switching to {target}; the old next step is parked."
    if kind == "gate":
        return f"Understood; nothing goes to {target} without asking first."
    if kind == "let-drive":
        return (
            f"I'll write to {scope} myself until {expiry}; "
            "you'll see each after it goes."
        )
    raise ValueError(f"unknown consent kind: {kind!r}")
