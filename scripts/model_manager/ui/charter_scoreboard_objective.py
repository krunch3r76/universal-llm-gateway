"""Parse charter scoreboard ``## Original objective`` for monitor + tick emit."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

_OBJECTIVE_HEADING = re.compile(
    r"^##\s+Original objective(?:\s+\([^)]+\))?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_NEXT_SECTION = re.compile(r"^##\s+", re.MULTILINE)


def parse_original_objective(markdown: str) -> str | None:
    """Return the first paragraph under ``## Original objective`` if present."""
    match = _OBJECTIVE_HEADING.search(markdown)
    if not match:
        return None
    tail = markdown[match.end() :]
    section_end = _NEXT_SECTION.search(tail)
    body = tail[: section_end.start()] if section_end else tail
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines:
        return None
    text = " ".join(lines)
    return text if text else None


def _cortex_files_root() -> Path:
    raw = os.environ.get("CORTEX_FILES_ROOT", "").strip()
    if raw:
        return Path(raw).resolve()
    host = Path("/mnt/torus/mcp-data/files")
    if host.is_dir():
        return host.resolve()
    return (Path.home() / "mcp-data" / "files").resolve()


def path_from_scoreboard_uri(uri: str) -> Path | None:
    """Map ``cortex://…`` scoreboard URI to an on-disk path under files root."""
    text = (uri or "").strip()
    if not text.startswith("cortex://"):
        return None
    rel = text.removeprefix("cortex://").lstrip("/")
    if not rel or ".." in Path(rel).parts:
        return None
    return _cortex_files_root() / rel


def scoreboard_path_for_root(root_id: str) -> Path:
    """Conventional on-disk path for ``{root_id}-charter-scoreboard.md``."""
    rel = f"notes/system/threads/{root_id}-charter-scoreboard.md"
    return _cortex_files_root() / rel


def read_objective_from_uri(uri: str | None) -> str | None:
    """Read + parse objective from a cortex scoreboard URI; silent on miss."""
    path = path_from_scoreboard_uri(uri or "")
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_original_objective(text)


def read_objective_for_root(
    root_id: str,
    *,
    scoreboard_uri: str | None = None,
) -> str | None:
    """Read scoreboard objective; prefer ledger URI, else conventional path."""
    if scoreboard_uri:
        objective = read_objective_from_uri(scoreboard_uri)
        if objective:
            return objective
    path = scoreboard_path_for_root(root_id)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_original_objective(text)


@dataclass(frozen=True)
class CharterTipMeta:
    """Identity tip fields the board grafts at cold-start."""

    root_id: str
    scoreboard_uri: str | None = None
    pickup_gid: str | None = None
    objective: str | None = None
    bus_slug: str | None = None
    bus_summary: str | None = None

    @property
    def has_identity(self) -> bool:
        return bool(
            self.objective
            or self.pickup_gid
            or self.bus_summary
            or self.bus_slug
        )


def _agent_bus_db_path() -> Path | None:
    raw = os.environ.get("AGENT_BUS_DB_PATH", "").strip()
    if raw:
        path = Path(raw).expanduser()
        return path if path.is_file() else None
    host = Path.home() / ".agent-bus" / "messages.db"
    if host.is_file():
        return host
    container = Path("/data/messages.db")
    return container if container.is_file() else None


def _read_bus_thread_meta(root_id: str) -> tuple[str | None, str | None]:
    """Return ``(slug, summary)`` from agent-bus sqlite (read-only; no store import)."""
    import sqlite3

    path = _agent_bus_db_path()
    if path is None:
        return None, None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None, None
    try:
        row = conn.execute(
            "SELECT slug, summary FROM threads WHERE id = ?",
            (root_id,),
        ).fetchone()
    except sqlite3.Error:
        return None, None
    finally:
        conn.close()
    if row is None:
        return None, None
    slug = (row[0] or "").strip() or None
    summary = (row[1] or "").strip() or None
    return slug, summary


def _read_ledger_tip(root_id: str) -> tuple[str | None, str | None]:
    """Return ``(scoreboard_uri, pickup_gid)`` via read-only sqlite (no root_ledger import).

    Importing ``charter_runner.root_ledger`` pulls package init / cortex noise onto
    the curses board stderr — keep this path sqlite-only.
    """
    import sqlite3

    try:
        from libs.charter_runner_store.db import default_ledger_path

        path = default_ledger_path()
    except Exception:  # noqa: BLE001 — tip graft is best-effort
        return None, None
    if not path.is_file():
        return None, None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None, None
    try:
        row = conn.execute(
            "SELECT scoreboard_uri, pickup_gid FROM root_ledger WHERE root_id = ?",
            (root_id,),
        ).fetchone()
    except sqlite3.Error:
        return None, None
    finally:
        conn.close()
    if row is None:
        return None, None
    uri = (row[0] or "").strip() or None
    gid = (row[1] or "").strip() or None
    return uri, gid


def tip_meta_for_root(root_id: str) -> CharterTipMeta:
    """Load ledger tip + bus slug/summary and resolve scoreboard objective."""
    scoreboard_uri, pickup_gid = _read_ledger_tip(root_id)
    bus_slug, bus_summary = _read_bus_thread_meta(root_id)
    objective = read_objective_for_root(root_id, scoreboard_uri=scoreboard_uri)
    return CharterTipMeta(
        root_id=root_id,
        scoreboard_uri=scoreboard_uri,
        pickup_gid=pickup_gid,
        objective=objective,
        bus_slug=bus_slug,
        bus_summary=bus_summary,
    )


def objective_meta_event(
    root_id: str,
    objective: str | None = None,
    *,
    pickup_gid: str | None = None,
    scoreboard_uri: str | None = None,
    bus_slug: str | None = None,
    bus_summary: str | None = None,
    ts_unix_ms: int | None = None,
) -> dict[str, object]:
    """Payload for ``monitor.meta.charter_objective`` graft events."""
    payload: dict[str, object] = {
        "root": root_id,
        "ts_unix_ms": ts_unix_ms or int(time.time() * 1000),
    }
    if objective:
        payload["objective"] = objective
    if pickup_gid:
        payload["pickup_gid"] = pickup_gid
    if scoreboard_uri:
        payload["scoreboard_uri"] = scoreboard_uri
    if bus_slug:
        payload["bus_slug"] = bus_slug
    if bus_summary:
        payload["bus_summary"] = bus_summary
    return payload
