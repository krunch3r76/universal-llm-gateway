"""Anomaly detection: short output on persona dispatches.

Persona dispatches that return ``output_tokens < 500`` are almost always a
silent failure (thinking-budget starvation, model confusion, tool-loop
misrouting). The content preview lets the next triage answer "nonsense or just
short?" from events alone.

Pure function — the caller decides how to emit the returned payload.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

SHORT_OUTPUT_TOKEN_THRESHOLD = 500
CONTENT_PREVIEW_CHAR_LIMIT = 500

# Detector gate: only emit short-output observability for persona dispatches.
# The handler derives ``boot_level="team" if agent else "none"`` at dispatch
# time; there is no caller-facing ``boot`` parameter on the public
# ``team_dispatch`` / ``frontier_dispatch`` MCP tools. ``boot_level`` is
# internal observability vocabulary marking the dispatch tier. The ``full``
# alias is preserved for event-store backward read compatibility on historical
# rows.
_GATED_BOOT_LEVELS: frozenset[str] = frozenset({"team", "full"})


@dataclass(slots=True)
class OutputShortPayload:
    """Structured payload for a ``output.short`` anomaly emission.

    Caller maps these fields to its own event factory (Stargate pipeline,
    future HTTP surface, etc.). ``as_dict()`` is a convenience for callers
    that forward payloads as kwargs into ``record(...)``-style emitters.
    """

    boot_level: str
    output_tokens: int
    tool_calls_made: int
    finish_reason: str | None
    block_reason: str | None
    content_preview: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def detect_output_short(
    *,
    boot_level: str,
    output_tokens: int,
    tool_calls_made: int,
    finish_reason: str | None,
    block_reason: str | None,
    content: str | None,
) -> OutputShortPayload | None:
    """Return a payload when the call is a short-output anomaly, else ``None``.

    Gate: ``boot_level`` must be ``team`` or ``full`` AND ``output_tokens``
    must be below ``SHORT_OUTPUT_TOKEN_THRESHOLD``. Persona-free / mcp-only
    dispatches intentionally skip the gate — short output is expected there.
    """
    if boot_level not in _GATED_BOOT_LEVELS:
        return None
    if output_tokens >= SHORT_OUTPUT_TOKEN_THRESHOLD:
        return None
    preview = (content or "")[:CONTENT_PREVIEW_CHAR_LIMIT]
    return OutputShortPayload(
        boot_level=boot_level,
        output_tokens=output_tokens,
        tool_calls_made=tool_calls_made,
        finish_reason=finish_reason,
        block_reason=block_reason,
        content_preview=preview,
    )
