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
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    raw = path.read_text(encoding="utf-8")
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


def _rewrite_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(_compact_payload(row) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def _append_row(row: dict[str, Any], *, watch_dir: Path | None = None) -> dict[str, Any]:
    payload = _compact_payload(row)
    if len(payload.encode("utf-8")) >= _LINE_BUDGET_BYTES:
        return {"ok": False, "error": "line_too_long"}
    path = ledger_path(watch_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if path.is_file() and path.stat().st_size > 0:
        with path.open("rb") as fh:
            fh.seek(-1, 2)
            if fh.read(1) != b"\n":
                prefix = "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(prefix + payload + "\n")
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
    """Update fields on an existing spawn row (same ``row_id``, one spawn row)."""
    rid = str(row_id or "").strip()
    if not _ROW_ID_RE.match(rid):
        return {"ok": False, "error": "row_not_found"}
    path = ledger_path(watch_dir)
    rows = _read_rows(path)
    updated = False
    for row in rows:
        if _row_kind(row) != "spawn":
            continue
        if str(row.get("row_id") or "").strip() != rid:
            continue
        for key, val in fields.items():
            if val is None and key in row:
                row.pop(key, None)
            elif val is not None:
                row[key] = val
        updated = True
        break
    if not updated:
        return {"ok": False, "error": "row_not_found"}
    _rewrite_rows(path, rows)
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
    row: dict[str, Any] = {
        "kind": "report",
        "row_id": rid,
        "status": str(status or "").strip(),
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
    for row in _read_rows(ledger_path(watch_dir)):
        if _row_kind(row) == "spawn" and str(row.get("row_id") or "").strip() == rid:
            return True
    return False


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
    for row in reversed(_read_rows(ledger_path(watch_dir))):
        if _row_kind(row) != "spawn":
            continue
        for key in ("dispatch_id", "execution_id"):
            val = str(row.get(key) or "").strip()
            if val and val in subj:
                return str(row.get("row_id") or "").strip() or None
    for row in reversed(_read_rows(ledger_path(watch_dir))):
        if _row_kind(row) != "spawn":
            continue
        if str(row.get("thread") or "").strip() == str(thread or "").strip():
            return str(row.get("row_id") or "").strip() or None
    return None


def ingest_report_turn(
    body: str,
    subject: str,
    thread: str,
    turn: int | None = None,
    *,
    watch_dir: Path | None = None,
    status: str = "live",
    source_status: str | None = None,
) -> dict[str, Any]:
    """Match a house spawn row and append ``kind=report`` (S5)."""
    rid = _find_spawn_row_id(
        body=body, subject=subject, thread=thread, watch_dir=watch_dir
    )
    if not rid:
        return {"ok": False, "error": "no_match"}
    mapped = map_source_status(source_status or status)
    proj = mapped if mapped != "unknown" else str(status or "live")
    return record_report_row(
        row_id=rid,
        status=proj,
        source_status=source_status or status,
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


def read_house_ledger(
    *,
    house: str | None = None,
    parent: str | None = None,
    limit: int = 5,
    watch_dir: Path | None = None,
) -> dict[str, Any]:
    """Serve JSONL rows for ``op=ledger`` house/parent reads (trimmed to budget)."""
    lim = max(1, min(10, int(limit)))
    key_house = str(house or "").strip()
    key_parent = str(parent or "").strip()
    rows = _read_rows(ledger_path(watch_dir))
    matched: list[dict[str, Any]] = []
    for row in reversed(rows):
        if key_house and str(row.get("thread") or "").strip() != key_house:
            continue
        if key_parent:
            parent_obj = row.get("parent")
            if not isinstance(parent_obj, dict):
                continue
            if str(parent_obj.get("thread") or "").strip() != key_parent:
                continue
        matched.append(row)
        if len(matched) >= lim:
            break
    matched.reverse()
    payload = {"rows": matched, "limit": lim}
    text = json.dumps(payload, sort_keys=True)
    while len(text.encode("utf-8")) > _SERVE_BUDGET_BYTES and matched:
        matched.pop()
        payload = {"rows": matched, "limit": lim}
        text = json.dumps(payload, sort_keys=True)
    if len(text.encode("utf-8")) > _SERVE_BUDGET_BYTES:
        payload = {"rows": [], "limit": lim, "trimmed": True}
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
