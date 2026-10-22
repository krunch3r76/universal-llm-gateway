"""Single-mission anti-multiplex policy for liaison ``now_row`` binds."""

from __future__ import annotations

from typing import Any

from bus_watch.events import emit_multiplex_refused
from bus_watch.now_row import _ENTITY_TOKEN_RE

_SPAWN_HINT = (
    'Use cursor_bridge(op="spawn_house") or cursor_bridge(op="spawn_tab") '
    "for a new mission house — do not multiplex onto this root."
)

_SINGLE_MISSION_ROOTS = frozenset({"16416"})


def _normalize_row(row: str) -> str:
    return " ".join(str(row or "").strip().split())


def _entity_tokens(row: str) -> set[str]:
    return {m.group(0).lower() for m in _ENTITY_TOKEN_RE.finditer(row)}


def _is_friction_gate_row(row: str) -> bool:
    compact = _normalize_row(row).lower()
    return compact.startswith("friction:")


def _allowed_work_keys(policy: dict[str, Any]) -> set[str]:
    raw = policy.get("allowed_work_keys")
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return set()
    return {str(item).strip().lower() for item in raw if str(item).strip()}


def _active_mission_key(policy: dict[str, Any]) -> str | None:
    bound = str(policy.get("single_mission_todo") or "").strip()
    if bound:
        return bound.lower()
    row = str(policy.get("now_row") or "").strip()
    if not row:
        return None
    tokens = _entity_tokens(row)
    if tokens:
        return sorted(tokens)[0]
    return None


def _house_is_single_mission(root_id: str, policy: dict[str, Any]) -> bool:
    if str(policy.get("single_mission_todo") or "").strip():
        return True
    if root_id in _SINGLE_MISSION_ROOTS:
        return bool(_active_mission_key(policy))
    return False


def assess_now_row_multiplex(
    root_id: str,
    policy: dict[str, Any],
    new_row: str,
) -> dict[str, Any]:
    """Return ``{ok: true}`` or a structured refusal payload."""
    new_norm = _normalize_row(new_row)
    current = _normalize_row(str(policy.get("now_row") or ""))
    if not new_norm:
        return {"ok": True, "action": "clear"}
    if new_norm == current:
        return {"ok": True, "action": "idempotent"}

    if _is_friction_gate_row(new_norm):
        return {"ok": True, "action": "friction_gate_swap"}

    allowed = _allowed_work_keys(policy)
    new_tokens = _entity_tokens(new_norm)
    if new_tokens & allowed:
        return {"ok": True, "action": "allowed_work_key"}
    if new_norm.lower() in allowed:
        return {"ok": True, "action": "allowed_work_key"}

    if not _house_is_single_mission(root_id, policy):
        return {"ok": True, "action": "unrestricted"}

    mission = _active_mission_key(policy)
    if not mission:
        return {"ok": True, "action": "unoccupied"}

    if not new_tokens:
        return {"ok": True, "action": "non_entity_row"}

    if len(new_tokens) == 1 and mission in new_tokens:
        return {"ok": True, "action": "same_mission"}

    refused = {
        "ok": False,
        "error": "multiplex_refused",
        "detail": _SPAWN_HINT,
        "root_id": root_id,
        "occupied_mission": mission,
        "requested_row": new_norm,
        "spawn_hint": _SPAWN_HINT,
    }
    emit_multiplex_refused(
        root_id=root_id,
        occupied_mission=mission,
        requested_row=new_norm,
    )
    return refused


__all__ = ["assess_now_row_multiplex"]
