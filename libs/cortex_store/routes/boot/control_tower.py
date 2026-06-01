"""GET /control-tower(+/data) — same-origin fleet aggregation dashboard.

Tier-1 of decision:cortex-control-tower-ui. ``/control-tower`` serves the
self-contained dashboard HTML so it runs same-origin against cortex-api
(kills CORS for same-origin fetch). ``/control-tower/data`` is the canonical
single-call merged surface: fleet state read from cortex's own tables plus
unread agent-bus threads (read over the bus UDS via the existing observability
bridge — no domain import, degrades to [] when the bus is unreachable), with
the ATTENTION signals computed server-side. The HTML client fetches this
endpoint directly (not the legacy ``/boot-*`` fan-out).

LOCALHOST-ONLY: cortex-api has no auth layer (assertion 10605). MCP uses UDS;
browser access is via manage's HTTP listener (default 0.0.0.0:8202; override
CORTEX_API_HTTP_HOST). Restrict at the network edge if exposing beyond trusted LAN.
Port 8200 is not cortex-api on typical hosts (often docker/cloud-proxy).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from ...db import cortex_conn, json_decode
from ...db import query as db_query
from ...observability_bridge import query_agent_bus_threads
from ..deadlines import _list_deadlines_impl
from .continuity import _build_continuity_chain

router = APIRouter(tags=["control-tower"])

_HTML_PATH = Path(__file__).parent / "control_tower.html"

_ONE_DAY = timedelta(days=1)

_TODO_PRIORITY_ORDER = (
    "CASE json_extract(e.attributes, '$.priority') "
    "WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END"
)


def _strip_todo_prefix(entity_id: str) -> str:
    return entity_id.replace("todo:", "", 1)


def _countdown(due: str | None) -> dict[str, Any]:
    """Days-until/overdue for a due string (mirrors the client countdown())."""
    if not due:
        return {"txt": "", "cls": "", "days": None}
    end: datetime | None = None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
        end = datetime.fromisoformat(f"{due}T23:59:59+00:00")
    elif re.fullmatch(r"\d{4}-\d{2}", due):
        year, month = (int(p) for p in due.split("-"))
        # JS Date.UTC(y, m, 0) → last day of month `m` (1-based here).
        first_next = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=UTC)
        end = first_next.replace(hour=23, minute=59, second=59) - _ONE_DAY
    elif re.fullmatch(r"\d{4}", due):
        end = datetime(int(due), 12, 31, 23, 59, 59, tzinfo=UTC)
    else:
        try:
            parsed = datetime.fromisoformat(due)
            end = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return {"txt": due, "cls": "", "days": None}
    days = round((end - datetime.now(UTC)).total_seconds() / 86400)
    if days < 0:
        return {"txt": f"{-days}d OVERDUE", "cls": "ov", "days": days}
    if days <= 30:
        return {"txt": f"{days}d", "cls": "soon", "days": days}
    return {"txt": f"{days}d", "cls": "ok", "days": days}


def _aggregate(conn: Any) -> dict[str, Any]:
    """Read cortex's own tables into the merged fleet-state shape."""
    latest = db_query(
        conn,
        "SELECT session_id, agent, summary, open_items "
        "FROM session_journals ORDER BY id DESC LIMIT 1",
    )
    if latest:
        head = latest[0]
        continuity = _build_continuity_chain(conn, head["session_id"])
        last_session = head["summary"]
        open_items = [
            {"d": str(item)} for item in json_decode(head["open_items"], fallback=[])
        ]
    else:
        continuity, last_session, open_items = [], None, []

    seats = db_query(
        conn,
        "SELECT agent, session_id, summary FROM session_journals j "
        "WHERE id = (SELECT MAX(id) FROM session_journals WHERE agent = j.agent) "
        "ORDER BY id DESC",
    )

    in_flight = db_query(
        conn,
        "SELECT e.id, json_extract(e.attributes, '$.domain') AS dom, "
        "e.description AS d, json_extract(e.attributes, '$.deadline_date') AS due "
        "FROM entities e WHERE e.type = 'todo' AND e.workflow_state = 'in_progress' "
        "ORDER BY e.updated_at DESC LIMIT 20",
    )

    todos = db_query(
        conn,
        "SELECT e.id, e.description AS d, "
        "json_extract(e.attributes, '$.priority') = 'high' AS high "
        "FROM entities e WHERE e.type = 'todo' AND e.workflow_state = 'open' "
        f"ORDER BY {_TODO_PRIORITY_ORDER}, e.updated_at DESC LIMIT 8",
    )
    todos_total = db_query(
        conn,
        "SELECT COUNT(*) AS n FROM entities WHERE type = 'todo' "
        "AND workflow_state = 'open'",
    )[0]["n"]

    plan_phases = [
        {"id": r["id"], "done": r["workflow_state"] == "done", "d": r["d"]}
        for r in db_query(
            conn,
            "SELECT id, workflow_state, description AS d FROM entities "
            "WHERE type = 'plan_phase' AND workflow_state IN ('in_progress', 'done') "
            "ORDER BY updated_at DESC LIMIT 5",
        )
    ]

    deadlines = [
        {
            "due": it["deadline_date"],
            "t": it["deadline_name"],
            "matter": it["matter_name"],
        }
        for it in _list_deadlines_impl()["items"]
        if it.get("deadline_date")
    ]

    mentions = db_query(
        conn,
        "SELECT e.id, e.name AS t, COUNT(a.id) AS n, MAX(a.created_at) AS last "
        "FROM entities e JOIN assertions a ON a.entity_id = e.id "
        "WHERE a.created_at > datetime('now', '-7 days') AND a.superseded_by IS NULL "
        "AND e.type NOT IN ('transcript','todo','journal','assertion','plan_phase',"
        "'agent_skill','boot_session') "
        "GROUP BY e.id ORDER BY last DESC LIMIT 10",
    )

    audit = {
        "crit": 0,
        "warn": db_query(
            conn,
            "SELECT COUNT(*) AS n FROM assertions "
            "WHERE review_status = 'flagged' AND superseded_by IS NULL",
        )[0]["n"],
        "info": db_query(
            conn,
            "SELECT COUNT(*) AS n FROM entities WHERE status = 'provisional'",
        )[0]["n"],
    }

    bus = [
        t.get("slug") or t.get("id")
        for t in query_agent_bus_threads()
        if (t.get("unread_count") or 0) > 0
    ]

    return {
        "session": latest[0]["session_id"] if latest else None,
        "continuity": continuity,
        "last_session": last_session,
        "seats": seats,
        "open_items": open_items,
        "in_flight": in_flight,
        "todos": [{"id": t["id"], "d": t["d"], "high": bool(t["high"])} for t in todos],
        "todos_total": todos_total,
        "plan_phases": plan_phases,
        "deadlines": deadlines,
        "mentions": mentions,
        "audit": audit,
        "bus": bus,
    }


def _derive_alerts(state: dict[str, Any]) -> list[dict[str, str]]:
    """Server-side port of the client deriveAlerts() — the divergence spec."""
    alerts: list[dict[str, str]] = []

    for d in state["deadlines"]:
        c = _countdown(d.get("due"))
        if c["days"] is not None and c["days"] < 0:
            alerts.append(
                {
                    "sev": "crit",
                    "msg": f"Deadline {c['txt']} — {d['t']}",
                    "tag": d.get("matter") or "",
                }
            )

    for t in state["in_flight"]:
        c = _countdown(t.get("due"))
        if c["days"] is not None and c["days"] < 0:
            alerts.append(
                {
                    "sev": "warn",
                    "msg": f"In-flight todo past due ({c['txt']}) — "
                    f"{_strip_todo_prefix(t['id'])}",
                    "tag": t.get("dom") or "",
                }
            )

    for t in state["todos"]:
        if re.search(r"block", t.get("d") or "", re.IGNORECASE):
            alerts.append(
                {"sev": "warn", "msg": f"Blocker open — {t['d']}", "tag": "high"}
            )

    # cross-surface divergence: a slug open on BOTH a todo and an unread bus thread
    todo_slugs = {
        _strip_todo_prefix(t["id"]) for t in (*state["in_flight"], *state["todos"])
    }
    for slug in state["bus"]:
        if slug in todo_slugs:
            alerts.append(
                {
                    "sev": "warn",
                    "msg": f"Divergence — {slug} is open on BOTH a todo and an "
                    f"unread bus thread",
                    "tag": "two surfaces",
                }
            )

    # deadline with no owning session: active deadline whose matter is not
    # referenced by any recent session journal summary/open_items
    for d in state["deadlines"]:
        matter = d.get("matter")
        c = _countdown(d.get("due"))
        if matter and c["days"] is not None and c["days"] >= 0:
            if not _matter_has_owning_session(matter):
                alerts.append(
                    {
                        "sev": "warn",
                        "msg": f"Deadline has no owning session — {d['t']}",
                        "tag": matter,
                    }
                )

    if len(state["bus"]) >= 8:
        alerts.append(
            {
                "sev": "info",
                "msg": f"{len(state['bus'])} unread bus threads across seats — "
                f"coordination backlog",
                "tag": "bus",
            }
        )
    audit = state["audit"]
    if audit["warn"] > 1000:
        alerts.append(
            {
                "sev": "info",
                "msg": f"Audit: {audit['warn']:,} warnings, {audit['crit']} critical",
                "tag": "audit",
            }
        )

    order = {"crit": 0, "warn": 1, "info": 2}
    return sorted(alerts, key=lambda a: order[a["sev"]])


def _matter_has_owning_session(matter: str) -> bool:
    conn = cortex_conn()
    try:
        rows = db_query(
            conn,
            "SELECT 1 FROM session_journals "
            "WHERE timestamp > datetime('now', '-30 days') "
            "AND (summary LIKE ? OR open_items LIKE ?) LIMIT 1",
            (f"%{matter}%", f"%{matter}%"),
        )
    finally:
        conn.close()
    return bool(rows)


@router.get("/control-tower/data")
def get_control_tower_data() -> dict[str, Any]:
    """Merged fleet state + server-computed ATTENTION signals (single call)."""
    conn = cortex_conn()
    try:
        state = _aggregate(conn)
    finally:
        conn.close()
    state["alerts"] = _derive_alerts(state)
    return state


@router.get("/control-tower", response_class=HTMLResponse)
def serve_control_tower() -> HTMLResponse:
    """Serve the self-contained dashboard HTML (same-origin, localhost-only)."""
    return HTMLResponse(_HTML_PATH.read_text(encoding="utf-8"))
