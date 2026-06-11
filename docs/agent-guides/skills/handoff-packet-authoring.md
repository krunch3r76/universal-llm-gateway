# Handoff Packet Authoring

Durable skeleton + checklist for authoring a `team_dispatch(op=handoff)` packet.
Promoted out of ephemeral `tmp/reviews/_handoff-packet-template.md` so it cannot
go missing under task pressure (incident threads 1296/1297). Authority for the
block contract: project `.cursor/rules/architecture-handoff-protocol.mdc`
§ "The Six Required Blocks".

## Dispatch lifecycle (when to author which packet)

**Invariant:** Reasoning tier (`web-consult` / `cursor-consult` / Opus) authors
dispatch-ready specs (`tasks/specs/{slug}.md` + todo seed); mechanical tier
(`cursor-implement` / Composer) executes them — **never the reverse**.

Read `attributes.dispatch_lane` on the leaf `todo:` before writing anything.

| `dispatch_lane` | Who authors | Packet type | Typical seat |
|---|---|---|---|
| `web-implement-packet` | web-claude | six-block **consult** packet that authors an implement packet | `team_dispatch(op=handoff, seat=claude-web)` (shorthand `web-consult`) |
| `web-spec` | web-claude | six-block **consult** packet (findings) | `team_dispatch(web-consult)` |
| `cursor-mechanical` | cursor IDE | skeleton or full packet on disk; **no web** when spec is sufficient | IDE or `cursor-implement` |
| `cursor-implement` | cursor (handoff) | bound implement packet with acceptance criteria | `team_dispatch(cursor-implement)` |
| `operator-gate` | operator | assert template / export — not a handoff packet | — |

**Canonical pipeline:** reasoning upstream (web consult or plan author) → dense artifact
(implement packet or phase doc) → mechanical downstream (`composer-2.5` / cursor-only).

**Counter-pattern:** mechanical work with a dense todo spec (e.g. corpus export) —
`dispatch_lane: cursor-mechanical`, `density: mechanical`; skip web entirely.

**Codified bug tickets bind to this same pipeline (two-phase):** a filed bug/friction
defaults to **Phase 1 investigate + decide** (`cursor-consult` / `web-consult` — the
reasoning-upstream hop that produces the dense spec) → **Phase 2 execute**
(`cursor-implement` against that spec, or web inline fix — the mechanical-downstream hop).
Do not author a `cursor-implement` packet as the first hop on a bug whose root cause or
design is still open; that collapses the upstream → dense-artifact → downstream pipeline
into a single mechanical step with no spec. **Pass zoom-out duty** binds every bug pickup:
zoom out beyond the filed symptom (touch-point inventory, bug-class grep, labeled
`## Secondary findings` in closeout). Full model: `agent-skills/consult-routing.md`
§ Codified bug reports → Pass zoom-out duty.

Upstream gates (falsifier, operator assert) must close before `web-implement-packet`
dispatch — set `workflow_state: blocked` + `block_reason` on the blocked leaf.

Full attribute table: `universal-llm-gateway/.cursor/rules/todo_ws.mdc` §Dispatch metadata.

## Preflight (mandatory — before writing a packet)

Complete for a **consult** AND a bound **implement** (`role=cursor-implement` or `role=web-implement` handoff):

```
fs(cortex,     agent-skills/consult-routing.md)                         # transport + authority map
fs(workspaces, .cursor/rules/architecture-handoff-protocol.mdc)         # md_read § The Six Required Blocks
fs(workspaces, .cursor/rules/handoff-dispatchers.mdc)                   # § target seat
```

The protocol files live at **project** `.cursor/rules/` (one level above the
repo; **no** `universal-llm-gateway/` prefix). Skipping the trio when the boot
card `_CONSULT_ROUTING_GATE` is present is a protocol violation.

## The Six Required Blocks

Author the packet in this order, in canonical XML tags (case-sensitive):

| # | Block | Required | Holds |
|---|---|---|---|
| 1 | `<scope>` | yes | what's reviewed/implemented (branch/HEAD, path) + selection mode |
| 2 | `<invariants>` | yes | compact workspace rules; MCP dispatchers use skill-ref lines + ≤15 task lines |
| 3 | `<task_guidance>` | yes | what to evaluate / do — questions, criteria; **acceptance criteria for `implement`** |
| 4 | `<corpus>` | yes | the artifact under review / context |
| 5 | `<mcp_capabilities>` | iff dispatcher has MCP | reviewer tools + evidence format (claude-web, claude-cursor) |
| 6 | `<output_format>` | yes | finding / closeout shape |
| — | `<excluded>` | optional | files/sections not sent, one-word reason |
| — | `<prior_pass>` | optional | iteration preamble (applied/rejected/surfaced) |

**Implement contract**: acceptance criteria live in `<task_guidance>`; closeout
evidence shape in `<output_format>`. The admission lint (`handoff.py`) rejects an
`implement` packet whose `<task_guidance>` contains no `acceptance` keyword.
Declare authority explicitly via front-matter `contract: implement` or the MCP
`contract=` param on `team_dispatch(op=handoff)` — a packet with acceptance
criteria but no contract signal is rejected (`handoff_contract_ambiguous`).

**Executor override (implement):** optional front-matter or request fields —
server resolves `recommended_executor` on the handoff response (advisory on
manual seats until cursorbuild):

```yaml
executor_override: composer | composer-fast | composer-thinking | web-inline | <non-composer-tier>
executor_override_reason_code: pure_cortex_doc_edit | capability_gap | protocol_heavy | design_judgment_remaining
executor_override_reason: "short required text when reason_code demands it"
```

Silence → `recommended_executor=composer`. See `agent-skills/consult-routing.md`
§ Executor tier for R1/R2 policy (reference, do not hand-copy).

## Skeleton

```
---
contract: consult   # or implement — explicit authority grant (optional; MCP contract= overrides)
---
<scope>
Goal: <one-line>. Selection mode: <targeted | branch | path>.
Primary artifacts: <paths>.  Out of scope: <...>.
</scope>

<invariants>
Read before editing:
- fs(cortex, agent-skills/architecture-invariants.md)   — universal layer
- fs(cortex, agent-skills/ulg-architecture.md)           — ULG layer (when ULG)
Per-task narrowing:
[universal:no-bc] <...>   [scope] every changed line traces to task   [quality] SLOC ≤400/≤300
</invariants>

<task_guidance>
<questions / phases>. For implement: ## Acceptance criteria (numbered, all required).
</task_guidance>

<corpus>
<incident / artifact / pointers>
</corpus>

<mcp_capabilities>
You have MCP. Investigate before forming findings. Cite every tool call.
</mcp_capabilities>

<output_format>
<finding shape for consult, or closeout table for implement>
</output_format>
```

## Naming + delivery

- Packet path: `tmp/reviews/<task>-<seat>-packet.md` (write the file **before** the handoff call).
- **`packet_path` root**: Stargate resolves `packet_path` relative to `PROJECT_ROOT`
  (`/mnt/torus/projects/universal-llm-gateway`). Use `tmp/reviews/<file>.md` — **no repo prefix**.
  `fs(sandbox="workspaces")` uses a different root (`/mnt/torus/projects`) and needs the
  `universal-llm-gateway/` prefix. These are different; conflating them gives `handoff_packet_missing`.
- Web boot-gate fields (when target is `claude-web`): ensure `<invariants>` carries the
  `architecture-invariants` + `ulg-architecture` skill-ref lines — web has no IDE `*_ws.mdc` backstop.
- Cursor note (when target is `claude-cursor`): keep the same skill-ref lines + narrowing + Block 5
  item 0 — a packet-booted IDE thread auto-loads only the engineering-discipline layer
  (`alwaysApply: true`); the arch layer (`topology_ws`, `mcp-integration_ws`, …) is
  description-gated and does NOT reliably attach, so the skill-refs are load-bearing, not a
  backstop. Only genuine trim: ¬ re-inline the auto-loaded engineering-discipline layer
  (SLOC/modularization, code style, scope, `no-bc`, logging).
- Only a ≤25-line pointer is posted to the bus; the packet stays on disk.

## Authority

| Topic | Source |
|---|---|
| Six-block contract | project `.cursor/rules/architecture-handoff-protocol.mdc` |
| Dispatcher matrix | project `.cursor/rules/handoff-dispatchers.mdc` |
| Transport routing | `agent-skills/consult-routing.md` (cortex) — §Dispatch lane |
| Dispatch metadata on todos | `universal-llm-gateway/.cursor/rules/todo_ws.mdc` §Dispatch metadata |
| Admission lint | `services/universal-stargate/systems/frontier_consult/handoff.py` |
