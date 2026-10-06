"""Steer directive spool — bridge hot-path helpers (no HTTP).

Rung-1 of the steer ladder: the stdio MCP middlebox claims a pending directive
from the per-dispatch spool and appends it as a second ``result.content`` block
on the next relayed ``tools/call`` response. Pure functions + filesystem spool
only — no network on the relay hot path.
"""

from __future__ import annotations

import fcntl
import json
import os
import pwd
import sqlite3
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CURSOR_SDK_DISPATCH_ID_ENV = "CURSOR_SDK_DISPATCH_ID"
CURSOR_SDK_DISPATCH_LEDGER_ENV = "CURSOR_SDK_DISPATCH_LEDGER"
ULG_STEER_SPOOL_DIR_ENV = "ULG_STEER_SPOOL_DIR"
# Sibling of ``steer-spool/`` under the gateway data dir (default ``~/.gateway``).
# Present file disarms the native hook without consuming a pending spool row.
NATIVE_HOOK_KILL_SENTINEL_NAME = "steer-native-hook.disabled"
_LOCK_NB_ATTEMPTS = 3
_LOCK_NB_SLEEP_S = 0.05
STEER_ENVELOPE_PREFIX = "ULG_STEER:"
# Ledger CHECK set is cursor_dispatch_ledger.py:46-49. TTL relaxation applies
# only while a model run is still in play. ``queued`` is active identity but
# has no live model. Terminal statuses refuse delivery.
STEER_LIVE_LEDGER_STATUSES = frozenset({"admitted", "running", "parked_waiting"})
STEER_TERMINAL_LEDGER_STATUSES = frozenset({"completed", "failed", "cancelled"})
TERMINAL_UNDELIVERED_REASON = "dispatch_terminal_undelivered"


@dataclass(frozen=True, slots=True)
class PendingSteer:
    """One claimed steer entry ready for append on the next tools/call frame."""

    entry_id: str
    dispatch_id: str
    authority_turn_id: str
    directive: str
    deposited_at: str
    ttl_s: int


def _operator_gateway_data_dir() -> Path:
    """Gateway data root for paths that must not follow a swapped dispatch HOME."""
    raw = os.environ.get("DATA_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / ".gateway"


def native_hook_kill_sentinel_path() -> Path:
    """In-flight kill switch. Exists ⇒ hook returns ``{}`` and leaves the row pending.

    When ``ULG_STEER_SPOOL_DIR`` is set (production bridge launch), the sentinel
    sits beside ``steer-spool/`` under the operator gateway dir, e.g.
    ``~/.gateway/steer-native-hook.disabled``. Otherwise falls back to
    ``DATA_DIR`` or the passwd-home ``~/.gateway``. Never ``Path.home()``.
    Checked at hook runtime, not at dispatch HOME setup.
    """
    root = _spool_root(None)
    if root is not None:
        return root.parent / NATIVE_HOOK_KILL_SENTINEL_NAME
    return _operator_gateway_data_dir() / NATIVE_HOOK_KILL_SENTINEL_NAME


def spool_path(spool_dir: Path | str, dispatch_id: str) -> Path:
    safe = dispatch_id.replace("/", "_").replace(":", "_")
    return Path(spool_dir) / f"{safe}.json"


def _load_spool(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"dispatch_id": "", "pending": [], "delivered": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"dispatch_id": "", "pending": [], "delivered": []}
    data.setdefault("pending", [])
    data.setdefault("delivered", [])
    return data


def _write_spool(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, separators=(",", ":")))
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _entry_expired(raw: dict[str, Any], *, now: float) -> bool:
    ttl_s = int(raw.get("ttl_s") or 0)
    if ttl_s <= 0:
        return False
    deposited_at = str(raw.get("deposited_at") or "")
    try:
        dep_ts = datetime.fromisoformat(deposited_at.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return False
    return now - dep_ts > ttl_s


def _spool_root(spool_dir: Path | str | None) -> Path | None:
    """Spool directory. Empty ``ULG_STEER_SPOOL_DIR`` is absent, not ``Path('')``."""
    if spool_dir is not None and str(spool_dir).strip():
        return Path(spool_dir)
    raw = os.environ.get(ULG_STEER_SPOOL_DIR_ENV, "").strip()
    if not raw:
        return None
    return Path(raw)


class _SpoolLockBusyError(Exception):
    """Non-blocking lock was not acquired after the short retry."""


@contextmanager
def _spool_lock(path: Path, *, nonblocking: bool = False) -> Iterator[None]:
    """Exclusive lock so MCP and the native hook cannot claim the same row.

    *nonblocking* is the hook path: ``LOCK_NB`` a few times, then
    ``_SpoolLockBusyError`` so the hook can return ``{}`` instead of stalling
    the tool result.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    handle = lock_path.open("a+")
    acquired = False
    try:
        if nonblocking:
            for attempt in range(_LOCK_NB_ATTEMPTS):
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if attempt + 1 < _LOCK_NB_ATTEMPTS:
                        time.sleep(_LOCK_NB_SLEEP_S)
            if not acquired:
                raise _SpoolLockBusyError
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            acquired = True
        yield
    finally:
        if acquired:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def steer_dispatch_is_live(dispatch_id: str) -> bool | None:
    """Ledger liveness for TTL relaxation.

    True when status is admitted, running, or parked_waiting.
    False when the dispatch has ended (completed, failed, cancelled).
    None when the row or ledger cannot be read (TTL skip stays in force).
    ``queued`` is None: no model is running yet.
    """
    if not dispatch_id:
        return None
    # stdlib read of the env pin. Do not import GIW: the hook must fail open
    # when the worker package is missing or the ledger path is unset.
    db_path = os.environ.get(CURSOR_SDK_DISPATCH_LEDGER_ENV, "").strip()
    if not db_path:
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return None
    if row is None:
        return None
    status = str(row[0])
    if status in STEER_LIVE_LEDGER_STATUSES:
        return True
    if status in STEER_TERMINAL_LEDGER_STATUSES:
        return False
    return None


def _pending_to_steer(raw: dict[str, Any], dispatch_id: str) -> PendingSteer:
    return PendingSteer(
        entry_id=str(raw.get("entry_id") or ""),
        dispatch_id=dispatch_id,
        authority_turn_id=str(raw.get("authority_turn_id") or ""),
        directive=str(raw.get("directive") or ""),
        deposited_at=str(raw.get("deposited_at") or ""),
        ttl_s=int(raw.get("ttl_s") or 0),
    )


def claim_pending(
    dispatch_id: str,
    *,
    spool_dir: Path | str | None = None,
    allow_expired: bool = False,
) -> PendingSteer | None:
    """Atomically claim the oldest undelivered steer entry for *dispatch_id*.

    Expired rows are skipped unless *allow_expired* (dispatch still live).
    """
    root = _spool_root(spool_dir)
    if not dispatch_id or root is None:
        return None
    path = spool_path(root, dispatch_id)
    if not path.is_file():
        return None
    with _spool_lock(path):
        return _claim_pending_unlocked(path, dispatch_id, allow_expired=allow_expired)


def _claim_pending_unlocked(
    path: Path,
    dispatch_id: str,
    *,
    allow_expired: bool,
) -> PendingSteer | None:
    now = time.time()
    data = _load_spool(path)
    pending = data.get("pending") or []
    if not isinstance(pending, list):
        return None
    for idx, raw in enumerate(pending):
        if not isinstance(raw, dict) or raw.get("claimed"):
            continue
        if not allow_expired and _entry_expired(raw, now=now):
            continue
        raw = {**raw, "claimed": True}
        pending[idx] = raw
        data["pending"] = pending
        _write_spool(path, data)
        return _pending_to_steer(raw, dispatch_id)
    return None


def format_directive_envelope(pending: PendingSteer) -> str:
    payload = {
        "dispatch_id": pending.dispatch_id,
        "authority_turn_id": pending.authority_turn_id,
        "entry_id": pending.entry_id,
        "directive": pending.directive,
    }
    return f"{STEER_ENVELOPE_PREFIX}{json.dumps(payload, separators=(',', ':'))}"


def append_directive(
    payload: dict[str, Any],
    pending: PendingSteer,
) -> dict[str, Any]:
    """Append steer envelope as second ``result.content`` block; block[0] untouched."""
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    content = result.get("content")
    if not isinstance(content, list) or not content:
        return payload
    first = content[0]
    if not isinstance(first, dict):
        return payload
    envelope = format_directive_envelope(pending)
    new_content = [first, {"type": "text", "text": envelope}]
    return {**payload, "result": {**result, "content": new_content}}


def mark_delivered(
    pending: PendingSteer,
    *,
    spool_dir: Path | str | None = None,
    delivered_at: str | None = None,
) -> None:
    """Move a claimed entry from pending to delivered in the spool file."""
    root = _spool_root(spool_dir)
    if not pending.dispatch_id or root is None:
        return
    path = spool_path(root, pending.dispatch_id)
    if not path.is_file():
        return
    with _spool_lock(path):
        _mark_delivered_unlocked(path, pending, delivered_at=delivered_at)


def _mark_delivered_unlocked(
    path: Path,
    pending: PendingSteer,
    *,
    delivered_at: str | None,
    delivered_via: str | None = None,
) -> None:
    ts = delivered_at or datetime.now(UTC).isoformat()
    data = _load_spool(path)
    pending_list = [e for e in (data.get("pending") or []) if isinstance(e, dict)]
    delivered_list = list(data.get("delivered") or [])
    kept: list[dict[str, Any]] = []
    moved = False
    for raw in pending_list:
        if not moved and str(raw.get("entry_id")) == pending.entry_id:
            record = {**raw, "delivered_at": ts, "claimed": True}
            if delivered_via:
                record["delivered_via"] = delivered_via
            delivered_list.append(record)
            moved = True
        else:
            kept.append(raw)
    data["pending"] = kept
    data["delivered"] = delivered_list
    data["dispatch_id"] = pending.dispatch_id
    _write_spool(path, data)


def consume_next_steer_envelope(
    dispatch_id: str,
    *,
    spool_dir: Path | str | None = None,
    live: bool | None = None,
    delivered_via: str | None = None,
    lock_nonblocking: bool = False,
) -> str | None:
    """Claim, format, and mark one steer. Shared by the MCP bridge and native hook.

    *live* overrides the ledger read. None looks up the ledger. False refuses
    (dispatch ended). True delivers even when TTL has elapsed.
    *delivered_via* is stored on the delivered row (``native_hook`` or
    ``mcp_bridge``). The row is marked delivered when this returns, which is
    before the model reads the tool result.

    Terminal window (low): a ledger flip to completed/failed/cancelled after
    the live read and before the mark can still record delivery. Closeout
    expire only moves still-pending rows, so that row is omitted from
    ``steer_undelivered``. The hook is fail-open; the window is one lock hold.
    """
    root = _spool_root(spool_dir)
    if not dispatch_id or root is None:
        return None
    path = spool_path(root, dispatch_id)
    if not path.is_file():
        return None
    if live is None:
        live = steer_dispatch_is_live(dispatch_id)
    if live is False:
        return None
    try:
        with _spool_lock(path, nonblocking=lock_nonblocking):
            pending = _claim_pending_unlocked(
                path, dispatch_id, allow_expired=live is True
            )
            if pending is None:
                return None
            envelope = format_directive_envelope(pending)
            _mark_delivered_unlocked(
                path, pending, delivered_at=None, delivered_via=delivered_via
            )
            return envelope
    except _SpoolLockBusyError:
        return None


def expire_pending_entries(
    dispatch_id: str,
    *,
    reason: str,
    spool_dir: Path | str | None = None,
    expired_at: str | None = None,
) -> list[dict[str, Any]]:
    """Move every still-pending row (including TTL-skipped) to ``expired``.

    Claimed-but-not-delivered rows are included. Already-delivered rows stay.
    Returns the moved records. Idempotent: a second call returns [].
    """
    root = _spool_root(spool_dir)
    if not dispatch_id or root is None:
        return []
    path = spool_path(root, dispatch_id)
    if not path.is_file():
        return []
    ts = expired_at or datetime.now(UTC).isoformat()
    with _spool_lock(path):
        data = _load_spool(path)
        pending = [e for e in (data.get("pending") or []) if isinstance(e, dict)]
        if not pending:
            return []
        expired = list(data.get("expired") or [])
        moved: list[dict[str, Any]] = []
        for raw in pending:
            record = {
                **raw,
                "expired_at": ts,
                "reason": reason,
                "claimed": True,
            }
            expired.append(record)
            moved.append(record)
        data["pending"] = []
        data["expired"] = expired
        data["dispatch_id"] = dispatch_id
        _write_spool(path, data)
        return moved


def _native_tool_name(tool_name: str) -> bool:
    """True for Shell/Read/Grep-class tools. MCP names stay on the bridge path.

    Names that start with ``MCP:`` are rejected here. Other MCP spellings
    (no prefix) would be treated as native; that mis-route is low impact
    because the bridge still delivers on the next ``tools/call`` only when
    this hook did not already claim the row.
    """
    name = tool_name.strip()
    if not name:
        return False
    if name.startswith("MCP:"):
        return False
    return True


def native_tool_steer_hook_response(payload: dict[str, Any]) -> dict[str, Any]:
    """postToolUse hook body. Empty object when this event must not consume."""
    tool_name = str(payload.get("tool_name") or "")
    if not _native_tool_name(tool_name):
        return {}
    dispatch_id = os.environ.get(CURSOR_SDK_DISPATCH_ID_ENV, "").strip()
    if not dispatch_id:
        return {}
    envelope = consume_next_steer_envelope(
        dispatch_id, delivered_via="native_hook", lock_nonblocking=True
    )
    if not envelope:
        return {}
    return {"additional_context": envelope}


def append_steer_text(payload: dict[str, Any], envelope: str) -> dict[str, Any]:
    """Append *envelope* as the second ``result.content`` block; block[0] untouched."""
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    content = result.get("content")
    if not isinstance(content, list) or not content:
        return payload
    first = content[0]
    if not isinstance(first, dict):
        return payload
    new_content = [first, {"type": "text", "text": envelope}]
    return {**payload, "result": {**result, "content": new_content}}


def append_spool_entry(
    dispatch_id: str,
    *,
    authority_turn_id: str,
    directive: str,
    ttl_s: int,
    spool_dir: Path | str,
    entry_id: str | None = None,
) -> str:
    """Register a pending steer entry (GIW deposit path). Returns ``entry_id``.

    The lock is held inside this producer so two deposits cannot drop a row.
    The spool file is replaced via a unique temp name.
    """
    eid = entry_id or uuid.uuid4().hex
    path = spool_path(spool_dir, dispatch_id)
    with _spool_lock(path):
        data = _load_spool(path)
        data["dispatch_id"] = dispatch_id
        pending = list(data.get("pending") or [])
        pending.append(
            {
                "entry_id": eid,
                "authority_turn_id": authority_turn_id,
                "directive": directive,
                "deposited_at": datetime.now(UTC).isoformat(),
                "ttl_s": ttl_s,
                "claimed": False,
            }
        )
        data["pending"] = pending
        _write_spool(path, data)
    return eid


def read_delivery_ack(
    dispatch_id: str,
    entry_id: str,
    *,
    spool_dir: Path | str,
) -> dict[str, Any] | None:
    """Return the delivered spool record when the bridge has acked *entry_id*."""
    path = spool_path(spool_dir, dispatch_id)
    if not path.is_file():
        return None
    data = _load_spool(path)
    for raw in data.get("delivered") or []:
        if isinstance(raw, dict) and str(raw.get("entry_id")) == entry_id:
            return raw
    return None
