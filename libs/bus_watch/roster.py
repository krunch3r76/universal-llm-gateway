"""Append-only liaison roster journal — per-row play/hold on the ticker path."""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import httpx

from bus_watch.fable_lock import WATCH_DIR
from bus_watch.now_row import resolve_now_row
from bus_watch.spawn_wake.play_classify import (
    extract_todo_slug,
    holder_lost_finished_hire,
    live_conductor_owner,
    mark_consult_reply_seats_empty,
)

_GIW_ACTIVE_WORK = os.environ.get(
    "LIAISON_GIW_ACTIVE_WORK",
    "http://127.0.0.1:8091/api/v1/integrate/active-work",
)
_FRICTION_NOW_RE = re.compile(r"Friction\s+a:(\d+)", re.I)


def _friction_gate_id(raw: str) -> str | None:
    match = _FRICTION_NOW_RE.search(str(raw or ""))
    return f"a:{match.group(1)}" if match else None


_ROW_ID_SAFE = re.compile(r"[^a-z0-9_-]+", re.I)

HIRE_AUTO = "auto"
HIRE_HOLD = "hold"
DECISION_PLAY = "play"
DECISION_HOLD = "hold"
DECISION_UNSURE = "unsure"


def roster_path(root_id: str) -> Path:
    rid = str(root_id or "").strip()
    if not rid or "/" in rid or "\\" in rid:
        raise ValueError(f"invalid root_id: {root_id!r}")
    return WATCH_DIR / f"liaison-{rid}.roster.jsonl"


def _parse_line(raw: str) -> dict[str, Any] | None:
    line = raw.strip()
    if not line:
        return None
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(row, dict):
        return None
    row_id = str(row.get("row_id") or "").strip()
    work_key = str(row.get("work_key") or "").strip()
    if not row_id or not work_key:
        return None
    hire = str(row.get("hire") or HIRE_AUTO).strip().lower()
    if hire not in (HIRE_AUTO, HIRE_HOLD):
        hire = HIRE_AUTO
    gate = str(row.get("gate") or "").strip()
    text = str(row.get("text") or "").strip()
    paths_raw = row.get("paths")
    paths: list[str] = []
    if isinstance(paths_raw, str) and paths_raw.strip():
        paths = [paths_raw.strip()]
    elif isinstance(paths_raw, list):
        paths = [str(p).strip() for p in paths_raw if str(p).strip()]
    last_hire = str(row.get("last_hire_dispatch_id") or "").strip()
    parsed: dict[str, Any] = {
        "row_id": row_id,
        "work_key": work_key,
        "hire": hire,
        "gate": gate,
        "text": text,
    }
    if paths:
        parsed["paths"] = paths
    if last_hire:
        parsed["last_hire_dispatch_id"] = last_hire
    return parsed


def read_journal(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_line(raw)
        if parsed:
            rows.append(parsed)
    return rows


def merge_by_row_id(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Last journal line wins per ``row_id``."""
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        rid = row["row_id"]
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = row
    return [by_id[rid] for rid in order]


def _default_row_id(work_key: str) -> str:
    slug = extract_todo_slug(work_key) or work_key.split(":", 1)[-1]
    base = _ROW_ID_SAFE.sub("-", slug.lower()).strip("-") or "row"
    return f"{base}-{uuid.uuid4().hex[:8]}"


def seed_row_from_now_row(now_row: str) -> dict[str, Any]:
    """One-time cutover row from legacy ``policy.now_row``."""
    raw = str(now_row or "").strip()
    slug = extract_todo_slug(raw)
    work_key = f"todo:{slug}" if slug else raw
    gate_match = _FRICTION_NOW_RE.search(raw)
    gate = f"a:{gate_match.group(1)}" if gate_match else ""
    return {
        "row_id": _default_row_id(work_key),
        "work_key": work_key,
        "hire": HIRE_AUTO,
        "gate": gate,
        "text": raw,
    }


def append_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def fetch_giw_live_work_keys() -> dict[str, bool] | None:
    """Map ``work_key`` → live conductor on GIW. ``None`` when unreachable."""
    try:
        response = httpx.get(_GIW_ACTIVE_WORK, timeout=3.0)
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    holders = (payload.get("sdk_gate") or {}).get("holders")
    if not isinstance(holders, list):
        holders = []
    live: dict[str, bool] = {}
    for holder in holders:
        if not isinstance(holder, dict):
            continue
        wk = str(holder.get("work_key") or holder.get("source_ref") or "").strip()
        if not wk:
            continue
        contract = str(holder.get("contract") or "").strip().lower()
        if contract == "conductor" or wk.startswith("todo:"):
            live[wk.lower()] = True
    cursor = payload.get("cursor_dispatches") or {}
    for dispatch_id in cursor.get("dispatch_ids") or []:
        del dispatch_id  # occupancy only; work_key lives in holders when present
    return live


def _todo_slug_from_row(row: dict[str, Any]) -> str | None:
    wk = str(row.get("work_key") or "")
    slug = extract_todo_slug(wk)
    if slug:
        return slug
    if wk.lower().startswith("todo:"):
        return wk.split(":", 1)[1].lower()
    return None


def _bus_live_work_keys(digest: dict[str, Any]) -> dict[str, bool | None]:
    """``work_key`` → True live / False terminal / None unsure (bus lanes)."""
    mark_consult_reply_seats_empty(digest)
    out: dict[str, bool | None] = {}
    lanes = digest.get("lanes")
    if lanes is None:
        return out
    for lane in lanes or []:
        if not isinstance(lane, dict):
            continue
        from bus_watch.spawn_wake.play_classify import (
            _conductor_signal,
            _lane_live,
            _lane_todos,
        )

        live = _lane_live(lane)
        conductor = _conductor_signal(lane)
        if conductor is not True:
            continue
        for slug in _lane_todos(lane):
            wk = f"todo:{slug}"
            if live is True:
                out[wk] = True
            elif live is False:
                out[wk] = False
            elif wk not in out:
                out[wk] = None
    return out


def enrich_row_liveness(
    row: dict[str, Any],
    digest: dict[str, Any],
    giw_live: dict[str, bool] | None,
) -> dict[str, Any]:
    wk = str(row.get("work_key") or "").lower()
    bus = _bus_live_work_keys(digest)
    giw: bool | None = None
    if giw_live is not None:
        giw = giw_live.get(wk)
    bus_live = bus.get(wk)
    slug = _todo_slug_from_row(row)
    owner_unsure = False
    if slug:
        owner = live_conductor_owner(digest, slug)
        if isinstance(owner, dict) and owner.get("unsure"):
            owner_unsure = True
    combined: bool | None
    if bus_live is True or giw is True:
        combined = True
    elif bus_live is False and giw is False:
        combined = False
    elif bus_live is None and giw is None and owner_unsure:
        combined = None
    elif bus_live is None and giw is None:
        combined = None
    else:
        combined = bus_live if bus_live is not None else giw
    return {
        **row,
        "giw_live": giw,
        "bus_live": bus_live,
        "live": combined,
    }


def fold_roster(
    root_id: str,
    policy: dict[str, Any],
    digest: dict[str, Any],
    *,
    giw_live: dict[str, bool] | None | object = ...,
) -> list[dict[str, Any]]:
    """Fold journal rows; seed once from ``policy.now_row`` when empty."""
    path = roster_path(root_id)
    journal = read_journal(path)
    if not journal:
        now_row = str(policy.get("now_row") or "").strip()
        if now_row:
            seed = seed_row_from_now_row(now_row)
            append_row(path, seed)
            journal = [seed]
    merged = merge_by_row_id(journal)
    if giw_live is ...:
        giw_live = fetch_giw_live_work_keys()
    giw_map = giw_live if isinstance(giw_live, dict) else None
    return [enrich_row_liveness(row, digest, giw_map) for row in merged]


def _normalize_repo_path(raw: str) -> str:
    text = str(raw or "").strip().replace("\\", "/").lstrip("./")
    return text.rstrip("/")


def row_paths(row: dict[str, Any]) -> list[str]:
    raw = row.get("paths")
    if not raw:
        return []
    if isinstance(raw, str):
        norm = _normalize_repo_path(raw)
        return [norm] if norm else []
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            norm = _normalize_repo_path(str(item))
            if norm:
                out.append(norm)
        return out
    return []


def paths_share_prefix(left: str, right: str) -> bool:
    a = _normalize_repo_path(left)
    b = _normalize_repo_path(right)
    if not a or not b:
        return False
    if a == b:
        return True
    return a.startswith(f"{b}/") or b.startswith(f"{a}/")


def paths_overlap_between(row_a: dict[str, Any], row_b: dict[str, Any]) -> bool:
    pa = row_paths(row_a)
    pb = row_paths(row_b)
    if not pa or not pb:
        return False
    return any(paths_share_prefix(x, y) for x in pa for y in pb)


def _live_conductor_keys(roster: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for row in roster:
        if row.get("live") is True:
            keys.add(str(row.get("work_key") or "").lower())
    return keys


def _scheduled_conductor_count(
    roster: list[dict[str, Any]], scheduled_plays: list[dict[str, Any]]
) -> int:
    live_keys = _live_conductor_keys(roster)
    scheduled_keys = {
        str(row.get("work_key") or "").lower()
        for row in scheduled_plays
        if str(row.get("work_key") or "")
    }
    return len(live_keys | scheduled_keys)


def _path_overlap_blocker(
    row: dict[str, Any],
    roster: list[dict[str, Any]],
    scheduled_plays: list[dict[str, Any]],
) -> str | None:
    for other in roster:
        if other.get("row_id") == row.get("row_id"):
            continue
        if other.get("live") is not True and other not in scheduled_plays:
            continue
        if paths_overlap_between(row, other):
            return str(other.get("row_id") or "")
    return None


def record_row_hire(root_id: str, row_id: str, dispatch_id: str) -> None:
    """Append a journal line latching ``last_hire_dispatch_id`` for crash re-admit."""
    path = roster_path(root_id)
    journal = merge_by_row_id(read_journal(path))
    base = next((r for r in journal if r.get("row_id") == row_id), None)
    if not base:
        return
    updated = {**base, "last_hire_dispatch_id": str(dispatch_id).strip()}
    append_row(path, updated)


def _friction_gate_active_for_row(digest: dict[str, Any], row: dict[str, Any]) -> bool:
    gate = str(row.get("gate") or "").strip()
    if not gate:
        return False
    raw_now, _source = resolve_now_row(digest)
    return _friction_gate_id(raw_now) == gate


def classify_row(
    digest: dict[str, Any],
    row: dict[str, Any],
    *,
    scheduled_plays: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Per-row play / hold / unsure — holds only this row, not siblings."""
    hire = str(row.get("hire") or HIRE_AUTO).strip().lower()
    work_key = str(row.get("work_key") or "").lower()
    policy = digest.get("policy") or {}
    max_conductors = int(policy.get("max_conductors") or 2)
    scheduled = list(scheduled_plays or [])
    roster = digest.get("roster") or []

    if hire == HIRE_HOLD:
        return {
            "decision": DECISION_HOLD,
            "reason": "hire_hold",
            "row_id": row.get("row_id"),
        }

    slug = _todo_slug_from_row(row)
    if not slug:
        return {
            "decision": DECISION_UNSURE,
            "reason": "no_todo",
            "row_id": row.get("row_id"),
        }

    owner = live_conductor_owner(digest, slug)
    if isinstance(owner, dict) and owner.get("unsure"):
        return {
            "decision": DECISION_HOLD,
            "reason": "unsure_live",
            "row_id": row.get("row_id"),
        }
    if (
        isinstance(owner, dict)
        and owner.get("lane") is not None
        and not owner.get("unsure")
    ):
        return {
            "decision": DECISION_HOLD,
            "reason": "live_conductor",
            "row_id": row.get("row_id"),
        }

    if row.get("live") is None and "lanes" not in digest:
        return {
            "decision": DECISION_HOLD,
            "reason": "unsure_live",
            "row_id": row.get("row_id"),
        }

    if _friction_gate_active_for_row(digest, row):
        return {
            "decision": DECISION_HOLD,
            "reason": "friction_gate",
            "row_id": row.get("row_id"),
        }

    latched = str(row.get("last_hire_dispatch_id") or "").strip()
    if latched and row.get("live") is not True and owner is None:
        from bus_watch.spawn_wake.play_classify import hire_latch_released

        if not holder_lost_finished_hire(digest, slug) and not hire_latch_released(
            digest, latched
        ):
            return {
                "decision": DECISION_HOLD,
                "reason": "hire_latched",
                "row_id": row.get("row_id"),
            }

    overlap_id = _path_overlap_blocker(row, roster if roster else [row], scheduled)
    if overlap_id:
        return {
            "decision": DECISION_HOLD,
            "reason": f"path_overlap:{overlap_id}",
            "row_id": row.get("row_id"),
        }

    conductor_count = _scheduled_conductor_count(roster if roster else [row], scheduled)
    live_keys = _live_conductor_keys(roster if roster else [row])
    if conductor_count >= max_conductors and work_key not in live_keys:
        return {
            "decision": DECISION_HOLD,
            "reason": "max_conductors",
            "row_id": row.get("row_id"),
        }

    return {
        "decision": DECISION_PLAY,
        "reason": "play_row",
        "row_id": row.get("row_id"),
    }


def classify_roster_rows(digest: dict[str, Any]) -> list[dict[str, Any]]:
    """Classify every roster row; earlier ``play`` rows count toward sibling caps."""
    roster = digest.get("roster") or []
    scheduled: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    for row in roster:
        verdict = classify_row(digest, row, scheduled_plays=scheduled)
        if verdict.get("decision") == DECISION_PLAY:
            scheduled.append(row)
        out.append(verdict)
    return out


def roster_play_rows(
    digest: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """All roster rows that classify ``play``, in journal order."""
    roster = digest.get("roster") or []
    scheduled: list[dict[str, Any]] = []
    found: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in roster:
        verdict = classify_row(digest, row, scheduled_plays=scheduled)
        if verdict.get("decision") == DECISION_PLAY:
            scheduled.append(row)
            found.append((row, verdict))
    return found


def roster_play_todo(
    digest: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None]:
    """First roster row that classifies ``play``; None when none."""
    for row, verdict in roster_play_rows(digest):
        slug = _todo_slug_from_row(row)
        return slug, verdict
    return None, None


def roster_classifications(digest: dict[str, Any]) -> list[dict[str, Any]]:
    verdicts = classify_roster_rows(digest)
    roster = digest.get("roster") or []
    out: list[dict[str, Any]] = []
    for row, verdict in zip(roster, verdicts, strict=False):
        out.append(
            {
                "row_id": row.get("row_id"),
                "hire": verdict.get("decision"),
                "reason": verdict.get("reason"),
                "decision": verdict.get("decision"),
            }
        )
    return out


__all__ = [
    "append_row",
    "classify_row",
    "classify_roster_rows",
    "fold_roster",
    "merge_by_row_id",
    "paths_overlap_between",
    "read_journal",
    "record_row_hire",
    "roster_classifications",
    "roster_path",
    "roster_play_rows",
    "roster_play_todo",
    "row_paths",
    "seed_row_from_now_row",
    "fetch_giw_live_work_keys",
]
