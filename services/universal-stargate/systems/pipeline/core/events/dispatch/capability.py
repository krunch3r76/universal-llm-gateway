"""CapabilityDispatch resolution event factories (G2 observability — thread 1234/1271).

Node-scoped signals (``scope="node"``) emitted at the single frontier
``gen_params`` resolution boundary. Three structurally distinct conditions:

- ``resolved``: ``resolve_dispatch`` produced the per-model max-output +
  reasoning resolution. Carries the pinned ``resolved_event_fields()`` payload
  (G2) so post-hoc audit can verify every dispatch's resolved values without
  re-running the boundary.
- ``knob_rejected``: G9 reject-loudly — one event per ``KnobViolation`` in the
  ``ProtocolError`` envelope (the boundary collects all violations, not
  first-fail). Replaces the adapters' prior silent ``logger.warning`` drop.
- ``catalog_miss``: G13 fail-fast — the dispatch provider/surface could not be
  inferred at all (``CatalogMissError``); never a silent default.

The ``event_name`` payload field carries the pinned cross-stack name
(``capability_dispatch.resolved`` / ``.knob_rejected`` / ``.catalog_miss``)
from ``capability_dispatch.boundary`` — the bus ``signal`` follows the
dot-notation spec (no underscores) while ``event_name`` is the stable name
tests assert.

Consumer: ``core/handlers/frontier_dispatch/gen_params.py``.

Signals: ``pipeline.frontier.dispatch.capability.{resolved,rejected,miss}``.
"""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory


@event_factory
def PipelineFrontierCapabilityResolved(  # noqa: N802
    execution_id: str,
    event_name: str,
    model: str,
    model_entity_id: str,
    provider: str,
    api_surface: str,
    resolved_fields: dict[str, Any],
) -> Event:
    """Emitted once per ``frontier_dispatch_v1`` execution after the boundary resolves.

    Fires from ``gen_params.build_frontier_request`` immediately after
    ``resolve_dispatch`` succeeds, before the ``FrontierRequest`` is built. The
    ``resolved_fields`` payload is ``DispatchResolution.resolved_event_fields()``
    verbatim (G2 pinned): ``max_output_requested``, ``max_output_resolved``,
    ``max_output_decision``, ``max_output_floor``, ``max_output_ceiling``,
    ``reasoning_budget``.

    Payload:
        execution_id: Pipeline execution UUID
        event_name: Pinned cross-stack name (``capability_dispatch.resolved``)
        model: Full admission id (``provider/model``) passed to the boundary
        model_entity_id: Canonical Cortex model entity id for the admitted model
        provider: Effective provider (``anthropic``, ``openai``, ``xai``, ``google``)
        api_surface: Resolved dispatch surface (``anthropic``/``openai_responses``/…)
        resolved_fields: The pinned ``resolved_event_fields()`` payload
    """
    return Event(
        signal="pipeline.frontier.dispatch.capability.resolved",
        payload={
            "execution_id": execution_id,
            "event_name": event_name,
            "model": model,
            "model_entity_id": model_entity_id,
            "provider": provider,
            "api_surface": api_surface,
            **resolved_fields,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierCapabilityKnobRejected(  # noqa: N802
    execution_id: str,
    event_name: str,
    model: str,
    model_entity_id: str,
    provider: str,
    knob: str,
    reject_code: str,
    reason: str,
) -> Event:
    """Emitted once per rejected knob in the G9 ``ProtocolError`` envelope.

    Precedes the terminal ``pipeline_execution_failed`` carrying the
    ``capability_knob_rejected`` error code. The boundary collects ALL
    violations before raising, so one of these events fires per
    ``KnobViolation`` — the full rejected set is observable, not just
    first-fail.

    Payload:
        execution_id: Pipeline execution UUID
        event_name: Pinned cross-stack name (``capability_dispatch.knob_rejected``)
        model: Full admission id passed to the boundary
        model_entity_id: Canonical Cortex model entity id for the admitted model
        provider: Effective provider
        knob: The rejected knob (e.g. ``reasoning.effort``, ``max_output``)
        reject_code: Structured reject reason (``unsupported_by_model``, …)
        reason: Human-readable explanation of the violation
    """
    return Event(
        signal="pipeline.frontier.dispatch.capability.rejected",
        payload={
            "execution_id": execution_id,
            "event_name": event_name,
            "model": model,
            "model_entity_id": model_entity_id,
            "provider": provider,
            "knob": knob,
            "reject_code": reject_code,
            "reason": reason,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierCapabilityCatalogMiss(  # noqa: N802
    execution_id: str,
    event_name: str,
    model: str,
    model_entity_id: str,
    miss_key: str,
    miss_reason: str,
) -> Event:
    """Emitted when ``resolve_dispatch`` cannot infer the provider/surface (G13).

    Precedes the terminal ``pipeline_execution_failed`` carrying the
    ``capability_catalog_miss`` error code. A within-surface fail-closed
    ceiling (e.g. unknown-claude → 8192) is NOT a catalog-miss and does not
    emit this event — only a provider-uninferable model does.

    Payload:
        execution_id: Pipeline execution UUID
        event_name: Pinned cross-stack name (``capability_dispatch.catalog_miss``)
        model: Full admission id passed to the boundary
        model_entity_id: Canonical Cortex model entity id for the admitted model
        miss_key: The model id the registry could not resolve
        miss_reason: Why the provider/surface could not be inferred
    """
    return Event(
        signal="pipeline.frontier.dispatch.capability.miss",
        payload={
            "execution_id": execution_id,
            "event_name": event_name,
            "model": model,
            "model_entity_id": model_entity_id,
            "miss_key": miss_key,
            "miss_reason": miss_reason,
        },
        scope="node",
    )
