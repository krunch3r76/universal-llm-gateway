"""Close draft TTL reaper — short TTL + long-stop reflection flush."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ..db import cortex_conn, json_decode
from ..events_close import close_draft_reflections_flushed
from ..routes.reflective_journal import _insert_reflective_entry_tx


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts
    except (ValueError, TypeError):
        return None


def _flush_reflections(
    conn: object,
    *,
    session_id: str,
    agent: str,
    fields: dict[str, Any],
) -> int:
    reflections = fields.get("reflections") or []
    if not isinstance(reflections, list):
        return 0
    count = 0
    for item in reflections:
        if not isinstance(item, dict):
            continue
        entry = str(item.get("entry") or "")
        if not entry.strip():
            continue
        _insert_reflective_entry_tx(
            conn,
            agent=agent,
            register=str(item.get("register") or "default"),
            entry=entry,
            kind=str(item.get("kind") or "reflection"),
            session_id=session_id,
            consolidation_data_json=(
                json.dumps(item["consolidation_data"])
                if item.get("consolidation_data") is not None
                else None
            ),
        )
        count += 1
    if count:
        close_draft_reflections_flushed(
            session_id=session_id, agent=agent, reflection_count=count
        )
    return count


def reap_expired_drafts(*, limit: int = 50) -> dict[str, Any]:
    """Sweep expired uncommitted drafts; flush reflections on long-stop."""
    now = _now()
    reaped = 0
    flushed = 0
    with cortex_conn() as conn:
        rows = conn.execute(
            "SELECT session_id, agent, fields, ttl_expires_at, reflection_flush_after "
            "FROM close_drafts WHERE committed_at IS NULL LIMIT ?",
            (limit * 4,),
        ).fetchall()
        for row in rows:
            fields = json_decode(row["fields"] or "{}")
            refs = fields.get("reflections") or []
            has_refs = isinstance(refs, list) and len(refs) > 0
            ttl = _parse_ts(row["ttl_expires_at"])
            long_stop = _parse_ts(row["reflection_flush_after"])
            should_reap = False
            if has_refs and long_stop and now >= long_stop:
                flushed += _flush_reflections(
                    conn,
                    session_id=row["session_id"],
                    agent=row["agent"],
                    fields=fields,
                )
                should_reap = True
            elif not has_refs and ttl and now >= ttl:
                should_reap = True
            if should_reap and reaped < limit:
                conn.execute(
                    "DELETE FROM close_drafts WHERE session_id = ? AND committed_at IS NULL",
                    (row["session_id"],),
                )
                reaped += 1
        conn.commit()
    return {"reaped": reaped, "reflections_flushed": flushed}
