"""Frontier dispatch admission checks — persona enforcement and thread validation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from agent_seat import AgentMeta
from model_id import ModelId
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

from .events import FrontierEndpointRejected

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


def is_chat_completions_only(model: str) -> bool:
    """True iff model is known to be unavailable on the OpenAI Responses API."""
    if model in _CHAT_COMPLETIONS_ONLY_MODELS:
        return True
    mid = ModelId.parse(model)
    return mid.provider == "openai" and mid.base_id.endswith("-search-api")


def mcp_enabled_for_team_dispatch(model: str) -> bool:
    """Derive team_dispatch MCP tooling from the effective model.

    Only xAI *multi-agent* models reject client-side function tools (server-side
    built-ins are injected via provider_options instead). Every other model —
    including non-multi-agent grok (grok-4.3, grok-4.20-0309-reasoning) —
    supports the in-process MCP tool loop. The legacy blanket ``provider !=
    "xai"`` flatten over-suppressed non-multi-agent grok; it is removed. The
    downstream tool-decision branch keeps an equivalent multi-agent guard as
    defense in depth, and inline-only capability tiers are suppressed separately
    via ``capability_tier``.
    """
    mid = ModelId.parse(model)
    return not (mid.provider == "xai" and "multi-agent" in mid.base_id)


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


async def verify_thread_writable(
    thread: str,
    *,
    request_id: str,
    url: str = DEFAULT_AGENT_BUS_URL,
    auth_token: str = "",
) -> None:
    """Raise FrontierEndpointError when the target thread is missing or closed."""
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
        ) from exc
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


def emit_rejection(
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


def enforce_model(
    *,
    request_id: str,
    agent: str | None,
    model: str,
    meta: AgentMeta,
    explicit_model: bool,
    event_publisher: EventPublisher | None,
) -> None:
    if explicit_model:
        return
    if not meta.allowed_models:
        return
    if model in meta.allowed_models:
        return
    reason = (
        f"model {model!r} not allowed for agent {agent!r}; "
        f"allowed: {sorted(meta.allowed_models)}"
    )
    raise emit_rejection(
        request_id=request_id,
        agent=agent,
        field="model",
        reason=reason,
        event_publisher=event_publisher,
    )


def enforce_options(
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
    raise emit_rejection(
        request_id=request_id,
        agent=agent,
        field="generation_options",
        reason=reason,
        event_publisher=event_publisher,
    )
