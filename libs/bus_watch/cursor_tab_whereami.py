"""Resolve where a Cursor Glass tab runs for endpoint subscription and whereami.

The agent-bus and MCP processes often lack the tab process environment. Resolution
for a remote seat keys off ``transcript_id`` in ``state.vscdb`` (and optional
liaison ``root_policy``), not the server process env. Callers that run inside the
tab may pass ``use_process_env=True`` to prefer local env signals first.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from cursor_bridge.cursor_window_config import subscription_display_for_gui_host

from bus_watch.cursor_xvfb_subscription import (
    _UUID_RE,
    _gui_host_from_process_env,
    _readable_vscdb,
    _vscdb_has_pane_key,
)

_BASIS_UNRESOLVED = "unresolved"
_VSCDB_CANDIDATES: tuple[Path, ...] = (
    Path.home() / ".gateway/cursor-io/User/globalStorage/state.vscdb",
    Path("/home/io/.gateway/cursor-io/User/globalStorage/state.vscdb"),
    Path("/home/io/.gateway/cursor-orion/User/globalStorage/state.vscdb"),
    Path("/home/mcp/.gateway/cursor-io/User/globalStorage/state.vscdb"),
)


def _all_readable_vscdbs() -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for path in _VSCDB_CANDIDATES:
        if path.is_file() and path not in seen:
            seen.add(path)
            out.append(path)
    primary = _readable_vscdb()
    if primary is not None and primary not in seen:
        out.insert(0, primary)
    return out


def _gui_host_for_vscdb_path(db_path: Path) -> str:
    text = str(db_path).lower()
    if "orion" in text:
        return "orion-node"
    if "cursor-io" in text:
        return ":8"
    return ":8"


def _vscdb_pane_metadata(db_path: Path, transcript_id: str) -> dict[str, Any]:
    key = f"glass.agentConversationPanes.{transcript_id}"
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT value FROM ItemTable WHERE key = ? LIMIT 1", (key,)
            ).fetchone()
    except sqlite3.Error:
        return {}
    if row is None or row[0] is None:
        return {}
    raw = row[0]
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {"layout": "glass"}
        try:
            blob = json.loads(text)
        except json.JSONDecodeError:
            return {"layout": "glass"}
        if isinstance(blob, dict):
            meta: dict[str, Any] = {"layout": "glass"}
            layout = blob.get("layout") or blob.get("CURSOR_LAYOUT")
            if layout:
                meta["layout"] = str(layout).strip()
            surface = blob.get("surface")
            if surface:
                meta["surface"] = str(surface).strip()
            return meta
        return {"layout": "glass"}
    return {"layout": "glass"}


def _resolve_from_vscdb(transcript_id: str) -> tuple[str | None, dict[str, Any]]:
    for db_path in _all_readable_vscdbs():
        try:
            if not _vscdb_has_pane_key(db_path, transcript_id):
                continue
        except sqlite3.Error:
            continue
        host = _gui_host_for_vscdb_path(db_path)
        meta = _vscdb_pane_metadata(db_path, transcript_id)
        return host, meta
    return None, {}


def resolve_cursor_tab_whereami(
    *,
    transcript_id: str | None = None,
    root_id: str | None = None,
    use_process_env: bool = False,
) -> dict[str, Any]:
    """Return gui host, subscription display, and provenance for one Cursor tab."""
    tid = str(transcript_id or "").strip()
    basis = _BASIS_UNRESOLVED
    gui_host: str | None = None
    meta: dict[str, Any] = {}

    if use_process_env:
        env_host = _gui_host_from_process_env()
        if env_host:
            gui_host = env_host
            basis = "env"

    if gui_host is None and tid and _UUID_RE.match(tid):
        vscdb_host, meta = _resolve_from_vscdb(tid)
        if vscdb_host:
            gui_host = vscdb_host
            basis = "vscdb"

    if gui_host is None and root_id:
        from bus_watch.ide_hop import policy_gui_host

        gui_host = policy_gui_host(str(root_id).strip())
        basis = "root_policy"

    layout = str(meta.get("layout") or "").strip() or None
    surface = str(meta.get("surface") or "").strip() or "cursor"
    if basis == "env":
        env_layout = os.environ.get("CURSOR_LAYOUT", "").strip()
        if env_layout:
            layout = env_layout
        if os.environ.get("CURSOR_CONVERSATION_ID", "").strip() == tid:
            surface = "cursor"

    if gui_host is None:
        return {
            "gui_host": None,
            "subscription_display": None,
            "transcript_id": tid or None,
            "surface": surface,
            "ssh_client": None,
            "layout": layout,
            "basis": _BASIS_UNRESOLVED,
            "limitation": (
                "No vscdb pane for transcript_id and no root_policy gui_host; "
                "MCP/agent-bus do not see the tab process environment."
            ),
        }

    subscription_display = subscription_display_for_gui_host(gui_host)
    ssh_client = gui_host == "orion-node"
    if layout is None and basis in {"vscdb", "env"}:
        layout = "glass" if basis == "vscdb" else layout

    return {
        "gui_host": gui_host,
        "subscription_display": subscription_display,
        "transcript_id": tid or None,
        "surface": surface,
        "ssh_client": ssh_client,
        "layout": layout,
        "basis": basis,
    }


__all__ = ["resolve_cursor_tab_whereami"]
