"""Frontier dispatch admission checks — persona enforcement and thread validation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from agent_seat import AgentMeta
from agent_seat.profiles import (
    CapabilityProfile,
    client_side_mcp_tool_loop_admitted,
    get_profile,
    load_roles,
)
from agent_seat.registry import normalize_agent_slug
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


def _mcp_base_admitted(model: str) -> bool:
    """Shared inline-only gate for every dispatch surface.

    Inline-only families (e.g. gemini) never get a client-side MCP tool loop.
    Both ``mcp_enabled_for_frontier_dispatch`` and
    ``mcp_enabled_for_team_dispatch`` MUST pass through here before applying
    their own (divergent) post-clamp policy, so a newly added inline-only
    family is clamped on *every* surface — not just the one that remembered to
    re-check. Returns ``False`` to force ``mcp=False``.
    """
    return client_side_mcp_tool_loop_admitted(model)


def mcp_enabled_for_frontier_dispatch(model: str, caller_mcp: bool | None) -> bool:
    """Persona-free ``frontier_dispatch``: honor ``req.mcp`` unless inline-only.

    Default ``mcp=False`` when omitted (one-shot). Inline-only families (e.g.
    gemini) clamp to ``False`` even when the caller passes ``mcp=True`` — the
    inline-only gate is shared with ``mcp_enabled_for_team_dispatch`` via
    ``_mcp_base_admitted``; the post-clamp policy (honor caller) is local.
    """
    if not _mcp_base_admitted(model):
        return False
    return bool(caller_mcp)


def mcp_enabled_for_team_dispatch(model: str) -> bool:
    """Derive team_dispatch MCP tooling from the effective model at admission.

    Guard 1 (thread 1206 turn 7): capability binds to the **effective model**.
    Inline-only families (e.g. gemini on any role) are admitted but get
    ``mcp=False`` here via the shared ``_mcp_base_admitted`` gate; hydration
    also sets ``inline_only`` and the pipeline suppresses the client-side tool
    loop — ¬ strict admission reject.

    Multi-agent and inline-only clamps: ``client_side_mcp_tool_loop_admitted``.
    """
    return _mcp_base_admitted(model)


@dataclass(slots=True)
class FrontierEndpointError(Exception):
    request_id: str
    field: str
    reason: str
    status_code: int = 400
    code: str = "persona_violation"

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {"code": self.code, "message": self.reason},
            "field": self.field,
            "request_id": self.request_id,
        }


def _resolve_role_or_seat_profile(
    role: str, *, request_id: str
) -> tuple[str, str, str, CapabilityProfile]:
    """Return ``(to_agent_slug, family, platform, profile)`` for a role or seat slug."""
    canonical = normalize_agent_slug(role)
    roles = load_roles()
    role_profile = roles.get(canonical)

    if role_profile is not None:
        family: str = role_profile.default_family
        platform: str = role_profile.default_platform
    else:
        parts = canonical.split("-", 1)
        if len(parts) != 2 or parts[0] not in {"claude", "gpt", "grok", "gemini"}:
            raise FrontierEndpointError(
                request_id=request_id,
                field="role",
                reason=f"Unknown role or seat {role!r}",
                status_code=422,
            )
        family, platform = parts[0], parts[1]

    try:
        profile = get_profile(family, platform)
    except KeyError:
        raise FrontierEndpointError(
            request_id=request_id,
            field="role",
            reason=f"Unknown role or seat {role!r}",
            status_code=422,
        )

    to_agent = f"{family}-{platform}"
    return to_agent, family, platform, profile


def enforce_team_dispatch_generate_admit(
    role: str,
    *,
    request_id: str,
    event_publisher: EventPublisher | None = None,
) -> None:
    """Reject ``op=generate`` / ``op=to_thread`` for non-dispatchable profiles.

    Admission predicate (FOL):
      admit_generate(role) ⟺ profile.dispatchable is True

    Web/manual seats (``claude/web``, ``grok/web``, …) and roles whose default
    platform is non-dispatchable (``web-consult``, ``investigator``) raise 422 with
    code ``web_seat_not_generate_target``. Explicit ``model=`` does not bypass.
    """
    to_agent, _family, _platform, profile = _resolve_role_or_seat_profile(
        role, request_id=request_id
    )
    if profile.dispatchable:
        return
    reason = (
        f"role {role!r} resolved to {to_agent} which is not dispatchable on "
        f"op=generate/to_thread (delivery={profile.delivery!r}, "
        f"dispatchable=false). Web/manual seats are reachable only via "
        f"op=handoff (inbound). If you are {to_agent}, use fs/cortex locally; "
        f"for peer consult use an API role (reviewer, gatherer, …) or "
        f"frontier_dispatch. Passing model= dispatches an API endpoint only — "
        f"it does not spawn a web session."
    )
    if event_publisher is not None:
        event_publisher(
            FrontierEndpointRejected(
                request_id=request_id, agent=role, field="role", reason=reason
            )
        )
    raise FrontierEndpointError(
        request_id=request_id,
        field="role",
        reason=reason,
        status_code=422,
        code="web_seat_not_generate_target",
    )


def resolve_web_handoff_seat(role: str, *, request_id: str) -> tuple[str, str, str]:
    """Return (to_agent_slug, family, platform) for a handoff-eligible role/seat.

    Admission predicate (FOL):
      admit(role) ⟺ profile.delivery == "manual" ∧ profile.dispatchable is False

    Role examples: ``web-consult`` → ``claude-web``; ``cursor-consult`` → ``claude-cursor``.
    Seat slugs and nicknames (``claude-web``, ``web-claude``, ``claude-cursor``,
    ``cursor-claude``, ``web``, ``cursor``) normalize via ``agent_seat.registry``.

    Raises FrontierEndpointError(field="role", status_code=422) when:
      - role/seat is unknown
      - resolved profile is not a manual, non-dispatchable seat
        → reason code "handoff_requires_web_seat"
    """
    to_agent, family, platform, profile = _resolve_role_or_seat_profile(
        role, request_id=request_id
    )

    if not (profile.delivery == "manual" and not profile.dispatchable):
        raise FrontierEndpointError(
            request_id=request_id,
            field="role",
            reason=(
                f"handoff op requires a web/manual seat (delivery=manual, "
                f"dispatchable=false); role {role!r} resolved to {to_agent} "
                f"which is dispatchable — use op=generate/to_thread"
            ),
            status_code=422,
            code="handoff_requires_web_seat",
        )

    return to_agent, family, platform


_HANDOFF_ROSTER: frozenset[str] = frozenset(
    {"web-consult", "cursor-consult", "cursor-implement"}
)


def resolve_handoff_target(
    *,
    role: str,
    request_id: str,
) -> tuple[str, str, str, str]:
    """Resolve a handoff target seat from ``role``.

    Only handoff roster slugs (``web-consult``, ``cursor-consult``, ``cursor-implement``) are
    admitted — seat aliases (``claude-web``, ``web``, …) are rejected.

    Returns ``(to_agent, family, platform, resolved_model)`` where
    ``resolved_model`` is the canonical synthetic seat slug.
    """
    canonical = normalize_agent_slug(role)
    if canonical not in _HANDOFF_ROSTER:
        raise FrontierEndpointError(
            request_id=request_id,
            field="role",
            reason=(
                f"handoff role {role!r} is not a roster slug; use web-consult "
                f"(→ claude-web), cursor-consult (→ claude-cursor), or "
                f"cursor-implement (bound implement → claude-cursor)"
            ),
            status_code=422,
            code="handoff_role_invalid",
        )
    to_agent, family, platform = resolve_web_handoff_seat(role, request_id=request_id)
    return to_agent, family, platform, to_agent


def resolve_handoff_contract(
    *,
    role: str,
    request_id: str,
) -> tuple[str, str]:
    """Resolve handoff work-intent from ``role`` only.

    ``cursor-implement`` → ``implement``; consult roles → ``consult``.
    """
    _ = request_id
    canonical = normalize_agent_slug(role)
    role_profile = load_roles().get(canonical)
    if role_profile is not None and role_profile.default_contract is not None:
        return role_profile.default_contract, "role_default"
    return "consult", "role_default"


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
    # Guard 1: explicit model= may fill any role (TEAM_CONSULTATION). Disallowed
    # capability (gemini inline-only on reviewer, etc.) is enforced via
    # mcp_enabled_for_team_dispatch + hydration inline_only — not here.
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
