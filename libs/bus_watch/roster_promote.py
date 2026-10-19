"""Roster promotion after completed rows — cortex-api over UDS only (fork 10)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from typing import Any

import httpx
from implement_admission.impresario_events import (
    ImpresarioPromotionAppended,
    ImpresarioPromotionFailed,
    ImpresarioSurfaceContention,
    ImpresarioTodoAutoclosed,
)
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

from bus_watch.events import _emit
from bus_watch.park_harvest import scoreboard_g_rows_done, work_item_slug
from bus_watch.roster import (
    HIRE_AUTO,
    append_row,
    fetch_giw_active_work_payload,
    keyed_conductor_work_keys,
    merge_by_row_id,
    read_journal,
    roster_path,
    store_non_terminal_conductor_count,
)

_PINNED_STOPS = frozenset({"OPERATOR_GATE", "HOLD_MERGE"})
_HOUSE_NEXT_ROW_RE = re.compile(
    r"(?im)^(?:##\s*House\s*)?next_row\s*:\s*(.+)$",
)
_CORTEX_TIMEOUT = 30.0

CortexDispatch = Callable[[str, dict[str, Any]], dict[str, Any]]


def _default_cortex_dispatch(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    body = {"tool": tool, "arguments": arguments}
    try:
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=_CORTEX_TIMEOUT) as client:
            response = client.post("/dispatch", json=body)
    except httpx.HTTPError as exc:
        return {"error": str(exc)[:200]}
    if response.status_code >= 400:
        return {"error": f"http_{response.status_code}", "body": response.text[:200]}
    try:
        payload = response.json()
    except ValueError:
        return {"error": "non_json"}
    return payload if isinstance(payload, dict) else {"result": payload}


def _deterministic_row_id(work_key: str) -> str:
    slug = work_item_slug(work_key) or work_key.split(":", 1)[-1]
    digest = hashlib.sha1(work_key.encode()).hexdigest()[:8]
    base = re.sub(r"[^a-z0-9_-]+", "-", slug.lower()).strip("-") or "row"
    return f"{base}-{digest}"


def _work_keys_in_journal(root_id: str) -> set[str]:
    path = roster_path(root_id)
    merged = merge_by_row_id(read_journal(path))
    return {str(r.get("work_key") or "").lower() for r in merged if r.get("work_key")}


def _all_auto_rows_complete(roster: list[dict[str, Any]]) -> bool:
    auto_rows = [
        r
        for r in roster
        if str(r.get("hire") or HIRE_AUTO).strip().lower() == HIRE_AUTO
    ]
    if not auto_rows:
        return False
    for row in auto_rows:
        wk = str(row.get("work_key") or "")
        if not wk:
            return False
        if not scoreboard_g_rows_done(wk):
            return False
    return True


def _house_has_live_conductor(roster: list[dict[str, Any]]) -> bool:
    return any(r.get("live") is True for r in roster)


def _giw_blocks_roster_promotion(work_key: str) -> str | None:
    """Hold promotion when GIW store/conductor projections disagree or todo is open."""
    payload = fetch_giw_active_work_payload()
    if payload is None:
        return "giw_unreachable"
    store_count = store_non_terminal_conductor_count(payload)
    keyed = keyed_conductor_work_keys(payload)
    if len(keyed) < store_count:
        return "giw_conductor_count_mismatch"
    if store_count > 0 and not keyed:
        return "giw_empty_live_map"
    if work_key.lower() in keyed:
        return "giw_conductor_open"
    return None


def _occupancy_pinned(work_key: str, *, occupancy_fn: Callable[[str], dict[str, Any]]) -> bool:
    try:
        occ = occupancy_fn(work_key)
    except Exception:
        return True
    if not isinstance(occ, dict):
        return False
    if str(occ.get("state") or "") == "pinned":
        stop = str(occ.get("stop") or "").upper()
        return stop in _PINNED_STOPS
    return False


def _resolve_next_row_text(
    policy: dict[str, Any],
    digest: dict[str, Any],
    *,
    cortex: CortexDispatch,
) -> tuple[str | None, str]:
    explicit = str(policy.get("next_row") or "").strip()
    if explicit:
        return explicit, "policy.next_row"
    for turn in (digest.get("root") or {}).get("recent_turns") or []:
        if not isinstance(turn, dict):
            continue
        body = str(turn.get("body") or "")
        match = _HOUSE_NEXT_ROW_RE.search(body)
        if match:
            return match.group(1).strip(), "continuity.next_row"
    root_id = str((digest.get("root") or {}).get("id") or "")
    if not root_id:
        return None, "none"
    payload = cortex(
        "entities",
        {
            "filter": f"derived_from:{root_id}",
            "type": "todo",
            "limit": 5,
        },
    )
    rows = payload.get("entities") or payload.get("results") or []
    if isinstance(rows, list):
        for ent in rows:
            if not isinstance(ent, dict):
                continue
            state = str(ent.get("workflow_state") or ent.get("status") or "").lower()
            if state in ("done", "closed", "terminal", "completed", "discharged"):
                continue
            eid = str(ent.get("id") or ent.get("entity_id") or "").strip()
            if eid:
                return eid, "cortex.linked_todo"
    return None, "none"


def _parse_next_work_key(raw: str) -> tuple[str, str]:
    text = str(raw or "").strip()
    if text.lower().startswith("todo:"):
        return text.lower(), work_item_slug(text) or text.split(":", 1)[-1]
    if ":" in text and not text.startswith("agent-bus:"):
        return text.lower(), work_item_slug(text) or text.split(":", 1)[-1]
    slug = text.split()[0].lower()
    return f"todo:{slug}", slug


def _autoclose_todo(
    work_key: str,
    *,
    cortex: CortexDispatch,
    closed: set[str],
) -> bool:
    todo_id = work_item_slug(work_key)
    if not todo_id:
        return False
    entity_id = f"todo:{todo_id}"
    if entity_id in closed:
        return False
    card = cortex("entity_get", {"entity_id": entity_id, "intent": "card"})
    if card.get("error"):
        return False
    state = str(card.get("workflow_state") or card.get("status") or "").lower()
    if state in ("done", "closed", "terminal", "completed", "discharged"):
        closed.add(entity_id)
        return False
    sidecar = cortex(
        "todo_close_sidecar",
        {
            "todo_id": entity_id,
            "summary": "impresario roster promotion autoclosed",
            "agent": "liaison-ticker",
        },
    )
    if sidecar.get("error"):
        _emit(
            ImpresarioPromotionFailed(
                work_key=work_key, reason=f"sidecar:{sidecar.get('error')}"
            )
        )
        return False
    upd = cortex(
        "entity_update",
        {"entity_id": entity_id, "workflow_state": "done"},
    )
    if upd.get("error"):
        _emit(
            ImpresarioPromotionFailed(
                work_key=work_key, reason=f"entity_update:{upd.get('error')}"
            )
        )
        return False
    _emit(ImpresarioTodoAutoclosed(work_key=work_key, entity_id=entity_id))
    closed.add(entity_id)
    return True


def maybe_surface_contention(
    digest: dict[str, Any],
    *,
    ticker_holder: str | None = None,
    house_holder_agent: str | None = None,
) -> dict[str, Any] | None:
    """Emit surface contention when ticker lease and house claim disagree."""
    ticker = str(ticker_holder or "").strip()
    house = str(house_holder_agent or "").strip()
    if not ticker or not house or ticker == house:
        return None
    roster = digest.get("roster") or []
    work_key = ""
    if roster and isinstance(roster[0], dict):
        work_key = str(roster[0].get("work_key") or "")
    _emit(
        ImpresarioSurfaceContention(
            work_key=work_key or "house",
            holder_dispatch_id=house,
            surface="ticker_vs_house_claim",
        )
    )
    return {
        "kind": "surface_contention",
        "id": "surface_contention",
        "ticker_holder": ticker,
        "house_holder": house,
    }


def maybe_promote_roster(
    root_id: str,
    policy: dict[str, Any],
    digest: dict[str, Any],
    state: dict[str, Any],
    *,
    cortex: CortexDispatch | None = None,
    occupancy_fn: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Append the next roster row when every auto row is complete and house is idle."""
    dispatch = cortex or _default_cortex_dispatch
    roster = digest.get("roster") or []
    if not _all_auto_rows_complete(roster):
        return {"action": "skip", "reason": "rows_incomplete"}
    if _house_has_live_conductor(roster):
        return {"action": "skip", "reason": "live_conductor"}
    occ_fn = occupancy_fn
    if occ_fn is not None:
        for row in roster:
            wk = str(row.get("work_key") or "")
            if wk and _occupancy_pinned(wk, occupancy_fn=occ_fn):
                return {"action": "skip", "reason": "pinned_occupancy"}
    next_raw, source = _resolve_next_row_text(policy, digest, cortex=dispatch)
    if not next_raw:
        return {"action": "skip", "reason": "no_next_row"}
    work_key, slug = _parse_next_work_key(next_raw)
    giw_reason = _giw_blocks_roster_promotion(work_key)
    if giw_reason:
        return {"action": "skip", "reason": giw_reason}
    if work_key.lower() in _work_keys_in_journal(root_id):
        return {"action": "skip", "reason": "already_rostered"}
    row_id = _deterministic_row_id(work_key)
    gate = ""
    if "friction" in next_raw.lower() or next_raw.strip().lower().startswith("a:"):
        gate = next_raw.strip().split()[0]
    new_row = {
        "row_id": row_id,
        "work_key": work_key,
        "hire": HIRE_AUTO,
        "gate": gate,
        "text": next_raw.strip(),
        "promoted_from": source,
    }
    append_row(roster_path(root_id), new_row)
    lease_epoch = int(state.get("promotion_lease_epoch") or 0) + 1
    state["promotion_lease_epoch"] = lease_epoch
    _emit(
        ImpresarioPromotionAppended(
            work_key=work_key,
            next_row=next_raw.strip(),
            lease_epoch=lease_epoch,
        )
    )
    closed: set[str] = set(state.get("autoclosed_todos") or [])
    for row in roster:
        wk = str(row.get("work_key") or "")
        if wk and scoreboard_g_rows_done(wk):
            _autoclose_todo(wk, cortex=dispatch, closed=closed)
    if closed:
        state["autoclosed_todos"] = sorted(closed)
    digest["roster"] = merge_by_row_id(read_journal(roster_path(root_id)))
    return {"action": "appended", "work_key": work_key, "row_id": row_id, "source": source}


__all__ = [
    "maybe_promote_roster",
    "maybe_surface_contention",
]
