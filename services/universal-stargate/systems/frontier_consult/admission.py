"""Frontier dispatch admission checks — persona enforcement and thread validation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from agent_seat import AgentMeta
from agent_seat.dispatch_role_catalog import (
    handoff_roles,
    handoff_seat_map_clause,
    is_legacy_role,
)
from agent_seat.profiles import (
    CapabilityProfile,
    client_side_mcp_tool_loop_admitted,
    get_profile,
    load_roles,
    seat_to_family,
)
from agent_seat.registry import normalize_agent_slug, normalize_bus_address
from model_capabilities import CapabilityCardError
from model_id import ModelId
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

from .events import DispatchCapabilityCardMissing, FrontierEndpointRejected
from .probe_caller_guard import reject_probe_on_reviewer

EventPublisher = Callable[[Any], None]

# Models that only support the Chat Completions API and are unavailable on the
# OpenAI Responses API path used by chat-dispatch respond (frontier_dispatch_v1).
# Role-less callers reach them via chat-dispatch respond_cc (generate →
# /v1/chat/completions). Role-carrying team_dispatch still rejects.
# ∀ new Chat-Completions-only OpenAI models: add to this set.
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


def assert_model_carded(
    model: str,
    *,
    request_id: str,
    event_publisher: EventPublisher | None,
) -> None:
    """Pre-hydration card gate — raises translated 422 on missing capability card."""
    if ModelId.parse(model).backend_type == "cursor_sdk":
        return
    try:
        client_side_mcp_tool_loop_admitted(model)
    except CapabilityCardError as exc:
        raise _translate_capability_card_error(
            exc,
            request_id=request_id,
            event_publisher=event_publisher,
        ) from exc


def _translate_capability_card_error(
    exc: CapabilityCardError,
    *,
    request_id: str,
    event_publisher: EventPublisher | None,
) -> FrontierEndpointError:
    if event_publisher is not None:
        event_publisher(
            DispatchCapabilityCardMissing(
                request_id=request_id,
                model=exc.model,
                capability_field=exc.capability_field,
                reason_code=exc.reason_code,
            )
        )
    return FrontierEndpointError(
        request_id=request_id,
        field="model",
        reason=str(exc),
        status_code=422,
        code=exc.reason_code,
        details={
            "model": exc.model,
            "capability_field": exc.capability_field,
            "reason_code": exc.reason_code,
        },
    )


def _mcp_base_admitted(
    model: str,
    *,
    request_id: str | None = None,
    event_publisher: EventPublisher | None = None,
) -> bool:
    """Shared inline-only gate for every dispatch surface.

    Inline-only models never get a client-side MCP tool loop.
    Both ``mcp_enabled_for_frontier_dispatch`` and
    ``mcp_enabled_for_team_dispatch`` MUST pass through here before applying
    their own (divergent) post-clamp policy, so a newly carded inline-only
    model is clamped on *every* surface — not just the one that remembered to
    re-check. Returns ``False`` to force ``mcp=False``.

    When ``request_id`` is supplied (dispatch admission), ``CapabilityCardError``
    is translated to a structured 422; otherwise the lib exception propagates.
    """
    if ModelId.parse(model).backend_type == "cursor_sdk":
        return True
    try:
        return client_side_mcp_tool_loop_admitted(model)
    except CapabilityCardError as exc:
        if request_id is None:
            raise
        raise _translate_capability_card_error(
            exc,
            request_id=request_id,
            event_publisher=event_publisher,
        ) from exc


def mcp_enabled_for_frontier_dispatch(
    model: str,
    caller_mcp: bool | None,
    *,
    request_id: str | None = None,
    event_publisher: EventPublisher | None = None,
) -> bool:
    """Persona-free ``POST /api/v1/frontier/dispatch``: honor ``req.mcp`` unless
    inline-only.

    Default ``mcp=False`` when omitted (one-shot). Inline-only families (e.g.
    gemini) clamp to ``False`` even when the caller passes ``mcp=True`` — the
    inline-only gate is shared with ``mcp_enabled_for_team_dispatch`` via
    ``_mcp_base_admitted``; the post-clamp policy (honor caller) is local.
    """
    if not _mcp_base_admitted(
        model, request_id=request_id, event_publisher=event_publisher
    ):
        return False
    return bool(caller_mcp)


def mcp_enabled_for_team_dispatch(
    model: str,
    caller_mcp: bool | None = None,
    *,
    request_id: str | None = None,
    event_publisher: EventPublisher | None = None,
) -> bool:
    """Derive team_dispatch MCP tooling from the effective model at admission.

    Guard 1 (thread 1206 turn 7): capability binds to the **effective model**.
    Inline-only families (e.g. gemini on any role) are admitted but get
    ``mcp=False`` here via the shared ``_mcp_base_admitted`` gate; hydration
    also sets ``inline_only`` and the pipeline suppresses the client-side tool
    loop — ¬ strict admission reject.

    ``caller_mcp`` honors an explicit caller intent symmetrically with
    ``mcp_enabled_for_frontier_dispatch``: the inline-only clamp runs first, then
    an explicit ``False`` opts the dispatch out of the MCP tool loop (and, via
    card-derived internal selection, out of Anthropic server-side remote MCP).
    When the caller omits the knob (``None``) the team_dispatch default remains
    tools-on for tool-capable families — peer consults are expected to reach
    cortex/rag/agent_bus. Without this, an Anthropic native model was forced
    ``mcp=True`` with card-selected remote connector even when the caller
    wanted a one-shot inline generation (thread 1653).

    Multi-agent and inline-only clamps: ``client_side_mcp_tool_loop_admitted``.
    """
    if not _mcp_base_admitted(
        model, request_id=request_id, event_publisher=event_publisher
    ):
        return False
    if caller_mcp is None:
        return True
    return bool(caller_mcp)


@dataclass(slots=True)
class FrontierEndpointError(Exception):
    request_id: str
    field: str
    reason: str
    status_code: int = 400
    code: str = "persona_violation"
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": {"code": self.code, "message": self.reason},
            "field": self.field,
            "request_id": self.request_id,
        }
        if self.details:
            payload["details"] = self.details
        return payload


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
        family = seat_to_family(canonical)
        parts = canonical.split("-", 1)
        if family is None or len(parts) != 2:
            raise FrontierEndpointError(
                request_id=request_id,
                field="role",
                reason=f"Unknown role or seat {role!r}",
                status_code=422,
            )
        platform = parts[1]

    try:
        profile = get_profile(family, platform)
    except KeyError:
        raise FrontierEndpointError(
            request_id=request_id,
            field="role",
            reason=f"Unknown role or seat {role!r}",
            status_code=422,
        )

    to_agent = normalize_bus_address(f"{family}-{platform}")
    return to_agent, family, platform, profile


def enforce_team_dispatch_generate_admit(
    role: str,
    *,
    request_id: str,
    event_publisher: EventPublisher | None = None,
    caller_agent: str | None = None,
) -> None:
    """Reject ``op=generate`` / ``op=to_thread`` when profile does not admit generate.

    Admission predicate (FOL):
      admit_generate(role) ⟺ profile.admits_generate() is True
      ¬(role=reviewer ∧ is_mcp_probe_caller(caller_agent))

    Web/manual seats (``claude/web``, ``grok/web``, …) and roles whose default
    platform is manual_handoff (``web-consult``, ``web-implement``, …) raise 422 with
    code ``web_seat_not_generate_target``. Explicit ``model=`` does not bypass.

    Probe callers (``mcp-l*-probe``, ``mcp-trace-matrix``) on ``role=reviewer``
    raise 422 ``probe_reviewer_forbidden`` — use chat ``-mcp`` or artisan/skeptic.
    """
    reject_probe_on_reviewer(
        role,
        request_id=request_id,
        caller_agent=caller_agent,
        event_publisher=event_publisher,
    )

    to_agent, _family, _platform, profile = _resolve_role_or_seat_profile(
        role, request_id=request_id
    )
    if profile.admits_generate():
        return
    reason = (
        f"role {role!r} resolved to {to_agent} which is not admitted on "
        f"op=generate/to_thread (delivery={profile.delivery!r}, "
        f"api_dispatchable={profile.api_dispatchable}, "
        f"auto_dispatchable={profile.auto_dispatchable}). "
        f"Manual seats are reachable only via op=handoff. "
        f"If you are {to_agent}, use fs/cortex locally; "
        f"for peer consult use `team_dispatch(op=generate, role=reviewer, "
        f"dispatch_thread_id=…)` or another API role. Passing model= dispatches "
        f"an API endpoint only — it does not spawn a web session."
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
      admit(role) ⟺ profile.admits_handoff() is True

    Role examples: ``web-consult`` → ``claude-web``;
    ``cursor-consult`` → ``claude-cursor``.
    Seat slugs and nicknames (``claude-web``, ``web-claude``, ``claude-cursor``,
    ``cursor-claude``, ``web``, ``cursor``) normalize via ``agent_seat.registry``.

    Raises FrontierEndpointError(field="role", status_code=422) when:
      - role/seat is unknown
      - resolved profile is not manual_handoff
        → reason code "handoff_requires_web_seat"
    """
    to_agent, family, platform, profile = _resolve_role_or_seat_profile(
        role, request_id=request_id
    )

    if not profile.admits_handoff():
        raise FrontierEndpointError(
            request_id=request_id,
            field="role",
            reason=(
                f"handoff op requires manual_handoff=true; role {role!r} "
                f"resolved to {to_agent} which admits generate "
                f"(api={profile.api_dispatchable}, auto={profile.auto_dispatchable}) "
                f"— use op=generate/to_thread"
            ),
            status_code=422,
            code="handoff_requires_web_seat",
        )

    return to_agent, family, platform


def _admitted_handoff_roster() -> frozenset[str]:
    return frozenset(r for r in handoff_roles() if not is_legacy_role(r))


def resolve_handoff_target(
    *,
    role: str,
    request_id: str,
) -> tuple[str, str, str, str]:
    """Resolve a handoff target seat from ``role``.

    Only handoff roster slugs (``web-consult``, ``web-implement``, ``cursor-consult``,
    ``cursor-implement``) are admitted — seat aliases (``claude-web``, ``web``, …)
    are rejected.

    Returns ``(to_agent, family, platform, resolved_model)`` where
    ``resolved_model`` is the canonical synthetic seat slug.
    """
    canonical = normalize_agent_slug(role)
    if canonical not in _admitted_handoff_roster():
        raise FrontierEndpointError(
            request_id=request_id,
            field="role",
            reason=(
                f"handoff role {role!r} is not a roster slug; handoff seat-map: "
                f"{handoff_seat_map_clause()}"
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
    """Resolve handoff work-intent from ``role`` only (roster-slug fallback path)."""
    _ = request_id
    from .contract_derivation import contract_from_role

    from_role = contract_from_role(role)
    if from_role is not None:
        return from_role
    return "consult", "role_default"


def resolve_handoff_seat(
    *,
    seat: str,
    request_id: str,
) -> tuple[str, str, str, str]:
    """Resolve handoff target from a manual seat slug (``claude-web``, ``web``, …)."""
    to_agent, family, platform = resolve_web_handoff_seat(seat, request_id=request_id)
    return to_agent, family, platform, to_agent


def resolve_cursor_sdk_handoff_seat(
    seat: str,
    *,
    request_id: str,
) -> tuple[str, str, str, str]:
    """Resolve automated cursor-sdk handoff seat (``cursor-sdk`` only).

    Deprecated at the HTTP handoff surface — ``op=handoff,seat=cursor-sdk``
    normalizes to ``dispatch_cursor_sdk_generate``. Retained for unit tests.

    This function returns the bare ``"cursor-sdk"`` recipient (not the
    per-dispatch scoped form ``cursor-sdk:dispatch:{execution_id}``).
    Production bus recipients are scoped in ``cursor_sdk_generate.py`` after
    ``execution_id`` is minted — this path is NOT on that code route.

    Admission predicate (FOL):
      admit(seat) ⟺ profile.delivery == auto
                 ∧ family == cursor
                 ∧ tool_surface == sdk

    Returns ``(to_agent, family, platform, resolved_model)`` where
    ``resolved_model`` is the profile ``default_model``.
    """
    canonical = normalize_agent_slug(seat)
    if canonical != "cursor-sdk":
        raise FrontierEndpointError(
            request_id=request_id,
            field="seat",
            reason=f"seat {seat!r} is not cursor-sdk",
            status_code=422,
            code="handoff_seat_invalid",
        )
    try:
        profile = get_profile("cursor", "sdk")
    except KeyError:
        raise FrontierEndpointError(
            request_id=request_id,
            field="seat",
            reason=f"cursor-sdk profile not registered for seat {seat!r}",
            status_code=422,
            code="handoff_seat_invalid",
        )
    if not (
        profile.delivery == "auto"
        and profile.family == "cursor"
        and profile.tool_surface == "sdk"
    ):
        raise FrontierEndpointError(
            request_id=request_id,
            field="seat",
            reason=(
                f"cursor-sdk requires delivery=auto, family=cursor, tool_surface=sdk; "
                f"got delivery={profile.delivery!r}, family={profile.family!r}, "
                f"tool_surface={profile.tool_surface!r}"
            ),
            status_code=422,
            code="handoff_seat_invalid",
        )
    default_model = profile.default_model
    if not default_model:
        raise FrontierEndpointError(
            request_id=request_id,
            field="seat",
            reason="cursor-sdk profile has no default_model",
            status_code=422,
            code="handoff_seat_invalid",
        )
    return "cursor-sdk", "cursor", "sdk", default_model


def is_sdk_substrate_profile(profile: CapabilityProfile) -> bool:
    """True when profile is the local SDK bridge (not cloud API)."""
    return (
        profile.family == "cursor"
        and profile.platform == "sdk"
        and profile.auto_dispatchable
    )


def resolve_cursor_sdk_generate_target(
    role: str,
    *,
    model: str | None,
    request_id: str,
) -> tuple[str, str, str, str]:
    """Resolve cursor-sdk generate target.

    Admission predicate (FOL):
      admit(role, model) ⟺ profile.auto_dispatchable
                 ∧ family=cursor ∧ platform=sdk

    Accepts role slug ``cursor-sdk`` or explicit ``model=cursor/…`` when role
    resolves to cursor/sdk. Returns ``(to_agent, family, platform, resolved_model)``.
    """
    to_agent, family, platform, profile = _resolve_role_or_seat_profile(
        role, request_id=request_id
    )
    if not is_sdk_substrate_profile(profile):
        raise FrontierEndpointError(
            request_id=request_id,
            field="role",
            reason=(
                f"role {role!r} resolved to ({family!r}, {platform!r}) which is "
                f"not an SDK auto-dispatch substrate"
            ),
            status_code=422,
            code="sdk_substrate_required",
        )
    resolved_model = model or profile.default_model
    if not resolved_model:
        raise FrontierEndpointError(
            request_id=request_id,
            field="model",
            reason="cursor-sdk requires default_model or explicit model=",
            status_code=422,
            code="sdk_generate_model_invalid",
        )
    if not resolved_model.startswith("cursor/"):
        raise FrontierEndpointError(
            request_id=request_id,
            field="model",
            reason=f"SDK substrate requires cursor/* model ids, got {resolved_model!r}",
            status_code=422,
            code="sdk_generate_model_invalid",
        )
    from cursor_capabilities import is_cursor_model_denied

    if profile.family == "cursor" and profile.platform == "sdk":
        if is_cursor_model_denied(resolved_model):
            raise FrontierEndpointError(
                request_id=request_id,
                field="model",
                reason=f"model {resolved_model!r} is denied for cursor/sdk substrate",
                status_code=422,
                code="sdk_generate_model_invalid",
            )
    elif profile.allowed_models and resolved_model not in profile.allowed_models:
        raise FrontierEndpointError(
            request_id=request_id,
            field="model",
            reason=(
                f"model {resolved_model!r} not in allowed_models: "
                f"{sorted(profile.allowed_models)}"
            ),
            status_code=422,
            code="sdk_generate_model_invalid",
        )
    return "cursor-sdk", family, platform, resolved_model


def is_cursor_sdk_generate_role(role: str, *, request_id: str) -> bool:
    """True when role/seat slug resolves to cursor/sdk auto_dispatchable profile."""
    try:
        _to, _fam, _plat, profile = _resolve_role_or_seat_profile(
            role, request_id=request_id
        )
    except FrontierEndpointError:
        return False
    return is_sdk_substrate_profile(profile)


def is_cursor_sdk_generate_admission(
    role: str,
    *,
    model: str | None,
    request_id: str,
) -> bool:
    """True when ``op=generate`` should enter the cursor-sdk handler branch."""
    if is_cursor_sdk_generate_role(role, request_id=request_id):
        return True
    if model:
        return ModelId.parse(model).backend_type == "cursor_sdk"
    return False


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
