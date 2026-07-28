"""Parse charter scoreboard ``## Original objective`` for monitor + tick emit."""

from __future__ import annotations

import os
import re
import time
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


def scoreboard_path_for_root(root_id: str) -> Path:
    """Conventional on-disk path for ``{root_id}-charter-scoreboard.md``."""
    rel = f"notes/system/threads/{root_id}-charter-scoreboard.md"
    return _cortex_files_root() / rel


def read_objective_for_root(root_id: str) -> str | None:
    """Read scoreboard from disk and parse objective; silent on missing file."""
    path = scoreboard_path_for_root(root_id)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_original_objective(text)


def objective_meta_event(
    root_id: str,
    objective: str,
    *,
    ts_unix_ms: int | None = None,
) -> dict[str, object]:
    """Payload dict for ``monitor.meta.charter_objective`` graft events."""
    return {
        "root": root_id,
        "objective": objective,
        "ts_unix_ms": ts_unix_ms or int(time.time() * 1000),
    }
