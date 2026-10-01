"""Thin adapter: open the dispatch ledger read-only and return the census.

The fold is ``conductor_census.census``. This step does not migrate the
ledger and does not take a write lock. Options are ``open_only``, ``days``,
and ``format`` (``table`` or ``json``).
"""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from services.git_integration_worker.cursor_dispatch_ledger import (
    resolve_cursor_sdk_dispatch_ledger_path,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_census import (
    census,
    open_census_connection,
    operator_lanes_from_sessions,
    render_json,
    render_table,
)


class ConductorCensusHandler(BaseHandler):
    """One read of the conductor census. No model call."""

    step_type = "conductor_census_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        del step
        opts = getattr(context, "options", {}) or {}
        open_only = bool(opts.get("open_only"))
        days = opts.get("days")
        if days is not None:
            days = int(days)
        fmt = str(opts.get("format") or "table")
        path = resolve_cursor_sdk_dispatch_ledger_path()
        if not path.is_file():
            err = {"ok": False, "error": f"ledger not found: {path}"}
            return StepOutput(raw=json.dumps(err), json=err, error=err["error"])
        sessions = None
        try:
            from claude_bundles.cdp_registry_store import load_sessions

            sessions = load_sessions()
        except Exception:
            sessions = None
        conn = open_census_connection(path)
        try:
            rows = census(
                conn,
                open_only=open_only,
                days=days,
                operator_lanes=operator_lanes_from_sessions(sessions),
            )
        finally:
            conn.close()
        text = render_json(rows) if fmt == "json" else render_table(rows)
        payload = {"ok": True, "rows": len(rows), "format": fmt, "text": text}
        return StepOutput(raw=text, json=payload)
