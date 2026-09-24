"""Lane closeout journal — structured O→L→N debrief rows on the parent root.

Terminal child lanes post one append-only ``LANE CLOSEOUT`` (or ``LANE ABANDONED``)
turn on their continuity parent. Status queries scan those turns — not
``tmp/watchers/liaison-*.tick.json`` scratch.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from bus_watch.digest_budget import _get
from bus_watch.events import emit_lane_closeout_observed
from bus_watch.lane_live import probe_live
from bus_watch.spawn_pending import row_is_terminal

CLOSEOUT_SUBJECT_PREFIX = "LANE CLOSEOUT"
ABANDONED_SUBJECT_PREFIX = "LANE ABANDONED"
CLOSEOUT_TAG = "lane:closeout"
_LANE_CLOSEOUT_BODY_RE = re.compile(
    r"^\s*LANE\s+(?:CLOSEOUT|ABANDONED)\s*\n(\{.*\})\s*$",
    re.DOTALL | re.MULTILINE,
)
_STATUS_RE = re.compile(r"^\s*status\s*:\s*(\S+)", re.I | re.MULTILINE)
_NEXT_RE = re.compile(r"^\s*next\s*:\s*(.+?)\s*$", re.I | re.MULTILINE)
_SHA_RE = re.compile(r"\b([0-9a-f]{7,40})\b", re.I)
_LANDED_SHA_RE = re.compile(
    r"(?:landed|commit|git_refs|git:)[^\n]{0,80}\b([0-9a-f]{7,40})\b",
    re.I,
)
_DISPOSITION_RE = re.compile(
    r"^\s*land_disposition\s*:\s*[`\"']?([A-Za-z_-]+)[`\"']?\s*$",
    re.I | re.MULTILINE,
)
_LIVE_TRISTATE = frozenset({"unprobed", "live", "stale"})
_ASSERTION_ID_IN_TEXT_RE = re.compile(r"\ba:\d+\b", re.I)
_OWNER_LINE_RE = re.compile(r"^\s*owner\s*:\s*(\S+)", re.I | re.MULTILINE)
_DEFECT_LINE_RE = re.compile(r"^\s*DEFECT:\s*(.+?)\s*$", re.I | re.MULTILINE)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def closeout_text_has_assertion_id(text: str) -> bool:
    """True when worker closeout prose already cites a friction assertion id."""
    return bool(_ASSERTION_ID_IN_TEXT_RE.search(str(text or "")))


def closeout_needs_friction_file(text: str) -> bool:
    """True for CONSULT_PENDING or a ``DEFECT:`` line without an existing ``a:<id>``."""
    body = str(text or "")
    if closeout_text_has_assertion_id(body):
        return False
    if "CONSULT_PENDING" in body:
        return True
    return bool(_DEFECT_LINE_RE.search(body))


def friction_owner_from_closeout(text: str) -> str:
    """Resolve friction owner from ``owner:`` token or default ``service:bus_watch``."""
    match = _OWNER_LINE_RE.search(str(text or ""))
    if match:
        token = match.group(1).strip()
        if token.startswith("service:") or token.startswith("agent_skill:"):
            return token
    return "service:bus_watch"


def friction_note_from_closeout(text: str) -> str:
    """Build a filing note from DEFECT line or CONSULT_PENDING closeout residue."""
    body = str(text or "").strip()
    defect = _DEFECT_LINE_RE.search(body)
    if defect:
        return defect.group(1).strip()[:500]
    if "CONSULT_PENDING" in body:
        return "lane closeout CONSULT_PENDING without assertion id"
    return body[:500] or "lane closeout defect"


def file_closeout_friction(
    *,
    worker_text: str,
    lane_id: str,
    worker_turn: int,
) -> tuple[str | None, str | None]:
    """File friction for a terminal closeout; return ``(friction_id, friction_error)``."""
    from bus_watch.friction_rows import row_id as friction_row_id
    from substrate_friction_file import file_friction

    owner = friction_owner_from_closeout(worker_text)
    note = friction_note_from_closeout(worker_text)
    evidence = f"agent-bus:{lane_id}#{worker_turn}" if worker_turn else f"agent-bus:{lane_id}"
    try:
        result = file_friction(
            owner=owner,
            note=note,
            category="bug",
            evidence_uris=[evidence],
            agent="bus_watch",
        )
    except Exception as exc:  # noqa: BLE001 — connection errors must not abort harvest
        return None, f"{type(exc).__name__}: {exc}"[:200]
    if result.get("error"):
        err = str(result.get("error") or "friction_error")[:200]
        return None, err
    item = result.get("item") if isinstance(result.get("item"), dict) else {}
    raw_id = item.get("id") or result.get("assertion_id")
    if raw_id is None:
        return None, "friction_missing_assertion_id"
    try:
        return friction_row_id(raw_id), None
    except ValueError:
        return str(raw_id), None


def closeout_idempotency_key(lane_id: str, terminal_status: str) -> str:
    return f"{lane_id}:{terminal_status}"


def _terminal_status(row: dict[str, Any]) -> str:
    lifecycle = str(row.get("lifecycle") or "").lower()
    if lifecycle:
        return lifecycle
    return str(row.get("status") or "unknown").lower()


def _land_disposition_from_envelope(data: dict[str, Any]) -> str:
    """Lane merge action — not whether a cited SHA is already on master."""
    explicit = str(data.get("land_disposition") or "").strip().lower()
    if explicit:
        return explicit
    landed_flag = data.get("landed")
    if landed_flag is True:
        return "landed"
    if landed_flag is False:
        commits_raw = data.get("commits_ahead")
        try:
            commits = int(commits_raw) if commits_raw is not None else 0
        except (TypeError, ValueError):
            commits = 0
        if commits >= 1:
            return "unlanded"
        return "discard"
    return ""


def _parse_json_closeout_envelope(body: str) -> dict[str, Any] | None:
    """cursor-sdk CLOSEOUT bodies are a JSON envelope, not ``status:`` prose."""
    text = body.strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    settled = str(data.get("work_outcome") or data.get("status") or "").lower()
    landed_sha = ""
    evidence = data.get("evidence_uris")
    if isinstance(evidence, dict):
        refs = evidence.get("git_refs") or []
        if isinstance(refs, list) and refs:
            landed_sha = str(refs[0]).lower()
    parsed: dict[str, Any] = {
        "settled": settled,
        "landed": landed_sha,
        "next": "",
        "land_disposition": _land_disposition_from_envelope(data),
    }
    reason = str(data.get("degraded_reason") or "")
    summary = str(data.get("summary") or "")
    if reason == "conductor_row_pinned" or "ROW_PINNED" in summary:
        parsed["stop"] = "ROW_PINNED"
    commits_raw = data.get("commits_ahead")
    if commits_raw is not None:
        try:
            parsed["commits_ahead"] = int(commits_raw)
        except (TypeError, ValueError):
            pass
    return parsed


def _parse_lane_worker_closeout(text: str) -> dict[str, Any]:
    """Harvest settled / landed / next from a worker lane's terminal turn body."""
    body = str(text or "")
    envelope = _parse_json_closeout_envelope(body)
    if envelope is not None:
        return envelope
    status_match = _STATUS_RE.search(body)
    settled = status_match.group(1).lower() if status_match else ""
    next_match = _NEXT_RE.search(body)
    next_val = next_match.group(1).strip() if next_match else ""
    disposition_match = _DISPOSITION_RE.search(body)
    disposition = (
        disposition_match.group(1).strip().lower() if disposition_match else ""
    )
    landed = ""
    land_match = _LANDED_SHA_RE.search(body)
    if land_match:
        landed = land_match.group(1).lower()
    elif disposition == "landed":
        sha_match = _SHA_RE.search(body)
        if sha_match:
            landed = sha_match.group(1).lower()
    return {
        "settled": settled,
        "landed": landed,
        "next": next_val,
        "land_disposition": disposition,
    }


def _turn_is_worker_closeout(turn: dict[str, Any]) -> bool:
    subject = str(turn.get("subject") or "")
    body = str(turn.get("body") or "")
    upper = subject.upper()
    if "CLOSEOUT" in upper or "status:done" in upper.lower():
        return True
    if _STATUS_RE.search(body) and (
        "closeout" in upper.lower() or "TYPE: CLOSEOUT" in body.upper()
    ):
        return True
    return False


def _find_worker_closeout_turn(turns: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the CLOSEOUT turn with the greatest ``turn_number`` (API order agnostic)."""
    candidates: list[dict[str, Any]] = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        if _turn_is_worker_closeout(turn):
            candidates.append(turn)
    if not candidates:
        return None
    return max(candidates, key=lambda t: int(t.get("turn_number") or 0))


def build_closeout_record(
    lane: dict[str, Any],
    *,
    parent_root: str,
    worker_closeout_text: str = "",
    work_key: str = "",
    abandoned: bool = False,
    closed_at: str | None = None,
) -> dict[str, Any]:
    """Structured row posted on the parent root at terminal transition."""
    parsed = _parse_lane_worker_closeout(worker_closeout_text)
    landed = parsed["landed"]
    terminal_status = _terminal_status(lane)
    record: dict[str, Any] = {
        "lane": str(lane.get("id") or ""),
        "parent_root": parent_root,
        "slug": lane.get("slug"),
        "work_key": work_key,
        "settled": parsed["settled"],
        "landed": landed,
        "live": probe_live(lane.get("id"), landed, parent_root=parent_root),
        "next": parsed["next"],
        "terminal_status": terminal_status,
        "closed_at": closed_at or _utcnow_iso(),
        "abandoned": abandoned,
    }
    if parsed["land_disposition"]:
        record["land_disposition"] = parsed["land_disposition"]
    if parsed.get("stop"):
        record["stop"] = parsed["stop"]
    if "commits_ahead" in parsed:
        record["commits_ahead"] = parsed["commits_ahead"]
    return record


def format_closeout_body(record: dict[str, Any], *, abandoned: bool = False) -> str:
    prefix = ABANDONED_SUBJECT_PREFIX if abandoned else CLOSEOUT_SUBJECT_PREFIX
    payload = {k: v for k, v in record.items() if v not in (None, "")}
    return f"{prefix}\n{json.dumps(payload, sort_keys=True, separators=(',', ':'))}"


def format_closeout_subject(record: dict[str, Any], *, abandoned: bool = False) -> str:
    prefix = ABANDONED_SUBJECT_PREFIX if abandoned else CLOSEOUT_SUBJECT_PREFIX
    lane = record.get("lane") or "?"
    status = record.get("terminal_status") or "terminal"
    return f"{prefix} agent-bus:{lane} {status}"


def parse_closeout_turn(turn: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a parent-root ``LANE CLOSEOUT`` / ``LANE ABANDONED`` turn."""
    subject = str(turn.get("subject") or "")
    body = str(turn.get("body") or "")
    if not (
        subject.startswith(CLOSEOUT_SUBJECT_PREFIX)
        or subject.startswith(ABANDONED_SUBJECT_PREFIX)
        or body.startswith(CLOSEOUT_SUBJECT_PREFIX)
        or body.startswith(ABANDONED_SUBJECT_PREFIX)
    ):
        return None
    match = _LANE_CLOSEOUT_BODY_RE.search(body)
    if match:
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            abandoned = bool(
                parsed.get("abandoned")
                or subject.startswith(ABANDONED_SUBJECT_PREFIX)
                or body.startswith(ABANDONED_SUBJECT_PREFIX)
            )
            parsed.setdefault("abandoned", abandoned)
            live = str(parsed.get("live") or "unprobed")
            parsed["live"] = live if live in _LIVE_TRISTATE else "unprobed"
            return parsed
    return None


def query_lane_closeouts(
    client: httpx.Client,
    root_id: str,
    *,
    last: int = 200,
    now_fn: Callable[[], str] | None = None,
) -> list[dict[str, Any]]:
    """Return debrief rows from append-only parent-root closeout turns (AC2)."""
    payload = _get(client, "/turns", thread=root_id, last=last) or {}
    turns = payload.get("turns") if isinstance(payload, dict) else None
    if not isinstance(turns, list):
        return []
    ordered = sorted(
        (t for t in turns if isinstance(t, dict)),
        key=lambda t: int(t.get("turn_number") or 0),
        reverse=True,
    )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for turn in ordered:
        if not isinstance(turn, dict):
            continue
        record = parse_closeout_turn(turn)
        if record is None:
            continue
        lane = str(record.get("lane") or "")
        terminal = str(record.get("terminal_status") or "")
        key = closeout_idempotency_key(lane, terminal)
        if key in seen:
            continue
        seen.add(key)
        landed = str(record.get("landed") or "")
        record["live"] = probe_live(lane, landed, parent_root=root_id)
        if now_fn is not None:
            record["queried_at"] = now_fn()
        rows.append(record)
    return rows


def lane_status_query_pointer(root_id: str) -> str:
    return f"liaison-tick.py --root {root_id} --lane-status"


def _already_emitted(state: dict[str, Any], key: str) -> bool:
    emitted = state.get("lane_closeouts_emitted") or {}
    return bool(emitted.get(key))


def _projected_worker_turn(state: dict[str, Any], lane_id: str) -> int:
    raw = (state.get("lane_closeout_worker_turn") or {}).get(lane_id)
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def _mark_projected_worker_turn(
    state: dict[str, Any], lane_id: str, worker_turn: int
) -> None:
    projected = dict(state.get("lane_closeout_worker_turn") or {})
    projected[lane_id] = worker_turn
    state["lane_closeout_worker_turn"] = projected


def _mark_emitted(state: dict[str, Any], key: str) -> None:
    emitted = dict(state.get("lane_closeouts_emitted") or {})
    emitted[key] = _utcnow_iso()
    state["lane_closeouts_emitted"] = emitted


def post_lane_closeout(
    client: httpx.Client,
    *,
    parent_root: str,
    record: dict[str, Any],
    abandoned: bool = False,
) -> dict[str, Any] | None:
    """Post one structured closeout turn on ``parent_root``."""
    body = format_closeout_body(record, abandoned=abandoned)
    subject = format_closeout_subject(record, abandoned=abandoned)
    payload: dict[str, Any] = {
        "thread": parent_root,
        "to": "web-anthropic",
        "from": "cursor",
        "subject": subject,
        "body": body,
        "tags": [CLOSEOUT_TAG],
    }
    try:
        resp = client.post("/threads/send", json=payload)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"http_{resp.status_code}"}
    try:
        data = resp.json()
    except ValueError:
        return {"ok": False, "error": "non_json_response"}
    turn = data.get("turn") if isinstance(data, dict) else None
    turn_number = turn.get("turn_number") if isinstance(turn, dict) else None
    emit_lane_closeout_observed(
        root=parent_root,
        lane=str(record.get("lane") or ""),
        terminal_status=str(record.get("terminal_status") or ""),
        abandoned=abandoned,
        turn=int(turn_number) if turn_number is not None else None,
    )
    return {"ok": True, "turn_number": turn_number}


def observe_terminal_lane_closeouts(
    parent_root: str,
    lanes: list[dict[str, Any]],
    state: dict[str, Any],
    client: httpx.Client,
    *,
    fetch_turns: Callable[[str], Any],
) -> list[dict[str, Any]]:
    """Emit idempotent closeout/abandon rows when digest lanes go terminal."""
    posted: list[dict[str, Any]] = []
    for lane in lanes:
        if not isinstance(lane, dict) or not row_is_terminal(lane):
            continue
        lane_id = str(lane.get("id") or "")
        if not lane_id or lane_id == parent_root:
            continue
        terminal_status = _terminal_status(lane)
        key = closeout_idempotency_key(lane_id, terminal_status)
        abandoned = terminal_status == "abandoned"
        worker_text = ""
        turns_raw = fetch_turns(lane_id)
        turns = turns_raw if isinstance(turns_raw, list) else []
        closeout_turn = _find_worker_closeout_turn(
            [t for t in turns if isinstance(t, dict)]
        )
        worker_turn = (
            int(closeout_turn.get("turn_number") or 0) if closeout_turn else 0
        )
        if worker_turn > 0:
            if _projected_worker_turn(state, lane_id) >= worker_turn:
                continue
        elif _already_emitted(state, key):
            continue
        if closeout_turn is not None:
            worker_text = str(closeout_turn.get("body") or "")
        record = build_closeout_record(
            lane,
            parent_root=parent_root,
            worker_closeout_text=worker_text,
            abandoned=abandoned,
        )
        if worker_text and closeout_needs_friction_file(worker_text):
            friction_id, friction_error = file_closeout_friction(
                worker_text=worker_text,
                lane_id=lane_id,
                worker_turn=worker_turn,
            )
            if friction_id:
                record["friction_id"] = friction_id
            if friction_error:
                record["friction_error"] = friction_error
        result = post_lane_closeout(
            client,
            parent_root=parent_root,
            record=record,
            abandoned=abandoned,
        )
        if result and result.get("ok"):
            _mark_emitted(state, key)
            if worker_turn > 0:
                _mark_projected_worker_turn(state, lane_id, worker_turn)
            posted.append(record)
    return posted


def maybe_emit_abandoned_on_grace_expiry(
    grace: Any,
    producer: dict[str, Any],
    turn_count: int,
    *,
    parent_root: str,
    lane: dict[str, Any],
    state: dict[str, Any],
    client: httpx.Client,
) -> dict[str, Any] | None:
    """Post ``LANE ABANDONED`` when producer grace expires without closeout (AC5)."""
    if not grace.observe(producer, turn_count):
        return None
    lane_id = str(lane.get("id") or "")
    key = closeout_idempotency_key(lane_id, "abandoned")
    if _already_emitted(state, key):
        return None
    record = build_closeout_record(
        {**lane, "lifecycle": "abandoned"},
        parent_root=parent_root,
        abandoned=True,
    )
    result = post_lane_closeout(
        client,
        parent_root=parent_root,
        record=record,
        abandoned=True,
    )
    if result and result.get("ok"):
        _mark_emitted(state, key)
    return result


__all__ = [
    "ABANDONED_SUBJECT_PREFIX",
    "CLOSEOUT_SUBJECT_PREFIX",
    "CLOSEOUT_TAG",
    "build_closeout_record",
    "closeout_idempotency_key",
    "format_closeout_body",
    "format_closeout_subject",
    "lane_status_query_pointer",
    "maybe_emit_abandoned_on_grace_expiry",
    "observe_terminal_lane_closeouts",
    "parse_closeout_turn",
    "post_lane_closeout",
    "probe_live",
    "query_lane_closeouts",
]
