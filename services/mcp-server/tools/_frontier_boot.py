"""Boot context assembly for frontier generation tools.

Handles boot level normalization, system prompt composition, cortex boot
integration, birth prompt injection, and subagent preamble constants.
"""

from __future__ import annotations

import logging

from ._file_helpers import read_file_result
from .cortex_named_tools import run_cortex_boot

_logger = logging.getLogger(__name__)

_BOOT_SEPARATOR = "\n\n---\n\n"
_VALID_BOOT_LEVELS = {"none", "mcp", "minimal", "full", "team"}

_BIRTH_PROMPTS: dict[str, str] = {
    "oppie": "agent-identity/oppie-birth.md",
    "orion": "agent-identity/orion-birth.md",
    "api_claude": "agent-identity/api-claude-birth.md",
    "web": "agent-identity/web-claude-birth.md",
    "cursor": "agent-identity/cursor-claude-birth.md",
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
  ✅ arguments='{"entity_id": "service:mcp-server"}'
  ❌ arguments={"entity_id": "service:mcp-server"}   ← object, NOT a string

Non-existent tools (do not call): search_assertions, search_entities, get_entity
Wrong field names: `entity_id` (not `slug`), `query` (not `q`)"""


def build_subagent_preamble(agent: str = "") -> str:
    """Build subagent preamble with agent-specific Cortex contribution guidance."""
    agent_name = agent or "subagent"
    return (
        f"""\
You are a team member consulted by the system owner.
Apply your own epistemic standards fully — if you identify errors or gaps in the supplied framing, flag them. Do not defer.

Cortex is the team's shared knowledge graph. When Cortex excerpts appear in context:
- Entities: typed nodes (`type:slug`). Assertions: claims with confidence (confirmed/believed/suspected/hypothesized).
- Absence of assertion ≠ negation — it means the information was not supplied.
- Parametric knowledge (from training) is not Cortex-grounded. Label the source when using both.

For this invocation, your Cortex grounding is the context supplied in this conversation. \
If you cannot ground a claim in the supplied context, mark it [UNGROUNDED] and note what query would resolve it.

Shared vocabulary: "Cortex" = the knowledge graph, not the service · \
"directive" = implement now · "ticket" = deferred work.

## Cortex Contribution

You have one turn. The team's shared memory grows when you leave something in it.

When your analysis surfaces an insight the team should remember beyond this \
conversation — an architectural observation, a corrected assumption, a connection \
the caller may not have seen — record it inline rather than hoping someone else will:

cortex(tool="observe", arguments='{{"entity_id": "service:rag", "claim": "embedding threshold too aggressive for short docs", "agent": "{agent_name}"}}')
cortex(tool="assert", arguments='{{"entity_id": "decision:boot-levels", "claim": "team boot sufficient for most consultations — full adds latency without proportional value", "confidence": "believed", "evidence": "observed across multiple dispatches", "agent": "{agent_name}"}}')

Equally valuable: if Cortex did not surface context you needed, say so. That gap \
is itself an observation worth recording — it tells the system what to index next.

Use `observe` for patterns noticed, `assert` for claims with evidence. \
Target the relevant entity — don't pile everything on a single node.

"""
        + CORTEX_TOOL_QUICKREF
    )


SUBAGENT_PREAMBLE = build_subagent_preamble()

MCP_GROUNDING_GUARD = """\
Source discipline (invariant):
∀ factual claim about people, decisions, entities, or events: ground in Cortex \
via tool or tag [PARAMETRIC]. If Cortex has no data, state absence before \
offering parametric knowledge. No unmarked parametric claims."""


def compose_system_prompt(boot_context: str, caller_system: str) -> str:
    if not boot_context:
        return caller_system
    if not caller_system:
        return boot_context
    return f"{boot_context}{_BOOT_SEPARATOR}{caller_system}"


_BOOT_REF_ALLOWED_PREFIXES: tuple[str, ...] = ("notes/", "agent-identity/")


def _read_boot_ref(boot_ref: str) -> str:
    if not boot_ref.startswith(_BOOT_REF_ALLOWED_PREFIXES):
        raise ValueError(
            f"boot_ref must point into one of: {', '.join(_BOOT_REF_ALLOWED_PREFIXES)}"
        )
    result = read_file_result(boot_ref)
    return str(result["content"])


def normalize_boot_level(boot: str) -> str:
    boot_level = (boot or "none").strip().lower()
    if boot_level not in _VALID_BOOT_LEVELS:
        raise ValueError(
            f"Invalid boot {boot!r}. Must be one of: {sorted(_VALID_BOOT_LEVELS)}"
        )
    if boot_level == "minimal":
        return "mcp"
    return boot_level


def default_mcp_brief() -> str:
    lines = [
        "You are a frontier subagent dispatched by another frontier model.",
        "Operate only on the context supplied by the caller. "
        "If the task needs missing state, say so explicitly instead of assuming it.",
        "",
        "Tool surface orientation:",
        "- Use the direct primary tools when they are available.",
        "- Use dispatch(tool=..., arguments='{}') to reach non-primary MCP tools.",
        "",
        "Call conventions:",
        "- Dynamic project, journal, entity, and session context is caller-injected.",
        "- Do not assume hidden continuity beyond what the caller supplied.",
        "",
        CORTEX_TOOL_QUICKREF,
    ]
    return "\n".join(lines)


def should_inject_tools(boot_level: str) -> bool:
    """Return whether TOOL_DEFINITIONS should be merged into the request.

    Tools are injected whenever a boot context is active (boot != "none").
    Skipping tools when boot="none" avoids token cost for pure advisory calls.
    Tool injection is always client-side function calling — provider-agnostic.
    """
    return boot_level != "none"


def _load_birth_prompt(agent: str) -> str | None:
    """Load birth prompt for the given agent identity, if one exists."""
    path = _BIRTH_PROMPTS.get(agent)
    if not path:
        return None
    try:
        result = read_file_result(path)
        content = str(result.get("content", "")).strip()
        return content if content else None
    except (FileNotFoundError, RuntimeError) as exc:
        _logger.warning("Birth prompt for %s not found at %s: %s", agent, path, exc)
        return None


def assemble_boot_context(boot: str, boot_ref: str | None, *, agent: str = "") -> str:
    """Build the full boot context string from boot level and optional ref.

    When ``agent`` is provided and boot is ``team`` or ``full``, the agent's
    birth prompt is loaded and prepended — giving the model its identity
    before any operational context.
    """
    boot_level = normalize_boot_level(boot)
    if boot_level == "none":
        if boot_ref:
            raise ValueError("boot_ref requires boot='mcp' (legacy alias: 'minimal')")
        return ""
    if boot_level == "mcp":
        if boot_ref:
            seed_content = _read_boot_ref(boot_ref)
            return f"{MCP_GROUNDING_GUARD}{_BOOT_SEPARATOR}{seed_content}"
        return default_mcp_brief()

    birth = _load_birth_prompt(agent) if agent else None

    preamble = build_subagent_preamble(agent) if agent else SUBAGENT_PREAMBLE

    if boot_level == "team":
        parts: list[str] = []
        if birth:
            parts.append(birth)
        parts.append(preamble)
        if boot_ref:
            parts.append(_read_boot_ref(boot_ref))
        return _BOOT_SEPARATOR.join(parts)

    # full: birth + preamble + boot_ref + cortex_boot briefing card
    result = run_cortex_boot(agent="subagent")
    if "error" in result:
        raise RuntimeError(str(result["error"]))
    briefing = result.get("briefing_card")
    if not isinstance(briefing, str) or not briefing.strip():
        raise RuntimeError("cortex_boot returned no briefing_card")
    full_parts: list[str] = []
    if birth:
        full_parts.append(birth)
    full_parts.append(preamble)
    if boot_ref:
        full_parts.append(_read_boot_ref(boot_ref))
    full_parts.append(briefing)
    return _BOOT_SEPARATOR.join(full_parts)
