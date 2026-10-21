"""Per-hop land registry — fired / landed / expired independent of JSONL lag.

Transcript grep alone cannot boot retire: Cursor often births the agent-transcript
JSONL minutes after submit (a:38474 measured gaps; specimen 15441 tip_cp=75 at
+842s). The resume-fence journal arms at ``beforeSubmitPrompt`` with the new
conversation id — that is land proof. This file records the hop's own lifecycle
so the departing tab can quiesce at ``fired`` and retire only at ``landed``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Literal

from durable_io.atomic import durable_write_text

from bus_watch.fable_lock import WATCH_DIR

logger = logging.getLogger(__name__)

HopLandState = Literal["fired", "landed", "expired"]
DEFAULT_LANDING_TIMEOUT_S = 600.0


def _resolve_watch_dir(watch_dir: Path | None) -> Path:
    return WATCH_DIR if watch_dir is None else watch_dir


def hop_land_path(root_id: str, watch_dir: Path | None = None) -> Path:
    return _resolve_watch_dir(watch_dir) / f"liaison-{root_id}.hop-land.json"


def write_hop_land(record: dict[str, Any], *, watch_dir: Path | None = None) -> Path:
    """Durable-write the hop-land record for ``record['root']``."""
    root = str(record.get("root") or "").strip()
    if not root:
        raise ValueError("hop land record requires root")
    target_dir = _resolve_watch_dir(watch_dir)
    path = hop_land_path(root, target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    durable_write_text(path, json.dumps(record, indent=2, sort_keys=True) + "\n")
    return path


def read_hop_land(
    root_id: str, *, watch_dir: Path | None = None
) -> dict[str, Any] | None:
    path = hop_land_path(root_id, _resolve_watch_dir(watch_dir))
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def mark_fired(
    *,
    root: str,
    tip_cp: int | None,
    seal_bus_turn: int | None,
    departing_transcript_id: str | None,
    fired_at: float | None = None,
    deadline_s: float = DEFAULT_LANDING_TIMEOUT_S,
    message_path: str | None = None,
    watch_dir: Path | None = None,
) -> dict[str, Any]:
    """Write ``state=fired`` after keystroke; departing tab must quiesce."""
    t = time.time() if fired_at is None else fired_at
    record: dict[str, Any] = {
        "root": str(root),
        "tip_cp": tip_cp,
        "seal_bus_turn": seal_bus_turn,
        "fired_at": t,
        "deadline_at": t + float(deadline_s),
        "departing_transcript_id": (departing_transcript_id or "").strip() or None,
        "state": "fired",
        "landed_transcript_id": None,
        "proof": None,
        "message_path": message_path,
    }
    write_hop_land(record, watch_dir=watch_dir)
    return record


def mark_landed(
    record: dict[str, Any],
    *,
    landed_transcript_id: str,
    proof: Literal["fence", "transcript", "pre_existing"],
    watch_dir: Path | None = None,
) -> dict[str, Any]:
    """Advance a fired record to ``landed``."""
    updated = {
        **record,
        "state": "landed",
        "landed_transcript_id": landed_transcript_id,
        "proof": proof,
        "landed_at": time.time(),
    }
    write_hop_land(updated, watch_dir=watch_dir)
    return updated


def is_fired_past_deadline(
    record: dict[str, Any] | None,
    *,
    now: float | None = None,
    watch_dir: Path | None = None,
) -> bool:
    """True when ``record`` is ``fired`` with no landed transcript past its deadline."""
    if not isinstance(record, dict):
        return False
    if record.get("state") != "fired":
        return False
    if record.get("landed_transcript_id"):
        return False
    t = time.time() if now is None else float(now)
    deadline_raw = record.get("deadline_at")
    if isinstance(deadline_raw, (int, float)):
        return t >= float(deadline_raw)
    fired_raw = record.get("fired_at")
    if isinstance(fired_raw, (int, float)):
        return t >= float(fired_raw) + DEFAULT_LANDING_TIMEOUT_S
    root = str(record.get("root") or "").strip()
    if root:
        path = hop_land_path(root, _resolve_watch_dir(watch_dir))
        try:
            return t >= path.stat().st_mtime + DEFAULT_LANDING_TIMEOUT_S
        except OSError:
            return False
    return False


def _drop_dead_hop_subscription(
    root_id: str,
    record: dict[str, Any],
    *,
    store: Any | None = None,
) -> bool:
    """Unsubscribe ``("cursor", root_id)`` when it still names an unlanded ``agent_id``."""
    root = str(root_id or "").strip()
    if not root or root == "*":
        return False
    landed_id = str(record.get("landed_transcript_id") or "").strip()
    dropped = False
    if store is not None:
        sub_fn = getattr(store, "subscription", None)
        unsub_fn = getattr(store, "unsubscribe", None)
        if callable(unsub_fn):
            sub_row: dict[str, Any] | None = None
            if callable(sub_fn):
                try:
                    resolved = sub_fn("cursor", root)
                    if isinstance(resolved, tuple) and len(resolved) == 2:
                        sub_row = resolved[1]
                except Exception:
                    logger.exception(
                        "ide_hop_land: subscription lookup failed root=%s", root
                    )
            sub_scope = str((sub_row or {}).get("thread_scope") or root).strip()
            sub_agent_id = str((sub_row or {}).get("agent_id") or "").strip()
            if sub_row is None or (
                sub_scope == root and sub_agent_id and sub_agent_id != landed_id
            ):
                if sub_row is not None:
                    try:
                        unsub_fn(
                            "cursor",
                            root,
                            bound_by="ide-hop-land",
                            evidence=f"hop_expired:{sub_agent_id}",
                            expected_agent_id=sub_agent_id,
                        )
                        dropped = True
                    except Exception:
                        logger.exception(
                            "ide_hop_land: unsubscribe failed root=%s", root
                        )
    if os.environ.get("AGENT_BUS_DB_PATH"):
        try:
            from agent_bus_store.db.endpoint_subscriptions import (
                current_subscription,
                unsubscribe_endpoint,
            )

            cur = current_subscription("cursor", root)
            if cur is not None and cur.get("state") == "subscribed":
                cur_agent_id = str(cur.get("agent_id") or "").strip()
                if cur_agent_id and cur_agent_id != landed_id:
                    unsubscribe_endpoint(
                        agent="cursor",
                        thread_scope=root,
                        bound_by="ide-hop-land",
                        evidence=f"hop_expired:{cur_agent_id}",
                        expected_agent_id=cur_agent_id,
                    )
                    dropped = True
        except Exception:
            logger.exception("ide_hop_land: local unsubscribe failed root=%s", root)
    return dropped


def mark_expired(
    record: dict[str, Any],
    *,
    watch_dir: Path | None = None,
    store: Any | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Advance a fired record to ``expired`` — OPERATOR_GATE, never re-fire."""
    t = time.time() if now is None else float(now)
    updated = {**record, "state": "expired", "expired_at": t}
    write_hop_land(updated, watch_dir=watch_dir)
    if not updated.get("landed_transcript_id"):
        root = str(updated.get("root") or "").strip()
        if root:
            _drop_dead_hop_subscription(root, updated, store=store)
    return updated


def expire_overdue_hop(
    root_id: str,
    *,
    store: Any | None = None,
    now: float | None = None,
    watch_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Expire a ``fired`` hop record past ``deadline_at`` with no landed tab and drop dead subscription."""
    root = str(root_id or "").strip()
    if not root:
        return None
    record = read_hop_land(root, watch_dir=watch_dir)
    if not is_fired_past_deadline(record, now=now, watch_dir=watch_dir):
        return None
    assert record is not None
    return mark_expired(record, watch_dir=watch_dir, store=store, now=now)


def expire_overdue_hops(
    store: Any | None = None,
    *,
    now: float | None = None,
    watch_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Scan ``watch_dir`` for overdue ``fired`` hop-land files and expire them."""
    target_dir = _resolve_watch_dir(watch_dir)
    if not target_dir.is_dir():
        return []
    expired: list[dict[str, Any]] = []
    for path in sorted(target_dir.glob("liaison-*.hop-land.json")):
        name = path.name
        if not (name.startswith("liaison-") and name.endswith(".hop-land.json")):
            continue
        root_id = name[len("liaison-") : -len(".hop-land.json")].strip()
        if not root_id:
            continue
        try:
            updated = expire_overdue_hop(
                root_id, store=store, now=now, watch_dir=target_dir
            )
        except OSError:
            logger.exception("ide_hop_land: expire overdue hop failed root=%s", root_id)
            continue
        if updated is not None:
            expired.append(updated)
    return expired


__all__ = [
    "DEFAULT_LANDING_TIMEOUT_S",
    "HopLandState",
    "expire_overdue_hop",
    "expire_overdue_hops",
    "hop_land_path",
    "is_fired_past_deadline",
    "mark_expired",
    "mark_fired",
    "mark_landed",
    "read_hop_land",
    "write_hop_land",
]
