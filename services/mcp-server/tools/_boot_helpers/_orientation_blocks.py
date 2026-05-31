"""Orientation blocks for the boot briefing card.

Renders the operator-approved "Dispatch & Consult" capability-axis block and
the co-located "Liveness" block, emitted ABOVE the skills list by
``render_briefing_card()``.

This module is the DURABLE HOME for the liveness content
(``todo:dispatch-surface-orientation-fix`` Part 2 / 2a). It deliberately lives
in renderer SOURCE rather than a rendered ``operational-context-*.md``, because
``render_operational_context`` recomposes that file on every boot and clobbers
manual edits (assertion 11520). Renderer source survives the next boot.

Block text is operator-approved (2026-05-31); the grok model string is
``xai/grok-4.3`` per operator. Spec:
``cortex:notes/system/threads/part2-cortex-boot-capability-axis-handoff.md`` and
``claude-web-dispatch-decision-table.md`` §4.
"""

from __future__ import annotations

# Operator-approved verbatim (2026-05-31). Do not paraphrase; the decision-table
# doc (§4) is reconciled to match this text. frontier_dispatch + team_dispatch
# are PRIMARY post-Part-1; advisor/agent_consult/pipeline_consult stay overflow.
_DISPATCH_CONSULT_BLOCK = """\
## Dispatch & Consult — pick by CAPABILITY, not model family
To consult a MODEL (any provider, incl. grok) you do NOT use grokbuild.
frontier_dispatch + team_dispatch are PRIMARY (loaded at boot — call directly, no dispatch step). Model strings = provider/model (bare name = 404).
- consult any model, one-shot       → frontier_dispatch (op=generate, model="provider/model": openai/gpt-5.5, xai/grok-4.3, anthropic/claude-opus-4-7)
- by role (reviewer/artisan/seat)   → team_dispatch (op=generate, role=…)
- stronger-model strategic advice   → advisor (problem)                       [overflow]
- multi-model advisory + cortex/RAG → agent_consult (query)                   [overflow]
- RAG advice inside a pipeline      → pipeline_consult (execution_id, step_name, problem)  [overflow]
- cheap close-to-code build         → grokbuild (op=build, mode=edit)  [build executor, NOT a model picker; cheap while sub sunk ~2wk; ≈cursorbuild soon]
- run a named pipeline              → pipeline (op=run|async)
⚠ grokbuild = build executor, not a model picker. "Want a grok answer" → frontier_dispatch xai/grok-4.3, never grokbuild.
Full shapes: reference:claude-web-lead-seat-surface → claude-web-dispatch-decision-table.md"""

# Co-located liveness block (2a durable home). Full substrate×load×probe table
# kept (block ≈2KB, high-value) per the handoff; three-question redirect and
# salience-trap line are mandatory and present.
_LIVENESS_BLOCK = """\
## Liveness — the running process is the source of truth (commit-decoupled)
A change is LIVE only when LOADED into the running process at its last deploy/restart. Git commit/master is neither necessary nor sufficient. (operator: "we never need to commit to main; committing doesn't guarantee live processes are synced.")
Before claiming a surface changed, ask three questions — do NOT read git for this:
  1. WHICH substrate?   2. Did its LOAD EVENT fire?   3. What does the LIVE PROBE say?
| substrate               | live =                          | load event                       | probe |
| service behavior        | running container image         | sync_restart / rebuild           | observability · boot_inspect · a real request |
| MCP tool surface        | server's registered primary set | mcp restart + descriptor refresh | tool_search · invocation |
| routing + model catalog | canonical.yaml baked at restart | sync_restart                     | /v1/models |
| agent-context (you)     | rules loaded at session boot    | cortex_boot                      | this card · gen-rules --check |
⚠ Salience trap: "commit" is the loudest done/durable signal, so it gets grabbed as a liveness proxy under load. It is not one. Verify against the load event + probe, never the tree."""


def render_orientation_blocks() -> list[str]:
    """Return the capability-axis + liveness orientation blocks as card parts.

    Emitted above the skills list by ``render_briefing_card()``. Each element
    carries a leading newline so the card's ``"\\n".join(parts)`` produces a
    blank-line separator consistent with the other sections.
    """
    return [f"\n{_DISPATCH_CONSULT_BLOCK}", f"\n{_LIVENESS_BLOCK}"]
