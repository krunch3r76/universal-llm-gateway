"""Scalars from js_spawn create-agent failure envelopes for spawn results and events."""

from __future__ import annotations

from typing import Any


def envelope_from_js_spawn_result(raw: object) -> dict[str, Any] | None:
    """Return the app failure envelope dict from a js_spawn eval result, if present."""
    if not isinstance(raw, dict):
        return None
    env = raw.get("envelope")
    if isinstance(env, dict):
        return dict(env)
    return None


def spawn_refused_event_scalars(result: dict[str, Any]) -> dict[str, Any]:
    """Flat payload fields for ``interlocutor.spawn.refused`` besides target/code/reason."""
    env = result.get("envelope")
    if not isinstance(env, dict):
        env = envelope_from_js_spawn_result(result.get("detail"))
    if not isinstance(env, dict):
        env = {}
    return {
        "envelope_code": _scalar_or_none(env.get("code")),
        "transport_kind": _scalar_or_none(env.get("transportKind")),
        "command": _scalar_or_none(env.get("command")),
        "needs_operator": bool(result.get("needs_operator")),
    }


def _scalar_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "envelope_from_js_spawn_result",
    "spawn_refused_event_scalars",
]
