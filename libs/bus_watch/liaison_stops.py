"""Typed liaison stop records — operator gate with provenance (a:34092).

Mirrors ``life_stops.life_knobs`` shape: every armed stop carries
``{value, source, as_of}`` so hop and page decisions never derive from
seat-writable free-text ``now_row``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

OPERATOR_GATE_KEY = "operator_gate"
OPERATOR_SOURCE = "operator"

_CLEAR_VALUES = frozenset({False, None, "false", "clear", "null", "off", ""})


def _iso(value: datetime | str) -> str:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
    else:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def stamp_operator_gate(raw: object, *, as_of: datetime | str) -> dict[str, Any]:
    """Build the typed gate record. Reached only from ``--operator-gate``."""
    as_of_iso = _iso(as_of)
    if isinstance(raw, dict):
        row = str(raw.get("row") or "").strip() or None
        out: dict[str, Any] = {
            "value": True,
            "source": OPERATOR_SOURCE,
            "as_of": as_of_iso,
        }
        if row:
            out["row"] = row
        return out
    if raw in _CLEAR_VALUES or (
        isinstance(raw, str) and raw.strip().lower() in _CLEAR_VALUES
    ):
        return {"value": False, "source": OPERATOR_SOURCE, "as_of": as_of_iso}
    if raw is True or (isinstance(raw, str) and raw.strip().lower() == "true"):
        return {"value": True, "source": OPERATOR_SOURCE, "as_of": as_of_iso}
    text = str(raw).strip()
    if text.lower() in {"true", "arm", "on"}:
        return {"value": True, "source": OPERATOR_SOURCE, "as_of": as_of_iso}
    return {
        "value": True,
        "row": text,
        "source": OPERATOR_SOURCE,
        "as_of": as_of_iso,
    }


def read_operator_gate(policy: dict[str, Any]) -> dict[str, Any] | None:
    """Return the typed operator-gate record when present in policy."""
    raw = policy.get(OPERATOR_GATE_KEY)
    if not isinstance(raw, dict):
        return None
    return dict(raw)


def operator_gate_armed(policy: dict[str, Any]) -> bool:
    """True only when an operator-sourced gate is actively armed."""
    rec = read_operator_gate(policy)
    if not rec:
        return False
    return rec.get("source") == OPERATOR_SOURCE and bool(rec.get("value"))


class OperatorGateChannelError(ValueError):
    """Raised when ``operator_gate`` is routed through the shared ``--set`` channel."""


def arm_operator_gate(
    policy: dict[str, Any],
    raw: object,
    *,
    as_of: datetime | str,
) -> dict[str, object]:
    """Arm or clear the gate. Sole arming path — reached only from the operator flag."""
    merged = dict(policy)
    merged[OPERATOR_GATE_KEY] = stamp_operator_gate(raw, as_of=as_of)
    return merged


def apply_policy_set(
    policy: dict[str, Any],
    set_items: dict[str, object],
    *,
    as_of: datetime | str,
) -> dict[str, object]:
    """Merge ``--set`` keys into policy.

    ``operator_gate`` is refused here rather than stamped: ``--set`` is the channel
    successors are handed, and ``stamp_operator_gate`` cannot tell callers apart, so
    admitting the key on this channel would let a seat self-certify ``source=operator``.
    """
    if OPERATOR_GATE_KEY in set_items:
        raise OperatorGateChannelError(
            f"refusing: {OPERATOR_GATE_KEY} cannot be armed via --set "
            "(successor-writable channel); use --operator-gate"
        )
    merged = dict(policy)
    merged.update(set_items)
    return merged


__all__ = [
    "OPERATOR_GATE_KEY",
    "OPERATOR_SOURCE",
    "OperatorGateChannelError",
    "apply_policy_set",
    "arm_operator_gate",
    "operator_gate_armed",
    "read_operator_gate",
    "stamp_operator_gate",
]
