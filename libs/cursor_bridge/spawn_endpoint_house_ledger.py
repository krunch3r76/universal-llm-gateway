"""Append-only spawn/house ledger (todo:spawn-endpoint-house-ledger)."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable
from typing import Any

from bus_watch.fable_lock import WATCH_DIR

CONSUMERS: tuple[str, ...] = ("mcp",)

_ROW_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_LEDGER_ROW_BODY_RE = re.compile(r"^ledger_row: ([0-9a-f]{32})$", re.MULTILINE)

LEDGER_FILENAME = "cursor-bridge-tab-wake-ledger.jsonl"
_LINE_BUDGET_BYTES = 4096
_SERVE_BUDGET_BYTES = 5120

_SURFACES = frozenset({"cursor_sdk", "cdp", "cursor_ide"})
_SPAWN_STATES = frozenset({"launching", "live", "failed"})
_STATUS_VALUES = frozenset({"live", "parked", "done", "failed", "unknown"})


def mint_row_id() -> str:
    return uuid.uuid4().hex


def format_ledger_prompt_prefix(*, row_id: str, purpose: str) -> str:
    """Two-line ledger header for spawned first messages and conductor packets."""
    rid = str(row_id or "").strip()
    if not _ROW_ID_RE.match(rid):
        return ""
    line = str(purpose or "").strip() or "(unstated)"
    if "\n" in line:
        line = "(unstated)"
    return f"ledger_row: {rid}\npurpose: {line}\n"


def ledger_path(watch_dir: Path | None = None) -> Path:
    base = watch_dir if watch_dir is not None else WATCH_DIR
    return base / LEDGER_FILENAME


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _row_kind(row: dict[str, Any]) -> str:
    kind = str(row.get("kind") or "").strip()
    return kind if kind else "wake"


def _compact_payload(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, separators=(",", ":"))


def _read_rows(path: Path) -> list[dict[str, Any]]:
    try:
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if raw and not raw.endswith("\n"):
        raw = raw[: raw.rfind("\n") + 1] if "\n" in raw else ""
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _fold_spawn_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Merge ``kind=patch`` event rows into their ``kind=spawn`` spine by ``row_id``."""
    folded: dict[str, dict[str, Any]] = {}
    for row in rows:
        kind = _row_kind(row)
        rid = str(row.get("row_id") or "").strip()
        if kind == "spawn" and _ROW_ID_RE.match(rid):
            folded[rid] = dict(row)
            continue
        if kind != "patch" or not _ROW_ID_RE.match(rid):
            continue
        base = folded.get(rid)
        if base is None:
            continue
        for key, val in row.items():
            if key in ("kind", "row_id", "at"):
                continue
            if val is None:
                base.pop(key, None)
            else:
                base[key] = val
    return folded


def _append_row(row: dict[str, Any], *, watch_dir: Path | None = None) -> dict[str, Any]:
    payload = _compact_payload(row)
    if len(payload.encode("utf-8")) >= _LINE_BUDGET_BYTES:
        return {"ok": False, "error": "line_too_long"}
    path = ledger_path(watch_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = ""
        if path.is_file() and path.stat().st_size > 0:
            with path.open("rb") as fh:
                fh.seek(-1, 2)
                if fh.read(1) != b"\n":
                    prefix = "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(prefix + payload + "\n")
    except OSError as exc:
        return {"ok": False, "error": "io_error", "detail": type(exc).__name__}
    return {"ok": True}


def _spawn_row_id_taken(row_id: str, *, watch_dir: Path | None = None) -> bool:
    rid = str(row_id or "").strip()
    for row in _read_rows(ledger_path(watch_dir)):
        if _row_kind(row) == "spawn" and str(row.get("row_id") or "").strip() == rid:
            return True
    return False


def record_tab_wake(
    *,
    gui_host: str,
    agent_id: str,
    op: str,
    ok: bool,
    reason: str = "",
    detail: dict[str, Any] | None = None,
    watch_dir: Path | None = None,
    row_id: str | None = None,
) -> dict[str, Any]:
    """Append one Glass tab wake outcome (``kind=wake``)."""
    row: dict[str, Any] = {
        "kind": "wake",
        "at": _utcnow(),
        "gui_host": str(gui_host or "").strip(),
        "agent_id": str(agent_id or "").strip(),
        "op": str(op or "").strip(),
        "ok": bool(ok),
        "reason": str(reason or "").strip(),
    }
    if row_id and _ROW_ID_RE.match(str(row_id).strip()):
        row["row_id"] = str(row_id).strip()
    if detail:
        row["detail"] = detail
    out = _append_row(row, watch_dir=watch_dir)
    if not out.get("ok"):
        return out
    return row


def record_cse_wake(
    *,
    chat_url: str,
    op: str,
    ok: bool,
    execution_id: str | None = None,
    reason: str = "",
    detail: dict[str, Any] | None = None,
    row_id: str | None = None,
    watch_dir: Path | None = None,
) -> dict[str, Any]:
    """Append one CDP CSE wake row (``kind=wake``)."""
    row: dict[str, Any] = {
        "kind": "wake",
        "at": _utcnow(),
        "chat_url": str(chat_url or "").strip(),
        "op": str(op or "").strip(),
        "ok": bool(ok),
        "reason": str(reason or "").strip(),
    }
    if execution_id:
        row["execution_id"] = str(execution_id).strip()
    if row_id and _ROW_ID_RE.match(str(row_id).strip()):
        row["row_id"] = str(row_id).strip()
    if detail:
        row["detail"] = detail
    out = _append_row(row, watch_dir=watch_dir)
    if not out.get("ok"):
        return out
    return row


def record_spawn(
    *,
    surface: str,
    thread: str,
    purpose: str,
    purpose_unstated: bool,
    parent: dict[str, str] | None = None,
    name: str = "",
    dispatch_id: str | None = None,
    execution_id: str | None = None,
    gui_host: str | None = None,
    agent_id: str | None = None,
    state: str = "live",
    row_id: str | None = None,
    watch_dir: Path | None = None,
) -> dict[str, Any]:
    """Append one ``kind=spawn`` spine row; mints ``row_id`` when omitted."""
    surf = str(surface or "").strip()
    if surf not in _SURFACES:
        return {"ok": False, "error": "surface_invalid"}
    rid = str(row_id or mint_row_id()).strip()
    if not _ROW_ID_RE.match(rid):
        return {"ok": False, "error": "surface_invalid"}
    if _spawn_row_id_taken(rid, watch_dir=watch_dir):
        return {"ok": False, "error": "duplicate_row_id"}
    st = str(state or "live").strip()
    if st not in _SPAWN_STATES:
        st = "live"
    row: dict[str, Any] = {
        "kind": "spawn",
        "row_id": rid,
        "surface": surf,
        "thread": str(thread or "").strip(),
        "purpose": str(purpose or "").strip() or "(unstated)",
        "purpose_unstated": bool(purpose_unstated),
        "name": str(name or "").strip(),
        "parent": parent,
        "spawned_at": _utcnow(),
        "state": st,
    }
    if dispatch_id:
        row["dispatch_id"] = str(dispatch_id).strip()
    if execution_id:
        row["execution_id"] = str(execution_id).strip()
    if gui_host:
        row["gui_host"] = str(gui_host).strip()
    aid = str(agent_id or "").strip()
    if aid:
        row["agent_id"] = aid
    out = _append_row(row, watch_dir=watch_dir)
    if not out.get("ok"):
        return out
    return {"ok": True, "row_id": rid, "row": row}


def patch_spawn_row(
    row_id: str,
    *,
    watch_dir: Path | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Append a ``kind=patch`` event for ``row_id``; folded into the spawn at read."""
    rid = str(row_id or "").strip()
    if not _ROW_ID_RE.match(rid):
        return {"ok": False, "error": "row_not_found"}
    if rid not in _fold_spawn_rows(_read_rows(ledger_path(watch_dir))):
        return {"ok": False, "error": "row_not_found"}
    patch: dict[str, Any] = {"kind": "patch", "row_id": rid, "at": _utcnow()}
    for key, val in fields.items():
        if val is None:
            patch[key] = None
        else:
            patch[key] = val
    out = _append_row(patch, watch_dir=watch_dir)
    if not out.get("ok"):
        return out
    return {"ok": True, "row_id": rid}


def record_status_row(
    *,
    row_id: str,
    status: str,
    source_status: str | None = None,
    watch_dir: Path | None = None,
) -> dict[str, Any]:
    st = str(status or "").strip()
    if st not in _STATUS_VALUES:
        return {"ok": False, "error": "status_invalid"}
    rid = str(row_id or "").strip()
    if not _ROW_ID_RE.match(rid):
        return {"ok": False, "error": "status_invalid"}
    row: dict[str, Any] = {
        "kind": "status",
        "row_id": rid,
        "status": st,
        "source_status": source_status,
        "at": _utcnow(),
    }
    out = _append_row(row, watch_dir=watch_dir)
    if not out.get("ok"):
        return out
    return row


def record_report_row(
    *,
    row_id: str,
    status: str,
    source_status: str | None,
    thread: str,
    turn: int | None,
    watch_dir: Path | None = None,
) -> dict[str, Any]:
    rid = str(row_id or "").strip()
    if not _ROW_ID_RE.match(rid):
        return {"ok": False, "error": "report_invalid"}
    st = str(status or "").strip()
    if st not in _STATUS_VALUES:
        return {"ok": False, "error": "status_invalid"}
    row: dict[str, Any] = {
        "kind": "report",
        "row_id": rid,
        "status": st,
        "source_status": source_status,
        "thread": str(thread or "").strip(),
        "turn": turn,
        "at": _utcnow(),
    }
    out = _append_row(row, watch_dir=watch_dir)
    if not out.get("ok"):
        return out
    return row


def map_source_status(raw: str | None) -> str:
    """Map a terminal/marker string to projected ``status`` (F3)."""
    text = str(raw or "").strip()
    if not text:
        return "unknown"
    low = text.lower()
    if "closeout" in low:
        if "complete" in low:
            return "done"
        if "partial" in low:
            return "parked"
        if "failed" in low or "blocked" in low:
            return "failed"
    if text.startswith("DONE|"):
        return "done"
    if "PARKED" in text:
        return "parked"
    if text.startswith("STATUS|") or "CHECKPOINT" in text:
        return "live"
    if "seat_closed_at" in text:
        return "done"
    return "unknown"


def _spawn_row_exists(row_id: str, *, watch_dir: Path | None = None) -> bool:
    rid = str(row_id or "").strip()
    return rid in _fold_spawn_rows(_read_rows(ledger_path(watch_dir)))


def _find_spawn_row_id(
    *,
    body: str,
    subject: str,
    thread: str,
    watch_dir: Path | None,
) -> str | None:
    for match in _LEDGER_ROW_BODY_RE.finditer(body or ""):
        rid = match.group(1)
        if _spawn_row_exists(rid, watch_dir=watch_dir):
            return rid
    subj = str(subject or "")
    folded = _fold_spawn_rows(_read_rows(ledger_path(watch_dir)))
    for row in reversed(list(folded.values())):
        for key in ("dispatch_id", "execution_id"):
            val = str(row.get(key) or "").strip()
            if val and val in subj:
                return str(row.get("row_id") or "").strip() or None
    for row in reversed(list(folded.values())):
        if str(row.get("thread") or "").strip() == str(thread or "").strip():
            return str(row.get("row_id") or "").strip() or None
    return None


def _extract_report_marker(subject: str, body: str) -> str | None:
    """First status-bearing marker from bus subject, else a non-ledger body line."""
    subj = str(subject or "").strip()
    if subj:
        return subj
    for line in (body or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("ledger_row:") or stripped.startswith("purpose:"):
            continue
        return stripped
    return None


def ingest_report_turn(
    body: str,
    subject: str,
    thread: str,
    turn: int | None = None,
    *,
    watch_dir: Path | None = None,
) -> dict[str, Any]:
    """Match a house spawn row and append ``kind=report`` (S5)."""
    rid = _find_spawn_row_id(
        body=body, subject=subject, thread=thread, watch_dir=watch_dir
    )
    if not rid:
        return {"ok": False, "error": "no_matching_spawn"}
    raw_marker = _extract_report_marker(subject, body)
    if raw_marker is None:
        proj = "unknown"
        stored_source: str | None = None
    else:
        stored_source = raw_marker
        proj = map_source_status(raw_marker)
    return record_report_row(
        row_id=rid,
        status=proj,
        source_status=stored_source,
        thread=thread,
        turn=turn,
        watch_dir=watch_dir,
    )


def project_row_status(
    row_id: str,
    *,
    watch_dir: Path | None = None,
    terminal_source: str | None = None,
    registry_read: Callable[[], str | None] | None = None,
    giw_read: Callable[[], str | None] | None = None,
) -> dict[str, Any]:
    """Project ``status`` for one spawn ``row_id`` (Status binding)."""
    rid = str(row_id or "").strip()
    if terminal_source:
        mapped = map_source_status(terminal_source)
        if mapped != "unknown":
            return {
                "status": mapped,
                "source_status": terminal_source,
            }
    rows = _read_rows(ledger_path(watch_dir))
    latest: dict[str, Any] | None = None
    for row in rows:
        if str(row.get("row_id") or "").strip() != rid:
            continue
        if _row_kind(row) in ("status", "report"):
            latest = row
    if latest is not None:
        return {
            "status": str(latest.get("status") or "unknown"),
            "source_status": latest.get("source_status"),
        }
    if registry_read is not None:
        raw = registry_read()
        if raw:
            mapped = map_source_status(raw)
            if mapped == "parked":
                return {"status": "parked", "source_status": raw}
    if giw_read is not None:
        raw = giw_read()
        if raw:
            mapped = map_source_status(raw)
            if mapped in {"live", "parked"}:
                return {"status": mapped, "source_status": raw}
    return {"status": "unknown", "source_status": None}


def _spawn_matches_parent(spawn: dict[str, Any], parent: dict[str, str]) -> bool:
    obj = spawn.get("parent")
    if not isinstance(obj, dict):
        return False
    return (
        str(obj.get("agent") or "").strip()
        == str(parent.get("agent") or "").strip()
        and str(obj.get("thread") or "").strip()
        == str(parent.get("thread") or "").strip()
    )


def _serve_row_with_status(
    spawn: dict[str, Any], *, watch_dir: Path | None
) -> dict[str, Any]:
    rid = str(spawn.get("row_id") or "").strip()
    proj = project_row_status(rid, watch_dir=watch_dir)
    out = dict(spawn)
    out["status"] = proj["status"]
    out["source_status"] = proj.get("source_status")
    return out


def _normalize_house_selector(
    house: str | dict[str, Any] | None,
) -> dict[str, str] | None:
    if house is None:
        return None
    if isinstance(house, str):
        thread = str(house).strip()
        return {"thread": thread} if thread else None
    if isinstance(house, dict):
        out: dict[str, str] = {}
        for key, val in house.items():
            if val is None:
                continue
            text = str(val).strip()
            if text:
                out[str(key)] = text
        return out or None
    return None


def _spawn_matches_house(spawn: dict[str, Any], house_sel: dict[str, str]) -> bool:
    for key, want in house_sel.items():
        if str(spawn.get(key) or "").strip() != want:
            return False
    return True


def read_house_ledger(
    *,
    house: str | dict[str, Any] | None = None,
    parent: dict[str, str] | None = None,
    limit: int = 5,
    watch_dir: Path | None = None,
) -> dict[str, Any]:
    """Serve folded spawn rows for ``op=ledger`` house/parent reads (trimmed)."""
    lim = max(1, min(10, int(limit)))
    house_sel = _normalize_house_selector(house)
    parent_sel = parent if isinstance(parent, dict) else None
    folded = _fold_spawn_rows(_read_rows(ledger_path(watch_dir)))
    candidates: list[dict[str, Any]] = []
    for spawn in folded.values():
        if house_sel and not _spawn_matches_house(spawn, house_sel):
            continue
        if parent_sel and not _spawn_matches_parent(spawn, parent_sel):
            continue
        candidates.append(spawn)
    candidates.sort(
        key=lambda r: str(r.get("spawned_at") or r.get("at") or ""),
        reverse=True,
    )
    matched = [
        _serve_row_with_status(row, watch_dir=watch_dir) for row in candidates[:lim]
    ]
    head_status = (
        project_row_status(
            str(matched[0].get("row_id") or ""), watch_dir=watch_dir
        )
        if matched
        else {"status": "unknown", "source_status": None}
    )
    budget = {
        "max_bytes": _SERVE_BUDGET_BYTES,
        "actual_bytes": 0,
        "truncated": False,
    }
    payload: dict[str, Any] = {
        "house": house_sel,
        "parent": parent_sel,
        "rows": matched,
        "status": head_status["status"],
        "source_status": head_status.get("source_status"),
        "limit": lim,
        "budget": budget,
    }

    def _compact_bytes(body: dict[str, Any]) -> int:
        return len(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )

    while _compact_bytes(payload) > _SERVE_BUDGET_BYTES and matched:
        matched.pop()
        budget["truncated"] = True
        head_status = (
            project_row_status(
                str(matched[0].get("row_id") or ""), watch_dir=watch_dir
            )
            if matched
            else {"status": "unknown", "source_status": None}
        )
        payload = {
            "house": house_sel,
            "parent": parent_sel,
            "rows": matched,
            "status": head_status["status"],
            "source_status": head_status.get("source_status"),
            "limit": lim,
            "budget": budget,
        }
    budget["actual_bytes"] = _compact_bytes(payload)
    payload["budget"] = budget
    if budget["actual_bytes"] > _SERVE_BUDGET_BYTES:
        budget["truncated"] = True
        payload["rows"] = []
        budget["actual_bytes"] = _compact_bytes(payload)
        payload["budget"] = budget
    return payload


def last_successful_wake(
    agent_id: str,
    *,
    watch_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Newest ``ok`` wake row for ``agent_id``, or None."""
    aid = str(agent_id or "").strip()
    if not aid:
        return None
    last: dict[str, Any] | None = None
    for row in _read_rows(ledger_path(watch_dir)):
        if _row_kind(row) != "wake":
            continue
        if str(row.get("agent_id") or "").strip() != aid:
            continue
        if not row.get("ok"):
            continue
        last = row
    return last


read_all_rows = _read_rows

__all__ = [
    "LEDGER_FILENAME",
    "format_ledger_prompt_prefix",
    "ingest_report_turn",
    "last_successful_wake",
    "ledger_path",
    "map_source_status",
    "mint_row_id",
    "patch_spawn_row",
    "project_row_status",
    "read_all_rows",
    "read_house_ledger",
    "record_cse_wake",
    "record_report_row",
    "record_spawn",
    "record_status_row",
    "record_tab_wake",
]
