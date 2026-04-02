"""Operational context renderer — server-side protocol templates for cortex_boot.

Renders agent-specific protocol reference (Cortex schema, agent-bus, journaling,
shared vocabulary, etc.) with {agent} substitution to eliminate cross-agent
copy-paste drift.

Deployment-specific content (owner name, vocabulary, persona seeds) is read from
environment variables at import time. See CORTEX_OWNER_NAME,
CORTEX_DEPLOYMENT_VOCABULARY, CORTEX_PERSONA_SEEDS, CORTEX_DEFAULT_USER_ENTITY.
"""

from __future__ import annotations

import json
import os
from typing import Any

_OWNER_NAME = os.getenv("CORTEX_OWNER_NAME", "the user")
_DEFAULT_USER_ENTITY = os.getenv("CORTEX_DEFAULT_USER_ENTITY", "")

_DEPLOYMENT_VOCABULARY: list[str] = []
_vocab_env = os.getenv("CORTEX_DEPLOYMENT_VOCABULARY", "")
if _vocab_env:
    try:
        _DEPLOYMENT_VOCABULARY = json.loads(_vocab_env)
    except (json.JSONDecodeError, TypeError):
        pass

_OPERATIONAL_FLAGS: dict[str, dict[str, bool]] = {
    "web": {"deadlines": True, "review_queue": True, "confirm_and_proceed": True},
    "cursor": {"deadlines": False, "review_queue": False, "confirm_and_proceed": False},
    "api": {"deadlines": True, "review_queue": False, "confirm_and_proceed": True},
    "oppie": {"deadlines": True, "review_queue": True, "confirm_and_proceed": True},
    "subagent": {
        "deadlines": False,
        "review_queue": False,
        "confirm_and_proceed": False,
    },
}

AGENT_PERSONA_SEEDS: dict[str, str] = {}
_seeds_env = os.getenv("CORTEX_PERSONA_SEEDS", "")
if _seeds_env:
    try:
        AGENT_PERSONA_SEEDS = json.loads(_seeds_env)
    except (json.JSONDecodeError, TypeError):
        pass

# ── Static protocol templates ───────────────────────────────────────────────

_CORTEX_SCHEMA_PREAMBLE = """\
## Cortex Model
Entities: typed nodes (person, decision, legal_matter, todo, document…) with canonical IDs (`type:slug`).
Assertions: claims attached to entities with confidence (confirmed/believed/suspected/hypothesized), evidence links, and source URIs.
Session edges: reasoning connections between entities, seeded during analysis.
Absence of assertion ≠ negation. Check `entity_get()` before assuming absence.
Confidence: confirmed = verified fact, believed = working assumption, suspected = pattern-based, hypothesized = theory under investigation.
Parametric knowledge (from training) is not Cortex-grounded. When using both, label the source explicitly. Prefer Cortex assertions over parametric claims when both exist.
Hold `session_id` (from boot response) for the entire session — pass it to every `edge_create` and `supersede` call."""

_SANDBOX_MAP = """\
## File Sandboxes
`fs(sandbox="files", …)` → user documents, notes, journals, prompts.
`fs(sandbox="project", …)` → source code — prefix path with repo name (e.g. `universal-llm-gateway/…`).
`fs(sandbox="context", …)` → tasks/, specs, lessons, discoveries."""

_AGENT_BUS_COMPACT = """\
## Agent Bus Protocol
Send: `agent_bus(tool="post", arguments='{{"slug": "topic", "to": "{agent}", "subject": "…", "body": "…"}}')`
Reply: `agent_bus(tool="reply", arguments='{{"thread": "ID", "to": "TARGET", "subject": "…", "body": "…", "after_turn": N, "from_agent": "{agent}"}}')`
Fetch inbox: `agent_bus(tool="fetch", arguments='{{"to": "{agent}", "last": 5, "unread": true}}')`
Always pass `mark_read: true` when fetching turns you intend to act on — stale unread counts create false urgency.
A *directive* means implement now. A *ticket* or *todo* means deferred work. Acknowledge receipt of directives before beginning."""

_AGENT_BUS_EXAMPLES = """\
### Replying to an unread turn
```
agent_bus(tool="reply", arguments='{{"thread": "THREAD_ID", "to": "TARGET", "subject": "Re: topic", "body": "Response text.", "after_turn": TURN_NUMBER, "from_agent": "{agent}"}}')
```
After implementing a work order, request confirmation from the requesting agent."""

_JOURNALING_PROTOCOL = """\
## Session Journaling
Every session MUST produce a journal. Write throughout, not just at the end.
File: `notes/system/journal/{journal_prefix}-YYYY-MM-DD-HHmm.md` via `fs(sandbox="files", op="write", …)`.
Row: `cortex(tool="journal_write", arguments='{{"agent": "{agent}", "summary": "…", "domains": ["…"], "decisions": ["…"], "open_items": ["…"]}}')`.

Template:
```
# {{Agent}} Session — YYYY-MM-DD HH:MM UTC
## Context
What prompted this session, continuation from what.
## Arc
Narrative of what happened.
## Decisions
Numbered, with reasoning (what was decided AND why, what was rejected).
## Observations
Behavioral/situational observations — also seed via observe().
## Open Items
Carried forward or newly created.
```
When: after significant work, before context switches, before ending."""

_THREAD_LIFECYCLE = """\
## Thread & Session Lifecycle
**Thread close**: (1) write thread summary, (2) seed Cortex assertions for decisions, (3) mark todos done.
**Session end**: (1) seed outstanding assertions, (2) reflect on session edges, (3) write session journal.
After implementing a work order from another agent, post a confirmation turn before closing."""

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

_TOOL_REFERENCE_POINTERS = """\
## Tool Reference
Full tool schemas: `fs(sandbox="files", op="read", path="notes/system/shared/mcp-tool-reference.md")`
Dispatch catalog: `fs(sandbox="files", op="read", path="notes/system/shared/tool-discovery.md")`
Edge protocol: entities only as edge nodes, never assertion IDs. `superseded_by` linkage is internal to the assertions table."""


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
    rag_search = (
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
        f"Search past sessions for episodic context: {rag_search}\n"
        f"Every session MUST produce a journal. The journal is your episodic memory "
        f"— without it, your next session starts with less context."
    )


_FRONTIER_MODEL_ROUTING = """\
## Frontier Model Routing
Two orthogonal axes — model selects capability, boot selects identity/context:

| Model | Capability | Default persona | Use when |
|---|---|---|---|
| `grok-4.20-multi-agent` | Multi-agent coordination | Oppie (auto) | Triad consultation, team coordination, multi-step orchestration |
| `grok-4.20` | Deep reasoning (CoT) | Neutral | Chain-of-thought analysis, complex reasoning without persona overhead |
| `grok-3-mini` | Fast advisory | Neutral | Quick checks via agent_consult, low-stakes validation |
| `claude-sonnet-4-6` (via claude_generate) | Deep synthesis | API Claude (auto) | Analytical work, evidence synthesis, structured extraction |

Boot axis: `none` (no context) · `mcp` (persona seed + MCP tools) · `team` (subagent preamble + seed) · `full` (boot + narrative).
Default to neutral reasoning models unless you specifically need a persona's team-lead context or tool mastery enforcement.
Any model can override the default persona with explicit `boot_ref`."""

_ON_DEMAND_POINTERS = """\
## On-Demand Modules (load when needed)
- Cortex full schema: `fs(sandbox="files", op="read", path="notes/system/cortex-spec-index.md")`
- Infrastructure session: `agent_bus(tool="threads", …)` + `cortex(tool="entities", arguments='{"type": "decision"}')` + open todos
- Frontier intelligence: `fs(sandbox="files", op="read", path="notes/system/shared/frontier-intelligence.md")`"""


def render_operational_context(
    agent: str,
    unread_count: int = 0,
    review_total: int | None = None,
) -> str:
    """Render protocol reference for the agent, conditionally gated by profile and state."""
    flags = _OPERATIONAL_FLAGS.get(agent, _OPERATIONAL_FLAGS["web"])
    subs: dict[str, Any] = {"agent": agent, "journal_prefix": agent}
    sections: list[str] = []

    sections.append(_CORTEX_SCHEMA_PREAMBLE)
    sections.append(_SANDBOX_MAP)
    sections.append(_AGENT_BUS_COMPACT.format(**subs))
    if unread_count > 0:
        sections.append(_AGENT_BUS_EXAMPLES.format(**subs))
    sections.append(_JOURNALING_PROTOCOL.format(**subs))
    sections.append(_THREAD_LIFECYCLE)
    if flags.get("deadlines"):
        sections.append(_DEADLINES_PROTOCOL)
    if flags.get("review_queue"):
        rq = _REVIEW_QUEUE_PROTOCOL
        if review_total is not None and review_total > 25:
            rq += f"\n**⚠️ {review_total} items — session blocker threshold exceeded.**"
        elif review_total is not None and review_total > 10:
            rq += f"\n**{review_total} items — priority agenda item.**"
        sections.append(rq)
    if flags.get("confirm_and_proceed"):
        sections.append(_CONFIRM_AND_PROCEED)
    sections.append(_render_observe_and_search(agent))
    sections.append(_SHARED_VOCABULARY)
    sections.append(_FRONTIER_MODEL_ROUTING)
    sections.append(_TOOL_REFERENCE_POINTERS)
    sections.append(_ON_DEMAND_POINTERS)

    return "\n\n".join(sections)
