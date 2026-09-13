"""Steer directive spool — bridge hot-path helpers (no HTTP).

Rung-1 of the steer ladder: the stdio MCP middlebox claims a pending directive
from the per-dispatch spool and appends it as a second ``result.content`` block
on the next relayed ``tools/call`` response. Pure functions + filesystem spool
only — no network on the relay hot path.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CURSOR_SDK_DISPATCH_ID_ENV = "CURSOR_SDK_DISPATCH_ID"
ULG_STEER_SPOOL_DIR_ENV = "ULG_STEER_SPOOL_DIR"
STEER_ENVELOPE_PREFIX = "ULG_STEER:"


@dataclass(frozen=True, slots=True)
class PendingSteer:
    """One claimed steer entry ready for append on the next tools/call frame."""

    entry_id: str
    dispatch_id: str
    authority_turn_id: str
    directive: str
    deposited_at: str
    ttl_s: int


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
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


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


def claim_pending(
    dispatch_id: str,
    *,
    spool_dir: Path | str | None = None,
) -> PendingSteer | None:
    """Atomically claim the oldest undelivered steer entry for *dispatch_id*."""
    root = Path(spool_dir or os.environ.get(ULG_STEER_SPOOL_DIR_ENV, ""))
    if not dispatch_id or not str(root):
        return None
    path = spool_path(root, dispatch_id)
    if not path.is_file():
        return None
    now = time.time()
    data = _load_spool(path)
    pending = data.get("pending") or []
    if not isinstance(pending, list):
        return None
    for idx, raw in enumerate(pending):
        if not isinstance(raw, dict) or raw.get("claimed"):
            continue
        if _entry_expired(raw, now=now):
            continue
        raw = {**raw, "claimed": True}
        pending[idx] = raw
        data["pending"] = pending
        _write_spool(path, data)
        return PendingSteer(
            entry_id=str(raw.get("entry_id") or ""),
            dispatch_id=dispatch_id,
            authority_turn_id=str(raw.get("authority_turn_id") or ""),
            directive=str(raw.get("directive") or ""),
            deposited_at=str(raw.get("deposited_at") or ""),
            ttl_s=int(raw.get("ttl_s") or 0),
        )
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
    root = Path(spool_dir or os.environ.get(ULG_STEER_SPOOL_DIR_ENV, ""))
    if not pending.dispatch_id or not str(root):
        return
    path = spool_path(root, pending.dispatch_id)
    if not path.is_file():
        return
    ts = delivered_at or datetime.now(UTC).isoformat()
    data = _load_spool(path)
    pending_list = [e for e in (data.get("pending") or []) if isinstance(e, dict)]
    delivered_list = list(data.get("delivered") or [])
    kept: list[dict[str, Any]] = []
    moved = False
    for raw in pending_list:
        if not moved and str(raw.get("entry_id")) == pending.entry_id:
            delivered_list.append({**raw, "delivered_at": ts, "claimed": True})
            moved = True
        else:
            kept.append(raw)
    data["pending"] = kept
    data["delivered"] = delivered_list
    data["dispatch_id"] = pending.dispatch_id
    _write_spool(path, data)


def append_spool_entry(
    dispatch_id: str,
    *,
    authority_turn_id: str,
    directive: str,
    ttl_s: int,
    spool_dir: Path | str,
    entry_id: str | None = None,
) -> str:
    """Register a pending steer entry (GIW deposit path). Returns ``entry_id``."""
    eid = entry_id or uuid.uuid4().hex
    path = spool_path(spool_dir, dispatch_id)
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
