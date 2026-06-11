"""Frontier-dispatch remote-MCP event factories (enablement + structural failures).

Node-scoped signals (``scope="node"``) emitted at the adapter-level
remote-MCP boundary. Three structurally distinct conditions:

- ``enabled``: remote-MCP descriptor successfully attached; client-side
  tool loop is disabled for this execution (``mcp_tool_loop=False``).
- ``misconfigured``: env resolution failed at build time (missing
  ``MCP_PUBLIC_URL`` or ``MCP_AUTH_TOKEN`` in the Stargate container env).
- ``unsupported``: caller asked for remote-MCP but the resolved provider's
  capability matrix cannot fulfil the request; admission rejects.

The ``misconfigured`` and ``unsupported`` branches both precede a terminal
``pipeline_execution_failed`` and include ``model_entity_id`` for post-hoc
correlation when ``.started`` is absent (these branches fire during admission,
before the started event would be emitted).

Consumers: ``core/handlers/frontier_dispatch.py`` (Enabled, Misconfigured),
``core/handlers/frontier_dispatch/admission_checks.py`` (Unsupported).

Signals: ``pipeline.frontier.dispatch.remotemcp.{enabled,misconfigured,unsupported}``.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def PipelineFrontierDispatchRemoteMcpEnabled(  # noqa: N802
    execution_id: str,
    agent: str | None,
    model: str,
    model_entity_id: str,
    provider: str,
) -> Event:
    """Emitted once per ``frontier_dispatch_v1`` execution with ``remote_mcp=True``.

    Fires before the native call, immediately after the adapter decides to
    attach the provider-native MCP descriptor. Implies client-side tool loop
    is disabled for this execution (``mcp_tool_loop=False``).

    Payload:
        execution_id: Pipeline execution UUID
        agent: Persona identity if set, else ``None`` for persona-free
        model: Raw model string as supplied by the caller
        model_entity_id: Canonical Cortex model entity id for the admitted model
        provider: Effective provider (``anthropic``, ``openai``, ``xai``)
    """
    return Event(
        signal="pipeline.frontier.dispatch.remotemcp.enabled",
        payload={
            "execution_id": execution_id,
            "agent": agent,
            "model": model,
            "model_entity_id": model_entity_id,
            "provider": provider,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchRemoteMcpMisconfigured(  # noqa: N802
    execution_id: str,
    agent: str | None,
    model: str,
    model_entity_id: str,
    reason: str,
) -> Event:
    """Emitted when remote-MCP env resolution fails at build time.

    Precedes the terminal ``pipeline_execution_failed`` — the caller can
    distinguish a structural misconfiguration (missing ``MCP_PUBLIC_URL`` or
    ``MCP_AUTH_TOKEN`` in the Stargate container env) from an upstream
    provider or tool-loop error by looking for this signal first.

    ``model_entity_id`` is included so post-hoc correlators can recover
    the canonical Cortex ``model:<slug>`` directly from this event when
    ``.started`` is absent — the misconfigured branch can race ahead of
    ``.started`` (env resolution failing during admission), leaving
    ``execution_id`` without an outcome event to join against.

    Payload:
        execution_id: Pipeline execution UUID
        agent: Persona identity if set, else ``None``
        model: Raw model string as supplied by the caller
        model_entity_id: Canonical Cortex model entity id for the admitted model
        reason: Human-readable ``RuntimeError`` message from ``resolve_mcp_env``
    """
    return Event(
        signal="pipeline.frontier.dispatch.remotemcp.misconfigured",
        payload={
            "execution_id": execution_id,
            "agent": agent,
            "model": model,
            "model_entity_id": model_entity_id,
            "reason": reason,
        },
        scope="node",
    )


@event_factory
def PipelineFrontierDispatchRemoteMcpUnsupported(  # noqa: N802
    execution_id: str,
    agent: str | None,
    model: str,
    model_entity_id: str,
    provider: str,
    requested: bool,
    reason: str,
) -> Event:
    """Emitted when the step handler rejects a ``remote_mcp`` request that
    conflicts with the resolved provider's capability matrix.

    Precedes the terminal ``pipeline_execution_failed`` carrying error
    ``code=remote_mcp_unsupported``. Distinguishes a structural
    provider/capability mismatch (caller asked for a mode the provider
    cannot fulfil) from ``remotemcp.misconfigured`` (env resolution failure)
    and from upstream provider errors.

    ``model_entity_id`` is included so post-hoc correlators can recover
    the canonical Cortex ``model:<slug>`` directly from this event when
    ``.started`` is absent — the unsupported branch fires during admission
    when the handler rejects an incompatible ``remote_mcp`` request,
    leaving ``execution_id`` without an outcome event to join against.
    Mirrors the recovery shape used on
    ``pipeline.frontier.dispatch.remotemcp.misconfigured``.

    Payload:
        execution_id: Pipeline execution UUID
        agent: Persona identity if set, else ``None``
        model: Raw model string as supplied by the caller
        model_entity_id: Canonical Cortex model entity id for the admitted model
        provider: Effective provider (``anthropic``, ``openai``, ``xai``, ``google``)
        requested: Value the caller asked for (True or False)
        reason: Human-readable explanation of the capability violation
    """
    return Event(
        signal="pipeline.frontier.dispatch.remotemcp.unsupported",
        payload={
            "execution_id": execution_id,
            "agent": agent,
            "model": model,
            "model_entity_id": model_entity_id,
            "provider": provider,
            "requested": requested,
            "reason": reason,
        },
        scope="node",
    )
