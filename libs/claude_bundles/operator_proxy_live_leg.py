"""Project the operator-proxy hop live leg from house-lane bus state."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from implement_admission.closeout_helpers import cortex_files_root
from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

_logger = logging.getLogger(__name__)

_ROSTER_REL = Path("notes/system/operational/maestro-sidekick-roster.md")
_FOLD_STATE_REL = Path("notes/system/operational/live-leg-fold-state.json")
_WARM_MAX_PAGES = 2
_EXTRA_HOUSE_LANES: tuple[str, ...] = ("15851", "12286")
_PAGE_SIZE = 200
_MAX_PAGES = 50
_BUS_TIMEOUT_S = 15.0

_SCHEME_WORK_KEY_PREFIXES: tuple[str, ...] = (
    "todo:",
    "plan:",
    "decision:",
    "friction:",
    "packet:",
    "agent-bus:",
)

_SUBJECT_RE = re.compile(
    r"^(?P<verb>[A-Z_]+)"
    r"(?:\|(?:(?P<kind>implement|investigate|freeform|verify)\|)?(?P<key>[a-z_]+:[^\s|]+))?"
)
_TURN_REF_RE = re.compile(r"#(\d+)")
_LANE_ID_RE = re.compile(r"(\d{5})")

GOVERNING_RULE: dict[str, str] = {
    "REQUEST": (
        "R5: no PICKUP|<work_key> within 10 min ⇒ ask a live Impresario house to recover "
        "the peer"
    ),
    "PICKUP": (
        "R3: read DONE / FAILED / QUESTION and nothing in between; answer QUESTION "
        "with RULING|<work_key>"
    ),
    "PLAN": ("R4: review the plan and post ACK or PARK before the implement leg"),
}

_REQUEST_CLOSE = frozenset({"DONE", "FAILED", "CANCEL", "WITHDRAW"})
_PICKUP_CLOSE = frozenset({"DONE", "FAILED"})
_PLAN_CLOSE = frozenset({"ACK", "PARK", "CANCEL"})
_ITEM_RETIRE_VERBS = frozenset(
    {"ACK", "PARK", "ACCEPT", "CANCEL", "WITHDRAW", "DONE", "FAILED"}
)
_WORK_KEY_IN_SUBJECT_RE = re.compile(r"[a-z_]+:[^\s|]+")


@dataclass(frozen=True, slots=True)
class OpenItem:
    lane: str
    turn: int
    verb: str
    kind: str
    key: str
    subject: str


@dataclass
class LiveLeg:
    items: list[OpenItem] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    truncated: bool = False
    lanes_queried: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MaestroRosterLoad:
    """Roster file read outcome for hop render (absent vs unreadable vs text)."""

    text: str
    unreadable: str | None = None


def load_maestro_roster_text() -> MaestroRosterLoad:
    """Load the maestro sidekick roster from the cortex files root.

    Missing file → empty text. ``OSError`` / ``UnicodeError`` on read → empty
    text with ``unreadable`` set to the exception class name (hop body surfaces it).
    """
    root = cortex_files_root()
    path = root / _ROSTER_REL
    if not path.is_file():
        return MaestroRosterLoad("")
    try:
        return MaestroRosterLoad(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        _logger.warning(
            "maestro roster unreadable; house lanes fall back to the fixed extras",
            exc_info=True,
        )
        return MaestroRosterLoad("", unreadable=type(exc).__name__)


def house_lanes(roster_text: str) -> list[str]:
    """Parse roster tables for lane ids, then append gc1/operator lanes."""
    seen: set[str] = set()
    ordered: list[str] = []
    for line in (roster_text or "").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) < 5 or cells[1] in {"N", "Display name", "---"}:
            continue
        if not cells[1].isdigit():
            continue
        match = _LANE_ID_RE.search(cells[3])
        if match is None:
            continue
        lane_id = match.group(1)
        if lane_id not in seen:
            seen.add(lane_id)
            ordered.append(lane_id)
    for extra in _EXTRA_HOUSE_LANES:
        if extra not in seen:
            seen.add(extra)
            ordered.append(extra)
    return ordered


def _parse_subject(subject: str) -> tuple[str, str, str]:
    text = (subject or "").strip()
    match = _SUBJECT_RE.match(text)
    if match is None:
        return "", "", ""
    verb = match.group("verb") or ""
    kind = match.group("kind") or ""
    key = match.group("key") or ""
    return verb, kind, key


def _scheme_work_key(work_key: str) -> bool:
    return any(work_key.startswith(prefix) for prefix in _SCHEME_WORK_KEY_PREFIXES)


def _work_keys_in_subject(subject: str) -> list[str]:
    return _WORK_KEY_IN_SUBJECT_RE.findall(subject or "")


def _subject_retires_lane_house(subject: str) -> bool:
    text = (subject or "").strip()
    if text.startswith(("CLOSE|", "CLOSED|")):
        return True
    return "CLOSE|house" in text or "CLOSED|house" in text


def _retire_work_keys(
    keys: list[str],
    *,
    open_requests: dict[str, tuple[int, str, str, str]],
    open_pickups: dict[str, tuple[int, str, str, str]],
    open_plans: dict[str, tuple[int, str, str, str]],
) -> None:
    for work_key in keys:
        open_requests.pop(work_key, None)
        open_pickups.pop(work_key, None)
        open_plans.pop(work_key, None)


def _cancel_targets_key(
    subject: str,
    open_requests: dict[str, int],
) -> str | None:
    """Match CANCEL|WITHDRAW by scheme key, else by a single explicit #turn ref."""
    _, _, key = _parse_subject(subject)
    if key:
        return key
    refs = [int(match.group(1)) for match in _TURN_REF_RE.finditer(subject or "")]
    if len(refs) != 1:
        return None
    req_turn = refs[0]
    matches = [
        work_key
        for work_key, start_turn in open_requests.items()
        if start_turn == req_turn
    ]
    if len(matches) != 1:
        return None
    matched = matches[0]
    if _scheme_work_key(matched):
        return None
    return matched


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def fetch_lane_status(lane: str) -> str | None:
    """Return agent-bus thread status for *lane*, or None when unavailable."""
    with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=_BUS_TIMEOUT_S) as client:
        resp = client.get(f"/threads/{lane}", headers=_auth_headers())
        if resp.status_code != 200:
            return None
        payload = resp.json()
    thread = payload.get("thread") if isinstance(payload, dict) else None
    if isinstance(thread, dict):
        status = thread.get("status")
        return str(status) if status is not None else None
    status = payload.get("status") if isinstance(payload, dict) else None
    return str(status) if status is not None else None


def fetch_turns_page(
    lane: str,
    *,
    after_turn: int = 0,
    last: int = _PAGE_SIZE,
    compact: bool = True,
    mark_read: bool = False,
) -> tuple[list[dict[str, Any]], bool, int | None]:
    """Fetch one compact page oldest-first; return (turns, truncated, next_after_turn)."""
    params = {
        "thread": str(lane),
        "last": last,
        "after_turn": after_turn,
        "compact": "true" if compact else "false",
        "mark_read": "true" if mark_read else "false",
    }
    with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=_BUS_TIMEOUT_S) as client:
        resp = client.get("/turns", params=params, headers=_auth_headers())
        if resp.status_code != 200:
            raise RuntimeError(
                f"agent-bus fetch HTTP {resp.status_code} for lane {lane}"
            )
        payload = resp.json()
    turns = list(payload.get("turns") or [])
    truncated = bool(payload.get("truncated"))
    next_after = payload.get("next_after_turn")
    turns.sort(key=lambda t: int(t.get("turn_number") or t.get("id") or 0))
    return turns, truncated, int(next_after) if next_after is not None else None


def fetch_turns_to_closure(
    lane: str,
    *,
    page_fetch: Callable[..., tuple[list[dict[str, Any]], bool, int | None]]
    | None = None,
    after_turn: int = 0,
    max_pages: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Page compact turns until thread start, a stop condition, or a bounded cap."""
    fetch = page_fetch or fetch_turns_page
    merged: dict[int, dict[str, Any]] = {}
    paging_truncated = False
    pages = 0
    page_limit = _MAX_PAGES if max_pages is None else max_pages
    while True:
        pages += 1
        page, truncated, next_after = fetch(
            lane, after_turn=after_turn, last=_PAGE_SIZE
        )
        max_seen_before = max(merged) if merged else 0
        for turn in page:
            turn_no = int(turn.get("turn_number") or turn.get("id") or 0)
            merged[turn_no] = turn
        if not page:
            if truncated:
                paging_truncated = True
            break
        if not truncated:
            break
        if pages >= page_limit:
            paging_truncated = True
            break

        if next_after is not None:
            if next_after <= after_turn or (
                max_seen_before and next_after <= max_seen_before
            ):
                paging_truncated = True
                break
            after_turn = next_after
        else:
            after_turn = max(
                int(t.get("turn_number") or t.get("id") or 0) for t in page
            )
    return [merged[k] for k in sorted(merged)], paging_truncated


def _production_fetch_turns(lane: str) -> list[dict[str, Any]]:
    turns, _ = fetch_turns_to_closure(lane)
    return turns


def _wrap_fetch_turns(
    fetch_turns: Callable[[str], list[dict]],
) -> Callable[[str], list[dict]]:
    """Use compact paging when the injected fetcher is the single-window bus helper."""
    mod = getattr(fetch_turns, "__module__", "")
    name = getattr(fetch_turns, "__name__", "")
    if (
        mod == "claude_bundles.operator_proxy_bus_fetch"
        and name == "fetch_turns_for_lane"
    ):
        return _production_fetch_turns
    return fetch_turns


def fold_lane(
    lane: str,
    turns: list[dict],
    *,
    lane_status: str | None = None,
) -> tuple[list[OpenItem], bool]:
    """Fold *turns* into open items for one house lane."""
    if (lane_status or "").strip().lower() == "closed":
        return [], False

    open_requests: dict[str, tuple[int, str, str, str]] = {}
    open_pickups: dict[str, tuple[int, str, str, str]] = {}
    open_plans: dict[str, tuple[int, str, str, str]] = {}
    request_start_turn: dict[str, int] = {}

    ordered_turns = sorted(
        turns,
        key=lambda t: int(t.get("turn_number") or t.get("id") or 0),
    )
    for turn in ordered_turns:
        turn_no = int(turn.get("turn_number") or turn.get("id") or 0)
        subject = str(turn.get("subject") or "")
        verb, kind, key = _parse_subject(subject)
        if not verb:
            continue

        if _subject_retires_lane_house(subject):
            open_requests.clear()
            open_pickups.clear()
            open_plans.clear()
            request_start_turn.clear()
            continue

        if verb == "REQUEST" and key:
            open_requests[key] = (turn_no, verb, kind, subject)
            request_start_turn[key] = turn_no
        elif verb in _REQUEST_CLOSE:
            close_key = key
            if verb in {"CANCEL", "WITHDRAW"} and not close_key:
                close_key = _cancel_targets_key(subject, request_start_turn)
            if close_key:
                open_requests.pop(close_key, None)
                if verb in _PICKUP_CLOSE:
                    open_pickups.pop(close_key, None)
        elif verb == "PICKUP" and key:
            open_pickups[key] = (turn_no, verb, kind, subject)
        elif verb == "PLAN" and key:
            open_plans[key] = (turn_no, verb, kind, subject)
        elif verb == "IMPLEMENT" and key:
            open_plans.pop(key, None)
        elif verb in _PLAN_CLOSE and key:
            open_plans.pop(key, None)

        if verb in _ITEM_RETIRE_VERBS:
            retire_keys = [key] if key else _work_keys_in_subject(subject)
            _retire_work_keys(
                retire_keys,
                open_requests=open_requests,
                open_pickups=open_pickups,
                open_plans=open_plans,
            )

    pickup_keys = set(open_pickups)
    for work_key in list(open_requests):
        if work_key in pickup_keys:
            open_requests.pop(work_key, None)

    items: list[OpenItem] = []
    for store in (open_requests, open_pickups, open_plans):
        for work_key, (turn_no, verb, kind, subject) in store.items():
            items.append(
                OpenItem(
                    lane=lane,
                    turn=turn_no,
                    verb=verb,
                    kind=kind,
                    key=work_key,
                    subject=subject,
                )
            )
    items.sort(key=lambda item: (item.lane, item.turn))
    return items, False


@dataclass
class LaneFoldState:
    watermark: int = 0
    open_keys: list[str] = field(default_factory=list)


FoldStateStore = Callable[[], dict[str, LaneFoldState] | None]
FoldStateSaver = Callable[[dict[str, LaneFoldState]], None]


def _default_fold_state_path() -> Path:
    return cortex_files_root() / _FOLD_STATE_REL


def _parse_lane_row(row: object) -> LaneFoldState | None:
    """Parse one lane row. None means the row is not usable and the lane cold-starts."""
    if not isinstance(row, dict):
        return None
    try:
        watermark = int(row.get("watermark") or 0)
    except (TypeError, ValueError):
        return None
    keys = row.get("open_keys") or []
    if not isinstance(keys, list):
        return None
    return LaneFoldState(watermark=watermark, open_keys=[str(k) for k in keys])


def load_fold_state(
    *,
    read_state: Callable[[], str | None] | None = None,
) -> dict[str, LaneFoldState] | None:
    """Load fold state. Corrupt JSON is empty writable state; OSError is unreadable."""
    if read_state is not None:
        try:
            raw = read_state()
        except OSError:
            _logger.warning("live-leg fold state unreadable", exc_info=True)
            return None
        except UnicodeError:
            _logger.warning(
                "live-leg fold state corrupt; treating as empty", exc_info=True
            )
            return {}
        if raw is None:
            return {}
    else:
        path = _default_fold_state_path()
        try:
            if not path.is_file():
                return {}
            raw = path.read_text(encoding="utf-8")
        except OSError:
            _logger.warning("live-leg fold state unreadable", exc_info=True)
            return None
        except UnicodeError:
            _logger.warning(
                "live-leg fold state corrupt; treating as empty", exc_info=True
            )
            return {}
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        _logger.warning("live-leg fold state corrupt; treating as empty", exc_info=True)
        return {}
    out: dict[str, LaneFoldState] = {}
    lanes = payload.get("lanes") if isinstance(payload, dict) else None
    if not isinstance(lanes, dict):
        return {}
    for lane_id, row in lanes.items():
        parsed = _parse_lane_row(row)
        if parsed is None:
            continue
        out[str(lane_id)] = parsed
    return out


def _fold_state_text(state: dict[str, LaneFoldState]) -> str:
    payload = {
        "lanes": {
            lane: {"watermark": st.watermark, "open_keys": list(st.open_keys)}
            for lane, st in state.items()
        }
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def save_fold_state(
    state: dict[str, LaneFoldState],
    *,
    write_state: Callable[[str], None] | None = None,
) -> None:
    """Persist fold state. The file write is a same-directory temp file plus rename."""
    text = _fold_state_text(state)
    if write_state is not None:
        try:
            write_state(text)
        except OSError:
            _logger.warning("live-leg fold state write failed", exc_info=True)
        return
    path = _default_fold_state_path()
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        _logger.warning("live-leg fold state write failed", exc_info=True)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            _logger.warning("live-leg fold state temp cleanup failed", exc_info=True)


def _stored_subject(item: OpenItem) -> str:
    verb, _kind, key = _parse_subject(item.subject)
    if verb and key:
        return item.subject.strip()
    kind_part = f"{item.kind}|" if item.kind else ""
    return f"{item.verb}|{kind_part}{item.key}"


def _item_key(item: OpenItem) -> str:
    """Persist lane, turn, and subject so a later fold keeps the original verb."""
    return f"{item.lane}:{item.turn}:{_stored_subject(item)}"


def _parse_stored_open_key(raw: str) -> OpenItem | None:
    """Load one open item. Legacy ``lane:turn:key`` carries verb REQUEST."""
    parts = (raw or "").split(":", 2)
    if len(parts) != 3:
        return None
    lane, turn_s, rest = parts
    if not lane or not rest:
        return None
    try:
        turn = int(turn_s)
    except (TypeError, ValueError):
        return None
    verb, kind, key = _parse_subject(rest)
    if verb and key:
        subject = rest
    else:
        verb, kind, key = "REQUEST", "", rest
        subject = f"REQUEST|{rest}"
    if not key:
        return None
    return OpenItem(
        lane=lane,
        turn=turn,
        verb=verb,
        kind=kind,
        key=key,
        subject=subject,
    )


def _carried_open_items(state: LaneFoldState) -> list[OpenItem] | None:
    """None when any stored key is unreadable, so the lane cold-starts."""
    items: list[OpenItem] = []
    for raw in state.open_keys:
        item = _parse_stored_open_key(str(raw))
        if item is None:
            return None
        items.append(item)
    return items


def _turn_number(turn: dict) -> int:
    try:
        return int(turn.get("turn_number") or turn.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _filter_live_leg_items(
    items: list[OpenItem],
    *,
    inflight_work_keys: frozenset[str] | None,
    hop_boundary_turn: int | None,
) -> list[OpenItem]:
    """L23: IN FLIGHT keys win; else only rows newer than the previous hop."""
    if inflight_work_keys is None:
        return items
    if inflight_work_keys:
        return [item for item in items if item.key in inflight_work_keys]
    if hop_boundary_turn is not None and hop_boundary_turn > 0:
        return [item for item in items if item.turn > hop_boundary_turn]
    return items


def project_live_leg(
    lanes: list[str],
    fetch_turns: Callable[[str], list[dict]],
    *,
    page_fetch: Callable[..., tuple[list[dict[str, Any]], bool, int | None]]
    | None = None,
    fold_state_load: FoldStateStore | None = None,
    fold_state_save: FoldStateSaver | None = None,
    inflight_work_keys: frozenset[str] | None = None,
    hop_boundary_turn: int | None = None,
) -> LiveLeg:
    """Query each lane and merge open items."""
    resolve = _wrap_fetch_turns(fetch_turns)
    leg = LiveLeg(lanes_queried=list(lanes))
    state_writable = False
    state_mut: dict[str, LaneFoldState] = {}
    if fold_state_load is not None:
        try:
            loaded = fold_state_load()
        except Exception:  # noqa: BLE001 — cold-start; a bad store must not raise
            _logger.warning("live-leg fold state load failed", exc_info=True)
            loaded = None
        if loaded is not None:
            state_mut = dict(loaded)
            state_writable = True
    elif page_fetch is not None or resolve is _production_fetch_turns:
        loaded = load_fold_state()
        if loaded is not None:
            state_mut = dict(loaded)
            state_writable = True

    for lane in lanes:
        try:
            lane_state = state_mut.get(lane) if state_writable else None
            carried: list[OpenItem] = []
            if lane_state is not None:
                parsed = _carried_open_items(lane_state)
                if parsed is None:
                    lane_state = None
                elif inflight_work_keys is not None:
                    if inflight_work_keys:
                        carried = [
                            item for item in parsed if item.key in inflight_work_keys
                        ]
                    elif hop_boundary_turn is not None and hop_boundary_turn > 0:
                        carried = [
                            item
                            for item in parsed
                            if item.turn > hop_boundary_turn
                        ]
                    else:
                        carried = []
                else:
                    carried = parsed
            warm = bool(lane_state and lane_state.watermark > 0)
            if resolve is _production_fetch_turns or page_fetch is not None:
                after = lane_state.watermark if warm else 0
                max_p = _WARM_MAX_PAGES if warm else _MAX_PAGES
                turns, lane_truncated = fetch_turns_to_closure(
                    lane,
                    page_fetch=page_fetch,
                    after_turn=after,
                    max_pages=max_p,
                )
                if lane_truncated:
                    leg.truncated = True
            else:
                turns = resolve(lane)
            lane_status: str | None = None
            if resolve is _production_fetch_turns:
                try:
                    lane_status = fetch_lane_status(lane)
                except Exception:  # noqa: BLE001 — degrade to turn-only retirement
                    lane_status = None
            synthetic = [
                {"turn_number": item.turn, "subject": item.subject} for item in carried
            ]
            items, _ = fold_lane(lane, synthetic + list(turns), lane_status=lane_status)
            leg.items.extend(items)
            if state_writable:
                old_watermark = lane_state.watermark if lane_state else 0
                max_turn = old_watermark
                for turn in turns:
                    max_turn = max(max_turn, _turn_number(turn))
                state_mut[lane] = LaneFoldState(
                    watermark=max_turn,
                    open_keys=[_item_key(i) for i in items],
                )
        except Exception as exc:  # noqa: BLE001 — render degraded line per lane
            leg.errors[lane] = str(exc)
            continue
    leg.items.sort(key=lambda item: (item.lane, item.turn))
    leg.items = _filter_live_leg_items(
        leg.items,
        inflight_work_keys=inflight_work_keys,
        hop_boundary_turn=hop_boundary_turn,
    )
    if state_writable:
        if fold_state_save is not None:
            try:
                fold_state_save(state_mut)
            except OSError:
                _logger.warning("live-leg fold state write failed", exc_info=True)
        else:
            save_fold_state(state_mut)
    return leg


def render_live_leg(
    leg: LiveLeg,
    *,
    declared_in_flight: int | None = None,
    max_bytes: int | None = None,
) -> str:
    """Render the live-leg block for the hop template."""
    lines: list[str] = []
    if declared_in_flight is not None:
        lines.append(f"declared_in_flight: {declared_in_flight}")
    if leg.truncated:
        lines.append(
            "(truncated: bus paging did not reach thread start; "
            "an open item may lie beyond the fetched window.)"
        )
    item_lines: list[str] = []
    for item in leg.items:
        rule = GOVERNING_RULE.get(item.verb, "")
        kind_part = f"{item.kind}|" if item.kind else ""
        item_lines.append(
            f"- lane {item.lane} #{item.turn} {item.verb}|{kind_part}{item.key} — "
            f"{rule}"
        )
    error_lines: list[str] = []
    for lane in leg.lanes_queried:
        if lane in leg.errors:
            error_lines.append(
                f"live leg unavailable for {lane}: {leg.errors[lane]}; "
                "read it with agent_bus_read fetch compact before any negative claim"
            )
    if max_bytes is None:
        body = item_lines + error_lines
        if not body:
            body = ["(no open REQUEST, PICKUP, or PLAN items)"]
        return "\n".join(lines + body) if lines else "\n".join(body)

    from claude_bundles.operator_proxy_standing_render import cap_utf8_for_hop

    prefix = "\n".join(lines)
    prefix_b = len(prefix.encode("utf-8")) + (1 if prefix and item_lines else 0)
    err_b = sum(len(s.encode("utf-8")) + 1 for s in error_lines)
    item_budget = max(0, max_bytes - prefix_b - err_b)
    capped_items = (
        cap_utf8_for_hop("\n".join(item_lines), item_budget, label="live_leg")
        if item_lines
        else ""
    )
    parts = [p for p in (prefix, capped_items, "\n".join(error_lines)) if p]
    if not parts:
        return "(no open REQUEST, PICKUP, or PLAN items)"
    return "\n".join(parts)


__all__ = [
    "GOVERNING_RULE",
    "LaneFoldState",
    "LiveLeg",
    "MaestroRosterLoad",
    "OpenItem",
    "load_fold_state",
    "save_fold_state",
    "fetch_lane_status",
    "fetch_turns_page",
    "fetch_turns_to_closure",
    "fold_lane",
    "house_lanes",
    "load_maestro_roster_text",
    "project_live_leg",
    "render_live_leg",
]
