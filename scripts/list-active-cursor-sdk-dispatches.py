#!/usr/bin/env python3
"""List cursor-sdk dispatches from the dispatch ledger (live + optional recent terminal).

Reads ``~/.gateway/cursor-sdk-dispatch.db`` (or ``CURSOR_SDK_DISPATCH_LEDGER``).
Times print in local timezone (``TZ`` when set, else system local).

Usage::

  ./scripts/list-active-cursor-sdk-dispatches.py
  ./scripts/list-active-cursor-sdk-dispatches.py --finished-hours 24
  ./scripts/list-active-cursor-sdk-dispatches.py --json --no-color
  ./scripts/list-active-cursor-sdk-dispatches.py --debug --finished-hours 12
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ruff: noqa: I001
from implement_admission.source_ref import todo_slug_from_ref  # noqa: E402

from services.git_integration_worker.cursor_dispatch_ledger import (  # noqa: E402
    resolve_cursor_sdk_dispatch_ledger_path,
)
from services.git_integration_worker.cursor_sdk_bridge_stderr import (  # noqa: E402
    _STDERR_DIR,
)
from services.git_integration_worker.cursor_sdk_context import steer_spool_dir  # noqa: E402
from services.git_integration_worker.cursor_sdk_row_density_harness_meter import (  # noqa: E402
    read_density_harness_meter,
)

_LIVE = ("queued", "admitted", "running", "parked_waiting")
_FINISHED = ("completed", "failed", "cancelled")
_CLAIM = "claim"
_LEDGER_SUFFIX = ".cdp-generates.jsonl"

_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_MAGENTA = "\033[35m"
_RED = "\033[31m"
_BLUE = "\033[34m"

_STATUS_COLOR = {
    "running": _GREEN,
    "admitted": _CYAN,
    "queued": _YELLOW,
    "parked_waiting": _MAGENTA,
    "completed": _GREEN,
    "failed": _RED,
    "cancelled": _DIM,
}


@dataclass(frozen=True)
class DispatchRow:
    phase: str
    dispatch_id: str
    execution_id: str | None
    status: str
    slug: str
    origin: str
    model: str
    contract: str | None
    thread_id: str
    work_key: str | None
    lane: str | None
    nest_under: str | None
    tool_call_count: int | None
    context_used: int | None
    context_limit: int | None
    transcript_tail: str
    fast: bool | None
    effort: str | None
    started_at: str | None
    last_activity_at: str | None
    terminal_at: str | None
    duration: str | None
    idle: str
    started_at_utc: str | None
    last_activity_at_utc: str | None
    terminal_at_utc: str | None
    debug: dict[str, Any] | None = None


def _local_tz() -> ZoneInfo:
    name = os.environ.get("TZ", "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    local = datetime.now().astimezone().tzinfo
    if isinstance(local, ZoneInfo):
        return local
    return ZoneInfo("America/Los_Angeles")


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts or not str(ts).strip():
        return None
    text = str(ts).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _fmt_local(dt: datetime | None, *, tz: ZoneInfo) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def _human_delta(start: datetime | None, end: datetime | None) -> str | None:
    if start is None or end is None:
        return None
    secs = max(0, int((end - start).total_seconds()))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    hours = secs // 3600
    mins = (secs % 3600) // 60
    return f"{hours}h {mins}m"


def _elapsed_parts(since: datetime, *, now: datetime) -> str:
    secs = max(0, int((now - since).total_seconds()))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    hours = secs // 3600
    mins = (secs % 3600) // 60
    return f"{hours}h {mins}m"


def _human_idle(since: datetime | None, *, now: datetime) -> str:
    if since is None:
        return "—"
    return _elapsed_parts(since, now=now)


def _human_ago(when: datetime | None, *, now: datetime) -> str | None:
    if when is None:
        return None
    if when > now:
        return None
    parts = _elapsed_parts(when, now=now)
    if parts == "0s":
        return "just now"
    return parts


def _ts_display(
    local_label: str | None,
    utc_iso: str | None,
    *,
    now: datetime,
) -> str:
    if not local_label:
        return "—"
    when = _parse_iso(utc_iso)
    ago = _human_ago(when, now=now)
    if ago is None:
        return local_label
    return f"{local_label} ({ago} ago)"


def _record(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _merged_knobs(rec: dict[str, Any]) -> dict[str, str]:
    """``model_knobs`` with ``model_knobs_requested`` fallback (aligned admit)."""
    out: dict[str, str] = {}
    requested = rec.get("model_knobs_requested")
    if isinstance(requested, dict):
        for key, val in requested.items():
            if val is not None and str(val).strip():
                out[str(key)] = str(val).strip()
    knobs = rec.get("model_knobs")
    if isinstance(knobs, dict):
        for key, val in knobs.items():
            if val is not None and str(val).strip():
                out[str(key)] = str(val).strip()
    return out


def _fast_from_rec(rec: dict[str, Any]) -> bool | None:
    """Tri-state ``fast`` from ledger record_json (dispatch-monitor parity)."""
    raw = _merged_knobs(rec)
    if not raw:
        return None
    fast_raw = raw.get("fast")
    if fast_raw == "true":
        return True
    if fast_raw == "false":
        return False
    return None


def _fast_token(fast: bool | None) -> str:
    if fast is True:
        return " fast"
    if fast is False:
        return " fast=no"
    return ""


def _effort_from_rec(rec: dict[str, Any]) -> str | None:
    effort = _merged_knobs(rec).get("effort")
    return effort if effort else None


def _effort_token(effort: str | None) -> str:
    if not effort:
        return ""
    return f" effort={effort}"


def _model_knobs_dict(rec: dict[str, Any]) -> dict[str, str] | None:
    merged = _merged_knobs(rec)
    return merged or None


def _slug(
    *,
    work_key: str | None,
    source_ref: str | None,
    packet_path: str | None,
    thread_id: str | None,
    rec: dict[str, Any],
) -> str:
    wk = (work_key or rec.get("work_key") or "").strip()
    if wk.startswith("todo:"):
        return wk.split(":", 1)[-1][:80]
    sr = (source_ref or rec.get("source_ref") or "").strip()
    if sr.startswith("todo:"):
        try:
            return todo_slug_from_ref(sr)
        except Exception:
            return sr.split(":", 1)[-1][:80]
    if wk:
        return wk.split(":")[-1][:80]
    path = (packet_path or rec.get("packet_path") or "").strip()
    if path:
        return Path(path).stem[:80]
    return (thread_id or "?")[:40]


def _origin(
    *,
    caller_agent: str | None,
    rec: dict[str, Any],
) -> str:
    caller = (caller_agent or rec.get("caller_agent") or "?").strip()
    via = (
        rec.get("admitted_via")
        or rec.get("caller_via")
        or rec.get("asked_by")
        or ""
    )
    via = str(via).strip()
    if via and via != caller:
        return f"{caller} ← {via}"
    return caller


def _tool_calls(rec: dict[str, Any], *, model: str) -> int | None:
    raw = rec.get("tool_call_count")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    park = rec.get("park")
    if isinstance(park, dict) and park.get("tool_call_count") is not None:
        try:
            return int(park["tool_call_count"])
        except (TypeError, ValueError):
            pass
    reading = read_density_harness_meter(rec, model=model or "")
    if reading.stream_tool_call_count > 0:
        return reading.stream_tool_call_count
    ctrl = rec.get("density_control")
    traj = rec.get("density_trajectory")
    if isinstance(ctrl, dict) or isinstance(traj, dict):
        return reading.stream_tool_call_count
    return None


def _context(rec: dict[str, Any]) -> tuple[int | None, int | None]:
    usage_live = rec.get("usage_live")
    if isinstance(usage_live, dict):
        used = usage_live.get("used_tokens")
        limit = usage_live.get("window_limit_tokens")
        try:
            used_i = int(used) if used is not None else None
        except (TypeError, ValueError):
            used_i = None
        try:
            limit_i = int(limit) if limit is not None else None
        except (TypeError, ValueError):
            limit_i = None
        if used_i is not None:
            return used_i, limit_i
    usage = rec.get("usage")
    if isinstance(usage, dict):
        for key in ("total_tokens", "input_tokens", "prompt_tokens"):
            val = usage.get(key)
            if val is not None:
                try:
                    return int(val), None
                except (TypeError, ValueError):
                    continue
    return None, None


def _address_kind(rec: dict[str, Any]) -> str | None:
    addr = rec.get("address")
    if isinstance(addr, dict):
        kind = addr.get("kind")
        if kind:
            return str(kind)
    return None


def _cdp_chat_url_from_rec(rec: dict[str, Any]) -> str | None:
    addr = rec.get("address")
    if not isinstance(addr, dict):
        return None
    url = addr.get("chat_url")
    if url and str(url).strip():
        return str(url).strip()
    return None


def _resolve_cdp_chat_url(
    *,
    dispatch_id: str,
    thread_id: str | None,
    rec: dict[str, Any],
) -> str | None:
    """Cowork tab URL for one CDP frontier row (ledger address or bus dispatch link)."""
    url = _cdp_chat_url_from_rec(rec)
    if url:
        return url
    exec_id = (
        str(rec.get("execution_id") or dispatch_id).strip() if dispatch_id else ""
    )
    tid = (thread_id or "").strip()
    if not tid or not exec_id:
        return None
    try:
        from agent_bus_store.db.threads_atomic import read_dispatch_link_chat_url

        return read_dispatch_link_chat_url(thread_id=tid, execution_id=exec_id)
    except Exception:
        return None


def _transcript_tail_path(
    dispatch_id: str,
    *,
    thread_id: str | None = None,
    rec: dict[str, Any] | None = None,
) -> str:
    rec = rec if isinstance(rec, dict) else {}
    kind = _address_kind(rec)
    if kind in ("cdp", "api_role"):
        if kind == "cdp":
            url = _resolve_cdp_chat_url(
                dispatch_id=dispatch_id, thread_id=thread_id, rec=rec
            )
            if url:
                return url
        return "—"
    stderr = _STDERR_DIR / f"{dispatch_id}.log"
    if stderr.is_file():
        return str(stderr)
    spool = steer_spool_dir() / f"{dispatch_id}{_LEDGER_SUFFIX}"
    if spool.is_file():
        return str(spool)
    giw = os.environ.get("GIT_INTEGRATION_WORKER_URL", "http://127.0.0.1:8091").rstrip(
        "/"
    )
    return f"{giw}/api/v1/cursor/dispatch/{dispatch_id}/conversation"


def _last_activity(
    *,
    last_heartbeat_at: str | None,
    terminal_at: str | None,
    rec: dict[str, Any],
    started_at: str | None,
    queued_at: str | None,
    status: str,
) -> datetime | None:
    if status in _FINISHED:
        parsed = _parse_iso(terminal_at)
        if parsed is not None:
            return parsed
    candidates: list[datetime] = []
    for ts in (last_heartbeat_at, started_at, queued_at):
        parsed = _parse_iso(ts)
        if parsed is not None:
            candidates.append(parsed)
    usage = rec.get("usage_live")
    if isinstance(usage, dict):
        parsed = _parse_iso(str(usage.get("as_of") or ""))
        if parsed is not None:
            candidates.append(parsed)
    if not candidates:
        return None
    return max(candidates)


def _giw_live_dispatch_ids() -> set[str] | None:
    """Best-effort live task ids from git-integration-worker (debug only)."""
    try:
        import httpx
    except ImportError:
        return None
    base = os.environ.get("GIT_INTEGRATION_WORKER_URL", "http://127.0.0.1:8091").rstrip(
        "/"
    )
    try:
        with httpx.Client(timeout=2.5) as client:
            resp = client.get(f"{base}/api/v1/integrate/active-work")
            resp.raise_for_status()
            body = resp.json()
    except Exception:
        return None
    ids: set[str] = set()
    cursor = body.get("cursor_dispatches")
    if isinstance(cursor, dict):
        for key in cursor.get("dispatch_ids") or []:
            ids.add(str(key))
    for op in body.get("active_ops") or []:
        if isinstance(op, dict) and op.get("op_id"):
            ids.add(str(op["op_id"]))
    return ids


def _last_tool_name(rec: dict[str, Any]) -> str | None:
    calls = rec.get("last_tool_calls")
    if isinstance(calls, list) and calls:
        last = calls[-1]
        if isinstance(last, dict):
            name = last.get("tool_name") or last.get("name")
            if name:
                return str(name)
    park = rec.get("park")
    if isinstance(park, dict):
        pcalls = park.get("last_tool_calls")
        if isinstance(pcalls, list) and pcalls:
            last = pcalls[-1]
            if isinstance(last, dict):
                name = last.get("tool_name")
                if name:
                    return str(name)
    return None


def _failure_summary(row: sqlite3.Row, rec: dict[str, Any]) -> str | None:
    parts: list[str] = []
    term = row["terminal_status"] if "terminal_status" in row.keys() else None
    if term:
        parts.append(str(term))
    for key in ("failure_reason", "degraded_reason", "terminal_reason", "bridge_death_class"):
        val = rec.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    if not parts:
        return None
    return " · ".join(parts)


def _closeout_paths(
    dispatch_id: str, source_repo: str | None
) -> tuple[str | None, bool]:
    if not source_repo:
        return None, False
    path = Path(source_repo) / "tmp/reviews/closeouts" / f"{dispatch_id}.md"
    return str(path), path.is_file()


def _usage_debug(row: sqlite3.Row, rec: dict[str, Any]) -> dict[str, Any] | None:
    from services.git_integration_worker.cursor_sdk_usage_extract import (
        read_persisted_usage,
    )

    persisted = read_persisted_usage(str(row["record_json"] or ""))
    if persisted is None or persisted.usage is None:
        return {"absent_reason": "no persisted usage on ledger row"}
    usage = dict(persisted.usage)
    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "cache_read_tokens": usage.get("cache_read_tokens"),
        "cache_write_tokens": usage.get("cache_write_tokens"),
        "usage_capture_status": persisted.usage_capture_status,
        "cost_usd": usage.get("cost_usd"),
    }


def _build_debug(
    row: sqlite3.Row,
    rec: dict[str, Any],
    *,
    giw_live_ids: set[str] | None,
    stale_admitted_hours: float,
    last: datetime | None,
    now: datetime,
) -> dict[str, Any]:
    dispatch_id = str(row["dispatch_id"])
    status = str(row["status"] or "")
    execution_id = (
        str(row["execution_id"]).strip()
        if "execution_id" in row.keys() and row["execution_id"]
        else rec.get("execution_id")
    )
    request_id = rec.get("request_id")
    packet = row["packet_path"] if "packet_path" in row.keys() else None
    if not packet:
        packet = rec.get("packet_path")
    source_repo = row["source_repo"] if "source_repo" in row.keys() else None
    closeout_path, closeout_exists = _closeout_paths(
        dispatch_id, str(source_repo) if source_repo else None
    )
    park = rec.get("park") if isinstance(rec.get("park"), dict) else {}
    giw_live: bool | None = None
    if giw_live_ids is not None:
        giw_live = dispatch_id in giw_live_ids
    likely_orphan = False
    if status == "admitted":
        if giw_live is False:
            likely_orphan = True
        elif last is not None:
            likely_orphan = (
                now - last
            ).total_seconds() >= stale_admitted_hours * 3600.0
    worktree = rec.get("worktree_path") or rec.get("checkout_branch")
    checkout_branch = rec.get("checkout_branch") or rec.get("branch")
    land = rec.get("landed")
    if land is None and isinstance(rec.get("closeout"), dict):
        land = rec["closeout"].get("landed")
    commit_sha = rec.get("head_sha") or rec.get("commit_sha") or rec.get("land_sha")
    model_knobs = _model_knobs_dict(rec)
    out: dict[str, Any] = {
        "execution_id": (
            str(execution_id).strip() if execution_id else None
        ),
        "request_id": (
            str(request_id).strip() if request_id else None
        ),
        "packet_path": str(packet) if packet else None,
        "closeout_path": closeout_path,
        "closeout_on_disk": closeout_exists,
        "terminal_status": (
            str(row["terminal_status"]) if "terminal_status" in row.keys() else None
        ),
        "failure": _failure_summary(row, rec),
        "last_tool_name": _last_tool_name(rec),
        "usage": _usage_debug(row, rec),
        "resume_of": (
            str(row["resume_of"]).strip()
            if "resume_of" in row.keys() and row["resume_of"]
            else rec.get("resume_of")
        ),
        "park_kind": park.get("kind") if isinstance(park, dict) else None,
        "park_child_dispatch_id": (
            str(row["park_child_dispatch_id"]).strip()
            if "park_child_dispatch_id" in row.keys() and row["park_child_dispatch_id"]
            else None
        ),
        "worktree_path": str(worktree) if worktree else None,
        "checkout_branch": str(checkout_branch) if checkout_branch else None,
        "lease_key": (
            str(row["lease_key"]) if "lease_key" in row.keys() and row["lease_key"] else None
        ),
        "source_repo": str(source_repo) if source_repo else None,
        "landed": land,
        "commit_sha": str(commit_sha) if commit_sha else None,
        "giw_live_task": giw_live,
        "likely_orphan_admitted": likely_orphan if status == "admitted" else None,
    }
    if model_knobs is not None:
        out["model_knobs"] = model_knobs
    kind = _address_kind(rec)
    if kind:
        out["frontier_kind"] = kind
    if kind == "cdp":
        cdp_url = _resolve_cdp_chat_url(
            dispatch_id=dispatch_id,
            thread_id=(
                str(row["thread_id"] or "")
                if "thread_id" in row.keys()
                else None
            ),
            rec=rec,
        )
        if cdp_url:
            out["cdp_chat_url"] = cdp_url
    return {k: v for k, v in out.items() if v is not None}


def _operationally_active(
    status: str,
    last: datetime | None,
    *,
    now: datetime,
    stale_admitted_hours: float,
) -> bool:
    if status in ("running", "queued", "parked_waiting"):
        return True
    if status != "admitted":
        return True
    if last is None:
        return False
    return (now - last).total_seconds() < stale_admitted_hours * 3600.0


def _row_from_sqlite(
    row: sqlite3.Row,
    *,
    phase: str,
    now: datetime,
    tz: ZoneInfo,
    include_stale: bool,
    stale_admitted_hours: float,
    debug: bool,
    giw_live_ids: set[str] | None,
) -> DispatchRow | None:
    contract = str(row["contract"] or "").strip().lower()
    if contract == _CLAIM:
        return None
    status = str(row["status"] or "?")
    rec = _record(row["record_json"])
    model = str(row["resolved_model"] or rec.get("model") or "")
    fast = _fast_from_rec(rec)
    effort = _effort_from_rec(rec)
    started_dt = _parse_iso(row["started_at"]) or _parse_iso(row["queued_at"])
    terminal_dt = _parse_iso(row["terminal_at"])
    last = _last_activity(
        last_heartbeat_at=row["last_heartbeat_at"],
        terminal_at=row["terminal_at"],
        rec=rec,
        started_at=row["started_at"],
        queued_at=row["queued_at"],
        status=status,
    )
    if phase == "live" and not include_stale and not _operationally_active(
        status, last, now=now, stale_admitted_hours=stale_admitted_hours
    ):
        return None
    used, limit = _context(rec)
    nest = row["nest_under"] if "nest_under" in row.keys() else None
    if not nest:
        nest = rec.get("nest_under")
    lane = rec.get("lane")
    if lane is not None:
        lane = str(lane).strip() or None
    wk = (row["work_key"] or rec.get("work_key") or "").strip() or None
    duration = _human_delta(started_dt, terminal_dt if phase == "finished" else now)
    idle = "—" if phase == "finished" else _human_idle(last, now=now)
    exec_id = (
        str(row["execution_id"]).strip()
        if "execution_id" in row.keys() and row["execution_id"]
        else None
    )
    if not exec_id and rec.get("execution_id"):
        exec_id = str(rec.get("execution_id")).strip()
    return DispatchRow(
        phase=phase,
        dispatch_id=str(row["dispatch_id"]),
        execution_id=exec_id or None,
        status=status,
        slug=_slug(
            work_key=row["work_key"],
            source_ref=row["source_ref"],
            packet_path=row["packet_path"],
            thread_id=row["thread_id"],
            rec=rec,
        ),
        origin=_origin(caller_agent=row["caller_agent"], rec=rec),
        model=model,
        contract=str(row["contract"] or rec.get("handoff_contract") or "") or None,
        thread_id=str(row["thread_id"] or ""),
        work_key=wk,
        lane=lane,
        nest_under=str(nest).strip() if nest else None,
        tool_call_count=_tool_calls(rec, model=model),
        context_used=used,
        context_limit=limit,
        transcript_tail=_transcript_tail_path(
            str(row["dispatch_id"]),
            thread_id=str(row["thread_id"] or ""),
            rec=rec,
        ),
        fast=fast,
        effort=effort,
        started_at=_fmt_local(started_dt, tz=tz),
        last_activity_at=_fmt_local(last, tz=tz),
        terminal_at=_fmt_local(terminal_dt, tz=tz),
        duration=duration if phase == "finished" else None,
        idle=idle,
        started_at_utc=started_dt.isoformat() if started_dt else None,
        last_activity_at_utc=last.isoformat() if last else None,
        terminal_at_utc=terminal_dt.isoformat() if terminal_dt else None,
        debug=(
            _build_debug(
                row,
                rec,
                giw_live_ids=giw_live_ids,
                stale_admitted_hours=stale_admitted_hours,
                last=last,
                now=now,
            )
            if debug
            else None
        ),
    )


_SELECT = (
    "SELECT dispatch_id, thread_id, execution_id, status, caller_agent, "
    "resolved_model, started_at, queued_at, last_heartbeat_at, terminal_at, "
    "terminal_status, work_key, source_ref, packet_path, record_json, contract, "
    "nest_under, resume_of, source_repo, lease_key, park_child_dispatch_id "
    "FROM cursor_sdk_dispatches "
)


def _fetch_by_id_prefix(
    db_path: Path,
    needle: str,
    *,
    debug: bool,
    giw_live_ids: set[str] | None,
    stale_admitted_hours: float,
) -> list[DispatchRow]:
    """Match ``dispatch_id`` or ``execution_id`` prefix (any status)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3.0)
    conn.row_factory = sqlite3.Row
    now = datetime.now(UTC)
    tz = _local_tz()
    pattern = needle.strip() + "%"
    try:
        rows = conn.execute(
            _SELECT + "WHERE dispatch_id LIKE ? OR execution_id LIKE ? "
            "ORDER BY COALESCE(terminal_at, started_at, queued_at) DESC",
            (pattern, pattern),
        ).fetchall()
    finally:
        conn.close()
    out: list[DispatchRow] = []
    for row in rows:
        phase = (
            "live"
            if str(row["status"] or "") in _LIVE
            else "finished"
        )
        built = _row_from_sqlite(
            row,
            phase=phase,
            now=now,
            tz=tz,
            include_stale=True,
            stale_admitted_hours=stale_admitted_hours,
            debug=debug,
            giw_live_ids=giw_live_ids,
        )
        if built is not None:
            out.append(built)
    return out


def _fetch_rows(
    db_path: Path,
    *,
    include_stale: bool,
    stale_admitted_hours: float,
    finished_hours: float,
    debug: bool,
    giw_live_ids: set[str] | None,
) -> tuple[list[DispatchRow], list[DispatchRow]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3.0)
    conn.row_factory = sqlite3.Row
    now = datetime.now(UTC)
    tz = _local_tz()
    live: list[DispatchRow] = []
    finished: list[DispatchRow] = []
    try:
        placeholders = ", ".join("?" for _ in _LIVE)
        live_sql = (
            _SELECT + f"WHERE status IN ({placeholders}) "
            "ORDER BY COALESCE(started_at, queued_at) DESC"
        )
        for row in conn.execute(live_sql, _LIVE).fetchall():
            built = _row_from_sqlite(
                row,
                phase="live",
                now=now,
                tz=tz,
                include_stale=include_stale,
                stale_admitted_hours=stale_admitted_hours,
                debug=debug,
                giw_live_ids=giw_live_ids,
            )
            if built is not None:
                live.append(built)
        if finished_hours > 0:
            cutoff = (now - timedelta(hours=finished_hours)).isoformat()
            fin_placeholders = ", ".join("?" for _ in _FINISHED)
            fin_sql = (
                _SELECT
                + f"WHERE status IN ({fin_placeholders}) AND terminal_at IS NOT NULL "
                "AND terminal_at >= ? "
                "ORDER BY terminal_at DESC"
            )
            params = [*_FINISHED, cutoff]
            for row in conn.execute(fin_sql, params).fetchall():
                built = _row_from_sqlite(
                    row,
                    phase="finished",
                    now=now,
                    tz=tz,
                    include_stale=True,
                    stale_admitted_hours=stale_admitted_hours,
                    debug=debug,
                    giw_live_ids=giw_live_ids,
                )
                if built is not None:
                    finished.append(built)
    finally:
        conn.close()
    return live, finished


def _c(code: str, text: str, *, use_color: bool) -> str:
    return f"{code}{text}{_RESET}" if use_color else text


def _print_row(row: DispatchRow, *, use_color: bool) -> None:
    now = datetime.now(UTC)
    status_c = _STATUS_COLOR.get(row.status, _BLUE)
    phase_tag = "" if row.phase == "live" else _c(_DIM, " [done]", use_color=use_color)
    model_short = row.model.removeprefix("cursor/") if row.model else "?"
    model_short = f"{model_short}{_fast_token(row.fast)}{_effort_token(row.effort)}"
    exec_hint = ""
    if row.execution_id:
        exec_hint = f" exec {_c(_DIM, row.execution_id[:8], use_color=use_color)}"
    head = (
        f"{_c(status_c, row.status.upper(), use_color=use_color)}"
        f"{phase_tag} "
        f"{_c(_BOLD, row.dispatch_id, use_color=use_color)}"
        f"{exec_hint} "
        f"{_c(_CYAN, row.slug, use_color=use_color)}"
    )
    print(head)
    extras: list[str] = [model_short]
    if row.contract:
        extras.append(row.contract)
    if row.lane:
        extras.append(f"lane {row.lane}")
    print(f"  model        {' · '.join(extras)}")
    print(f"  origin       {_c(_DIM, row.origin, use_color=use_color)}")
    print(f"  thread       {row.thread_id or '—'}")
    if row.work_key:
        print(f"  work_key     {row.work_key}")
    if row.nest_under:
        print(f"  nest_under   {row.nest_under}")
    tools = "—" if row.tool_call_count is None else str(row.tool_call_count)
    if row.context_used is not None and row.context_limit is not None:
        ctx = f"{row.context_used:,} / {row.context_limit:,} tok"
    elif row.context_used is not None:
        ctx = f"{row.context_used:,} tok"
    else:
        ctx = "—"
    print(f"  tools        {tools}    context    {ctx}")
    started = _ts_display(row.started_at, row.started_at_utc, now=now)
    print(f"  started      {started}")
    if row.phase == "finished":
        finished = _ts_display(row.terminal_at, row.terminal_at_utc, now=now)
        print(f"  finished     {finished}  (ran {row.duration or '—'})")
    else:
        idle_col = _YELLOW if row.idle not in {"—", "0s"} else _DIM
        last = _ts_display(row.last_activity_at, row.last_activity_at_utc, now=now)
        print(
            f"  last active  {last}  "
            f"({_c(idle_col, f'idle {row.idle}', use_color=use_color)})"
        )
    tail_label = (
        "cdp_url"
        if (
            row.transcript_tail != "—"
            and row.transcript_tail.startswith(("http://", "https://"))
        )
        else "tail"
    )
    print(f"  {tail_label:<12} {row.transcript_tail}")
    if row.debug:
        _print_debug(row.debug, use_color=use_color)


_DEBUG_KEY_ORDER = (
    "execution_id",
    "request_id",
    "packet_path",
    "closeout_path",
    "closeout_on_disk",
    "terminal_status",
    "failure",
    "last_tool_name",
    "usage",
    "model_knobs",
    "resume_of",
    "nest_under",
    "park_kind",
    "park_child_dispatch_id",
    "worktree_path",
    "checkout_branch",
    "lease_key",
    "source_repo",
    "landed",
    "commit_sha",
    "giw_live_task",
    "likely_orphan_admitted",
    "frontier_kind",
    "cdp_chat_url",
)


def _print_debug(extra: dict[str, Any], *, use_color: bool) -> None:
    print(_c(_DIM, "  — debug —", use_color=use_color))
    keys = [k for k in _DEBUG_KEY_ORDER if k in extra]
    keys += sorted(k for k in extra if k not in _DEBUG_KEY_ORDER)
    for key in keys:
        val = extra[key]
        if isinstance(val, dict):
            flat = json.dumps(val, separators=(",", ":"))
            print(f"  {key:18} {flat}")
        else:
            print(f"  {key:18} {val}")


def _print_table(
    live: list[DispatchRow],
    finished: list[DispatchRow],
    *,
    use_color: bool,
) -> None:
    if not live and not finished:
        print(_c(_DIM, "No matching cursor-sdk dispatches.", use_color=use_color))
        return
    if live:
        if finished:
            print(_c(_BOLD, "Live", use_color=use_color))
        for i, row in enumerate(live):
            if i:
                print()
            _print_row(row, use_color=use_color)
    if finished:
        if live:
            print()
            print(_c(_BOLD, "Finished (recent)", use_color=use_color))
        for i, row in enumerate(finished):
            if i:
                print()
            _print_row(row, use_color=use_color)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List live cursor-sdk dispatches and optional recently finished runs."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON object with live and finished arrays.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Plain text table on stdout.",
    )
    parser.add_argument(
        "--include-stale",
        action="store_true",
        help=(
            "Include long-idle ``admitted`` rows (ledger orphans — see --debug "
            "``likely_orphan_admitted``)."
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Extra fields: execution_id, packet/closeout paths, usage/cost, park, land, GIW live probe.",
    )
    parser.add_argument(
        "--match",
        metavar="ID_PREFIX",
        help="Find by dispatch_id or execution_id prefix (any status; ignores live-only filters).",
    )
    parser.add_argument(
        "--stale-admitted-hours",
        type=float,
        default=72.0,
        metavar="H",
        help="Max idle hours for ``admitted`` when stale rows are hidden (default: 72).",
    )
    parser.add_argument(
        "--finished-hours",
        type=float,
        default=0.0,
        metavar="H",
        help="Also list terminal rows finished within the last H hours (0=off).",
    )
    args = parser.parse_args(argv)
    db_path = resolve_cursor_sdk_dispatch_ledger_path()
    if not db_path.is_file():
        print(f"Ledger not found: {db_path}", file=sys.stderr)
        return 1
    try:
        giw_live = _giw_live_dispatch_ids() if args.debug else None
        if args.match:
            matched = _fetch_by_id_prefix(
                db_path,
                args.match,
                debug=args.debug,
                giw_live_ids=giw_live,
                stale_admitted_hours=args.stale_admitted_hours,
            )
            live = [r for r in matched if r.phase == "live"]
            finished = [r for r in matched if r.phase == "finished"]
        else:
            live, finished = _fetch_rows(
                db_path,
                include_stale=args.include_stale,
                stale_admitted_hours=args.stale_admitted_hours,
                finished_hours=max(0.0, args.finished_hours),
                debug=args.debug,
                giw_live_ids=giw_live,
            )
    except sqlite3.Error as exc:
        print(f"Ledger read failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        def _json_row(row: DispatchRow) -> dict[str, Any]:
            data = {k: v for k, v in row.__dict__.items() if k != "debug" or args.debug}
            if not args.debug:
                data.pop("debug", None)
            return data

        payload = {
            "timezone": str(_local_tz()),
            "live": [_json_row(row) for row in live],
            "finished": [_json_row(row) for row in finished],
        }
        print(json.dumps(payload, indent=2))
        return 0

    use_color = not args.no_color and sys.stdout.isatty()
    _print_table(live, finished, use_color=use_color)
    footer = f"{len(live)} live"
    if finished:
        footer += f" · {len(finished)} finished"
    footer += f" · ledger {db_path} · tz {_local_tz()}"
    print(f"\n{_c(_DIM, footer, use_color=use_color) if use_color else footer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
