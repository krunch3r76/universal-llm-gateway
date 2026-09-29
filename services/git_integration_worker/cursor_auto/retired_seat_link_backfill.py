"""Stamp operator cdp-generate links orphaned when a hop overwrote the watch.

Dry-run is the default. ``--stamp`` POSTs the same ``dispatch-terminate`` payload
the seating hook uses, and only after the stop rule accepts the match set.

The stop rule refuses the stamp when ``match`` contains an id outside the five
links observed at 2026-09-29T09:11Z, unless that id's holder is ``superseded``
and a ``TYPE: SEAT_REGISTRATION`` after 09:11Z names a different birth. It also
refuses when ``exclude`` is not the execution named by the newest
``TYPE: SEAT_REGISTRATION`` on the lane.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_bundles.cse_url import normalize_cse_url

from services.git_integration_worker.cursor_auto.cse_seating_hook import (
    post_retired_operator_link,
)

# Links open on 12286 at the 2026-09-29T09:11:47Z read, excluding the live operator.
KNOWN_ORPHAN_IDS = frozenset(
    {
        "140c033e-6e8f-4072-8ef7-ba1c06ad3d3f",
        "b7cade49-513a-4c83-b5a3-28c0a7657663",
        "68155936-aa4d-4758-a944-50e4d7d969e8",
        "88a53ac0-fc39-4d2e-84a9-2e27ae7faf06",
        "662daf5d-c192-46fa-bec3-066aa4284f1a",
    }
)
# A hop after this instant may retire one more id than the five above.
_HOP_AFTER = datetime(2026, 9, 29, 9, 11, tzinfo=UTC)
_DEFAULT_LANE = "12286"
_RETIRED_HOLDER_STATES = frozenset({"superseded", "released"})
_EXEC_RE = re.compile(
    r"execution_id\s*[:=]\s*"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)
_BIRTH_RE = re.compile(r"successor_birth_id\s*[:=]\s*([0-9a-fA-F]{32})")

PostFn = Callable[..., bool]


@dataclass(frozen=True)
class BackfillPlan:
    """One read of the watch, holders, open links, and seat-registration turns."""

    lane: str
    open_cdp_before: int
    exclude: str
    match_ids: tuple[str, ...]
    exclude_link_open: bool
    newest_registration_execution: str
    driving_holder_count: int
    unexpected_ids: tuple[str, ...]


def _ro_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_ts(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _execution_named(body: str) -> str:
    """First ``execution_id`` field in a seat-registration body."""
    match = _EXEC_RE.search(body or "")
    return match.group(1) if match else ""


def _birth_named(body: str) -> str:
    match = _BIRTH_RE.search(body or "")
    return match.group(1) if match else ""


def _url_key(url: str | None) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    return normalize_cse_url(raw) or raw


def live_paths() -> tuple[Path, Path, Path]:
    """Watch file, bus DB, and holder ledger under the operator home.

    Dispatch HOME overlays are not the files the hop wrote.
    """
    from services.git_integration_worker.cursor_home import operator_real_home

    home = operator_real_home()
    watch = home / ".gateway" / "cdp-registry" / "hop_cadence_watches.json"
    ledger = home / ".gateway" / "cursor-sdk-dispatch.db"
    bus_env = os.environ.get("AGENT_BUS_DB_PATH", "").strip()
    bus = Path(bus_env) if bus_env else home / ".agent-bus" / "messages.db"
    if "cursor-dispatch-homes" in bus.as_posix():
        bus = home / ".agent-bus" / "messages.db"
    return watch, bus, ledger


def _watch_execution(watch_path: Path, lane: str) -> str:
    if not watch_path.is_file():
        return ""
    try:
        raw = json.loads(watch_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(raw, dict):
        return ""
    row = raw.get(lane)
    if not isinstance(row, dict):
        return ""
    return str(row.get("execution_id") or "").strip()


def _load_holders(ledger_path: Path, lane: str) -> list[dict[str, Any]]:
    if not ledger_path.is_file():
        return []
    conn = _ro_connect(ledger_path)
    try:
        rows = conn.execute(
            "SELECT chat_url, registration_id, seat_state, execution_id "
            "FROM cse_session_holders WHERE lane_thread_id=?",
            (lane,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _load_open_links(bus_path: Path, lane: str) -> list[dict[str, Any]]:
    if not bus_path.is_file():
        return []
    conn = _ro_connect(bus_path)
    try:
        rows = conn.execute(
            "SELECT execution_id, pipeline_id, terminal_status, chat_url, linked_at "
            "FROM thread_dispatch_links "
            "WHERE thread_id=? AND terminal_status IS NULL "
            "ORDER BY linked_at, execution_id",
            (lane,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _load_seat_registrations(bus_path: Path, lane: str) -> list[dict[str, Any]]:
    if not bus_path.is_file():
        return []
    conn = _ro_connect(bus_path)
    try:
        rows = conn.execute(
            "SELECT turn_number, created_at, subject, body FROM turns "
            "WHERE thread=? AND subject LIKE 'TYPE: SEAT_REGISTRATION%' "
            "ORDER BY turn_number",
            (lane,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _newest_registration_execution(turns: list[dict[str, Any]]) -> str:
    if not turns:
        return ""
    newest = max(turns, key=lambda row: int(row["turn_number"]))
    return _execution_named(str(newest.get("body") or ""))


def _holder_states_for_url(
    chat_url: str | None,
    holders: list[dict[str, Any]],
) -> set[str]:
    key = _url_key(chat_url)
    if not key:
        return set()
    return {
        str(row.get("seat_state") or "")
        for row in holders
        if _url_key(str(row.get("chat_url") or "")) == key
    }


def _registration_names_other_birth(
    execution_id: str,
    *,
    driving_registration: str,
    turns: list[dict[str, Any]],
) -> bool:
    """True when some seat registration quotes *execution_id* under another birth."""
    if not driving_registration:
        return False
    for turn in turns:
        body = str(turn.get("body") or "")
        if execution_id not in body:
            continue
        birth = _birth_named(body)
        if birth and birth != driving_registration:
            return True
    return False


def _retired_after_cutoff(
    link: dict[str, Any],
    *,
    holders: list[dict[str, Any]],
    turns: list[dict[str, Any]],
) -> bool:
    """True when a hop after 09:11Z left this link's holder superseded."""
    states = _holder_states_for_url(link.get("chat_url"), holders)
    if "superseded" not in states:
        return False
    key = _url_key(link.get("chat_url"))
    regs = {
        str(row.get("registration_id") or "").strip()
        for row in holders
        if _url_key(str(row.get("chat_url") or "")) == key
        and str(row.get("seat_state") or "") == "superseded"
    }
    regs.discard("")
    if not regs:
        return False
    for turn in turns:
        created = _parse_ts(str(turn.get("created_at") or ""))
        if created is None or created <= _HOP_AFTER:
            continue
        birth = _birth_named(str(turn.get("body") or ""))
        if birth and birth not in regs:
            return True
    return False


def plan_backfill(
    *,
    lane: str,
    watch_path: Path,
    bus_db: Path,
    ledger_db: Path,
) -> BackfillPlan:
    """Re-read watch, driving holder, and open links. Does not write."""
    exclude = _watch_execution(watch_path, lane)
    holders = _load_holders(ledger_db, lane)
    links = _load_open_links(bus_db, lane)
    turns = _load_seat_registrations(bus_db, lane)
    driving = [row for row in holders if str(row.get("seat_state") or "") == "driving"]
    driving_registration = ""
    if len(driving) == 1:
        driving_registration = str(driving[0].get("registration_id") or "").strip()
    open_cdp = [
        row for row in links if str(row.get("pipeline_id") or "") == "cdp-generate"
    ]
    open_ids = {str(row["execution_id"]) for row in open_cdp}
    match: list[str] = []
    for row in open_cdp:
        exec_id = str(row["execution_id"])
        if exec_id == exclude:
            continue
        chat_url = str(row.get("chat_url") or "").strip()
        states = _holder_states_for_url(chat_url, holders)
        by_holder = bool(chat_url) and bool(states & _RETIRED_HOLDER_STATES)
        by_registration = (not chat_url) and _registration_names_other_birth(
            exec_id,
            driving_registration=driving_registration,
            turns=turns,
        )
        if by_holder or by_registration:
            match.append(exec_id)
    unexpected = tuple(
        exec_id
        for exec_id in match
        if exec_id not in KNOWN_ORPHAN_IDS
        and not _retired_after_cutoff(
            next(row for row in open_cdp if str(row["execution_id"]) == exec_id),
            holders=holders,
            turns=turns,
        )
    )
    return BackfillPlan(
        lane=lane,
        open_cdp_before=len(open_cdp),
        exclude=exclude,
        match_ids=tuple(match),
        exclude_link_open=bool(exclude) and exclude in open_ids,
        newest_registration_execution=_newest_registration_execution(turns),
        driving_holder_count=len(driving),
        unexpected_ids=unexpected,
    )


def _refusal(plan: BackfillPlan) -> list[str]:
    """Stop-rule lines. Empty when a stamp is allowed."""
    if not plan.exclude or not plan.exclude_link_open:
        return ["stamp_refused=exclude_unverified", f"exclude={plan.exclude}"]
    if plan.driving_holder_count != 1:
        return [
            "stamp_refused=driving_holder_count",
            f"driving_holder_count={plan.driving_holder_count}",
        ]
    if plan.exclude != plan.newest_registration_execution:
        return [
            "stamp_refused=exclude_not_newest_seat_registration",
            f"exclude={plan.exclude}",
            f"newest_seat_registration={plan.newest_registration_execution}",
        ]
    if plan.unexpected_ids:
        lines = ["stamp_refused=match_outside_allowlist"]
        lines.extend(f"match={exec_id}" for exec_id in plan.match_ids)
        return lines
    return []


def _open_cdp_count(bus_db: Path, lane: str) -> int:
    return sum(
        1
        for row in _load_open_links(bus_db, lane)
        if str(row.get("pipeline_id") or "") == "cdp-generate"
    )


def _excluded_still_null(bus_db: Path, lane: str, exclude: str) -> str:
    if not exclude:
        return ""
    for row in _load_open_links(bus_db, lane):
        if (
            str(row["execution_id"]) == exclude
            and str(row.get("pipeline_id") or "") == "cdp-generate"
        ):
            return exclude
    return ""


def _link_still_open(bus_db: Path, lane: str, execution_id: str) -> bool:
    for row in _load_open_links(bus_db, lane):
        if (
            str(row["execution_id"]) == execution_id
            and str(row.get("pipeline_id") or "") == "cdp-generate"
        ):
            return True
    return False


def render_backfill(
    plan: BackfillPlan,
    *,
    stamp: bool,
    bus_db: Path,
    post: PostFn | None = None,
) -> list[str]:
    """Dry-run lines, then either a refusal or the post-stamp counts."""
    lines = [
        f"open_cdp_before={plan.open_cdp_before}",
        f"exclude={plan.exclude}",
    ]
    lines.extend(f"match={exec_id}" for exec_id in plan.match_ids)
    if not stamp:
        return lines
    refused = _refusal(plan)
    if refused:
        return lines + refused
    poster = post or post_retired_operator_link
    for exec_id in plan.match_ids:
        if not _link_still_open(bus_db, plan.lane, exec_id):
            continue
        if not poster(lane=plan.lane, execution_id=exec_id):
            lines.append(f"stamp_failed={exec_id}")
    lines.append(f"open_cdp_after={_open_cdp_count(bus_db, plan.lane)}")
    lines.append(
        f"excluded_still_null={_excluded_still_null(bus_db, plan.lane, plan.exclude)}"
    )
    return lines


def execute_backfill(
    *,
    lane: str,
    watch_path: Path,
    bus_db: Path,
    ledger_db: Path,
    stamp: bool,
    post: PostFn | None = None,
) -> list[str]:
    """Read, print the plan, and stamp only when ``stamp`` and the stop rule pass."""
    plan = plan_backfill(
        lane=lane,
        watch_path=watch_path,
        bus_db=bus_db,
        ledger_db=ledger_db,
    )
    return render_backfill(plan, stamp=stamp, bus_db=bus_db, post=post)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run (default) or stamp retired operator cdp-generate links.",
    )
    parser.add_argument(
        "--stamp",
        action="store_true",
        help="POST dispatch-terminate for each match. Default is dry-run.",
    )
    parser.add_argument("--lane", default=_DEFAULT_LANE)
    args = parser.parse_args(argv)
    watch, bus_db, ledger = live_paths()
    lines = execute_backfill(
        lane=str(args.lane),
        watch_path=watch,
        bus_db=bus_db,
        ledger_db=ledger,
        stamp=bool(args.stamp),
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
