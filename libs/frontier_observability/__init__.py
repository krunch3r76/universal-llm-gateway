"""Caller-agnostic observability helpers for frontier dispatch responses.

Shared between:

- Stargate's ``frontier_dispatch_v1`` pipeline handler (primary caller post
  Task-7 Phase 1 — emits ``pipeline.frontier.dispatch.*`` bus events).
- Any future non-MCP caller running ``run_native_tool_loop`` (e.g. an HTTP
  gateway surface) that wants the same anomaly detection for free.

Two helpers live here:

- ``detect_output_short(*, boot_level, output_tokens, ...)`` — returns an
  ``OutputShortPayload`` when ``output_tokens < 500`` on team/full boot.
- ``TerminationShadowDetector`` — Gemini-scoped phrase/position/n-gram/
  token-budget heuristic; returns a ``TerminationShadowPayload`` when a
  thought trace looks like a silent refusal / loop / MAX_TOKENS-on-thought.

Both helpers return a dataclass; emission is the caller's responsibility so
the library stays transport-agnostic (no ``mcp_events`` or
``universal_event_bus`` coupling).
"""

from __future__ import annotations

from .output_short import OutputShortPayload, detect_output_short
from .termination_shadow import (
    TerminationEvidence,
    TerminationShadowDetector,
    TerminationShadowPayload,
)

__all__ = [
    "OutputShortPayload",
    "TerminationEvidence",
    "TerminationShadowDetector",
    "TerminationShadowPayload",
    "detect_output_short",
]
