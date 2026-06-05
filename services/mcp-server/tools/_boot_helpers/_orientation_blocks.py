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
Probe guidance: "absent from initial set" ≠ "connector dropped it". Before filing `tool_absent` friction, run a `tool_search` load hop, then correlate with events — a `mcp.request.started` for that `tool_name` proves the server was reached. Only a genuinely unreachable tool (no load via `tool_search`, no `started` event) is connector omission; then hand off to `team_dispatch(op=handoff, role=claude-cursor|cursor-lead, …)` — do not loop `tool_search`.

**Overflow / deferred load via `tool_search`**:
```
tool_search(query="<keywords>")          # surfaces overflow AND deferred primary tools
```
  - A deferred SERVER-PRIMARY tool loaded this way becomes a direct callable — call it by name, NOT via `dispatch(tool=primary_name)` (dispatch rejects primary names).
  - A true OVERFLOW tool is reached via `dispatch(tool="<name>", arguments='…')` (only when `dispatch` is itself bound)."""

_DISPATCH_CONSULT_BLOCK_CLAUDE = """\
## Dispatch & Consult — pick by CAPABILITY, not model family
To consult a MODEL (any provider, incl. grok) you do NOT use a build harness.
When connector-bound: frontier_dispatch + team_dispatch + panel_dispatch are server-primary — call directly (if unbound, see MCP binding block above). Model strings = provider/model (bare name = 404).
- consult any model, one-shot       → frontier_dispatch (op=generate, model="provider/model": openai/gpt-5.5, xai/grok-4.3, anthropic/claude-opus-4-8)  → returns execution_id; poll pipeline(op="result", execution_id=…)
- by API role (reviewer/artisan/…) → team_dispatch (op=generate, role=…) — ¬ synthetic seat models on generate (422)
- manual seat handoff → team_dispatch (op=handoff, model=claude-web|claude-cursor and/or role=lead|cursor-lead|implementer, handoff_contract=consult|implement, packet_path=…, subject=…) — model+role co-equal selectors (mismatch → 422 handoff_seat_role_conflict); consult = review/revise/expand; implement = bound; handoff_contract omitted ⟹ role default_contract (implementer → implement) else consult
- claude-web handoff → operator push; claude-cursor handoff → open IDE thread
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
- by API role (reviewer/artisan/…) → team_dispatch (op=generate, role=…) — ¬ synthetic seat models on generate (422)
- manual seat handoff → team_dispatch (op=handoff, model=claude-web|claude-cursor and/or role=lead|cursor-lead|implementer, handoff_contract=consult|implement, packet_path=…, subject=…) — model+role co-equal selectors (mismatch → 422 handoff_seat_role_conflict); consult = review/revise/expand; implement = bound; handoff_contract omitted ⟹ role default_contract (implementer → implement) else consult
- claude-web handoff → operator push; claude-cursor handoff → open IDE thread
- consensus panel (≥2 families)     → panel_dispatch(messages=[…], dispatch_thread_id="…", disposition="panel")
- stronger-model strategic advice   → advisor (problem)                       [overflow]
- RAG advice inside a pipeline      → pipeline_consult (execution_id, step_name, problem)  [overflow]
- close-to-code build (multi-writer) → cursorbuild (forward harness; grokbuild retired 11588)
- run a named pipeline              → pipeline (op=run|async)
⚠ "Want a grok answer" → frontier_dispatch xai/grok-4.3, never a build harness.
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
- team_dispatch(op=generate) with synthetic seat model (claude-web|claude-cursor) → 422; manual seats take op=handoff with model=.
- "Want a grok answer" is not a build harness → frontier_dispatch(model="xai/grok-4.3").
- Wrong rules tree: the handoff protocol is NOT under `universal-llm-gateway/.cursor/rules/`. It lives at PROJECT `.cursor/rules/architecture-handoff-protocol.mdc` + `handoff-dispatchers.mdc` (no repo prefix).
Mandatory preflight before ANY handoff packet or team_dispatch(op=handoff) — implement (handoff_contract=implement) is NOT exempt:
  fs(cortex, agent-skills/consult-routing.md)
  fs(workspaces, .cursor/rules/architecture-handoff-protocol.mdc)   # § Six Blocks
  fs(workspaces, .cursor/rules/handoff-dispatchers.mdc)             # § target seat
Surface axis: team_dispatch handoff = synthetic seat model (claude-web/claude-cursor); team_dispatch generate = API role; frontier_dispatch = provider model (mcp= default False). MCP on/off is never the team-vs-frontier selector."""


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
