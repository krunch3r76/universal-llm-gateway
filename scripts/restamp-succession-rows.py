#!/usr/bin/env python3
"""One-shot F2 repair for succession rows stamped with wrong dominant_lane.

Default is dry-run. Pass --live to apply mutations (operator-gated).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from cortex_store.dispatch_ops._session_bus_thread_disposition import parse_bus_thread_refs
from cortex_store.transcript_assembly import _transcripts_root
from cortex_store.transcript_lane_touch import dominant_lane, lane_touches
from cortex_store.transcript_session_id import _jsonl_paths_by_mtime_desc
from cortex_store.session_close_successor_hop import conversation_uuid_from_jsonl_path


def _cortex_db_path() -> Path:
    return Path.home() / ".cortex" / "cortex.db"


def _window_for_uuid(uuid: str) -> Path | None:
    root = _transcripts_root()
    for path in _jsonl_paths_by_mtime_desc(root):
        try:
            if conversation_uuid_from_jsonl_path(path) == uuid:
                return path
        except (OSError, ValueError):
            continue
    return None


def restamp_rows(*, live: bool) -> dict[str, object]:
    db_path = _cortex_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT session_id, conversation_uuid, dominant_lane, entity_ids, sealed_on "
        "FROM session_journals WHERE closed_by = 'succession' "
        "AND sealed_on >= '2026-09-07T23:39:11' AND sealed_on <= '2026-09-07T23:39:19'"
    ).fetchall()
    updates: list[dict[str, object]] = []
    for row in rows:
        uuid = row["conversation_uuid"]
        if not uuid:
            continue
        jsonl = _window_for_uuid(str(uuid))
        if jsonl is None:
            continue
        touches = lane_touches(jsonl)
        computed = dominant_lane(touches)
        entity_ids = json.loads(row["entity_ids"] or "[]")
        refs = parse_bus_thread_refs([str(x) for x in entity_ids])
        new_entity_ids = sorted(refs)
        if computed:
            new_entity_ids = sorted({*new_entity_ids, f"agent-bus:{computed}"})
        payload = {
            "session_id": row["session_id"],
            "conversation_uuid": uuid,
            "prior_dominant_lane": row["dominant_lane"],
            "new_dominant_lane": computed,
            "prior_entity_ids": entity_ids,
            "new_entity_ids": new_entity_ids,
        }
        updates.append(payload)
        if live and (
            computed != row["dominant_lane"]
            or new_entity_ids != entity_ids
        ):
            conn.execute(
                "UPDATE session_journals SET dominant_lane = ?, entity_ids = ? "
                "WHERE session_id = ?",
                (
                    computed,
                    json.dumps(new_entity_ids),
                    row["session_id"],
                ),
            )
    if live:
        conn.commit()
    conn.close()
    return {"dry_run": not live, "candidate_count": len(updates), "updates": updates}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Apply mutations (default: dry-run only)",
    )
    args = parser.parse_args()
    result = restamp_rows(live=bool(args.live))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
