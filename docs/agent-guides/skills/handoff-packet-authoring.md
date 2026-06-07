# Handoff Packet Authoring

Durable skeleton + checklist for authoring a `team_dispatch(op=handoff)` packet.
Promoted out of ephemeral `tmp/reviews/_handoff-packet-template.md` so it cannot
go missing under task pressure (incident threads 1296/1297). Authority for the
block contract: project `.cursor/rules/architecture-handoff-protocol.mdc`
§ "The Six Required Blocks".

## Preflight (mandatory — before writing a packet)

Complete for a **consult** AND a bound **implement** (`role=cursor-implement` handoff):

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

## Skeleton

```
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
| Transport routing | `agent-skills/consult-routing.md` (cortex) |
| Admission lint | `services/universal-stargate/systems/frontier_consult/handoff.py` |
