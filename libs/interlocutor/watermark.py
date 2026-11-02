"""Pinned operator-relay phrases (assertion 41171 / interlocutor B2)."""

from __future__ import annotations

OPERATOR_HEADER = "Operator request — Kaywan, relayed on his behalf"
WATERMARK = "Pasted on Kaywan's behalf by Orion — AI-assisted"


def wrap_operator_message(*, text: str, authorization_source: str, scope: str) -> str:
    """Format one operator relay body with header, source, scope, and watermark."""
    body = (text or "").strip()
    src = (authorization_source or "").strip()
    scope_s = (scope or "").strip()
    return (
        f"{OPERATOR_HEADER}\n\nSource: {src}\nScope: {scope_s}\n\n{body}\n\n{WATERMARK}"
    )


def wrap_operator_message_flat(*, text: str, authorization_source: str, scope: str) -> str:
    """Single-line operator relay for flattening adapters (:8)."""
    body = " ".join((text or "").splitlines()).strip()
    src = (authorization_source or "").strip()
    scope_s = (scope or "").strip()
    return (
        f"{OPERATOR_HEADER} — Source: {src} — Scope: {scope_s} — {body} — {WATERMARK}"
    )


__all__ = [
    "OPERATOR_HEADER",
    "WATERMARK",
    "wrap_operator_message",
    "wrap_operator_message_flat",
]
