"""Render helpers and fetch-profile constants for dispatched-agent hydration.

Extracted from hydration.py to keep that module under the 400-line SLOC
limit. Callers import ``_PROFILES``, ``_safe_list``, ``_as_str_list``,
``_as_optional_str_list``, and ``_render_briefing`` from here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

_LA = ZoneInfo("America/Los_Angeles")

# Boot profiles for dispatched-agent hydration. Mirrors _BOOT_PROFILES in
# cortex_named_tools.py but scoped to what the dispatched-agent briefing needs
# (no cursor-specific knobs).
#
# ``default`` — full briefing for full-context dispatches
# ``light``   — lightweight briefing for team_dispatch / frontier_dispatch(agent=...)
#               soft boot. Drops deadlines + review-queue fetches (latency win
#               beyond token reduction — the include_* gates in hydrate_agent
#               short-circuit the fetches entirely, not just rendering).
#               self_reflections_limit kept at 3 as a floor — they encode the
#               persona's "how I work as me" memory and drive consult quality
#               more than any other section. Do not strip below 3.
_DEFAULT_PROFILE: dict[str, Any] = {
    "include_deadlines": True,
    "include_review_queue": True,
    "session_limit": 3,
    "self_reflections_limit": 5,
}

_LIGHT_PROFILE: dict[str, Any] = {
    "include_deadlines": False,
    "include_review_queue": False,
    "session_limit": 1,
    "self_reflections_limit": 3,
}

PROFILES: dict[str, dict[str, Any]] = {
    "default": _DEFAULT_PROFILE,
    "light": _LIGHT_PROFILE,
}


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
    skills: list[dict[str, Any]],
    inline_only: bool = False,
) -> str:
    """Render the compact briefing card for the dispatched agent.

    Intentionally simpler than MCP's ``render_briefing_card`` — the pipeline
    handler does not need the full MCP UI affordances (manifest hints,
    fetch-hint code snippets) because the agent has the team toolset wired
    directly. Keeps hydration self-contained.
    """
    today = datetime.now(UTC).astimezone(_LA)
    parts: list[str] = [
        f"# Boot Briefing — {agent} — {today.strftime('%Y-%m-%dT%H:%M:%S%z')}",
    ]

    if skills:
        if inline_only:
            # No tool loop — suppress the fs(...) read instruction so the model
            # is not given a false affordance. Skills are listed by name only;
            # trigger descriptors still help the persona decide what it would
            # ask the dispatching agent to fetch.
            parts.append(
                "\n## Agent Skills "
                "(reference only — no tool loop this invocation; "
                "request the body from the dispatching agent if needed)",
            )
        else:
            parts.append(
                "\n## Agent Skills "
                "(read on trigger match — "
                "`fs(sandbox='cortex', op='read', "
                "path='agent-skills/<NAME>.md')`)",
            )
        for s in skills:
            slug = s.get("name") or (s.get("id") or "?").removeprefix("agent_skill:")
            trigger = (s.get("description") or "").strip()
            parts.append(f"- **{slug}** — {trigger}")

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
            f"({last.get('timestamp', '?')})",
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
