#!/usr/bin/env python3
"""Probe: correlate frontier.poll.hint.issued with mcp.agentbus.wait.called.

Friction 24081 / todo:agent-bus-poll-hint-wait-correlation (E1 alarm v1).
Complementary relay detector: todo:mcp-local-api-orphan-detector.

Alerts when a poll hint is overdue (issued ≥ window ago, within lookback) with
no matching wait.called and no successful wait.completed in the post-issue window.

Usage:
  python scripts/probes/poll_hint_wait_correlation.py
  python scripts/probes/poll_hint_wait_correlation.py --window-s 300 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
_QUERY_SCRIPT = _REPO / "scripts" / "query-events"
_DEFAULT_WINDOW_S = 300
_DEFAULT_LOOKBACK_S = 86400

_HINTS_SQL = """
SELECT seq, ts_unix_ms, payload
FROM events
WHERE signal = 'frontier.poll.hint.issued'
  AND ts_unix_ms <= ?
  AND ts_unix_ms >= ?
ORDER BY ts_unix_ms ASC
"""

_WAIT_CALLED_SQL = """
SELECT 1 FROM events
WHERE signal = 'mcp.agentbus.wait.called'
  AND ts_unix_ms >= ?
  AND ts_unix_ms <= ?
  AND json_extract(payload, '$.thread') = ?
LIMIT 1
"""

_WAIT_COMPLETED_SQL = """
SELECT 1 FROM events
WHERE signal = 'mcp.agentbus.wait.completed'
  AND ts_unix_ms >= ?
  AND ts_unix_ms <= ?
  AND json_extract(payload, '$.thread') = ?
  AND COALESCE(json_extract(payload, '$.status'), '') NOT IN ('', 'error', 'relay_error')
LIMIT 1
"""


def _default_query(
    sql: str, params: list[str], *, limit: int = 10_000
) -> list[dict[str, Any]]:
    if not _QUERY_SCRIPT.is_file():
        raise FileNotFoundError(f"query-events not found: {_QUERY_SCRIPT}")
    cmd = [str(_QUERY_SCRIPT), "--sql", sql, "--limit", str(limit), "--compact"]
    for param in params:
        cmd.extend(["--sql-param", param])
    socket = os.environ.get("EVENTS_QUERY_SOCK")
    if socket:
        cmd.extend(["--socket", socket])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or "query-events failed"
        raise RuntimeError(err)
    payload = json.loads(proc.stdout or "{}")
    rows = payload.get("rows")
    if isinstance(rows, list):
        return rows
    return []


def _has_match(sql: str, params: list[str], query_fn: Callable[..., list[dict]]) -> bool:
    return bool(query_fn(sql, params, limit=1))


def _extract_thread_id(row: dict[str, Any]) -> str | None:
    payload_raw = row.get("payload") or row.get("payload_json")
    if not payload_raw:
        return None
    if isinstance(payload_raw, dict):
        payload = payload_raw
    else:
        try:
            payload = json.loads(str(payload_raw))
        except json.JSONDecodeError:
            return None
    thread_id = payload.get("thread_id")
    if thread_id is None:
        return None
    text = str(thread_id).strip()
    return text or None


def classify_hint(
    *,
    thread_id: str,
    issued_ms: int,
    window_ms: int,
    query_fn: Callable[..., list[dict]],
) -> str:
    """Return matched | cleared | alert for one overdue hint."""
    upper_ms = issued_ms + window_ms
    params = [str(issued_ms), str(upper_ms), thread_id]
    if _has_match(_WAIT_CALLED_SQL, params, query_fn):
        return "matched"
    if _has_match(_WAIT_COMPLETED_SQL, params, query_fn):
        return "cleared"
    return "alert"


def find_alertable_hints(
    *,
    now_ms: int | None = None,
    window_s: int = _DEFAULT_WINDOW_S,
    lookback_s: int = _DEFAULT_LOOKBACK_S,
    query_fn: Callable[..., list[dict]] | None = None,
) -> list[dict[str, Any]]:
    """Overdue hints with no wait.called and no successful wait.completed."""
    effective_now = now_ms if now_ms is not None else int(time.time() * 1000)
    window_ms = window_s * 1000
    lookback_ms = lookback_s * 1000
    cutoff_ms = effective_now - window_ms
    floor_ms = effective_now - lookback_ms
    run_query = query_fn or _default_query
    rows = run_query(
        _HINTS_SQL,
        [str(cutoff_ms), str(floor_ms)],
    )
    alerts: list[dict[str, Any]] = []
    for row in rows:
        thread_id = _extract_thread_id(row)
        if not thread_id:
            continue
        issued_ms = int(row.get("ts_unix_ms") or 0)
        if classify_hint(
            thread_id=thread_id,
            issued_ms=issued_ms,
            window_ms=window_ms,
            query_fn=run_query,
        ) != "alert":
            continue
        alerts.append(
            {
                "seq": row.get("seq"),
                "thread_id": thread_id,
                "issued_ms": issued_ms,
            }
        )
    return alerts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--window-s",
        type=int,
        default=int(os.environ.get("POLL_HINT_WAIT_WINDOW_S", _DEFAULT_WINDOW_S)),
        help=f"Overdue age in seconds (default {_DEFAULT_WINDOW_S})",
    )
    parser.add_argument(
        "--lookback-s",
        type=int,
        default=int(os.environ.get("POLL_HINT_WAIT_LOOKBACK_S", _DEFAULT_LOOKBACK_S)),
        help=f"Retention bound in seconds (default {_DEFAULT_LOOKBACK_S})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print alertable hints without failing",
    )
    args = parser.parse_args(argv)

    print(
        "poll_hint_wait_correlation "
        "(friction 24081 · todo:agent-bus-poll-hint-wait-correlation · "
        "todo:mcp-local-api-orphan-detector)"
    )
    print(
        f"window={args.window_s}s lookback={args.lookback_s}s "
        f"dry_run={args.dry_run}"
    )

    try:
        alerts = find_alertable_hints(
            window_s=args.window_s,
            lookback_s=args.lookback_s,
        )
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if alerts:
        print(json.dumps({"alertable": alerts}, indent=2))
    else:
        print("clean: no unmatched overdue poll hints in window")

    if alerts and not args.dry_run:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
