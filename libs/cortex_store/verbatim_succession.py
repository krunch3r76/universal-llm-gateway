"""Verbatim-layer helpers for succession seal / fill / tape render (R3)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

STRUCTURAL_MARKER = "\n## Session Summary"


def split_verbatim_layer(
    full_md: str,
    *,
    verbatim_bytes: int | None = None,
) -> str:
    """Return the verbatim prefix of a composed transcript file.

    Prefer ``verbatim_bytes`` from ``session_journals`` (R3); fall back to the
    structural marker only when the column is absent (legacy rows).
    """
    if verbatim_bytes is not None and verbatim_bytes >= 0:
        raw = full_md.encode("utf-8")
        if verbatim_bytes <= len(raw):
            return raw[:verbatim_bytes].decode("utf-8")
    idx = full_md.find(STRUCTURAL_MARKER)
    if idx == -1:
        return full_md
    return full_md[:idx]


def verbatim_fingerprint(verbatim: str) -> tuple[str, int]:
    """Return ``(sha256:…, utf-8 byte length)`` for a verbatim layer."""
    raw = verbatim.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return f"sha256:{digest}", len(raw)


def stamp_verbatim_fields(
    conn: Any,
    *,
    session_id: str,
    verbatim: str,
) -> None:
    """Persist R3 verbatim fingerprint columns on ``session_journals``."""
    sha, nbytes = verbatim_fingerprint(verbatim)
    conn.execute(
        "UPDATE session_journals SET verbatim_sha256 = ?, verbatim_bytes = ? "
        "WHERE session_id = ?",
        (sha, nbytes, session_id),
    )


def load_sealed_verbatim_for_session(
    session_id: str,
    *,
    files_root: Path,
) -> tuple[str, str] | None:
    """Load verbatim layer from a succession-sealed journal row, if present."""
    from .db import cortex_conn
    from .session_close_successor_hop import lookup_sealed_journal

    sealed = lookup_sealed_journal(session_id)
    if sealed is None or sealed.closed_by != "succession":
        return None
    conn = cortex_conn()
    try:
        row = conn.execute(
            "SELECT file_path, verbatim_bytes FROM session_journals WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["file_path"]:
        return None
    rel_path = str(row["file_path"])
    path = files_root / rel_path
    if not path.is_file():
        return None
    full = path.read_text(encoding="utf-8")
    verbatim = split_verbatim_layer(
        full, verbatim_bytes=journal_verbatim_bytes(row)
    )
    return verbatim, rel_path


def journal_verbatim_bytes(row: dict[str, Any] | Any) -> int | None:
    """Read ``verbatim_bytes`` from a journal row mapping or sqlite Row."""
    if row is None:
        return None
    try:
        val = row["verbatim_bytes"]
    except (KeyError, TypeError, IndexError):
        return None
    if val is None:
        return None
    return int(val)


__all__ = [
    "STRUCTURAL_MARKER",
    "journal_verbatim_bytes",
    "load_sealed_verbatim_for_session",
    "split_verbatim_layer",
    "stamp_verbatim_fields",
    "verbatim_fingerprint",
]
