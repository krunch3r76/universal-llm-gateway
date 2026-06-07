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
# thread 1167). Lead seats (config/agents.yaml lead_seats) use the
# claude-form block on /mcp (mcp_claude). team_dispatch (+ panel_dispatch) are
# PRIMARY/direct-call on lead surfaces after the standalone-domain re-land:
#
#   - claude /mcp + gpt /mcp (mcp, mcp_claude): standalone `team_dispatch`
#     DOMAIN → _PRIMARY_TOOLS — direct call; advisor/pipeline_consult overflow.
#   - grok /mcp/grok (mcp_grok): flat catalog for grok-direct/build worker only
#     — consult/infrastructure surface, not an operator lead seat.
#
# NOTE: `cache_priority` in canonical.yaml is INERT (not consumed by derivation,
# per _derive.py). The lever that makes these primary on claude is the standalone
# DOMAIN, and it MUST stay committed — an uncommitted change reverts on rebuild
# (the Part-1 regression, 11528/11549). Source of truth: decision-table §2/§3/§4.
#
# Binding caveat (thread 1292 / todo:mcp-web-ops-primary-surface): server-primary
# (_PRIMARY_TOOLS / tools/list) ≠ connector-bound callable set on claude-web.
# Dispatch shapes below apply only to tools bound THIS session — probe first.

_MCP_BINDING_LIVENESS_BLOCK = """\
## MCP binding — connector-bound callable set (live probe required)
Three layers — do not conflate:
  1. **Server primary** — `_PRIMARY_TOOLS` / `tools/list` (manifest line follows this block)
  2. **Overflow** — demoted tools; reachable only via `dispatch(tool=…)` when `dispatch` is bound
  3. **Connector-bound** — what claude.ai loads into your callable set THIS session

**Invariant**: server-primary ≠ initial callable set. The connector loads tools in two shapes:
  - **Pre-bound** — tool is in the initial callable set → call directly.
  - **Deferred** — tool absent initially but loadable via `tool_search` → one load hop, then direct call. This is a VALID connector-bound shape; session 0856 observed N=0 pre-bound with all 15 server-primary tools deferred behind `tool_search` (every loaded tool reached a healthy server).
Probe guidance: "absent from initial set" ≠ "connector dropped it". Before filing `tool_absent` friction, run a `tool_search` load hop, then correlate with events — a `mcp.request.started` for that `tool_name` proves the server was reached. Only a genuinely unreachable tool (no load via `tool_search`, no `started` event) is connector omission; then hand off to `team_dispatch(op=handoff, role=cursor-consult, …)` — do not loop `tool_search`.

**Overflow / deferred load via `tool_search`**:
```
tool_search(query="<keywords>")          # surfaces overflow AND deferred primary tools
```
  - A deferred SERVER-PRIMARY tool loaded this way becomes a direct callable — call it by name, NOT via `dispatch(tool=primary_name)` (dispatch rejects primary names).
  - A true OVERFLOW tool is reached via `dispatch(tool="<name>", arguments='…')` (only when `dispatch` is itself bound)."""

_DISPATCH_CONSULT_BLOCK_CLAUDE = """\
## Dispatch & Consult — pick by CAPABILITY, not model family
To consult a MODEL (any provider, incl. grok) you do NOT use a build harness.
When connector-bound: team_dispatch + panel_dispatch are server-primary — call directly (if unbound, see MCP binding block above). Model strings = provider/model on optional model= override (bare name = 404).
- API consult (any provider)        → team_dispatch (op=generate, role=reviewer|artisan|skeptic|…, dispatch_thread_id=…, model="provider/model"?) → execution_id; poll pipeline(op="result", execution_id=…)
- by API role (reviewer/artisan/…) → team_dispatch (op=generate, role=…) — ¬ synthetic seat models on generate (422)
- manual seat handoff → team_dispatch (op=handoff, role=web-consult|cursor-consult|cursor-implement, packet_path=…, subject=…) — web-consult (push); cursor-consult (IDE); cursor-implement (bound → claude-cursor)
- claude-web handoff → operator push; claude-cursor handoff → open IDE thread
- consensus panel (≥2 families)     → panel_dispatch(messages=[…], dispatch_thread_id="…", disposition="panel")  [primary]
- stronger-model strategic advice   → dispatch(tool="advisor", arguments='{"problem":"…"}')                                  [overflow]
- RAG advice inside a pipeline      → dispatch(tool="pipeline_consult", arguments='{"execution_id":"…","step_name":"…","problem":"…"}')  [overflow]
- close-to-code build (multi-writer) → cursorbuild (forward harness; grokbuild retired 11588)
- run a named pipeline              → pipeline (op=run|async)
⚠ A build harness is not a model picker. "Want a grok answer" → team_dispatch(op=generate, role=artisan, model="xai/grok-4.3", …), never a build harness.
Full shapes: reference:claude-web-lead-seat-surface → claude-web-dispatch-decision-table.md"""

_DISPATCH_CONSULT_BLOCK_GROK = """\
## Dispatch & Consult — pick by CAPABILITY, not model family
To consult a MODEL (any provider, incl. grok) you do NOT use a build harness.
On THIS surface (/mcp/grok, flat catalog) team_dispatch is PRIMARY — call directly, no dispatch step. Model strings = provider/model on optional model= (bare name = 404).
- API consult (any provider)        → team_dispatch (op=generate, role=reviewer|artisan|skeptic|…, dispatch_thread_id=…, model="provider/model"?)
- by API role (reviewer/artisan/…) → team_dispatch (op=generate, role=…) — ¬ synthetic seat models on generate (422)
- manual seat handoff → team_dispatch (op=handoff, role=web-consult|cursor-consult|cursor-implement, packet_path=…, subject=…) — web-consult (push); cursor-consult (IDE); cursor-implement (bound → claude-cursor)
- claude-web handoff → operator push; claude-cursor handoff → open IDE thread
- consensus panel (≥2 families)     → panel_dispatch(messages=[…], dispatch_thread_id="…", disposition="panel")
- stronger-model strategic advice   → advisor (problem)                       [overflow]
- RAG advice inside a pipeline      → pipeline_consult (execution_id, step_name, problem)  [overflow]
- close-to-code build (multi-writer) → cursorbuild (forward harness; grokbuild retired 11588)
- run a named pipeline              → pipeline (op=run|async)
⚠ "Want a grok answer" → team_dispatch role=artisan model=xai/grok-4.3, never a build harness.
Full shapes: reference:claude-web-lead-seat-surface → claude-web-dispatch-decision-table.md"""

# Co-located liveness block (2a durable home). Trimmed per F4-A finding (thread
# 1289): 3-question redirect + salience line kept inline; substrate table collapsed
# to prose — it is reference-density, recoverable from commit-and-git-scope_ws.mdc.
_LIVENESS_BLOCK = """\
## Liveness — the running process is the source of truth (commit-decoupled)
A change is LIVE only when LOADED into the running process at its last deploy/restart. Git commit/master is neither necessary nor sufficient.
Before claiming a surface changed, ask three questions — do NOT read git for this:
  1. WHICH substrate?   2. Did its LOAD EVENT fire?   3. What does the LIVE PROBE say?
Substrates: service behavior (sync_restart/rebuild → observability probe) · MCP tool surface (mcp restart → boot manifest + binding probe, ¬ tool_search alone) · routing+catalog (sync_restart → /v1/models) · agent-context (cortex_boot → this card).
⚠ Salience trap: "commit" is the loudest done/durable signal, so it gets grabbed as a liveness proxy under load. It is not one. Verify against the load event + probe, never the tree."""

# Compact index — full playbook is agent-skills/consult-routing.md (current superset,
# verified 2026-06-04). The two highest-frequency traps are kept inline; everything
# else defers to the skill. See F2 finding, thread 1289.
_CONSULT_ROUTING_GATE = """\
## Consult routing — read the skill before dispatching
On any consult / review / second-opinion / handoff / dispatch outside this seat:
read `agent-skills/consult-routing.md` BEFORE choosing transport (full playbook; this is only the index).
Three traps that cost a round-trip:
- team_dispatch(op=generate) with synthetic seat model (claude-web|claude-cursor) → 422; manual seats take op=handoff with role=.
- "Want a grok answer" is not a build harness → team_dispatch(role=artisan, model="xai/grok-4.3").
- Wrong rules tree: the handoff protocol is NOT under `universal-llm-gateway/.cursor/rules/`. It lives at PROJECT `.cursor/rules/architecture-handoff-protocol.mdc` + `handoff-dispatchers.mdc` (no repo prefix).
Mandatory preflight before ANY handoff packet or team_dispatch(op=handoff) — implement (role=cursor-implement) is NOT exempt:
  fs(cortex, agent-skills/consult-routing.md)
  fs(workspaces, .cursor/rules/architecture-handoff-protocol.mdc)   # § Six Blocks
  fs(workspaces, .cursor/rules/handoff-dispatchers.mdc)             # § target seat
Surface axis: team_dispatch handoff = role (web-consult/cursor-consult/cursor-implement); team_dispatch generate = API role + optional model= override within allowed_models.
Codified bug report (operator directs report bug/friction to cursor) = bound implement → team_dispatch(op=handoff, role=cursor-implement), lifecycle investigate→fix→report; an operator-named transport wins (never silently substitute agent_bus). See skill § Codified bug reports."""


def _render_server_primary_manifest_line() -> str:
    """Inject live ``tools/list`` primary names from derivation (layer 1 truth)."""
    from _derive import get_claude_manifest  # noqa: PLC0415

    manifest = get_claude_manifest()
    names = sorted(e["tool_name"] for e in manifest)
    joined = ", ".join(names)
    return (
        f"\n## MCP server primary (`tools/list`, N={len(names)})\n"
        f"Assembly advertises: `{joined}`.\n"
        f"¬ identical to connector-bound callables — see MCP binding block above."
    )


_SESSION_CLOSE_WEB_BLOCK = """\
## Session Close — MANDATORY on "close session" / "session end"
Web seats have **no** auto-loaded `session-close.mdc`. Before `cortex(tool="session_close", ...)`:
1. `fs(sandbox="cortex", op="md_read", path="agent-skills/session-close-kernel.md")` — canonical protocol
2. `fs(sandbox="cortex", op="md_read", path="agent-skills/session-close-audit.md")` — run before close
3. claude-web only: `fs(sandbox="cortex", op="md_read", path="agent-skills/web-transcript-preprocessing.md")` before assembling `transcript_md`
4. `cortex(tool="session_close_preflight", ...)` → then `cortex(tool="session_close", ...)`
Every `session_close` / `session_close_preflight` response carries `_protocol` with this pointer."""


def _render_op_skill_bindings_line() -> str | None:
    """Render per-op skill bindings recovered from the grouped Claude manifest.

    The grouped manifest collapses per-op ``skill_uri`` into a domain binding;
    ``derive_claude_manifest`` re-surfaces divergent ones under ``op_skills``.
    This emits them machine-readably (``domain.op → skill_uri``) so web seats —
    which have no auto-loaded ``session-close.mdc`` — get the correct per-op
    routing (e.g. ``cortex.session_close → agent_skill:session-close``) sourced
    from canonical.yaml, not hardcoded prose. Auto-tracks future divergences.
    """
    from _derive import get_claude_manifest  # noqa: PLC0415

    bindings: list[str] = []
    for entry in get_claude_manifest():
        domain = entry["domain"]
        for op, skill_uri in entry.get("op_skills", {}).items():
            bindings.append(f"`{domain}.{op} → {skill_uri}`")
    if not bindings:
        return None
    return (
        "\n**Per-op skill bindings** (manifest-sourced, from `canonical.yaml`): "
        + ", ".join(bindings)
    )


def _session_close_orientation_for_agent(agent: str | None) -> str | None:
    if agent and agent.endswith("-web"):
        block = _SESSION_CLOSE_WEB_BLOCK
        bindings = _render_op_skill_bindings_line()
        if bindings:
            block = f"{block}{bindings}"
        return f"\n{block}"
    return None


def render_orientation_blocks(
    family: str | None = None,
    agent: str | None = None,
) -> list[str]:
    """Return the capability-axis + liveness orientation blocks as card parts.

    Surface-aware: the Dispatch & Consult block's callable shape depends on the
    rendering seat's catalog (thread 1167, 2026-06-01 re-land):

    - ``family == "grok"`` → grok-direct/build-worker flat /mcp/grok block
      (infrastructure surface — not a lead seat).
    - any other family on ``mcp``/``mcp_claude`` (claude, gpt leads; gemini
      pending) → ``team_dispatch`` in ``_PRIMARY_TOOLS`` direct-call form.
      ``panel_dispatch`` is primary on claude-web; overflow via
      ``dispatch(tool="…")`` for advisor/pipeline_consult.

    Default (``family is None``) renders the claude direct-call form, matching
    the default ``(claude, cursor)`` seat.

    Emitted above the skills list by ``render_briefing_card()``. Each element
    carries a leading newline so the card's ``"\\n".join(parts)`` produces a
    blank-line separator consistent with the other sections.
    """
    session_close_block = _session_close_orientation_for_agent(agent)
    if family == "grok":
        blocks = [
            f"\n{_DISPATCH_CONSULT_BLOCK_GROK}",
            f"\n{_CONSULT_ROUTING_GATE}",
            f"\n{_LIVENESS_BLOCK}",
        ]
        if session_close_block:
            blocks.insert(1, session_close_block)
        return blocks
    blocks = [
        f"\n{_MCP_BINDING_LIVENESS_BLOCK}",
        _render_server_primary_manifest_line(),
        f"\n{_DISPATCH_CONSULT_BLOCK_CLAUDE}",
        f"\n{_CONSULT_ROUTING_GATE}",
        f"\n{_LIVENESS_BLOCK}",
    ]
    if session_close_block:
        blocks.insert(3, session_close_block)
    return blocks
