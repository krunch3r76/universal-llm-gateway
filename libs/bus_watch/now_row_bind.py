"""Gear-3 ticker persist resolved NOW into policy.now_row."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bus_watch.events import emit_now_row_bound, emit_now_row_released
from bus_watch.hop_qualify import HOLD_MARKERS, quiet_row
from bus_watch.now_row import policy_bind_satisfied, resolve_now_row
from bus_watch.spawn_pending import (
    LAND_RESULT_RE,
    ROOT_SUCCESSOR_TERMINAL_RE,
    attention_now_lane,
    row_is_terminal,
)
from bus_watch.tick_state import update_state

NOW_ROW_BIND_KEY = "now_row_bind"
PERSIST_TIERS = frozenset({"attention"})


class _OperatorRaced(Exception):  # noqa: N818
    """Disk policy.now_row changed between absorb and CAS write."""


def spent_subject(text: str) -> str | None:
    """Return spent reason for ``text``, first match wins."""
    subject = str(text or "")
    if LAND_RESULT_RE.search(subject):
        return "land_result"
    if ROOT_SUCCESSOR_TERMINAL_RE.search(subject):
        return "closeout_class"
    upper = subject.upper()
    if any(marker in upper for marker in HOLD_MARKERS):
        return "hold_marker"
    if quiet_row(subject):
        return "quiet_row"
    return None


def lane_spent(lane: dict[str, Any]) -> str | None:
    """True when a digest lane row is spent for pin purposes."""
    if row_is_terminal(lane):
        return "lifecycle"
    if lane.get("terminal"):
        return "terminal_subject"
    return spent_subject(str(lane.get("last_subject") or ""))


def ticker_owns_bind(state: dict[str, Any]) -> bool:
    """Ticker owns the current ``policy.now_row`` when provenance matches."""
    bind = state.get(NOW_ROW_BIND_KEY)
    policy_row = str((state.get("policy") or {}).get("now_row") or "")
    return bool(bind) and bind.get("row") == policy_row and policy_row != ""


def maybe_bind_now_row(
    digest: dict[str, Any],
    state: dict[str, Any],
    state_path: Path,
    *,
    as_of: str,
) -> dict[str, Any]:
    """Persist or release attention-tier NOW into ``policy.now_row`` (gear-3 only)."""
    expected_prior = str((state.get("policy") or {}).get("now_row") or "")
    owned = ticker_owns_bind(state)
    release_reason: str | None = None
    bind = state.get(NOW_ROW_BIND_KEY) or {}

    if owned:
        lanes = digest.get("lanes")
        root_error = (digest.get("root") or {}).get("error")
        if lanes is not None and not root_error:
            lane_id = str(bind.get("lane_id") or "")
            lane_match: dict[str, Any] | None = None
            for item in lanes:
                if isinstance(item, dict) and str(item.get("id")) == lane_id:
                    lane_match = item
                    break
            if lane_match is None:
                release_reason = "lane_gone"
            else:
                release_reason = lane_spent(lane_match)
            if release_reason:
                policy = digest.setdefault("policy", {})
                if isinstance(policy, dict):
                    policy["now_row"] = ""

    if expected_prior and not owned:
        cache = digest.get("policy_entity_cache") or {}
        if policy_bind_satisfied(expected_prior, cache):
            return {"action": "kept_operator_bind"}

    raw, source = resolve_now_row(digest)
    release_only = bool(release_reason and source != "attention")

    if not release_reason and source != "attention":
        return {"action": "none", "source": source}

    lane: dict[str, Any] | None = None
    refused_reason: str | None = None
    if source == "attention":
        lane = attention_now_lane(digest)
        if lane:
            spent = lane_spent(lane)
            if spent:
                if release_reason:
                    release_only = True
                    refused_reason = spent
                    lane = None
                else:
                    return {"action": "refused", "reason": spent}
        if raw == expected_prior and not release_reason:
            return {"action": "unchanged"}

    if release_only or (source == "attention" and (raw != expected_prior or release_reason)):
        root_id = str((digest.get("root") or {}).get("id") or "")
        released_row = str(bind.get("row") or expected_prior)
        released_lane_id = str(bind.get("lane_id") or "")

        def mutate(fresh: dict[str, Any]) -> None:
            disk_prior = str((fresh.get("policy") or {}).get("now_row") or "")
            if disk_prior != expected_prior:
                raise _OperatorRaced()
            policy = dict(fresh.get("policy") or {})
            if release_only:
                policy["now_row"] = ""
                fresh.pop(NOW_ROW_BIND_KEY, None)
            else:
                policy["now_row"] = raw
                fresh[NOW_ROW_BIND_KEY] = {
                    "row": raw,
                    "tier": "attention",
                    "lane_id": str(lane["id"]) if lane else None,
                    "lane_subject": lane.get("last_subject") if lane else None,
                    "as_of": as_of,
                    "superseded": expected_prior or None,
                    "release": release_reason,
                }
            fresh["policy"] = policy

        try:
            fresh = update_state(state_path, mutate)
        except _OperatorRaced:
            return {"action": "operator_raced"}

        state["policy"] = dict(fresh.get("policy") or {})
        if NOW_ROW_BIND_KEY in fresh:
            state[NOW_ROW_BIND_KEY] = fresh[NOW_ROW_BIND_KEY]
        else:
            state.pop(NOW_ROW_BIND_KEY, None)

        if release_only:
            digest.setdefault("policy", {})["now_row"] = ""
        else:
            digest.setdefault("policy", {})["now_row"] = raw

        if release_reason:
            emit_now_row_released(
                root_id=root_id,
                row=released_row,
                lane_id=released_lane_id,
                reason=release_reason,
                as_of=as_of,
            )
        if not release_only:
            emit_now_row_bound(
                root_id=root_id,
                row=raw,
                tier="attention",
                lane_id=str(lane["id"]) if lane else "",
                superseded=expected_prior or None,
                as_of=as_of,
            )
            result: dict[str, Any] = {
                "action": "bound",
                "row": raw,
                "lane_id": str(lane["id"]) if lane else None,
                "superseded": expected_prior or None,
            }
            if release_reason:
                result["released"] = release_reason
            return result

        if refused_reason:
            return {
                "action": "refused",
                "reason": refused_reason,
                "released": release_reason,
            }
        return {
            "action": "none",
            "source": source,
            "released": release_reason,
        }

    return {"action": "none", "source": source}


__all__ = [
    "NOW_ROW_BIND_KEY",
    "PERSIST_TIERS",
    "lane_spent",
    "maybe_bind_now_row",
    "spent_subject",
    "ticker_owns_bind",
]
