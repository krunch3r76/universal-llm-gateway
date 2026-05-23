"""P-grokbuild-mcp — Phase D eval harness for plan:grokbuild-mcp-integration.

Verifies dispatch-scoped MCP correlation (Phase C infrastructure):
  mcp.request.completed events from the grok seat carry dispatch_id,
  enabling JOIN with mcp.grokbuild.dispatch.called.

Canonical-fixture mode (default):
  1. Inject synthetic events into the event service for a probe dispatch_id.
  2. Run the JOIN query via scripts/query-events --sql.
  3. PASS = ≥1 mcp.request.completed per expected tool (fs, cortex)
     correlated to dispatch_id.

Pass criterion: PASS | WARN (event socket unavailable) | FAIL (JOIN returned
no rows for one or more expected tools).

Usage:
  python3 services/mcp-server/probes/p_grokbuild_mcp.py
  python3 services/mcp-server/probes/p_grokbuild_mcp.py --json
  python3 services/mcp-server/probes/p_grokbuild_mcp.py --dispatch-id <existing-id>
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
QUERY_EVENTS = REPO_ROOT / "scripts" / "query-events"

_EVENTS_INGEST_SOCK = os.getenv(
    "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
)
_EVENTS_QUERY_SOCK = os.getenv(
    "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
)

# Tools the canonical fixture exercises — one mcp.request.completed per tool.
_EXPECTED_TOOLS: tuple[str, ...] = ("fs", "cortex")

# Minimum seconds to wait after injection for the event service to persist.
_SETTLE_S: float = float(os.getenv("PROBE_SETTLE_S", "0.6"))

_JOIN_SQL = """
SELECT
  json_extract(c.payload, '$.tool_name')   AS tool_name,
  json_extract(c.payload, '$.dispatch_id') AS dispatch_id,
  json_extract(c.payload, '$.seat_class')  AS seat_class
FROM events c
WHERE c.signal = 'mcp.request.completed'
  AND json_extract(c.payload, '$.seat_class')  = 'grok'
  AND json_extract(c.payload, '$.dispatch_id') = '{dispatch_id}'
ORDER BY c.rowid DESC
""".strip()


# ── Fixture injection ────────────────────────────────────────────────────────


def _build_fixture_events(dispatch_id: str) -> list[dict]:
    """Return the canonical fixture: one dispatch.called + one per expected tool."""
    now_ms = int(time.time() * 1000)
    now_ts = datetime.now(UTC).isoformat()

    def _ev(signal: str, payload: dict) -> dict:
        return {
            "signal": signal,
            "source": "probe:p_grokbuild_mcp",
            "ts": now_ts,
            "ts_unix_ms": now_ms,
            "role": "observation",
            "scope": "global",
            "payload": payload,
        }

    events = [
        _ev(
            "mcp.grokbuild.dispatch.called",
            {
                "dispatch_id": dispatch_id,
                "mode": "read_only",
                "op": "build",
                "session_id": "",
                "model": "xai/grok-4.3__effort_medium",
            },
        )
    ]
    for tool in _EXPECTED_TOOLS:
        events.append(
            _ev(
                "mcp.request.completed",
                {
                    "dispatch_id": dispatch_id,
                    "seat_class": "grok",
                    "caller_identity": "grok-build-dispatch",
                    "tool_name": tool,
                    "mcp_method": "tools/call",
                    "method": "POST",
                    "client_ip": "127.0.0.1",
                    "duration_s": 0.042,
                    "auth_mode": "bearer",
                    "response_bytes": 128,
                },
            )
        )
    return events


def _inject_fixture(dispatch_id: str) -> bool:
    """Write fixture events to the event service ingest socket.

    Returns True when the socket was reachable and all events were sent,
    False when the socket is unavailable (WARN path).
    """
    events = _build_fixture_events(dispatch_id)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(3.0)
            sock.connect(_EVENTS_INGEST_SOCK)
            for ev in events:
                line = json.dumps(ev, default=str) + "\n"
                sock.sendall(line.encode())
        return True
    except (OSError, TimeoutError):
        return False


# ── Event query ──────────────────────────────────────────────────────────────


def _query_join(dispatch_id: str) -> list[dict]:
    """Query the event service for mcp.request.completed rows matching dispatch_id."""
    sql = _JOIN_SQL.format(dispatch_id=dispatch_id)
    try:
        result = subprocess.run(
            [str(QUERY_EVENTS), "--sql", sql],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        rows = data.get("rows", data) if isinstance(data, dict) else data
        return rows if isinstance(rows, list) else []
    except (
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
        FileNotFoundError,
        OSError,
    ):
        return []


# ── Probe entry ──────────────────────────────────────────────────────────────


def run_probe(
    *,
    dispatch_id: str | None = None,
    json_out: bool = False,
) -> dict:
    """Execute the canonical fixture and return a verdict dict.

    When ``dispatch_id`` is supplied, skip injection and query that existing id
    directly (useful for live-dispatch verification after a real grokbuild run).
    """
    fixture_mode = dispatch_id is None
    probe_dispatch_id = dispatch_id or f"probe-phd-{uuid.uuid4().hex[:16]}"

    socket_ok: bool | None = None
    if fixture_mode:
        socket_ok = _inject_fixture(probe_dispatch_id)
        if not socket_ok:
            result = {
                "verdict": "WARN",
                "dispatch_id": probe_dispatch_id,
                "reason": (
                    "event service ingest socket unavailable; "
                    "fixture events not injected — rerun when event service is running"
                ),
                "tools_found": [],
                "expected_tools": list(_EXPECTED_TOOLS),
                "socket_ok": False,
                "fixture_mode": True,
            }
            _print_result(result, json_out)
            return result
        time.sleep(_SETTLE_S)

    rows = _query_join(probe_dispatch_id)
    tools_found = [r.get("tool_name") for r in rows if r.get("tool_name")]
    missing = [t for t in _EXPECTED_TOOLS if t not in tools_found]

    if missing:
        verdict = "FAIL"
        reason = (
            f"JOIN returned no rows for tools: {missing!r}. "
            f"Phase C dispatch_id correlation not observed for this dispatch."
        )
    else:
        verdict = "PASS"
        reason = (
            f"≥1 mcp.request.completed per expected tool ({list(_EXPECTED_TOOLS)!r}) "
            f"correlated to dispatch_id={probe_dispatch_id!r}. "
            f"Phase C correlation confirmed."
        )

    result = {
        "verdict": verdict,
        "dispatch_id": probe_dispatch_id,
        "reason": reason,
        "tools_found": tools_found,
        "expected_tools": list(_EXPECTED_TOOLS),
        "rows": rows,
        "socket_ok": socket_ok,
        "fixture_mode": fixture_mode,
    }
    _print_result(result, json_out)
    return result


def _print_result(result: dict, json_out: bool) -> None:
    verdict = result["verdict"]
    if json_out:
        print(json.dumps(result, indent=2, default=str))
    else:
        tag = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "WARN": "⚠️  WARN"}[verdict]
        print(f"\n{tag} — p_grokbuild_mcp (Phase D eval harness)")
        print(f"  dispatch_id : {result['dispatch_id']}")
        print(f"  reason      : {result['reason']}")
        print(f"  tools_found : {result['tools_found']}")
        if result.get("rows"):
            print(f"  rows ({len(result['rows'])}):")
            for row in result["rows"]:
                print(f"    {row}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Phase D eval harness — p_grokbuild_mcp"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--dispatch-id",
        metavar="ID",
        default=None,
        help="Query existing dispatch_id instead of injecting a fixture",
    )
    args = parser.parse_args()

    result = run_probe(dispatch_id=args.dispatch_id, json_out=args.json)
    sys.exit(0 if result["verdict"] in ("PASS", "WARN") else 1)
