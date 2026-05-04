"""Frontier-generate endpoint orchestration and persona admission checks."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from agent_seat import AgentMeta, assemble_system_prompt, hydrate_agent
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

from .events import (
    FrontierEndpointPersonaResolved,
    FrontierEndpointRejected,
    FrontierEndpointRequested,
)

_PIPELINE_ID = "frontier-dispatch"
EventPublisher = Callable[[Any], None]

# Models that only support the Chat Completions API and are unavailable on the
# OpenAI Responses API path used by frontier_dispatch. Callers must use
# llm_generate (which routes through /v1/chat/completions) for these models.
# ∀ new Chat-Completions-only OpenAI models: add to this set AND update
# llm_generate docstring in services/mcp-server/tools/llm.py.
_CHAT_COMPLETIONS_ONLY_MODELS: frozenset[str] = frozenset(
    {
        "openai/gpt-5-search-api",
    }
)


def _is_chat_completions_only(model: str) -> bool:
    """True iff model is known to be unavailable on the OpenAI Responses API."""
    if model in _CHAT_COMPLETIONS_ONLY_MODELS:
        return True
    # Defense-in-depth: catch versioned/future *-search-api variants before
    # they reach the Responses API endpoint and fail with an opaque 400/500.
    return model.startswith("openai/") and model.endswith("-search-api")


@dataclass(slots=True)
class FrontierGenerateRequest:
    messages: list[dict[str, Any]]
    model: str | None = None
    agent: str | None = None
    system: str = ""
    mcp: bool | None = None
    reasoning_effort: str | None = None
    generation_options: dict[str, Any] | None = None
    max_tool_turns: int | None = None
    transcript_id: str | None = None
    remote_mcp: bool | None = None
    caller_agent: str | None = None
    timeout_seconds: int | None = None
    # dispatch-surface-split Phase 1: explicit op discrimination
    output_contract: Literal["inline", "thread"] = "inline"
    target_thread: str | None = None
    op: Literal["generate", "to_thread"] | None = None


@dataclass(slots=True)
class FrontierEndpointError(Exception):
    request_id: str
    field: str
    reason: str
    status_code: int = 400

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {"code": "persona_violation", "message": self.reason},
            "field": self.field,
            "request_id": self.request_id,
        }


async def _verify_thread_writable(
    thread: str,
    *,
    request_id: str,
    url: str = DEFAULT_AGENT_BUS_URL,
    auth_token: str = "",
) -> None:
    """Raise FrontierEndpointError when the target thread is missing or closed.

    Called by ``build_dispatch_body()`` for ``op="to_thread"`` dispatches before
    the request reaches the pipeline tracker.  Fast-fail before admission so
    callers discover thread problems via a 422 rather than a timeout.

    ∀ transport/auth failure: raise with ``status_code=503`` so the route
    returns a 5xx rather than silently admitting an undeliverable dispatch.
    """
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    try:
        async with make_async_client(url, timeout=5.0) as client:
            resp = await client.get(f"/threads/{thread}", headers=headers)
    except httpx.HTTPError as exc:
        raise FrontierEndpointError(
            request_id=request_id,
            field="thread",
            reason=f"Agent-bus unreachable during thread check: {exc}",
            status_code=503,
        )
    if resp.status_code == 404:
        raise FrontierEndpointError(
            request_id=request_id,
            field="thread",
            reason=f"Thread '{thread}' not found.",
            status_code=422,
        )
    if resp.status_code >= 400:
        raise FrontierEndpointError(
            request_id=request_id,
            field="thread",
            reason=f"Thread '{thread}' check failed: HTTP {resp.status_code}.",
            status_code=503,
        )
    data: dict[str, Any] = resp.json()
    status: str = data.get("status", "")
    if status == "closed":
        raise FrontierEndpointError(
            request_id=request_id,
            field="thread",
            reason=f"Thread '{thread}' is closed; cannot deliver to_thread dispatch.",
            status_code=422,
        )


def _emit_rejection(
    *,
    request_id: str,
    agent: str | None,
    field: str,
    reason: str,
    event_publisher: EventPublisher | None,
) -> FrontierEndpointError:
    if event_publisher is not None:
        event_publisher(
            FrontierEndpointRejected(
                request_id=request_id, agent=agent, field=field, reason=reason
            )
        )
    return FrontierEndpointError(request_id=request_id, field=field, reason=reason)


def _enforce_model(
    *,
    request_id: str,
    agent: str | None,
    model: str,
    meta: AgentMeta,
    event_publisher: EventPublisher | None,
) -> None:
    if not meta.allowed_models:
        return
    if model in meta.allowed_models:
        return
    reason = (
        f"model {model!r} not allowed for agent {agent!r}; "
        f"allowed: {sorted(meta.allowed_models)}"
    )
    raise _emit_rejection(
        request_id=request_id,
        agent=agent,
        field="model",
        reason=reason,
        event_publisher=event_publisher,
    )


def _enforce_options(
    *,
    request_id: str,
    agent: str | None,
    opts: dict[str, Any],
    meta: AgentMeta,
    event_publisher: EventPublisher | None,
) -> None:
    if meta.allowed_options is None:
        return
    extra = sorted(set(opts.keys()) - set(meta.allowed_options))
    if not extra:
        return
    reason = (
        f"generation_options keys {extra} not allowed for agent {agent!r}; "
        f"persona allows: {sorted(meta.allowed_options)}"
    )
    raise _emit_rejection(
        request_id=request_id,
        agent=agent,
        field="generation_options",
        reason=reason,
        event_publisher=event_publisher,
    )


async def build_dispatch_body(
    req: FrontierGenerateRequest, event_publisher: EventPublisher | None = None
) -> dict[str, Any]:
    """Apply persona rules and shape dispatch JSON for ``/pipelines/dispatch``."""
    request_id = uuid.uuid4().hex[:12]
    if event_publisher is not None:
        event_publisher(
            FrontierEndpointRequested(
                request_id=request_id,
                agent=req.agent,
                model=req.model,
            )
        )

    meta = AgentMeta()
    system_assembled = req.system or ""

    if req.agent:
        # Soft boot: team_dispatch / frontier_dispatch dispatches use the
        # lightweight profile by default. Drops deadlines + review-queue
        # fetches; keeps a 3-reflection floor. The pipeline-handler hydration
        # in resolve_dispatch_tool_set must mirror this profile to avoid the
        # final dispatched prompt regaining a heavy briefing card.
        bundle = await hydrate_agent(req.agent, profile="light", model=req.model)
        meta = bundle.agent_meta
        if event_publisher is not None:
            event_publisher(
                FrontierEndpointPersonaResolved(
                    request_id=request_id,
                    agent=req.agent,
                    frontier_kind=meta.frontier_kind,
                    default_model=meta.default_model,
                    allowed_models_count=len(meta.allowed_models),
                    allowed_options_count=(
                        len(meta.allowed_options)
                        if meta.allowed_options is not None
                        else None
                    ),
                )
            )
        # Persona injection is driven by agent presence. Persona-free dispatches
        # skip this branch entirely via the outer ``if req.agent`` guard.
        # The team_dispatch surface has no ``mcp`` knob — tool surface is
        # derived from the agent provider in the mcp_enabled computation
        # below (xAI agents → mcp=False; all others → mcp=True).
        system_assembled = assemble_system_prompt(
            req.agent,
            briefing_card_md=bundle.briefing_card_md,
            continuation_md=bundle.continuation_md,
            extra_system=req.system,
            inline_only=bundle.inline_only,
        )

    effective_model = req.model or meta.default_model
    if not effective_model:
        raise FrontierEndpointError(
            request_id=request_id,
            field="model",
            reason="`model` is required when agent has no default_model",
        )

    _enforce_model(
        request_id=request_id,
        agent=req.agent,
        model=effective_model,
        meta=meta,
        event_publisher=event_publisher,
    )
    if _is_chat_completions_only(effective_model):
        raise _emit_rejection(
            request_id=request_id,
            agent=req.agent,
            field="model",
            reason=(
                f"{effective_model!r} is a Chat Completions-only model — "
                "it is unavailable on the OpenAI Responses API that "
                "team_dispatch / frontier_dispatch use. "
                f"Use llm_generate(model={effective_model!r}, messages=...) instead "
                "(note: llm_generate has a narrower surface — no agent, tools, "
                "or transcript_id)."
            ),
            event_publisher=event_publisher,
        )
    generation_options = dict(req.generation_options or {})
    if req.reasoning_effort is not None:
        # Typed param surfaces in generation_options so persona
        # allowed_options enforcement applies uniformly. setdefault so an
        # explicit dict entry wins over the typed convenience arg.
        generation_options.setdefault("reasoning_effort", req.reasoning_effort)
    if "max_tool_turns" in generation_options:
        # max_tool_turns is a dispatch-control param routed at the top level of
        # pipeline_options — placing it inside generation_options has no effect.
        # Hard-reject so the misuse surfaces as a 4xx rather than a silent no-op.
        raise FrontierEndpointError(
            request_id=request_id,
            field="generation_options.max_tool_turns",
            reason=(
                "'max_tool_turns' inside generation_options is not supported — "
                "use the typed top-level parameter instead"
            ),
        )
    _enforce_options(
        request_id=request_id,
        agent=req.agent,
        opts=generation_options,
        meta=meta,
        event_publisher=event_publisher,
    )
    # Tools field retired from the public dispatch surface
    # (todo:retire-tools-param-from-dispatch-mcp-surface). Tool surface is now
    # contract-derived per dispatch surface:
    # - team_dispatch (req.agent is set, no caller mcp knob): xAI agents
    #   (oppie, forge) get mcp=False — multi-agent variants reject client-side
    #   function tools at the API layer; non-multi-agent xAI reasoning models
    #   are inline-substrate by team-seat contract. All other team agents
    #   (orion, bard, api_claude) get mcp=True.
    # - frontier_dispatch (no req.agent): caller's mcp knob is honored;
    #   defaults to False at the wire (one-shot reasoning).
    if req.agent is not None:
        mcp_enabled = not effective_model.startswith("xai/")
    else:
        mcp_enabled = bool(req.mcp) if req.mcp is not None else False

    pipeline_options: dict[str, Any] = {
        "model": effective_model,
        "system": system_assembled,
        "generation_parameters": generation_options,
        "mcp": mcp_enabled,
        "_endpoint_request_id": request_id,
    }
    if req.agent:
        pipeline_options["agent"] = req.agent
    if req.max_tool_turns is not None:
        pipeline_options["max_tool_turns"] = req.max_tool_turns
    if req.remote_mcp is not None:
        pipeline_options["remote_mcp"] = req.remote_mcp

    # Phase 2 admission fast-fail: verify the target thread is open before admitting.
    # This prevents silent undeliverable dispatches — callers learn via 422 rather
    # than a 30-second delivery timeout.  Only performed for to_thread dispatches
    # with a known token; skip if AGENT_BUS_TOKEN is absent (dev/test environments).
    if req.output_contract == "thread" and req.target_thread:
        _agent_bus_token = os.getenv("AGENT_BUS_TOKEN", "")
        if _agent_bus_token:
            await _verify_thread_writable(
                req.target_thread,
                request_id=request_id,
                auth_token=_agent_bus_token,
            )

    body: dict[str, Any] = {
        "model": _PIPELINE_ID,
        "messages": req.messages,
        "pipeline_options": pipeline_options,
        # dispatch-surface-split Phase 1: pass op discrimination through to tracker
        "output_contract": req.output_contract,
    }
    if req.timeout_seconds is not None:
        body["timeout_seconds"] = req.timeout_seconds
    if req.caller_agent:
        body["caller_agent"] = req.caller_agent
    if req.transcript_id:
        # Provenance attribution only: records the caller's session ID in the
        # execution envelope so dispatches can be traced back to their origin.
        # ∀ dispatched agents: this field is never forwarded into pipeline_options
        # or the agent's context — the receiving model never sees it.
        body["caller_transcript_id"] = req.transcript_id
    if req.target_thread is not None:
        body["target_thread"] = req.target_thread
    if req.op is not None:
        body["op"] = req.op
    return body
