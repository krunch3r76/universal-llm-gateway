"""Standing concurrency + ambient census meter for fold-2 falsifier telemetry.

# row10-probe-A: branch-isolation window — no behavior change
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_capacity_invariant import (
    Lane,
    resolve_admit_lane,
)

HISTORICAL_INCLUSION_RULE = (
    "corrected peaks: contract in {implement, light-bounded} only; "
    "exclude contract_unknown (NULL contract); exclude lane_unknown "
    "(NULL lease_key and source_repo); exclude reaper-inflated terminals "
    "(terminal_at >> last_heartbeat_at); overlap pairs lane-scoped"
)
from services.git_integration_worker.cursor_sdk_deliverables import (
    STRUCTURED_CLOSEOUT_FULL_HEADING,
    sidecar_has_structured_closeout_full,
)

# Commit 10811941 — own-commit path attribution floor (fold-2 census).
ATTRIBUTION_FLOOR_ISO = "2026-08-01T22:24:33+00:00"
_IMPLEMENT_CLASS_CONTRACTS = frozenset({"implement", "light-bounded"})


@dataclass(frozen=True, slots=True)
class DispatchInterval:
    """Wall-clock dispatch interval from ledger ``started_at`` / ``terminal_at``."""

    dispatch_id: str
    contract: str | None
    read_only: bool
    started_at: str
    terminal_at: str
    lane: Lane = "A"
    last_heartbeat_at: str | None = None


def _parse_iso(ts: str) -> datetime:
    normalized = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def is_contract_unknown(*, contract: str | None, read_only: bool) -> bool:
    """True when contract is NULL on a non-read-only row (D1 contract_unknown bucket)."""
    return not read_only and contract is None


def is_declared_write_implement(*, contract: str | None, read_only: bool) -> bool:
    """Implement-class rows with explicit contract only (D1 — no NULL fallback)."""
    if read_only:
        return False
    return contract in _IMPLEMENT_CLASS_CONTRACTS


def is_implement_class(*, contract: str | None, read_only: bool) -> bool:
    """Legacy implement-class predicate (includes NULL contract — legacy peaks)."""
    if read_only:
        return False
    if contract in _IMPLEMENT_CLASS_CONTRACTS:
        return True
    return contract is None


def is_reaper_inflated_terminal(
    *,
    terminal_at: str | None,
    last_heartbeat_at: str | None,
) -> bool:
    """True when terminal_at is far after last heartbeat (reaper stamp, D3)."""
    if not terminal_at or not last_heartbeat_at:
        return False
    gap_s = (_parse_iso(terminal_at) - _parse_iso(last_heartbeat_at)).total_seconds()
    return gap_s > 3600.0


def is_write_implement(interval: DispatchInterval, *, corrected: bool = False) -> bool:
    if corrected:
        if is_reaper_inflated_terminal(
            terminal_at=interval.terminal_at,
            last_heartbeat_at=interval.last_heartbeat_at,
        ):
            return False
        return is_declared_write_implement(
            contract=interval.contract, read_only=interval.read_only
        )
    return is_implement_class(contract=interval.contract, read_only=interval.read_only)


def peak_concurrent(intervals: list[DispatchInterval], *, write_only: bool) -> int:
    """Peak simultaneous intervals; end boundary decrements before start increments."""
    return peak_concurrent_for_lane(intervals, write_only=write_only, lane=None)


def peak_concurrent_for_lane(
    intervals: list[DispatchInterval],
    *,
    write_only: bool,
    lane: Lane | None,
    corrected: bool = False,
) -> int:
    """Peak concurrency optionally filtered to one admit lane."""
    events: list[tuple[datetime, int]] = []
    for row in intervals:
        if lane is not None and row.lane != lane:
            continue
        if lane is not None and corrected and row.lane == "unknown":
            continue
        if write_only and not is_write_implement(row, corrected=corrected):
            continue
        start = _parse_iso(row.started_at)
        end = _parse_iso(row.terminal_at)
        if end <= start:
            continue
        events.append((start, 1))
        events.append((end, -1))
    events.sort(key=lambda item: (item[0], item[1]))
    peak = 0
    active = 0
    for _, delta in events:
        active += delta
        if active > peak:
            peak = active
    return peak


def _legacy_peak_concurrent(intervals: list[DispatchInterval], *, write_only: bool) -> int:
    """Original peak counter — kept for AC3 byte-identical regression guard."""
    events: list[tuple[datetime, int]] = []
    for row in intervals:
        if write_only and not is_write_implement(row):
            continue
        start = _parse_iso(row.started_at)
        end = _parse_iso(row.terminal_at)
        if end <= start:
            continue
        events.append((start, 1))
        events.append((end, -1))
    events.sort(key=lambda item: (item[0], item[1]))
    peak = 0
    active = 0
    for _, delta in events:
        active += delta
        if active > peak:
            peak = active
    return peak


def count_overlap_pairs(
    intervals: list[DispatchInterval],
    *,
    write_only: bool,
    lane: Lane | None = None,
    corrected: bool = False,
) -> int:
    """Count unordered pairs with strictly positive overlap duration."""
    selected = [
        row
        for row in intervals
        if (lane is None or row.lane == lane)
        and (not write_only or is_write_implement(row, corrected=corrected))
        and not (corrected and row.lane == "unknown")
    ]
    total = 0
    for left in range(len(selected)):
        a_start = _parse_iso(selected[left].started_at)
        a_end = _parse_iso(selected[left].terminal_at)
        for right in range(left + 1, len(selected)):
            b_start = _parse_iso(selected[right].started_at)
            b_end = _parse_iso(selected[right].terminal_at)
            if a_start < b_end and b_start < a_end:
                total += 1
    return total


def _extract_structured_closeout_json(sidecar_text: str) -> dict[str, Any] | None:
    marker = f"{STRUCTURED_CLOSEOUT_FULL_HEADING}\n\n"
    if marker not in sidecar_text:
        return None
    json_text = sidecar_text.split(marker, 1)[1].strip()
    if not json_text:
        return None
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def classify_closeout_receipt(*, sidecar_text: str, dispatch_id: str) -> str:
    """Return ``parseable`` | ``unparseable`` | ``key_absent`` for censoring triple."""
    if not sidecar_has_structured_closeout_full(sidecar_text):
        return "key_absent"
    payload = _extract_structured_closeout_json(sidecar_text)
    if payload is None:
        return "unparseable"
    if payload.get("schema_version") != 1:
        return "unparseable"
    if "files_ambient_repo_movement" not in payload:
        return "key_absent"
    if dispatch_id not in json.dumps(payload, sort_keys=True):
        return "unparseable"
    return "parseable"


def has_ambient_star_cause(payload: dict[str, Any]) -> bool:
    """True when any movement row carries an ``ambient:*`` cause prefix."""
    movements = payload.get("files_ambient_repo_movement") or []
    if not isinstance(movements, list):
        return False
    for entry in movements:
        if not isinstance(entry, dict):
            continue
        cause = str(entry.get("cause", ""))
        if cause.startswith("ambient:"):
            return True
    return False


def ambient_cause_filtered_stats(
    *,
    ledger_rows: list[dict[str, Any]],
    closeout_root: Path,
    floor_iso: str = ATTRIBUTION_FLOOR_ISO,
    lane_filter: Lane | None = None,
) -> dict[str, Any]:
    """Post-floor ``ambient:*`` rate with explicit censoring counts."""
    floor = _parse_iso(floor_iso)
    n_parseable = 0
    n_unparseable = 0
    n_key_absent = 0
    post_floor_n_parseable = 0
    post_floor_ambient_star = 0

    for row in ledger_rows:
        contract = row.get("contract")
        read_only = bool(row.get("read_only"))
        if not is_implement_class(contract=contract, read_only=read_only):
            continue
        row_lane = resolve_admit_lane(
            record_json=row.get("record_json"),
            lease_key=row.get("lease_key"),
            source_repo=row.get("source_repo"),
        )
        if lane_filter is not None and row_lane != lane_filter:
            continue
        dispatch_id = str(row["dispatch_id"])
        terminal_at = row.get("terminal_at")
        sidecar_path = closeout_root / f"{dispatch_id}.md"
        sidecar_text = sidecar_path.read_text(encoding="utf-8") if sidecar_path.is_file() else ""
        bucket = classify_closeout_receipt(
            sidecar_text=sidecar_text,
            dispatch_id=dispatch_id,
        )
        if bucket == "parseable":
            n_parseable += 1
        elif bucket == "unparseable":
            n_unparseable += 1
            continue
        else:
            n_key_absent += 1
            continue

        payload = _extract_structured_closeout_json(sidecar_text)
        if terminal_at is None:
            continue
        if _parse_iso(str(terminal_at)) < floor:
            continue
        post_floor_n_parseable += 1
        if has_ambient_star_cause(payload):
            post_floor_ambient_star += 1

    rate = (
        f"{post_floor_ambient_star}/{post_floor_n_parseable}"
        if post_floor_n_parseable
        else "0/0"
    )
    return {
        "n_parseable": n_parseable,
        "n_unparseable": n_unparseable,
        "n_key_absent": n_key_absent,
        "post_floor_n_parseable": post_floor_n_parseable,
        "post_floor_ambient_star": post_floor_ambient_star,
        "post_floor_rate": rate,
        "attribution_floor": floor_iso,
    }


def intervals_from_ledger_rows(rows: list[dict[str, Any]]) -> list[DispatchInterval]:
    out: list[DispatchInterval] = []
    for row in rows:
        started_at = row.get("started_at")
        terminal_at = row.get("terminal_at")
        if not started_at or not terminal_at:
            continue
        lane = resolve_admit_lane(
            record_json=row.get("record_json"),
            lease_key=row.get("lease_key"),
            source_repo=row.get("source_repo"),
        )
        out.append(
            DispatchInterval(
                dispatch_id=str(row["dispatch_id"]),
                contract=row.get("contract"),
                read_only=bool(row.get("read_only")),
                started_at=str(started_at),
                terminal_at=str(terminal_at),
                lane=lane,
                last_heartbeat_at=(
                    str(row["last_heartbeat_at"])
                    if row.get("last_heartbeat_at")
                    else None
                ),
            )
        )
    return out


def lane_b_inventory_snapshot(*, source_repo: Path) -> dict[str, Any]:
    """Live Lane-B worktree / unlanded-branch counters for active-work probes."""
    from services.git_integration_worker.cursor_dispatch_ledger import _connect
    from services.git_integration_worker.cursor_sdk_lane_b_commit import branch_state

    worktrees_live = 0
    branches_unlanded = 0
    oldest_unlanded_age_s: float | None = None
    now = datetime.now().astimezone()

    with _connect() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cursor_sdk_dispatch_worktrees ("
            "dispatch_id TEXT PRIMARY KEY, worktree_path TEXT NOT NULL, "
            "branch_name TEXT NOT NULL, branch_point TEXT NOT NULL, minted_at TEXT NOT NULL)"
        )
        rows = conn.execute(
            "SELECT worktree_path, branch_name, branch_point, minted_at "
            "FROM cursor_sdk_dispatch_worktrees"
        ).fetchall()

    repo = source_repo.resolve()
    for row in rows:
        wt_path = Path(row["worktree_path"])
        if wt_path.is_dir():
            worktrees_live += 1
        state = branch_state(
            repo,
            branch_name=row["branch_name"],
            branch_point=row["branch_point"],
        )
        if state.head_sha is None or state.merged_into_master:
            continue
        branches_unlanded += 1
        minted_at = row["minted_at"]
        if minted_at:
            age_s = (now - _parse_iso(str(minted_at))).total_seconds()
            if oldest_unlanded_age_s is None or age_s > oldest_unlanded_age_s:
                oldest_unlanded_age_s = age_s

    return {
        "worktrees_live": worktrees_live,
        "branches_unlanded": branches_unlanded,
        "oldest_unlanded_age_s": oldest_unlanded_age_s,
    }


def active_work_lane_fields(*, source_repo: Path) -> dict[str, Any]:
    """``lane_b_regime`` + inventory block for ``/active-work`` and concurrency-stats."""
    from services.git_integration_worker.cursor_sdk_lane_regime import lane_b_regime_active

    return {
        "lane_b_regime": lane_b_regime_active(),
        "lane_b": lane_b_inventory_snapshot(source_repo=source_repo),
    }


def concurrency_stats(
    *,
    ledger: CursorDispatchLedger | None = None,
    closeout_root: Path,
    window_start: str | None = None,
    window_end: str | None = None,
    floor_iso: str = ATTRIBUTION_FLOOR_ISO,
    source_repo: Path | None = None,
) -> dict[str, Any]:
    """Rolling-window peak write-implement overlap + post-floor ambient:* census."""
    ledger = ledger or CursorDispatchLedger.instance()
    interval_rows = ledger.interval_rows_in_window(
        window_start=window_start,
        window_end=window_end,
    )
    intervals = intervals_from_ledger_rows(interval_rows)
    ambient = ambient_cause_filtered_stats(
        ledger_rows=interval_rows,
        closeout_root=closeout_root,
        floor_iso=floor_iso,
    )
    ambient_by_lane = {
        lane: ambient_cause_filtered_stats(
            ledger_rows=interval_rows,
            closeout_root=closeout_root,
            floor_iso=floor_iso,
            lane_filter=lane,
        )
        for lane in ("A", "B")
    }
    repo = source_repo or closeout_root.parent.parent.parent
    lane_b_inventory = lane_b_inventory_snapshot(source_repo=repo)
    legacy_by_lane = {
        lane: peak_concurrent_for_lane(intervals, write_only=True, lane=lane)
        for lane in ("A", "B")
    }
    corrected_by_lane = {
        lane: peak_concurrent_for_lane(
            intervals, write_only=True, lane=lane, corrected=True
        )
        for lane in ("A", "B")
    }
    return {
        "window_start": window_start,
        "window_end": window_end,
        "historical_inclusion_rule": HISTORICAL_INCLUSION_RULE,
        "peak_concurrent_write_implement": _legacy_peak_concurrent(
            intervals, write_only=True
        ),
        "peak_concurrent_write_implement_by_lane": legacy_by_lane,
        "peak_concurrent_write_implement_by_lane_corrected": corrected_by_lane,
        "write_overlap_pairs": count_overlap_pairs(intervals, write_only=True),
        "write_overlap_pairs_corrected": count_overlap_pairs(
            intervals, write_only=True, corrected=True
        ),
        "ambient_cause_filtered": ambient,
        "ambient_rate_by_lane": {
            lane: ambient_by_lane[lane]["post_floor_rate"]
            for lane in ("A", "B")
        },
        "lane_b_worktrees_live": lane_b_inventory["worktrees_live"],
        "lane_b_branches_unlanded": lane_b_inventory["branches_unlanded"],
        "lane_b_oldest_unlanded_age_s": lane_b_inventory["oldest_unlanded_age_s"],
    }
