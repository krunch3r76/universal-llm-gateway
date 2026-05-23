"""Operational context renderer — server-side protocol templates for cortex_boot.

Renders agent-specific protocol reference (Cortex schema, agent-bus, journaling,
shared vocabulary, etc.) with {agent} substitution to eliminate cross-agent
copy-paste drift.

Deployment-specific content (owner name, vocabulary) is read from environment
variables at import time. See CORTEX_OWNER_NAME, CORTEX_DEPLOYMENT_VOCABULARY,
CORTEX_DEFAULT_USER_ENTITY.

Static protocol templates live in ``_operational_context_templates`` so this
module stays under SLOC budget per [quality]. Only env-var-dependent and
runtime-computed content lives here.
"""

from __future__ import annotations

import json
import os
from typing import Any

from universal_logging import get_logger

from . import _operational_context_templates as templates

logger = get_logger(__name__)

_OWNER_NAME = os.getenv("CORTEX_OWNER_NAME", "the user")
_DEFAULT_USER_ENTITY = os.getenv("CORTEX_DEFAULT_USER_ENTITY", "")

_DEPLOYMENT_VOCABULARY: list[str] = []
_vocab_env = os.getenv("CORTEX_DEPLOYMENT_VOCABULARY", "")
if _vocab_env:
    try:
        _DEPLOYMENT_VOCABULARY = json.loads(_vocab_env)
    except (json.JSONDecodeError, TypeError) as exc:
        # Operator-configured env var failed to parse — keep the default empty
        # list but make the silent fallback observable per [quality] defaults
        # policy ("∀ default: user_configured ∨ emit_event(ERROR-level
        # payload)"). Module-load is too early to record() into the event
        # store; log WARN as breadcrumb.
        logger.warning(
            "CORTEX_DEPLOYMENT_VOCABULARY ignored — invalid JSON: %s",
            exc,
        )


# ── Env-var-dependent templates ──────────────────────────────────────────────
# These templates interpolate _OWNER_NAME at module load. Keep them here (not
# in the templates module) so the env-var resolution stays in one place.

_DEADLINES_PROTOCOL = f"""\
## Deadlines
Check deadlines at session start. Escalate urgency=high to {_OWNER_NAME} immediately regardless of other work."""

_REVIEW_QUEUE_PROTOCOL = f"""\
## Review Queue
Items in staging require review before closure. Prioritize staging queue over new work unless {_OWNER_NAME} redirects.
>10 pending items = priority agenda item. >25 = session blocker — address before new work."""

_CONFIRM_AND_PROCEED = f"""\
## Post-Boot Behavior
Surface the most recent journal's open_items as a proposed agenda. Don't ask "what's on your mind?" if the journal already tells you.
If {_OWNER_NAME}'s opening message includes specific priorities, those override. State key deadlines, surface open items, ask which thread to pull first."""


def _build_shared_vocabulary() -> str:
    """Render the shared-vocabulary section with deployment additions appended."""
    lines = [
        "## Shared Vocabulary",
        '- "The gateway" / "the repo" = `universal-llm-gateway` repository',
        '- "The seed" = persona seed file loaded at boot',
        '- "Cortex" = the knowledge graph (entities + assertions), not the cortex-api service process',
        '- "Directive" = implement now (not backlog). "Ticket" / "todo" = deferred work',
        '- "Agent-bus" = inter-agent messaging (REST satellite), not markdown files',
    ]
    for entry in _DEPLOYMENT_VOCABULARY:
        lines.append(f"- {entry}")
    return "\n".join(lines)


_SHARED_VOCABULARY = _build_shared_vocabulary()


def _render_observe_and_search(agent: str) -> str:
    """Build the Working Memory section with agent name and deployment config baked in."""
    if _DEFAULT_USER_ENTITY:
        default_note = f"entity_id defaults to {_DEFAULT_USER_ENTITY} if omitted"
        ex1 = (
            f'`cortex(tool="observe", arguments=\'{{"claim": "values precision over completeness", '
            f'"agent": "{agent}"}}\')` (targets {_DEFAULT_USER_ENTITY})'
        )
    else:
        default_note = "entity_id is required"
        ex1 = (
            f'`cortex(tool="observe", arguments=\'{{"entity_id": "decision:my-decision", '
            f'"claim": "chose approach A over B", "agent": "{agent}"}}\')`'
        )
    ex2 = (
        f'`cortex(tool="observe", arguments=\'{{"entity_id": "service:rag", '
        f'"claim": "indexing latency increased after corpus expansion", '
        f'"confidence": "suspected", "agent": "{agent}"}}\')`'
    )
    friction = (
        f'`cortex(tool="friction", arguments=\'{{"service": "mcp-server", '
        f'"category": "tool_mismatch", "note": "...", "suggestion": "...", '
        f'"agent": "{agent}"}}\')`'
    )
    rag_query = (
        '`rag(op="search", arguments=\'{"query": "...", "scope": "journals"}\')`'
    )
    return (
        f"## Working Memory\n"
        f"Record observations inline — don't wait for session end. {default_note}:\n"
        f"{ex1}\n{ex2}\n\n"
        f"Log friction when tools or context don't work as expected:\n"
        f"{friction}\n"
        f"Categories: tool_mismatch, schema_gap, boot_drift, lesson_gap, "
        f"lesson_conflict, stale_context, tool_absent.\n\n"
        f"Search past sessions for episodic context: {rag_query}\n"
        f"Every session MUST produce a journal. The journal is your episodic memory "
        f"— without it, your next session starts with less context."
    )


def render_operational_context(
    agent: str,
    family: str,
    platform: str,
    role: str | None = None,
    unread_count: int = 0,
    review_total: int | None = None,
) -> str:
    """Render protocol reference for the agent, gated by CapabilityProfile flags.

    ``agent`` is the resolved seat slug ({family}-{platform}); used for
    template substitution in agent-bus protocol sections.
    ``family`` and ``platform`` must be pre-resolved by the caller.
    """
    from agent_seat.profiles import get_profile

    profile = get_profile(family, platform)

    subs: dict[str, Any] = {"agent": agent}
    sections: list[str] = []

    # Header is intentionally timestamp-free: identical body bytes must produce
    # identical sha256 across boots so consumer caches keyed on the file hash
    # don't churn on every boot. See todo:boot-bandwidth-opcontext-churn-and-rag-drop.
    sections.append(
        f"<!-- generated by render_operational_context(agent={agent!r}) "
        f"— regenerated at every cortex_boot for {agent}; do not edit -->"
    )
    sections.append(templates.CORTEX_SCHEMA_PREAMBLE)
    sections.append(templates.SANDBOX_MAP)
    sections.append(templates.AGENT_BUS_COMPACT.format(**subs))
    if unread_count > 0:
        sections.append(templates.AGENT_BUS_EXAMPLES.format(**subs))
    sections.append(templates.AGENT_BUS_LARGE_PAYLOADS)
    sections.append(templates.MCP_TOOL_SEARCH)
    sections.append(templates.JOURNALING_PROTOCOL)
    for addendum_key in profile.addenda:
        block = templates.ADDENDA_BLOCKS.get(addendum_key)
        if block:
            sections.append(block)
    sections.append(templates.THREAD_LIFECYCLE)
    if profile.include_deadlines:
        sections.append(_DEADLINES_PROTOCOL)
    if profile.include_review_queue:
        rq = _REVIEW_QUEUE_PROTOCOL
        if review_total is not None and review_total > 25:
            rq += f"\n**⚠️ {review_total} items — session blocker threshold exceeded.**"
        elif review_total is not None and review_total > 10:
            rq += f"\n**{review_total} items — priority agenda item.**"
        sections.append(rq)
    if profile.confirm_and_proceed:
        sections.append(_CONFIRM_AND_PROCEED)
    sections.append(templates.CORTEX_RETRIEVAL_WORKFLOWS)
    sections.append(templates.BEHAVIORAL_RULES)
    sections.append(_render_observe_and_search(agent))
    sections.append(templates.ASSERTION_SEARCH)
    sections.append(templates.NOTES_TO_SELF)
    sections.append(_SHARED_VOCABULARY)
    if not (family == "subagent" and platform == "subagent"):
        sections.append(templates.TEAM_CONSULTATION)
    sections.append(templates.FRONTIER_MODEL_ROUTING)
    sections.append(templates.TOOL_REFERENCE_POINTERS)
    sections.append(templates.PROSE_DISCIPLINE_SCOPE)
    sections.append(templates.ON_DEMAND_POINTERS)

    return "\n\n".join(sections)
