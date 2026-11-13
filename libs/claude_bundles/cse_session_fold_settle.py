"""Settled-input guard for the CSR transition fold.

``fold_pending_transitions`` is a pure function of ``session_transitions.jsonl``
and ``sessions.json``. Most log records fold to a no-op, so they are never
recorded as applied and every call re-parsed and re-folded the whole log. The
boot lane plan asks for wake debt once per registry row, so Jupiter's 4.4k-record
log cost ~15M record folds (~45 s of one CPU) on every cdp-ask start.

A pass that changes nothing is repeatable until either input changes, so the
fold skips when both file signatures match the last settled pass.
"""

from __future__ import annotations

import os
from pathlib import Path

from claude_bundles import cdp_registry_store as store

FileSignature = tuple[str, int, int, int] | tuple[str, None]
InputsSignature = tuple[FileSignature, FileSignature]

_settled: InputsSignature | None = None


def _file_signature(path: Path) -> FileSignature:
    """Identify one file version: path, inode, size and mtime (replace changes inode)."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return (str(path), None)
    return (str(path), st.st_ino, st.st_size, st.st_mtime_ns)


def inputs_signature() -> InputsSignature:
    """Signature of both fold inputs, read through the store's current paths."""
    return (
        _file_signature(store.SESSION_TRANSITIONS_JSONL),
        _file_signature(store.SESSIONS_JSON),
    )


def is_settled(signature: InputsSignature) -> bool:
    """True when the last fold pass over exactly these inputs applied nothing."""
    return signature == _settled


def mark_settled(signature: InputsSignature) -> None:
    """Record a no-change pass.

    Callers pass the signature captured *before* reading, so a line appended by
    another process mid-pass changes the next signature and is folded then.
    """
    global _settled
    _settled = signature


def clear_settled() -> None:
    """Forget the settled pass (a pass that wrote ``sessions.json`` is not settled)."""
    global _settled
    _settled = None
