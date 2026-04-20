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
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode

from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_CORTEX_URL,
    make_async_client,
)

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT = 20.0

# Per-agent boot profile. Mirrors _BOOT_PROFILES in cortex_named_tools.py but
# scoped to what the dispatched-agent briefing needs (no cursor-specific
# knobs). Unknown agents fall back to _DEFAULT_PROFILE.
_DEFAULT_PROFILE: dict[str, Any] = {
    "include_deadlines": True,
    "include_review_queue": True,
    "session_limit": 3,
    "self_reflections_limit": 5,
}

_SELF_ENTITY: dict[str, str] = {
    "oppie": "ai_agent:oppie",
    "orion": "ai_agent:orion",
    "web": "ai_agent:web-claude",
    "bard": "ai_agent:bard",
    "api_claude": "ai_agent:api-claude",
    "cursor": "ai_agent:cursor-claude",
}


@dataclass(slots=True)
class AgentMeta:
    """Persona contract loaded from ``ai_agent:{slug}.attributes``."""

    frontier_kind: str | None = None
    default_model: str | None = None
    allowed_models: list[str] = field(default_factory=list)
    tools: list[str] | None = None
    allowed_options: list[str] | None = None
    persona_seed_ref: str | None = None


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


async def _cortex_get(path: str) -> Any:
    """Single GET to Cortex UDS; returns parsed JSON or ``{"error": ...}``."""
    try:
        async with make_async_client(
            DEFAULT_CORTEX_URL, timeout=_FETCH_TIMEOUT
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
            DEFAULT_AGENT_BUS_URL, timeout=_FETCH_TIMEOUT
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


def _safe_list(raw: Any, key: str = "items") -> list[Any]:
    """Extract a list from an API response; returns [] on error or wrong shape."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        if "error" in raw:
            return []
        val = raw.get(key, [])
        return val if isinstance(val, list) else []
    return []


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _as_optional_str_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    return [str(item) for item in value if isinstance(item, str)]


def _parse_agent_meta(entity: Any) -> AgentMeta:
    if not isinstance(entity, dict):
        return AgentMeta()
    attributes = entity.get("attributes")
    if not isinstance(attributes, dict):
        return AgentMeta()
    frontier_kind_raw = attributes.get("frontier_kind")
    default_model_raw = attributes.get("default_model")
    persona_seed_ref_raw = attributes.get("persona_seed_ref")
    return AgentMeta(
        frontier_kind=(
            str(frontier_kind_raw) if isinstance(frontier_kind_raw, str) else None
        ),
        default_model=(
            str(default_model_raw) if isinstance(default_model_raw, str) else None
        ),
        allowed_models=_as_str_list(attributes.get("allowed_models")),
        tools=_as_optional_str_list(attributes.get("tools")),
        allowed_options=_as_optional_str_list(attributes.get("allowed_options")),
        persona_seed_ref=(
            str(persona_seed_ref_raw) if isinstance(persona_seed_ref_raw, str) else None
        ),
    )


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
    data = await _cortex_get(f"/entities/{quote(entity_key, safe=':')}")
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


def _render_briefing(
    agent: str,
    *,
    sessions: list[dict[str, Any]],
    deadlines: list[dict[str, Any]],
    todos: list[dict[str, Any]],
    unread_count: int,
    unread_threads: list[dict[str, Any]],
    self_reflections: list[dict[str, Any]],
    review_total: int,
) -> str:
    """Render the compact briefing card for the dispatched agent.

    Intentionally simpler than MCP's ``render_briefing_card`` — the pipeline
    handler does not need the full MCP UI affordances (manifest hints,
    fetch-hint code snippets) because the agent has the team toolset wired
    directly. Keeps hydration self-contained.
    """
    today = datetime.now(UTC).date().isoformat()
    parts: list[str] = [f"# Boot Briefing — {agent} — {today}"]

    if deadlines:
        parts.append(f"\n## Deadlines ({len(deadlines)})")
        for d in deadlines[:10]:
            dl = d.get("deadline_date", "?")
            name = d.get("deadline_name", "?")
            matter = d.get("matter_name", "")
            parts.append(f"- **{dl}** — {name}" + (f" ({matter})" if matter else ""))

    if unread_count > 0:
        slugs = ", ".join(t.get("slug", t.get("id", "?")) for t in unread_threads[:10])
        parts.append(f"\n## Agent Bus — {unread_count} unread")
        if slugs:
            parts.append(f"Threads with unread: {slugs}")

    if review_total > 0:
        parts.append(f"\n## Review Queue — {review_total} item(s)")

    if sessions:
        last = sessions[0]
        parts.append(
            f"\n## Last Session — {last.get('agent', '?')} "
            f"({last.get('timestamp', '?')})"
        )
        summary = (last.get("summary") or "")[:300]
        if summary:
            parts.append(summary)
        open_items = last.get("open_items") or []
        if isinstance(open_items, list) and open_items:
            parts.append(f"**Open items** ({len(open_items)}):")
            for item in open_items[:5]:
                parts.append(f"- {item}")
            if len(open_items) > 5:
                parts.append(f"- ...{len(open_items) - 5} more")

    if todos:
        parts.append(f"\n## Todos — {len(todos)} open")
        for t in todos[:10]:
            tid = t.get("id", "?")
            priority = t.get("priority", "")
            p_tag = f" [{priority}]" if priority else ""
            title = t.get("title") or t.get("name", "")
            parts.append(f"- `{tid}`{p_tag} {title}")

    if self_reflections:
        parts.append(f"\n## Your Notes ({len(self_reflections)})")
        for a in self_reflections[:5]:
            claim = (a.get("claim") or "")[:200]
            if claim:
                parts.append(f"- {claim}")

    return "\n".join(parts)


async def hydrate_agent(
    agent: str,
    transcript_id: str | None = None,
) -> HydrationBundle:
    """Fetch the dispatched agent's boot state and render a briefing card.

    Parallel fetches: session-journals, deadlines, unread bus turns, threads,
    todos, self-assertions, optional transcript continuation. Any individual
    fetch failure is absorbed — the briefing simply omits that section.
    """
    profile = _DEFAULT_PROFILE

    # Query parameters for per-agent scoping.
    session_qs = urlencode({"limit": profile["session_limit"]})
    unread_qs = urlencode(
        {"to": agent, "unread": "true", "last": 10, "compact": "true"}
    )
    todo_qs = urlencode({"limit": 15})

    normalized_agent = agent.replace("-", "_")
    tasks: dict[str, asyncio.Task[Any]] = {
        "sessions": asyncio.create_task(_cortex_get(f"/session-journals?{session_qs}")),
        "threads": asyncio.create_task(_bus_get("/threads?status=active")),
        "unread_turns": asyncio.create_task(_bus_get(f"/turns?{unread_qs}")),
        "todos": asyncio.create_task(_cortex_get(f"/boot-todos?{todo_qs}")),
        "agent_entity": asyncio.create_task(
            _cortex_get(f"/entities/ai_agent:{quote(agent.replace('_', '-'), safe='')}")
        ),
    }
    if profile["include_deadlines"]:
        tasks["deadlines"] = asyncio.create_task(_cortex_get("/deadlines"))
    if profile["include_review_queue"]:
        tasks["staging"] = asyncio.create_task(
            _cortex_get("/staging?status=pending&limit=5")
        )

    self_entity = _SELF_ENTITY.get(normalized_agent)
    if self_entity:
        refl_qs = urlencode(
            {
                "entity_id": self_entity,
                "superseded": "false",
                "limit": profile["self_reflections_limit"],
            }
        )
        tasks["self_reflections"] = asyncio.create_task(
            _cortex_get(f"/assertions?{refl_qs}")
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
    deadlines = _safe_list(raw.get("deadlines"))
    threads = _safe_list(raw.get("threads"), "threads")
    unread_turns = _safe_list(raw.get("unread_turns"), "turns")
    todos = _safe_list(raw.get("todos"))
    staging = _safe_list(raw.get("staging"))
    self_reflections = _safe_list(raw.get("self_reflections"))
    agent_meta = _parse_agent_meta(raw.get("agent_entity"))

    unread_threads = [
        t for t in threads if isinstance(t, dict) and t.get("unread_count", 0) > 0
    ]

    briefing = _render_briefing(
        agent,
        sessions=sessions,
        deadlines=deadlines,
        todos=todos,
        unread_count=len(unread_turns),
        unread_threads=unread_threads,
        self_reflections=self_reflections,
        review_total=len(staging),
    )

    section_counts = {
        "briefing_bytes": len(briefing),
        "sessions": len(sessions),
        "deadlines": len(deadlines),
        "todos": len(todos),
        "unread_turns": len(unread_turns),
        "unread_threads": len(unread_threads),
        "review_queue": len(staging),
        "self_reflections": len(self_reflections),
    }

    return HydrationBundle(
        briefing_card_md=briefing,
        continuation_md=continuation_md,
        continuation_id=continuation_id,
        section_counts=section_counts,
        agent_meta=agent_meta,
    )
