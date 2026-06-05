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

# Surface-aware Dispatch & Consult blocks (operator-approved override 2026-06-01,
# thread 1167). frontier_dispatch/team_dispatch are PRIMARY/direct-call on BOTH
# surfaces after the standalone-domain re-land (thread 1146/1167):
#
#   - claude /mcp (mcp, mcp_claude): canonical.yaml now declares standalone
#     `frontier_dispatch`/`team_dispatch` DOMAINS (visibility mcp/mcp_claude), so
#     their tool_name enters _PRIMARY_TOOLS in _derive.derive_claude_manifest —
#     direct call, no dispatch step. (advisor/pipeline_consult are NOT promoted
#     → still OVERFLOW via dispatch(tool="…"). agent_consult removed 2026-06.)
#   - grok /mcp/grok (mcp_grok): _derive.derive_grok_manifest emits a FLAT
#     manifest where dispatch_frontier/dispatch_team are standalone tools — direct
#     call as well. The grok-serving dispatch_* entries no longer carry mcp_claude
#     (stripped in the re-land) so grok stays flat.
#
# NOTE: `cache_priority` in canonical.yaml is INERT (not consumed by derivation,
# per _derive.py). The lever that makes these primary on claude is the standalone
# DOMAIN, and it MUST stay committed — an uncommitted change reverts on rebuild
# (the Part-1 regression, 11528/11549). Source of truth: decision-table §2/§3/§4.

_DISPATCH_CONSULT_BLOCK_CLAUDE = """\
## Dispatch & Consult — pick by CAPABILITY, not model family
To consult a MODEL (any provider, incl. grok) you do NOT use a build harness.
On THIS surface (Anthropic /mcp) frontier_dispatch + team_dispatch are PRIMARY — call directly, no dispatch step. Model strings = provider/model (bare name = 404).
- consult any model, one-shot       → frontier_dispatch (op=generate, model="provider/model": openai/gpt-5.5, xai/grok-4.3, anthropic/claude-opus-4-8)  → returns execution_id; poll pipeline(op="result", execution_id=…)
- by API role (reviewer/artisan/…) → team_dispatch (op=generate, role=…) — ¬ role=claude-web|lead|web|claude-cursor|cursor-lead|cursor|implementer (422 web_seat_not_generate_target)
- to claude-web / lead / web-claude  → team_dispatch (op=handoff, role=…) → claude-web (operator push)
- to claude-cursor / cursor-lead / implementer → team_dispatch (op=handoff, role=…) → claude-cursor (open thread in IDE)
- consensus panel (≥2 families)     → panel_dispatch(messages=[…], dispatch_thread_id="…", disposition="panel")  [primary]
- stronger-model strategic advice   → dispatch(tool="advisor", arguments='{"problem":"…"}')                                  [overflow]
- RAG advice inside a pipeline      → dispatch(tool="pipeline_consult", arguments='{"execution_id":"…","step_name":"…","problem":"…"}')  [overflow]
- close-to-code build (multi-writer) → cursorbuild (forward harness; grokbuild retired 11588)
- run a named pipeline              → pipeline (op=run|async)
⚠ A build harness is not a model picker. "Want a grok answer" → frontier_dispatch model="xai/grok-4.3", never a build harness.
Full shapes: reference:claude-web-lead-seat-surface → claude-web-dispatch-decision-table.md"""

_DISPATCH_CONSULT_BLOCK_GROK = """\
## Dispatch & Consult — pick by CAPABILITY, not model family
To consult a MODEL (any provider, incl. grok) you do NOT use a build harness.
On THIS surface (/mcp/grok, flat catalog) frontier_dispatch + team_dispatch are PRIMARY — call directly, no dispatch step. Model strings = provider/model (bare name = 404).
- consult any model, one-shot       → frontier_dispatch (op=generate, model="provider/model": openai/gpt-5.5, xai/grok-4.3, anthropic/claude-opus-4-8)
- by API role (reviewer/artisan/…) → team_dispatch (op=generate, role=…) — ¬ role=claude-web|lead|web|claude-cursor|cursor-lead|cursor|implementer (422 web_seat_not_generate_target)
- to claude-web / lead / web-claude  → team_dispatch (op=handoff, role=…) → claude-web (operator push)
- to claude-cursor / cursor-lead / implementer → team_dispatch (op=handoff, role=…) → claude-cursor (open thread in IDE)
- consensus panel (≥2 families)     → panel_dispatch(messages=[…], dispatch_thread_id="…", disposition="panel")
- stronger-model strategic advice   → advisor (problem)                       [overflow]
- RAG advice inside a pipeline      → pipeline_consult (execution_id, step_name, problem)  [overflow]
- close-to-code build (multi-writer) → cursorbuild (forward harness; grokbuild retired 11588)
- run a named pipeline              → pipeline (op=run|async)
⚠ "Want a grok answer" → frontier_dispatch xai/grok-4.3, never a build harness.
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

# Emitted on boot when the seat can dispatch (mcp surfaces). Gate for operator/agent
# prompts like "consult", "get a second opinion", "review this" — pick transport
# before calling tools. Unified path: handoff + lean packet for manual IDE/web seats;
# frontier vs team for API one-shot. Spec: agent-bus thread 1252 / bus-dispatch-unify arc.
_CONSULT_ROUTING_GATE = """\
## Consult routing gate — when prompted to consult outside this seat
Stop and classify BEFORE dispatching. Pick by **latency**, **substrate**, **operator step**, **MCP on consult**.

**Surface axis (not MCP):** `team_dispatch` selects by **role/function** (MCP is a derived consequence of the role's effective model). `frontier_dispatch` selects by **explicit model** (`mcp=` is an explicit caller knob, default False). MCP on/off is never the selector between the two surfaces.

| You need | Path | Poll / retrieve |
|----------|------|-----------------|
| One-shot, corpus fully inline | `frontier_dispatch(op=generate, model=…, mcp=False)` | `pipeline(op="result", …)` |
| One-shot + live fs/cortex/RAG on API | `frontier_dispatch(…, mcp=True)` **required** — default is False | `pipeline(op="result", …)` |
| One-shot + **role contract** | `team_dispatch(op=generate, role=reviewer|gatherer|…)` — MCP follows role's effective model (see below) | `pipeline(op="result", …)` |
| **IDE** consult — seat MCP (not dispatch loop) | `team_dispatch(op=handoff, role=claude-cursor, packet_path=…)` | `agent_bus(wait, …)` — ¬ pipeline |
| **Web** dialectic — seat MCP | `team_dispatch(op=handoff, role=lead|claude-web, …)` | `agent_bus(wait, …)` |
| **Same seat, fresh bus thread** (self-handoff) | `team_dispatch(op=handoff, role=<own alias>, packet_path=…)` | push/open IDE → work new `thread_id` — ¬ `op=generate` to own seat (422) |
| **Implement** (packet-bound, Cursor) — bound todo/spec + acceptance criteria | `team_dispatch(op=handoff, role=implementer, packet_path=…)` → claude-cursor (handoff-only; generate → 422) | `agent_bus(wait, …)` |
| **Material decision — ≥2-family panel** (policy/invariant, hard-to-reverse, deadline/legal/financial) | `panel_dispatch(disposition=panel, messages=…, dispatch_thread_id=…)` → skeptic + reviewer; then adjudicating-caller steelman + `panel_adjudication_artifact` + assert | `pipeline(op="result", …)` per member; load `consensus-steelman-posture` §1 |
| **Material decision — soft / competing options** | Steelman every live option in lead context first (`consensus-steelman-posture` §2); panel only when hard trigger fires | — |
| Implement ping (thin) | `agent_bus(post, to=claude-cursor, …)` + spec | fetch / reply |

**Self-handoff:** manual seats may handoff to themselves via `op=handoff` only. Deep spec: `handoff-dispatchers.mdc` § Self-handoff; `agent-skills/consult-routing.md`.

**MCP on API consult (dispatch tool loop — not handoff seats):**
| Surface | MCP default | Caller action |
|---------|-------------|---------------|
| `team_dispatch(generate)` | **On** when `client_side_mcp_tool_loop_admitted(model)` (denylist: inline-only + xai multi-agent) | Pick role; check role default model. |
| `frontier_dispatch` | **Off** (`mcp=False`) | Pass **`mcp=True` explicitly** when consult must read repo/cortex/RAG. Same shared denylist clamps `mcp=True` for inline-only and xai multi-agent. |

Admission (`mcp_enabled_for_team_dispatch`) is a **denylist** — not openai/anthropic-only. xAI single-agent (e.g. `xai/grok-4.3`) is admitted with MCP on; only **openai/** and **anthropic/** are verified to run a reliable in-loop tool cycle on API consult (confirm against pipeline executor before treating as ground truth).
Gemini (inline-only family) and xAI multi-agent are admitted on team generate but get **no** client-side MCP loop on either API surface.
If consult must verify live files: prefer `openai/gpt-5.5` or `anthropic/claude-*` with MCP on, or **handoff→claude-cursor** (native IDE MCP).

**Defaults (scoped — not competing):**
- **Material decision** (invariant change, hard-to-reverse scope, deadline/legal/financial, or close call + reversal cost) → read `agent-skills/consensus-steelman-posture.md` §1; steelman unconditionally; **`panel_dispatch`** when hard trigger fires (≥2 provider families).
- **API one-shot / hands-off review** → `frontier_dispatch` or `team_dispatch(generate, role=reviewer)` (see `agent-skills/consult-routing.md`; deep matrix: `projects/.cursor/rules/handoff-dispatchers.mdc`, project-level above the repo).
- **From Cursor: fresh perspective / tier / IDE substrate** → `handoff→claude-cursor` + lean packet.

**frontier vs team (API one-shot):**
- **frontier_dispatch** — pick **model**; you own system prompt; **`mcp=True` is explicit**.
- **team_dispatch(generate)** — pick **function**; MCP implied by role→model unless inline-only.

**Handoff:** consultee seat has its own MCP (IDE/web) — not governed by dispatch `mcp=` flag.

On ambiguous "consult X": read `agent-skills/consult-routing.md`. Need live substrate → MCP-capable API path or handoff→cursor; inline opinion only → `frontier_dispatch(mcp=False)` or synthesizer."""


def render_orientation_blocks(family: str | None = None) -> list[str]:
    """Return the capability-axis + liveness orientation blocks as card parts.

    Surface-aware: the Dispatch & Consult block's callable shape depends on the
    rendering seat's catalog (thread 1167, 2026-06-01 re-land):

    - ``family == "grok"`` → the flat /mcp/grok manifest exposes
      ``frontier_dispatch``/``team_dispatch`` as standalone tools → direct-call form.
    - any other family (claude/gpt/gemini on the ``mcp``/``mcp_claude`` surface) →
      the standalone-domain re-land puts ``frontier_dispatch``/``team_dispatch`` in
      ``_PRIMARY_TOOLS`` → direct-call form here too. ``panel_dispatch`` is primary
      on claude-web; ``advisor``/``pipeline_consult`` remain OVERFLOW via
      ``dispatch(tool="…")``.

    Default (``family is None``) renders the claude direct-call form, matching
    the default ``(claude, cursor)`` seat.

    Emitted above the skills list by ``render_briefing_card()``. Each element
    carries a leading newline so the card's ``"\\n".join(parts)`` produces a
    blank-line separator consistent with the other sections.
    """
    dispatch_block = (
        _DISPATCH_CONSULT_BLOCK_GROK
        if family == "grok"
        else _DISPATCH_CONSULT_BLOCK_CLAUDE
    )
    return [
        f"\n{dispatch_block}",
        f"\n{_CONSULT_ROUTING_GATE}",
        f"\n{_LIVENESS_BLOCK}",
    ]
