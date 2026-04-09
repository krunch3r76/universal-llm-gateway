"""Boot context assembly for frontier generation tools.

Handles boot level normalization, system prompt composition, cortex boot
integration, and subagent preamble/grounding guard constants.
"""

from __future__ import annotations

from ._file_helpers import read_file_result
from .cortex_named_tools import run_cortex_boot

_BOOT_SEPARATOR = "\n\n---\n\n"
_VALID_BOOT_LEVELS = {"none", "mcp", "minimal", "full", "team"}

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


SUBAGENT_PREAMBLE = (
    """\
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

"""
    + CORTEX_TOOL_QUICKREF
)

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


def _read_boot_ref(boot_ref: str) -> str:
    if not boot_ref.startswith("notes/"):
        raise ValueError("boot_ref must point into the notes/ tree")
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


def assemble_boot_context(boot: str, boot_ref: str | None) -> str:
    """Build the full boot context string from boot level and optional ref."""
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
    if boot_level == "team":
        parts: list[str] = [SUBAGENT_PREAMBLE]
        if boot_ref:
            parts.append(_read_boot_ref(boot_ref))
        return _BOOT_SEPARATOR.join(parts)
    result = run_cortex_boot(agent="subagent")
    if "error" in result:
        raise RuntimeError(str(result["error"]))
    narrative = result.get("boot_narrative")
    if not isinstance(narrative, str) or not narrative.strip():
        raise RuntimeError("cortex_boot returned no boot_narrative")
    full_parts: list[str] = [SUBAGENT_PREAMBLE]
    if boot_ref:
        full_parts.append(_read_boot_ref(boot_ref))
    full_parts.append(narrative)
    return _BOOT_SEPARATOR.join(full_parts)
