"""Frontier-generate endpoint orchestration and persona admission checks."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_seat import AgentMeta, assemble_system_prompt, hydrate_agent
from .events import (
    FrontierEndpointPersonaResolved,
    FrontierEndpointRejected,
    FrontierEndpointRequested,
)

_PIPELINE_ID = "frontier-dispatch"
EventPublisher = Callable[[Any], None]

# Models that only support the Chat Completions API and are unavailable on the
# OpenAI Responses API path used by frontier_generate. Callers must use
# llm_generate (which routes through /v1/chat/completions) for these models.
# ∀ new Chat-Completions-only OpenAI models: add to this set AND update
# llm_generate docstring in services/mcp-server/tools/llm.py.
_CHAT_COMPLETIONS_ONLY_MODELS: frozenset[str] = frozenset({
    "openai/gpt-5-search-api",
})


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
    boot: str = "none"
    system: str = ""
    tools: list[str] | None = None
    reasoning_effort: str | None = None
    generation_options: dict[str, Any] | None = None
    max_tool_turns: int | None = None
    transcript_id: str | None = None
    remote_mcp: bool | None = None
    result_delivery: dict[str, Any] | None = None
    caller_agent: str | None = None
    timeout_seconds: int | None = None


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


def _enforce_tools(
    *,
    request_id: str,
    agent: str | None,
    requested: list[str],
    meta: AgentMeta,
    event_publisher: EventPublisher | None,
) -> None:
    if meta.tools is None:
        return
    extra = sorted(set(requested) - set(meta.tools))
    if not extra:
        return
    reason = (
        f"tools {extra} not allowed for agent {agent!r}; "
        f"persona allows: {sorted(meta.tools)}"
    )
    raise _emit_rejection(
        request_id=request_id,
        agent=agent,
        field="tools",
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


def _resolve_effective_tools(
    requested: list[str] | None, meta: AgentMeta
) -> list[str] | None:
    if requested is not None:
        return list(requested)
    if meta.tools is not None:
        return list(meta.tools)
    return None


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
                boot=req.boot,
                has_tools=bool(req.tools),
            )
        )

    meta = AgentMeta()
    system_assembled = req.system or ""

    if req.agent:
        bundle = await hydrate_agent(req.agent, req.transcript_id)
        meta = bundle.agent_meta
        if event_publisher is not None:
            event_publisher(
                FrontierEndpointPersonaResolved(
                    request_id=request_id,
                    agent=req.agent,
                    frontier_kind=meta.frontier_kind,
                    default_model=meta.default_model,
                    allowed_models_count=len(meta.allowed_models),
                    tools_count=(len(meta.tools) if meta.tools is not None else None),
                    allowed_options_count=(
                        len(meta.allowed_options)
                        if meta.allowed_options is not None
                        else None
                    ),
                )
            )
        if req.boot in {"team", "full"}:
            system_assembled = assemble_system_prompt(
                req.agent,
                briefing_card_md=bundle.briefing_card_md,
                continuation_md=bundle.continuation_md,
                extra_system=req.system,
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
                f"{effective_model!r} is a Chat Completions-only model — it is unavailable "
                "on the OpenAI Responses API that frontier_generate uses. "
                f"Use llm_generate(model={effective_model!r}, messages=...) instead "
                "(note: llm_generate has a narrower surface — no agent, tools, boot, "
                "result_delivery, or transcript_id)."
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
    if req.tools is not None:
        _enforce_tools(
            request_id=request_id,
            agent=req.agent,
            requested=req.tools,
            meta=meta,
            event_publisher=event_publisher,
        )
    effective_tools = _resolve_effective_tools(req.tools, meta)
    mcp_enabled = effective_tools is None or bool(effective_tools)

    pipeline_options: dict[str, Any] = {
        "model": effective_model,
        "system": system_assembled,
        "generation_parameters": generation_options,
        "mcp": mcp_enabled,
        "_endpoint_request_id": request_id,
    }
    if req.agent:
        pipeline_options["agent"] = req.agent
    if effective_tools is not None:
        pipeline_options["tools"] = effective_tools
    if req.max_tool_turns is not None:
        pipeline_options["max_tool_turns"] = req.max_tool_turns
    if req.transcript_id:
        pipeline_options["transcript_id"] = req.transcript_id
    if req.remote_mcp is not None:
        pipeline_options["remote_mcp"] = req.remote_mcp

    body: dict[str, Any] = {
        "model": _PIPELINE_ID,
        "messages": req.messages,
        "pipeline_options": pipeline_options,
    }
    if req.timeout_seconds is not None:
        body["timeout_seconds"] = req.timeout_seconds
    if req.result_delivery is not None:
        body["result_delivery"] = req.result_delivery
    if req.caller_agent:
        body["caller_agent"] = req.caller_agent
    return body
