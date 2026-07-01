"""Async Cortex hydration for dispatched agents.

Mirrors the slim-briefing shape of MCP's ``run_cortex_boot`` but async-native
and self-contained: no filesystem side effects, no import of MCP-server-private
helpers. Parallel fetch of journals / deadlines / todos / bus threads /
self-assertions / reflective journal / temporal, then renders a compact
Markdown briefing card the handler drops into the system prompt.

Caller provides the dispatched agent's canonical identity; the function loads
that agent's own boot state ("function as a team member" semantic — each
sibling dispatch sees its own journal, todos, continuation, etc.).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import quote, urlencode

from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_CORTEX_URL,
    make_async_client,
)
from universal_logging import get_logger

from agent_seat.body_injection import RequiredBodyUnresolved
from agent_seat.inject_registry import resolve_injected_bodies

from ._hydration_render import (
    PROFILES as _PROFILES,
)
from ._hydration_render import (
    _as_optional_str_list,
    _as_str_list,
    _render_briefing,
    _safe_list,
)
from .profiles import (
    client_side_mcp_tool_loop_admitted,
    family_anchor,
    get_profile,
    get_role,
    load_roles,
    role_anchor,
    seat_to_family,
)
from .registry import normalize_agent_slug

logger = get_logger(__name__)

_FETCH_TIMEOUT = 20.0


def _normalize_slug_to_anchors(
    agent_or_role: str, role: str | None = None
) -> list[str]:
    """Return ordered list of Cortex memory anchor entity IDs for a hydration request.

    Always loads the family anchor; optionally loads the role anchor if a role
    is explicitly supplied or the slug itself is a role name.

    Returns: [family_anchor(family)] + optional [role_anchor(role)]
    """
    canonical = normalize_agent_slug(agent_or_role)
    anchors: list[str] = []

    if canonical in load_roles():
        # Caller passed a role slug directly (team_dispatch path)
        role_profile = get_role(canonical)
        anchors.append(family_anchor(role_profile.default_family))
        anchors.append(role_anchor(canonical))
        return anchors

    # Caller passed a seat slug ({family}-{platform})
    family = seat_to_family(canonical)
    if family is not None:
        anchors.append(family_anchor(family))
    else:
        # Unknown slug — fall back to family:claude for the cursor seat default
        anchors.append(family_anchor("claude"))

    if role is not None:
        anchors.append(role_anchor(role))
    return anchors


@dataclass(slots=True)
class AgentMeta:
    """Execution contract loaded from Cortex role:* or family:* entity attributes.

    Role slugs (team_dispatch) source execution fields from role:*; seat slugs
    from family:*. Registry fallback fills gaps when Cortex attributes are empty.

    ``capability_tier`` is an agent-level dispatch-surface gate. When set to
    ``"inline-only"`` the dispatch handler coerces the tool surface to empty
    regardless of provider/model — no MCP, no client tools, no Cortex quickref.
    Reinstatement is a single entity-attribute update, no code change.
    """

    frontier_kind: str | None = None
    default_model: str | None = None
    allowed_models: list[str] = field(default_factory=list)
    allowed_options: list[str] | None = None
    capability_tier: str | None = None


@dataclass(slots=True)
class HydrationBundle:
    """Output of ``hydrate_agent``.

    - ``briefing_card_md``: compact Markdown briefing (~3-5KB) drop-in for
      the agent's system prompt. Rendered from the parallel fetches below.
    - ``continuation_md``: optional transcript-continuation section,
      populated only when ``transcript_id`` is provided and resolves.
    - ``continuation_id``: entity_id of the continuation transcript, if any.
    - ``section_counts``: payload-friendly counts for event emission
      (briefing_bytes, todos, unread_turns, deadlines, etc.).
    """

    briefing_card_md: str
    continuation_md: str | None = None
    continuation_id: str | None = None
    section_counts: dict[str, int] = field(default_factory=dict)
    agent_meta: AgentMeta = field(default_factory=AgentMeta)
    inline_only: bool = False
    injected_bodies_md: str | None = None
    required_body_unresolved: bool = False
    injection_meta: dict[str, Any] = field(default_factory=dict)


async def _fetch_guidance_entities(limit: int = 50) -> dict[str, list[dict[str, Any]]]:
    """Widened boot skills fetch: agent_skill + rule + skill (never drop agent_skill)."""
    merged: list[dict[str, Any]] = []
    for entity_type in ("agent_skill", "rule", "skill"):
        qs = urlencode({"type": entity_type, "limit": limit})
        raw = await _cortex_get(f"/entities?{qs}")
        merged.extend(_safe_list(raw))
    return {"items": merged}


async def _cortex_get(path: str) -> Any:
    """Single GET to Cortex UDS; returns parsed JSON or ``{"error": ...}``."""
    try:
        async with make_async_client(
            DEFAULT_CORTEX_URL,
            timeout=_FETCH_TIMEOUT,
        ) as client:
            resp = await client.get(path)
    except Exception as exc:
        return {"error": f"cortex {path} failed: {exc}"}
    if resp.status_code >= 400:
        return {"error": f"cortex {path} HTTP {resp.status_code}"}
    try:
        return resp.json()
    except Exception:
        return {"error": f"cortex {path} invalid JSON"}


async def _bus_get(path: str) -> Any:
    """Single GET to agent-bus UDS; returns parsed JSON or ``{"error": ...}``."""
    try:
        async with make_async_client(
            DEFAULT_AGENT_BUS_URL,
            timeout=_FETCH_TIMEOUT,
        ) as client:
            resp = await client.get(path)
    except Exception as exc:
        return {"error": f"agent-bus {path} failed: {exc}"}
    if resp.status_code >= 400:
        return {"error": f"agent-bus {path} HTTP {resp.status_code}"}
    try:
        return resp.json()
    except Exception:
        return {"error": f"agent-bus {path} invalid JSON"}


def _parse_agent_meta(entity: Any) -> AgentMeta:
    if not isinstance(entity, dict):
        return AgentMeta()
    attributes = entity.get("attributes")
    if not isinstance(attributes, dict):
        return AgentMeta()
    frontier_kind_raw = attributes.get("frontier_kind")
    default_model_raw = attributes.get("default_model")
    capability_tier_raw = attributes.get("capability_tier")
    if capability_tier_raw is not None and str(capability_tier_raw) != "inline-only":
        logger.warning(
            "agent_seat: unrecognized capability_tier %r — treating as None",
            capability_tier_raw,
        )
        capability_tier_raw = None
    return AgentMeta(
        frontier_kind=(
            str(frontier_kind_raw) if isinstance(frontier_kind_raw, str) else None
        ),
        default_model=(
            str(default_model_raw) if isinstance(default_model_raw, str) else None
        ),
        allowed_models=_as_str_list(attributes.get("allowed_models")),
        allowed_options=_as_optional_str_list(attributes.get("allowed_options")),
        capability_tier=(
            str(capability_tier_raw) if isinstance(capability_tier_raw, str) else None
        ),
    )


def _execution_meta_entity_id(anchors: list[str]) -> str:
    """Pick the Cortex entity that carries dispatch execution contract.

    Role slugs hydrate with [family_anchor, role_anchor]; execution fields
    (default_model, allowed_models, frontier_kind) live on role:* after sync.
    Seat slugs use family:* only.
    """
    if len(anchors) > 1 and anchors[-1].startswith("role:"):
        return anchors[-1]
    return anchors[0] if anchors else "family:claude"


def _registry_meta_fallback(agent: str, meta: AgentMeta) -> AgentMeta:
    """Fill missing execution fields from agents.yaml when Cortex is sparse."""
    canonical = normalize_agent_slug(agent)
    role_profile = load_roles().get(canonical)
    if role_profile is None:
        return meta
    updates: dict[str, Any] = {}
    if not meta.default_model and role_profile.default_model:
        updates["default_model"] = role_profile.default_model
    if not meta.allowed_models and role_profile.allowed_models:
        updates["allowed_models"] = list(role_profile.allowed_models)
    if not meta.frontier_kind:
        try:
            profile = get_profile(
                role_profile.default_family, role_profile.default_platform
            )
            updates["frontier_kind"] = profile.provider
        except KeyError:
            pass
    return replace(meta, **updates) if updates else meta


async def _fetch_agent_meta(agent: str) -> AgentMeta:
    """Fetch and parse execution-contract attributes for *agent* from Cortex.

    Role slugs read role:* (execution contract); seat slugs read family:*.
    Falls back to agents.yaml registry when Cortex attributes are empty.
    Returns AgentMeta() on fetch/parse error so the caller is never blocked.
    """
    anchors = _normalize_slug_to_anchors(agent)
    entity_id = _execution_meta_entity_id(anchors)
    raw = await _cortex_get(f"/entities/{quote(entity_id, safe=':')}?intent=full")
    if isinstance(raw, dict) and "error" in raw:
        return _registry_meta_fallback(agent, AgentMeta())
    return _registry_meta_fallback(agent, _parse_agent_meta(raw))


async def _resolve_continuation(
    transcript_id: str,
) -> tuple[str | None, str | None]:
    """Fetch continuation transcript by id. Returns (markdown, entity_id).

    Missing-transcript errors do not fail the hydration — logged and ignored.
    """
    if not transcript_id:
        return None, None
    clean_id = transcript_id.removeprefix("transcript:")
    entity_key = f"transcript:{clean_id}"
    data = await _cortex_get(f"/entities/{quote(entity_key, safe=':')}?intent=full")
    if not isinstance(data, dict) or "error" in data:
        logger.warning(
            "agent_seat.hydrate: transcript %s not found — continuation skipped",
            entity_key,
        )
        return None, None
    summary = data.get("description", "")
    if not summary:
        assertions = data.get("assertions", [])
        if isinstance(assertions, list):
            for a in assertions:
                if isinstance(a, dict) and not a.get("superseded_by"):
                    summary = a.get("claim", "")
                    if summary:
                        break
    md = f"## Resuming From: `{entity_key}`\n" + (
        f"**Summary**: {summary}\n" if summary else ""
    )
    return md, entity_key


async def hydrate_agent(
    agent: str,
    transcript_id: str | None = None,
    *,
    fetch_profile: str = "default",
    model: str | None = None,
    inject_profile: str | None = None,
    code_touching: bool = False,
    packet_invariant_ids: tuple[str, ...] = (),
) -> HydrationBundle:
    """Fetch the dispatched agent's boot state and render a briefing card.

    Parallel fetches: session-journals, deadlines, unread bus turns, threads,
    todos, self-assertions, optional transcript continuation. Any individual
    fetch failure is absorbed — the briefing simply omits that section.

    ``fetch_profile`` selects fetch + render shape from ``_PROFILES``:
    - ``"default"`` — full context (deadlines, review queue, 5 reflections, 3 sessions)
    - ``"light"``   — soft boot for team_dispatch and role-envelope HTTP dispatches
                      (drops deadlines + review queue, 3 reflections floor, 1 session)
    Truthiness gates in ``_render_briefing`` collapse empty sections naturally;
    no separate render-shape flag needed.
    """
    profile_dict = _PROFILES[fetch_profile]

    # Query parameters for per-agent scoping.
    session_qs = urlencode({"limit": profile_dict["session_limit"]})
    normalized_agent = normalize_agent_slug(agent)
    unread_qs = urlencode(
        {"to": normalized_agent, "unread": "true", "last": 10, "compact": "true"},
    )
    todo_qs = urlencode({"limit": 15})

    continuity_qs = urlencode({"agent": normalized_agent})
    tasks: dict[str, asyncio.Task[Any]] = {
        "sessions": asyncio.create_task(_cortex_get(f"/session-journals?{session_qs}")),
        "continuity": asyncio.create_task(
            _cortex_get(f"/boot-continuity?{continuity_qs}"),
        ),
        "threads": asyncio.create_task(_bus_get("/threads?status=active")),
        "unread_turns": asyncio.create_task(_bus_get(f"/turns?{unread_qs}")),
        "todos": asyncio.create_task(_cortex_get(f"/boot-todos?{todo_qs}")),
        "agent_meta": asyncio.create_task(_fetch_agent_meta(normalized_agent)),
        "skills": asyncio.create_task(_fetch_guidance_entities(limit=50)),
    }
    if profile_dict["include_deadlines"]:
        tasks["deadlines"] = asyncio.create_task(_cortex_get("/deadlines"))
    if profile_dict["include_review_queue"]:
        tasks["staging"] = asyncio.create_task(
            _cortex_get("/staging?status=pending&limit=5"),
        )

    memory_anchors = _normalize_slug_to_anchors(normalized_agent)
    if memory_anchors:
        # Fetch self-reflections from the primary anchor (family anchor).
        # Multiple anchors (e.g. family + role) are fetched sequentially in
        # _render_briefing via the merged result; here we fetch the first
        # (family) anchor which carries the most persistent self-knowledge.
        refl_qs = urlencode(
            {
                "entity_id": memory_anchors[0],
                "superseded": "false",
                "limit": profile_dict["self_reflections_limit"],
            },
        )
        tasks["self_reflections"] = asyncio.create_task(
            _cortex_get(f"/assertions?{refl_qs}"),
        )

    continuation_task: asyncio.Task[tuple[str | None, str | None]] | None = None
    if transcript_id:
        continuation_task = asyncio.create_task(_resolve_continuation(transcript_id))

    await asyncio.gather(*tasks.values(), return_exceptions=False)
    raw = {k: t.result() for k, t in tasks.items()}

    continuation_md: str | None = None
    continuation_id: str | None = None
    if continuation_task is not None:
        continuation_md, continuation_id = await continuation_task

    sessions = _safe_list(raw.get("sessions"))
    continuity_raw = raw.get("continuity")
    last_session: dict[str, Any] | None = None
    if isinstance(continuity_raw, dict):
        ls = continuity_raw.get("last_session")
        if isinstance(ls, dict):
            last_session = ls
    if last_session is None and sessions and isinstance(sessions[0], dict):
        last_session = sessions[0]
    deadlines = _safe_list(raw.get("deadlines"))
    threads = _safe_list(raw.get("threads"), "threads")
    unread_turns = _safe_list(raw.get("unread_turns"), "turns")
    todos = _safe_list(raw.get("todos"))
    staging = _safe_list(raw.get("staging"))
    self_reflections = _safe_list(raw.get("self_reflections"))
    skills = _safe_list(raw.get("skills"))
    agent_meta = raw["agent_meta"]
    effective_model = model if model is not None else agent_meta.default_model
    if normalized_agent in load_roles() and effective_model is not None:
        # Capability tier binds to the EFFECTIVE model, not the role default
        # profile: an explicit model= override (e.g. skeptic + model=gemini)
        # must clear stale inline-only from grok/api-multi so MCP-capable
        # models get the tool surface; conversely reviewer + xai multi-agent
        # must recompute inline-only even when the role default is MCP-capable.
        if client_side_mcp_tool_loop_admitted(effective_model):
            agent_meta = replace(agent_meta, capability_tier=None)
        else:
            agent_meta = replace(agent_meta, capability_tier="inline-only")

    unread_threads = [
        t for t in threads if isinstance(t, dict) and t.get("unread_count", 0) > 0
    ]

    if effective_model is not None:
        inline_only = not client_side_mcp_tool_loop_admitted(effective_model)
    else:
        inline_only = agent_meta.capability_tier == "inline-only"

    briefing = _render_briefing(
        agent,
        sessions=sessions,
        last_session=last_session,
        deadlines=deadlines,
        todos=todos,
        unread_count=len(unread_turns),
        unread_threads=unread_threads,
        self_reflections=self_reflections,
        review_total=len(staging),
        skills=skills,
        inline_only=inline_only,
    )

    injected_bodies_md: str | None = None
    required_body_unresolved = False
    injection_meta: dict[str, Any] = {}
    resolve_role = normalized_agent if normalized_agent in load_roles() else None
    resolve_platform = "*"
    if resolve_role is not None:
        role_profile = get_role(resolve_role)
        resolve_platform = role_profile.default_platform
    else:
        parts = normalized_agent.split("-", 1)
        if len(parts) == 2:
            resolve_platform = parts[1]

    already_present = (briefing or "") + (continuation_md or "")
    try:
        resolution = await asyncio.to_thread(
            resolve_injected_bodies,
            normalized_agent,
            role=resolve_role,
            platform=resolve_platform,
            inject_profile=inject_profile,
            code_touching=code_touching,
            packet_invariant_ids=packet_invariant_ids,
            already_present=already_present,
        )
        if resolution.block_md:
            injected_bodies_md = resolution.block_md
        injection_meta = {
            "injected": resolution.injected,
            "dropped": resolution.dropped,
            "metrics": resolution.telemetry,
        }
    except RequiredBodyUnresolved as exc:
        required_body_unresolved = True
        injection_meta = {
            "injected": [],
            "dropped": exc.dropped,
            "metrics": {
                "elapsed_ms": 0,
                "cold_fetches": 0,
                "cache_hit": False,
                "deadline_hit": False,
            },
        }

    section_counts = {
        "briefing_bytes": len(briefing),
        "sessions": len(sessions),
        "deadlines": len(deadlines),
        "todos": len(todos),
        "unread_turns": len(unread_turns),
        "unread_threads": len(unread_threads),
        "review_queue": len(staging),
        "self_reflections": len(self_reflections),
        "skills": len(skills),
    }

    return HydrationBundle(
        briefing_card_md=briefing,
        continuation_md=continuation_md,
        continuation_id=continuation_id,
        section_counts=section_counts,
        agent_meta=agent_meta,
        inline_only=inline_only,
        injected_bodies_md=injected_bodies_md,
        required_body_unresolved=required_body_unresolved,
        injection_meta=injection_meta,
    )
