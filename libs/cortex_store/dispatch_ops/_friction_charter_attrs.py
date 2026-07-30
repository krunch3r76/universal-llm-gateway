"""Protocol friction anchor helpers — charter and continuity variants (G0).

``protocol_anchor`` is a sum type stored as assertion attributes:

- charter: ``charter_root`` + ``window_index`` (enrolled lane; unchanged)
- continuity: ``root_thread`` + ``cp_ordinal`` (enrollment=none roots)

Normalized ``(anchor_kind, anchor_root, anchor_seq)`` is derived at read time
via ``_friction_anchor_view`` — never written to the store.

``checkpoint_turn`` is a bus turn pointer; ``cp_ordinal`` is the monotone
checkpoint ordinal — do not merge or alias them.

``reconcile_charter_frictions`` sweeps charter_root only; continuity-anchored
rows are actioned via friction-review + CHECKPOINT ``## Frictions``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

_ANCHOR_COMPLETENESS_ERROR = (
    "anchor provenance requires exactly one complete variant — "
    "charter requires charter_root and window_index, or "
    "continuity requires root_thread and cp_ordinal when any anchor field is set"
)

_PROTOCOL_ANCHOR_REQUIRED_ERROR = (
    "protocol friction requires exactly one anchor variant: "
    "charter{charter_root, window_index} or continuity{root_thread, cp_ordinal} "
    "(see file_charter_protocol_friction)"
)


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


def _charter_root_present(root: str | None) -> bool:
    """Return True when a thread id field carries a non-empty value."""
    if root is None:
        return False
    return bool(str(root).strip())


def _valid_cp_ordinal(value: int | None) -> bool:
    if value is None:
        return False
    try:
        return int(value) >= 1
    except (TypeError, ValueError):
        return False


def _charter_variant_complete(
    *,
    charter_root: str | None,
    window_index: int | None,
) -> bool:
    return _charter_root_present(charter_root) and window_index is not None


def _continuity_variant_complete(
    *,
    root_thread: str | None,
    cp_ordinal: int | None,
) -> bool:
    return _charter_root_present(root_thread) and _valid_cp_ordinal(cp_ordinal)


def _anchor_stamp_partial(
    *,
    charter_root: str | None,
    window_index: int | None,
    root_thread: str | None,
    cp_ordinal: int | None,
    scoreboard_uri: str | None,
    checkpoint_turn: int | None,
) -> bool:
    """True when any anchor-ish stamp field is set (completeness gate input)."""
    return any(
        v is not None
        for v in (
            charter_root,
            window_index,
            root_thread,
            cp_ordinal,
            scoreboard_uri,
            checkpoint_turn,
        )
    )


def _anchor_variant_conflict(
    *,
    charter_root: str | None,
    window_index: int | None,
    root_thread: str | None,
    cp_ordinal: int | None,
) -> bool:
    """True when both variant field-sets are partially or fully present."""
    charter_touched = charter_root is not None or window_index is not None
    continuity_touched = root_thread is not None or cp_ordinal is not None
    return charter_touched and continuity_touched


def _validate_anchor_completeness(
    *,
    charter_root: str | None,
    window_index: int | None,
    root_thread: str | None,
    cp_ordinal: int | None,
    scoreboard_uri: str | None,
    checkpoint_turn: int | None,
) -> str | None:
    """Return an error when anchor-ish fields do not form one complete variant."""
    if not _anchor_stamp_partial(
        charter_root=charter_root,
        window_index=window_index,
        root_thread=root_thread,
        cp_ordinal=cp_ordinal,
        scoreboard_uri=scoreboard_uri,
        checkpoint_turn=checkpoint_turn,
    ):
        return None
    if _anchor_variant_conflict(
        charter_root=charter_root,
        window_index=window_index,
        root_thread=root_thread,
        cp_ordinal=cp_ordinal,
    ):
        return _ANCHOR_COMPLETENESS_ERROR
    if cp_ordinal is not None and not _valid_cp_ordinal(cp_ordinal):
        return _ANCHOR_COMPLETENESS_ERROR
    if _charter_variant_complete(charter_root=charter_root, window_index=window_index):
        return None
    if _continuity_variant_complete(root_thread=root_thread, cp_ordinal=cp_ordinal):
        return None
    return _ANCHOR_COMPLETENESS_ERROR


def _friction_anchor_view(attrs: dict[str, Any]) -> dict[str, Any]:
    """Derive normalized anchor view from stored attributes (read-side only)."""
    if _charter_variant_complete(
        charter_root=attrs.get("charter_root"),
        window_index=attrs.get("window_index"),
    ):
        return {
            "anchor_kind": "charter",
            "anchor_root": str(attrs["charter_root"]),
            "anchor_seq": int(attrs["window_index"]),
        }
    if _continuity_variant_complete(
        root_thread=attrs.get("root_thread"),
        cp_ordinal=attrs.get("cp_ordinal"),
    ):
        return {
            "anchor_kind": "continuity",
            "anchor_root": str(attrs["root_thread"]),
            "anchor_seq": int(attrs["cp_ordinal"]),
        }
    return {"anchor_kind": "unanchored"}


def _friction_provenance_summary(attrs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "charter_root" in attrs:
        out["charter_root"] = attrs["charter_root"]
    if "window_index" in attrs:
        out["window_index"] = attrs["window_index"]
    if "root_thread" in attrs:
        out["root_thread"] = attrs["root_thread"]
    if "cp_ordinal" in attrs:
        out["cp_ordinal"] = attrs["cp_ordinal"]
    if "actionable" in attrs:
        out["actionable"] = attrs["actionable"]
    out.update(_friction_anchor_view(attrs))
    return out


def _friction_charter_filters(
    items: list[dict[str, Any]],
    *,
    charter_root: str | None,
    window_index: int | None,
    actionable: bool | None,
    since: str | None,
    anchor_kind: str | None = None,
    anchor_root: str | None = None,
    anchor_seq: int | None = None,
) -> list[dict[str, Any]]:
    root = _normalize_charter_root(charter_root)
    anchor_root_norm = _normalize_charter_root(anchor_root)
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
        view = _friction_anchor_view(attrs)
        if anchor_kind is not None and view.get("anchor_kind") != anchor_kind:
            continue
        if anchor_root_norm is not None and str(view.get("anchor_root") or "") != anchor_root_norm:
            continue
        if anchor_seq is not None:
            try:
                if int(view.get("anchor_seq")) != int(anchor_seq):
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


def _project_friction_full_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach derived anchor view to full-intent rows without mutating stored keys."""
    projected: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        attrs = _parse_assertion_attributes(item.get("attributes"))
        merged = dict(attrs)
        merged.update(_friction_anchor_view(attrs))
        row["attributes"] = merged
        projected.append(row)
    return projected


def _build_friction_provenance_attrs(
    *,
    charter_root: str | None,
    window_index: int | None,
    root_thread: str | None,
    cp_ordinal: int | None,
    scoreboard_uri: str | None,
    session_id: str | None,
    actionable: bool | None,
    actionable_false_reason: str | None,
    checkpoint_turn: int | None,
    defer_enqueue: bool | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Merge protocol-anchor provenance into assertion attributes."""
    if actionable is False and not (actionable_false_reason or "").strip():
        return None, "actionable=false requires non-empty actionable_false_reason"
    completeness_err = _validate_anchor_completeness(
        charter_root=charter_root,
        window_index=window_index,
        root_thread=root_thread,
        cp_ordinal=cp_ordinal,
        scoreboard_uri=scoreboard_uri,
        checkpoint_turn=checkpoint_turn,
    )
    if completeness_err:
        return None, completeness_err
    if not any(
        v is not None
        for v in (
            charter_root,
            window_index,
            root_thread,
            cp_ordinal,
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
        normalized = _normalize_charter_root(charter_root)
        if normalized is not None:
            attrs["charter_root"] = normalized
    if window_index is not None:
        attrs["window_index"] = int(window_index)
    if root_thread is not None:
        normalized = _normalize_charter_root(root_thread)
        if normalized is not None:
            attrs["root_thread"] = normalized
    if cp_ordinal is not None:
        attrs["cp_ordinal"] = int(cp_ordinal)
    if scoreboard_uri is not None:
        attrs["scoreboard_uri"] = scoreboard_uri
    if session_id is not None:
        attrs["session_id"] = session_id
    if actionable is not None:
        attrs["actionable"] = bool(actionable)
    elif charter_root is not None or root_thread is not None:
        attrs["actionable"] = True
    if actionable_false_reason is not None:
        attrs["actionable_false_reason"] = actionable_false_reason.strip()
    if checkpoint_turn is not None:
        attrs["checkpoint_turn"] = int(checkpoint_turn)
    if defer_enqueue is not None:
        attrs["defer_enqueue"] = bool(defer_enqueue)
    return attrs, None


__all__ = [
    "_ANCHOR_COMPLETENESS_ERROR",
    "_PROTOCOL_ANCHOR_REQUIRED_ERROR",
    "_build_friction_provenance_attrs",
    "_friction_anchor_view",
    "_friction_charter_filters",
    "_friction_provenance_summary",
    "_normalize_charter_root",
    "_parse_assertion_attributes",
    "_project_friction_full_items",
    "_project_friction_summary_items",
    "_validate_anchor_completeness",
]
