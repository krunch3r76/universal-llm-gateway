"""Agent-bus result delivery for async-dispatched pipeline executions.

Two delivery paths gated on ``record.op``:

**Legacy path** (``record.op is None``):
  When a record carries a ``result_delivery`` config, post a compact
  metadata envelope turn to the configured thread at terminal transition.
  Failures are observable but do not mutate tracker state. Implementation:
  ``legacy_path._deliver_legacy_envelope``.

**Bus-mode path** (``record.op == "to_thread"``):
  Stargate posts ``record.result.content`` to ``record.target_thread`` on
  behalf of the dispatched role/model. ``from_agent`` is supplied at
  admission (role for team_dispatch, model identifier for
  frontier_dispatch); ``to_agent`` is resolved from ``record.caller_agent``
  with a thread last-turn-from fallback. Long content (> ~1.5 KB)
  passes ``allow_long_body=true`` so the agent-bus briefing-rule warning
  is suppressed. Content above the bus 8 000-char hard limit fails with
  ``content_exceeds_bus_limit`` (poll ``pipeline(op="result")`` for the
  full text; sidecar-write fallback is a v2 follow-up). Implementation:
  ``on_behalf._post_content_on_behalf``.

  This replaces the previous "observe the model self-posting" contract
  (architectural fix 2026-05-22 —
  ``notes/system/decisions/dispatch-to-thread-delivery-architecture-2026-05-22.md``).
  That contract failed structurally for ``mcp=False`` dispatches (no
  agent_bus tool available) and for tool-budget-exhausted dispatches
  (model ran out of turns before posting), as observed on executions
  ``9d970982`` and ``8c1df5d3``.

Invariants:
- ∀ legacy record with ``result_delivery`` ∧ terminal transition: emit
  exactly one of ``.sent`` or ``.failed``.
- ∀ bus-mode record with non-empty ``result.content``: emit ``.sent`` on
  POST 2xx, ``.failed`` on POST non-2xx or content-too-large.
- ∀ bus-mode record with empty ``result.content``: skip POST and emit
  ``.skipped`` — the record is already ``failed`` by EmptyCompletionError.
- ∀ delivery failure: tracker record mutation is the caller's
  responsibility (see ``async_tracker._run_delivery_with_outcome``).
- ¬retry: one-shot per terminal transition.

Public surface:
- ``deliver_result`` — router entry point (legacy or bus-mode).
- ``DeliveryOutcome`` — return-value dataclass for tracker status demotion.

The ``_build_envelope`` helper is also exposed for test access; it is not
part of the consumer-facing API and may be removed if test coverage shifts
to higher-level assertions.

Package layout (split from monolith 463-SLOC ``async_tracker_delivery.py``
at git ``c47304ed5a8ceeb2168bab373edbf802f00b4815``):

- ``constants`` — body-size limits and HTTP timeout
- ``outcome`` — ``DeliveryOutcome`` dataclass
- ``protocol`` — ``_EventBusProtocol`` Protocol
- ``envelope`` — body and subject composition
- ``agent_bus_http`` — raw HTTP transport (POST /turns, PATCH close, GET thread)
- ``resolution`` — ``_resolve_to_agent`` + UTC-ISO helper
- ``delivery_events`` — ``_emit`` + lazy event-class factories
- ``on_behalf`` — bus-mode (``op="to_thread"``) implementation
- ``legacy_path`` — legacy ``result_delivery`` envelope post + ephemeral close
- ``deliver`` — ``deliver_result`` router

Tests patch the agent-bus HTTP transport via
``async_tracker_delivery.agent_bus_http.make_async_client`` (the sole
import site for ``transport_utils.make_async_client`` in the package).
"""

from __future__ import annotations

from .deliver import deliver_result
from .envelope import _build_envelope
from .outcome import DeliveryOutcome

__all__ = ["DeliveryOutcome", "_build_envelope", "deliver_result"]
