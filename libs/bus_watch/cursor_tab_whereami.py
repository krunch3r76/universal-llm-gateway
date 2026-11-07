"""Resolve where a Cursor Glass tab runs for endpoint subscription and whereami."""

from __future__ import annotations

import os
from typing import Any

from cursor_bridge.cursor_window_config import (
    normalize_gui_host,
    subscription_display_for_gui_host,
)

from bus_watch.cursor_vscdb import (
    TRANSCRIPT_UUID_RE,
    probe_vscdb_readability,
    resolve_gui_host_from_vscdb,
)
from bus_watch.cursor_xvfb_subscription import gui_host_from_process_env

_BASIS_UNRESOLVED = "unresolved"


def explicit_policy_gui_host(root_id: str | None) -> str | None:
    """Return policy.gui_host only when the liaison tick file sets it explicitly."""
    if not root_id:
        return None
    from bus_watch.fable_lock import WATCH_DIR
    from bus_watch.liaison_digest import effective_policy
    from bus_watch.state import read_state

    tick = WATCH_DIR / f"liaison-{str(root_id).strip()}.tick.json"
    if not tick.is_file():
        return None
    state = read_state(tick)
    raw = effective_policy(state).get("gui_host")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    return normalize_gui_host(text)


def _vscdb_diagnostics() -> dict[str, Any]:
    out = probe_vscdb_readability()
    return {
        "vscdbs_checked": out["vscdbs_checked"],
        "readable_count": out["readable_count"],
    }


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
    vscdb_limitation: str | None = None
    diagnostics = _vscdb_diagnostics()

    if use_process_env:
        env_host = gui_host_from_process_env()
        if env_host:
            gui_host = env_host
            basis = "env"

    if gui_host is None and tid and TRANSCRIPT_UUID_RE.match(tid):
        vscdb_host, meta, lim = resolve_gui_host_from_vscdb(tid)
        vscdb_limitation = lim
        if vscdb_host:
            gui_host = normalize_gui_host(vscdb_host)
            basis = "vscdb"

    if gui_host is None and root_id:
        policy_host = explicit_policy_gui_host(root_id)
        if policy_host:
            gui_host = policy_host
            basis = "root_policy"

    layout = str(meta.get("layout") or "").strip() or None
    surface = str(meta.get("surface") or "").strip() or "cursor"
    if basis == "env":
        env_layout = os.environ.get("CURSOR_LAYOUT", "").strip()
        if env_layout:
            layout = env_layout

    if gui_host is None:
        if diagnostics["readable_count"] == 0:
            limitation = "db_unreadable"
        elif vscdb_limitation == "pane_absent":
            limitation = "pane_absent"
        elif vscdb_limitation == "db_unreadable":
            limitation = "db_unreadable"
        else:
            limitation = "pane_absent"
        return {
            "gui_host": None,
            "subscription_display": None,
            "transcript_id": tid or None,
            "surface": surface,
            "ssh_client": None,
            "ssh_client_derivation": "derived",
            "layout": layout,
            "basis": _BASIS_UNRESOLVED,
            "limitation": limitation,
            **diagnostics,
        }

    subscription_display = subscription_display_for_gui_host(gui_host)
    if layout is None and basis == "vscdb":
        layout = "glass"

    return {
        "gui_host": gui_host,
        "subscription_display": subscription_display,
        "transcript_id": tid or None,
        "surface": surface,
        "ssh_client": gui_host == "orion-node",
        "ssh_client_derivation": "derived",
        "layout": layout,
        "basis": basis,
        **diagnostics,
    }


__all__ = ["explicit_policy_gui_host", "resolve_cursor_tab_whereami"]
