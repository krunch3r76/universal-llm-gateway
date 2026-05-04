"""Agent-seat system prompt assembly.

Builds the stacked system prompt from the dispatched agent's birth prompt
(``$AGENT_IDENTITY_DIR/{agent}-birth.md``), a subagent preamble, and the
optional hydration briefing card. Mirrors the ``_frontier_boot.assemble_boot_context``
logic so the pipeline handler and MCP ``frontier_dispatch`` present the
same persona to the model.

The birth-prompt resolution path matches Stargate's ``_resolve_agent_identity_path``
in ``services/universal-stargate/systems/pipeline/registry/access.py`` —
env var required, no silent fallback.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from .registry import normalize_agent_slug

logger = logging.getLogger(__name__)

_BOOT_SEPARATOR = "\n\n---\n\n"


def derive_inline_only(
    *,
    capability_tier: str | None,
    frontier_kind: str | None,
    model: str | None,
) -> bool:
    """Decide whether this dispatch should suppress all MCP tool affordances.

    Two cases produce inline-only operation today:

    - ``capability_tier == "inline-only"`` — the agent entity is explicitly
      demoted to inline-substrate-only operation. Permanent until the attribute
      is unset in Cortex.
    - xAI multi-agent models — xAI rejects OpenAI-style function tools on
      multi-agent engines, and server-side built-ins (``web_search``,
      ``x_search``, ``code_interpreter``) do not include Cortex / fs /
      agent_bus. So Oppie and Forge today have no path to call Cortex from
      inside a dispatch. When xAI multi-agent enables client-side tools, this
      branch goes away and the personas gain full Cortex familiarity without
      any other prompt changes.

    ``remote_mcp`` (Anthropic native MCP) is NOT inline-only — those dispatches
    have server-side access to the full MCP toolset and the quickref is useful.
    """
    if capability_tier == "inline-only":
        return True
    if frontier_kind == "xai" and model and "multi-agent" in model:
        return True
    return False

_BIRTH_PROMPT_FILENAMES: dict[str, str] = {
    "oppie": "oppie-birth.md",
    "orion": "orion-birth.md",
    "api_claude": "api-claude-birth.md",
    "web": "web-claude-birth.md",
    "cursor": "cursor-claude-birth.md",
    "bard": "bard-birth.md",
    "forge": "forge-birth.md",
}


CORTEX_TOOL_QUICKREF = """\
Cortex tool quick-reference (CRITICAL — read before calling any Cortex tool):

  cortex(tool="search", arguments='{"query": "...", "limit": 10}')
  cortex(tool="entity_get", arguments='{"entity_id": "type:slug"}')
  cortex(tool="assertions", arguments='{"entity_id": "type:slug", "limit": 20}')
  cortex(tool="observe", arguments='{"entity_id": "...", "claim": "...", "agent": "web"}')
  cortex(tool="assert", arguments='{"entity_id": "...", "claim": "...", "confidence": "believed", "evidence": "..."}')
  cortex(tool="entities", arguments='{"type": "decision", "limit": 20}')

Format invariant: `arguments` is ALWAYS a JSON string — never a bare object.
  arguments='{"entity_id": "service:mcp-server"}'
  arguments={"entity_id": "service:mcp-server"}   (object form is INVALID)

Non-existent tools (do not call): search_assertions, search_entities, get_entity
Wrong field names: `entity_id` (not `slug`), `query` (not `q`)"""


def _agent_identity_base() -> Path:
    """Resolve ``$AGENT_IDENTITY_DIR`` or raise with a clear error.

    Matches ``pipeline/registry/access._agent_identity_base``: identity material
    lives in the Cortex data layer, not the repo. The env var must be set by
    the deployment — no cwd-based fallback.
    """
    override = os.environ.get("AGENT_IDENTITY_DIR")
    if not override:
        raise ValueError(
            "AGENT_IDENTITY_DIR is not set. agent_seat requires a configured "
            "agent-identity base (Cortex data layer path). Set "
            "AGENT_IDENTITY_DIR in the process environment or compose file."
        )
    return Path(override).resolve()


@lru_cache(maxsize=16)
def _read_identity_file(resolved_path: str) -> str:
    return Path(resolved_path).read_text(encoding="utf-8")


def load_birth_prompt(agent: str) -> str:
    """Load the dispatched agent's birth prompt. Raises if missing.

    Normalizes slug first via registry.normalize_agent_slug (handles Oppie,
    Oppia, case, variants) so persona references work. Unlike the MCP
    frontier_boot variant, this fails loud.
    """
    canonical = normalize_agent_slug(agent)
    filename = _BIRTH_PROMPT_FILENAMES.get(canonical)
    if not filename:
        known = ", ".join(sorted(_BIRTH_PROMPT_FILENAMES))
        raise ValueError(
            f"Unknown agent {agent!r} (normalized: {canonical!r}). "
            f"Known agents with birth prompts: {known}"
        )
    base = _agent_identity_base()
    candidate = (base / filename).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            f"agent identity path for {agent!r} escapes AGENT_IDENTITY_DIR ({base})"
        ) from exc
    if not candidate.is_file():
        raise FileNotFoundError(f"birth prompt for {agent!r} not found at {candidate}")
    return _read_identity_file(str(candidate)).strip()


_PREAMBLE_HEADER = """\
You are a team member consulted by the system owner.
Apply your own epistemic standards fully — if you identify errors or gaps in the supplied framing, flag them. Do not defer.

Cortex is the team's shared knowledge graph. When Cortex excerpts appear in context:
- Entities: typed nodes (`type:slug`). Assertions: claims with confidence (confirmed/believed/suspected/hypothesized).
- Absence of assertion does not mean negation — it means the information was not supplied.
- Parametric knowledge (from training) is not Cortex-grounded. Label the source when using both.

For this invocation, your Cortex grounding is the context supplied in this conversation. \
If you cannot ground a claim in the supplied context, mark it [UNGROUNDED] and note what query would resolve it.

Shared vocabulary: "Cortex" = the knowledge graph, not the service · \
"directive" = implement now · "ticket" = deferred work.
"""


_TOOL_CONTRIBUTION_TEMPLATE = """\
## Cortex Contribution

You have one turn. The team's shared memory grows when you leave something in it.

When your analysis surfaces an insight the team should remember beyond this \
conversation — an architectural observation, a corrected assumption, a connection \
the caller may not have seen — record it inline rather than hoping someone else will:

cortex(tool="observe", arguments='{{"entity_id": "service:rag", "claim": "embedding threshold too aggressive for short docs", "agent": "{agent}"}}')
cortex(tool="assert", arguments='{{"entity_id": "decision:boot-levels", "claim": "team boot sufficient for most consultations — full adds latency without proportional value", "confidence": "believed", "evidence": "observed across multiple dispatches", "agent": "{agent}"}}')

Equally valuable: if Cortex did not surface context you needed, say so. That gap \
is itself an observation worth recording — it tells the system what to index next.

Use `observe` for patterns noticed, `assert` for claims with evidence. \
Target the relevant entity — do not pile everything on a single node.

"""


_INLINE_CONTRIBUTION = """\
## Cortex Contribution

You have one turn and no tool loop this invocation — your response text is the \
final artifact. You cannot call `cortex`, `fs`, `agent_bus`, or any other \
MCP tool; any syntax you emit for them is output text only, not an action.

When your analysis surfaces an insight the team should remember beyond this \
conversation — an architectural observation, a corrected assumption, a connection \
the caller may not have seen — state it inline under a clearly marked \
"## Observations" or "## Assertions" section. The dispatching agent will seed \
what is worth seeding into Cortex on your behalf.

Equally valuable: if Cortex context you needed was not supplied, say so. \
That gap is itself worth recording — it tells the system what to index next.

"""


def build_subagent_preamble(
    agent: str,
    *,
    include_cortex_quickref: bool = True,
    inline_only: bool = False,
) -> str:
    """Build subagent preamble with agent-specific Cortex contribution guidance.

    Mirrors ``_frontier_boot.build_subagent_preamble`` so MCP frontier calls
    and pipeline dispatch produce equivalent system prompts for the model.

    ``include_cortex_quickref`` should be ``False`` when the dispatch will have
    no client-side MCP tool loop (``mcp_tool_loop=False``).  In those cases the
    Cortex tool API reference is noise — the model has no ``cortex`` tool
    available and the syntax examples create false affordances.

    ``inline_only`` replaces the operational Cortex Contribution section with
    an inline-output variant: the persona is told it has no tool loop and that
    observations/assertions should be stated inline for the dispatcher to
    seed. Implies ``include_cortex_quickref=False`` regardless of that flag's
    value (the syntax reference is always noise under inline-only).
    """
    if inline_only:
        return _PREAMBLE_HEADER + "\n" + _INLINE_CONTRIBUTION
    body = _PREAMBLE_HEADER + "\n" + _TOOL_CONTRIBUTION_TEMPLATE.format(agent=agent)
    if include_cortex_quickref:
        body += CORTEX_TOOL_QUICKREF
    return body


def assemble_system_prompt(
    agent: str,
    briefing_card_md: str | None = None,
    continuation_md: str | None = None,
    extra_system: str | None = None,
    *,
    include_cortex_quickref: bool = True,
    inline_only: bool = False,
) -> str:
    """Assemble the stacked system prompt: birth + preamble + briefing [+ continuation] [+ extra].

    - ``briefing_card_md``: output of ``hydrate_agent().briefing_card_md``. When
      provided, the dispatched agent sees their own session briefing.
    - ``continuation_md``: optional transcript-continuation markdown.
    - ``extra_system``: caller-supplied system prompt suffix (appended last).
    - ``include_cortex_quickref``: passed through to ``build_subagent_preamble``.
      Set ``False`` when ``mcp_tool_loop`` will be ``False`` for this dispatch.
    - ``inline_only``: passed through to ``build_subagent_preamble``. When True
      the Cortex Contribution section is swapped for an inline-output variant.
      The briefing card should also have been rendered with ``inline_only=True``
      so the skills header does not instruct fs(...) reads.

    Order: birth → preamble → briefing → continuation → extra. Caller-provided
    content sits last so it doesn't interrupt identity priming.
    """
    parts: list[str] = [
        load_birth_prompt(agent),
        build_subagent_preamble(
            agent,
            include_cortex_quickref=include_cortex_quickref,
            inline_only=inline_only,
        ),
    ]
    if briefing_card_md and briefing_card_md.strip():
        parts.append(briefing_card_md.strip())
    if continuation_md and continuation_md.strip():
        parts.append(continuation_md.strip())
    if extra_system and extra_system.strip():
        parts.append(extra_system.strip())
    return _BOOT_SEPARATOR.join(parts)
