"""Resolve conductor summon_mode from explicit override, caller, and summon text."""

from __future__ import annotations

_VALID_SUMMON_MODES = frozenset({"attended", "confer_and_finish"})

_ATTENDED_CALLERS = frozenset({"cursor", "cursor-ide"})

_CONFER_MARKERS = (
    "confer-and-finish",
    "confer and finish",
    "run with it",
    "confer with the others",
    "don't come back",
    "do not come back",
    "dont come back",
)


def resolve_summon_mode(
    *,
    explicit: str | None = None,
    caller_agent: str | None = None,
    summon_text: str | None = None,
) -> str:
    """Pick attended vs confer_and_finish for conductor spawn."""
    if explicit is not None:
        mode = explicit.strip().lower().replace("-", "_")
        if mode not in _VALID_SUMMON_MODES:
            msg = f"invalid summon_mode: {explicit!r}"
            raise ValueError(msg)
        return mode

    text_lower = (summon_text or "").lower()
    if any(marker in text_lower for marker in _CONFER_MARKERS):
        return "confer_and_finish"

    caller = (caller_agent or "").strip().lower()
    if caller in _ATTENDED_CALLERS:
        return "attended"

    return "confer_and_finish"
