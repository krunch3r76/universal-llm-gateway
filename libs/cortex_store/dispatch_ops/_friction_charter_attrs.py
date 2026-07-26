"""Charter-window friction provenance attribute helpers (G0).

Builds and filters charter_root/window_index stamps so reconcile and harvest
G3 list-by-charter_root queries stay aligned with filed protocol frictions.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def _normalize_charter_root(root: str | None) -> str | None:
    if root is None:
        return None
    text = str(root).strip()
    if not text:
        return None
    if text.lower().startswith("agent-bus:"):
        return text.split(":", 1)[1].strip()
    return text


def _parse_assertion_attributes(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _friction_provenance_summary(attrs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "charter_root" in attrs:
        out["charter_root"] = attrs["charter_root"]
    if "window_index" in attrs:
        out["window_index"] = attrs["window_index"]
    if "actionable" in attrs:
        out["actionable"] = attrs["actionable"]
    return out


def _friction_charter_filters(
    items: list[dict[str, Any]],
    *,
    charter_root: str | None,
    window_index: int | None,
    actionable: bool | None,
    since: str | None,
) -> list[dict[str, Any]]:
    root = _normalize_charter_root(charter_root)
    since_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.fromisoformat(str(since).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            pass

    filtered: list[dict[str, Any]] = []
    for item in items:
        attrs = _parse_assertion_attributes(item.get("attributes"))
        if root is not None and str(attrs.get("charter_root") or "") != root:
            continue
        if window_index is not None:
            try:
                if int(attrs.get("window_index")) != int(window_index):
                    continue
            except (TypeError, ValueError):
                continue
        if actionable is not None:
            row_actionable = attrs.get("actionable", True)
            if bool(row_actionable) != bool(actionable):
                continue
        if since_dt is not None:
            observed = item.get("observed_at")
            if not observed:
                continue
            try:
                obs_dt = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if obs_dt < since_dt:
                continue
        filtered.append(item)
    return filtered


def _project_friction_summary_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for item in items:
        attrs = _parse_assertion_attributes(item.get("attributes"))
        row = {
            "id": item.get("id"),
            "entity_id": item.get("entity_id"),
            "claim": item.get("claim"),
            "observed_at": item.get("observed_at"),
            "attributes": _friction_provenance_summary(attrs),
            "has_evidence_uris": bool(item.get("evidence_uris")),
            "_deepen": f"cortex(tool=assertion_get, assertion_id={item.get('id')})",
        }
        projected.append({k: v for k, v in row.items() if v is not None})
    return projected


def _charter_root_present(charter_root: str | None) -> bool:
    """Return True when charter_root carries a non-empty thread id."""
    if charter_root is None:
        return False
    return bool(str(charter_root).strip())


def _charter_stamp_partial(
    *,
    charter_root: str | None,
    window_index: int | None,
    scoreboard_uri: str | None,
    checkpoint_turn: int | None,
) -> bool:
    """True when any charter-window stamp field is set (completeness gate input)."""
    return any(
        v is not None
        for v in (charter_root, window_index, scoreboard_uri, checkpoint_turn)
    )


def _build_friction_provenance_attrs(
    *,
    charter_root: str | None,
    window_index: int | None,
    scoreboard_uri: str | None,
    session_id: str | None,
    actionable: bool | None,
    actionable_false_reason: str | None,
    checkpoint_turn: int | None,
    defer_enqueue: bool | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Merge charter-window provenance into assertion attributes.

    When any charter stamp field is present, both ``charter_root`` and
    ``window_index`` are required so reconcile/harvest filters stay aligned.
    """
    if actionable is False and not (actionable_false_reason or "").strip():
        return None, "actionable=false requires non-empty actionable_false_reason"
    if _charter_stamp_partial(
        charter_root=charter_root,
        window_index=window_index,
        scoreboard_uri=scoreboard_uri,
        checkpoint_turn=checkpoint_turn,
    ) and not (_charter_root_present(charter_root) and window_index is not None):
        return (
            None,
            "charter provenance requires both charter_root and window_index when "
            "any of charter_root, window_index, scoreboard_uri, or checkpoint_turn is set",
        )
    if not any(
        v is not None
        for v in (
            charter_root,
            window_index,
            scoreboard_uri,
            session_id,
            actionable,
            actionable_false_reason,
            checkpoint_turn,
            defer_enqueue,
        )
    ):
        return None, None

    attrs: dict[str, Any] = {}
    if charter_root is not None:
        root = str(charter_root).strip()
        if root.lower().startswith("agent-bus:"):
            root = root.split(":", 1)[1].strip()
        attrs["charter_root"] = root
    if window_index is not None:
        attrs["window_index"] = int(window_index)
    if scoreboard_uri is not None:
        attrs["scoreboard_uri"] = scoreboard_uri
    if session_id is not None:
        attrs["session_id"] = session_id
    if actionable is not None:
        attrs["actionable"] = bool(actionable)
    elif charter_root is not None:
        attrs["actionable"] = True
    if actionable_false_reason is not None:
        attrs["actionable_false_reason"] = actionable_false_reason.strip()
    if checkpoint_turn is not None:
        attrs["checkpoint_turn"] = int(checkpoint_turn)
    if defer_enqueue is not None:
        attrs["defer_enqueue"] = bool(defer_enqueue)
    return attrs, None


__all__ = [
    "_build_friction_provenance_attrs",
    "_friction_charter_filters",
    "_friction_provenance_summary",
    "_normalize_charter_root",
    "_parse_assertion_attributes",
    "_project_friction_summary_items",
]
