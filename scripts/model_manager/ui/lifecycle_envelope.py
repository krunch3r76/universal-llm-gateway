"""Lifecycle message → manage API envelope mapping."""

from __future__ import annotations


def _start_failed(msg: str) -> bool:
    lower = msg.lower()
    return " failed" in lower or "configuration error" in lower


def start_envelope(msg: str) -> dict[str, str]:
    """Map a start lifecycle message to manage API status + verbatim message."""
    status = "error" if _start_failed(msg) else "ok"
    return {"status": status, "message": msg}
