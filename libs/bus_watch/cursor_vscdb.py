"""Glass ``state.vscdb`` discovery with explicit (path, gui_host) bindings."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

TRANSCRIPT_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)

_DEFAULT_VSCDB_BINDINGS: tuple[tuple[str, str], ...] = (
    (
        str(Path.home() / ".gateway/cursor-io/User/globalStorage/state.vscdb"),
        ":8",
    ),
    ("/home/io/.gateway/cursor-io/User/globalStorage/state.vscdb", ":8"),
    ("/home/io/.gateway/cursor-orion/User/globalStorage/state.vscdb", "orion-node"),
    ("/home/mcp/.gateway/cursor-io/User/globalStorage/state.vscdb", ":8"),
)


def parse_vscdb_path_env(raw: str | None = None) -> list[tuple[Path, str]]:
    """Parse ``CURSOR_VSCDB_PATHS`` as ``path|host`` entries separated by ``;``."""
    text = str(raw if raw is not None else os.environ.get("CURSOR_VSCDB_PATHS", ""))
    text = text.strip()
    if not text:
        return []
    out: list[tuple[Path, str]] = []
    for chunk in text.split(";"):
        piece = chunk.strip()
        if not piece or "|" not in piece:
            continue
        path_part, host_part = piece.rsplit("|", 1)
        path_part = path_part.strip()
        host_part = host_part.strip()
        if path_part and host_part:
            out.append((Path(path_part), host_part))
    return out


def vscdb_candidate_bindings() -> list[tuple[Path, str]]:
    """Configured and default vscdb paths with explicit gui_host labels."""
    seen: set[str] = set()
    out: list[tuple[Path, str]] = []
    for path, host in parse_vscdb_path_env():
        key = str(path)
        if key not in seen:
            seen.add(key)
            out.append((path, host))
    for path_s, host in _DEFAULT_VSCDB_BINDINGS:
        path = Path(path_s)
        key = str(path)
        if key not in seen:
            seen.add(key)
            out.append((path, host))
    return out


def vscdb_has_pane_key(db_path: Path, transcript_id: str) -> bool:
    """Return whether the Glass pane key exists for one transcript UUID."""
    key = f"glass.agentConversationPanes.{transcript_id}"
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT 1 FROM ItemTable WHERE key = ? LIMIT 1", (key,)
        ).fetchone()
    return row is not None


def vscdb_has_any_glass_pane(db_path: Path) -> bool:
    """Return whether any Glass agent conversation pane row exists."""
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT 1 FROM ItemTable WHERE key LIKE 'glass.agentConversationPanes.%' LIMIT 1"
        ).fetchone()
    return row is not None


def vscdb_pane_metadata(db_path: Path, transcript_id: str) -> dict[str, Any]:
    """Extract layout/surface hints from a pane blob when present."""
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
    if not isinstance(raw, bytes):
        return {}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"layout": "glass"}
    try:
        blob = json.loads(text)
    except json.JSONDecodeError:
        return {"layout": "glass"}
    if not isinstance(blob, dict):
        return {"layout": "glass"}
    meta: dict[str, Any] = {"layout": "glass"}
    layout = blob.get("layout") or blob.get("CURSOR_LAYOUT")
    if layout:
        meta["layout"] = str(layout).strip()
    surface = blob.get("surface")
    if surface:
        meta["surface"] = str(surface).strip()
    return meta


def probe_vscdb_readability() -> dict[str, Any]:
    """Report which configured vscdb paths exist and are sqlite-readable."""
    bindings = vscdb_candidate_bindings()
    checked = [str(path) for path, _ in bindings]
    readable: list[str] = []
    for path, _host in bindings:
        if not path.is_file():
            continue
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
                conn.execute("SELECT 1").fetchone()
            readable.append(str(path))
        except (OSError, sqlite3.Error):
            continue
    return {
        "vscdbs_checked": checked,
        "readable_count": len(readable),
        "readable_paths": readable,
    }


def resolve_gui_host_from_vscdb(transcript_id: str) -> tuple[str | None, dict[str, Any], str | None]:
    """Resolve gui_host from vscdb bindings plus the master other-glass heuristic."""
    meta: dict[str, Any] = {}
    bindings = vscdb_candidate_bindings()
    readable = 0
    for path, host in bindings:
        if not path.is_file():
            continue
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
                conn.execute("SELECT 1").fetchone()
            readable += 1
        except (OSError, sqlite3.Error):
            continue
        try:
            if vscdb_has_pane_key(path, transcript_id):
                meta = vscdb_pane_metadata(path, transcript_id)
                return host, meta, None
        except sqlite3.Error:
            continue
    for path, _host in bindings:
        if not path.is_file():
            continue
        try:
            if vscdb_has_any_glass_pane(path):
                return "orion-node", {}, None
        except sqlite3.Error:
            continue
    if readable == 0:
        return None, {}, "db_unreadable"
    return None, {}, "pane_absent"


__all__ = [
    "TRANSCRIPT_UUID_RE",
    "parse_vscdb_path_env",
    "probe_vscdb_readability",
    "resolve_gui_host_from_vscdb",
    "vscdb_candidate_bindings",
    "vscdb_has_any_glass_pane",
    "vscdb_has_pane_key",
    "vscdb_pane_metadata",
]
