# Handoff Packet Authoring

Durable skeleton + checklist for authoring a bound implement / consult packet — the
six-block shape is shared by both `team_dispatch(op=handoff)` and the default implement
transport `team_dispatch(op=generate, role=cursor-sdk, packet_path=…)`.
Promoted out of ephemeral `tmp/reviews/_handoff-packet-template.md` so it cannot
go missing under task pressure (incident threads 1296/1297). Authority for the
block contract: project `.cursor/rules/architecture-handoff-protocol.mdc`
§ "The Six Required Blocks".

## Dispatch lifecycle (when to author which packet)

**Invariant:** Reasoning tier (`web-consult` / `cursor-consult` / Opus) authors
dispatch-ready specs (`tasks/specs/{slug}.md` + todo seed); mechanical tier
(`cursor-sdk` generate / Composer — default; `cursor-implement` handoff fallback)
executes them — **never the reverse**. Because Composer is mechanical, the implement
packet MUST be dense and complete (every file/function/test shape pinned, forks resolved);
an under-specified cursor-sdk packet is a routing error, not a thin spec the executor rescues.

Read `attributes.dispatch_lane` on the leaf `todo:` before writing anything.

| `dispatch_lane` | Who authors | Packet type | Typical seat |
|---|---|---|---|
| `web-implement-packet` | web-claude | six-block **consult** packet that authors an implement packet | `team_dispatch(op=handoff, seat=claude-web)` (shorthand `web-consult`) |
| `web-spec` | web-claude | six-block **consult** packet (findings) | `team_dispatch(web-consult)` |
| `cursor-sdk-implement` *(default for bound implement)* | any dispatching seat | **dense** six-block implement packet + acceptance criteria | `team_dispatch(op=generate, role=cursor-sdk, packet_path=…, contract=implement)` — auto Composer, no IDE pickup |
| `cursor-mechanical` | cursor IDE | skeleton or full packet on disk; **no web** when spec is sufficient | `cursor-sdk` generate (default) · IDE / `cursor-implement` when already in Cursor |
| `cursor-implement` | cursor (handoff) | bound implement packet with acceptance criteria | `team_dispatch(op=handoff, role=cursor-implement)` — **fallback**: operator opens IDE |
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

## General execution without packet (messages-only)

**Schema-free is NOT direction-free.** A `cursor-sdk` dispatch for a fully
**determinate** task with **pre-authored** values may omit the six-block packet —
`team_dispatch(op=generate, role=cursor-sdk, dispatch_thread_id=…, messages=[…])` —
but Composer 2.5 is still a mechanical executor, so messages-only directions must be
explicit, detailed, restrictive, and bounded. This is a distinct lane from the dense
implement packet, **not** a lighter packet.

Canonical lane definition — the three-point spectrum (Dense Implement / Light Bounded
Execution / Pure Mechanical Write Loop) and the 10-point messages-only instruction
checklist — lives in the SOT: `agent-skills/consult-routing.md` § General execution
lane (messages-only — no packet). Do not duplicate the body here.

## Friction-ticket packets (extra preflight)

When the handoff targets a filed friction (not a todo spec arc):

| Check | Why (16849 / thread 1576) |
|---|---|
| Friction ID resolves to `service:*` assertion | Assertion 16737 was a `task:` completion row, not friction 16737 |
| Bound task not already `done` | Dispatched investigate on a closed arc |
| Corpus names exact `entity_id` | Web queried `service:stargate`; friction lived on `service:universal-stargate` |
| `<mcp_capabilities>` uses assertion lookup, not guessed service | `assertions(entity_id=service:…)` or `frictions` with the **same** service slug |
| Operator confirmed dispatch intent | Typo request → void protocol, not `team_dispatch` |

See `friction-review.md` § Friction ID preflight / Void recall.

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
manual seats; IDE picker binds the executor tier):

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
- fs(cortex, agent-skills/architecture-invariants.md) — universal tag index
- fs(cortex, agent-skills/ulg-architecture.md) — ULG tag index (when ULG)
- fs(workspaces, .cursor/rules/architecture-handoff-protocol.mdc) — six-block contract
Per-task narrowing (tag | rule — reason):
| Tag | Rule |
|---|---|
| [universal:no-bc] | delete old surfaces; update consumers same change |
| [scope] | every changed line traces to task |
| [quality] | SLOC ≤400/≤300; load quality-gates.md on code change |
| [ulg:service-ops] | manage MCP only; load service-ops.md on deploy |
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

## Preliminary scaffold (Composer) → densification (reasoner)

**Invariant:** ∀ packet whose mechanical blocks are templated: a cheap tier
(`composer-2.5`) MAY draft the **scaffold**; the reasoning tier (web-claude /
cursor-consult / Opus) **densifies** the judgment-bearing blocks. The dense,
dispatch-ready artifact's authorship stays with the reasoner — this does NOT
invert the § Dispatch lifecycle invariant, because the scaffold carries structure,
¬ conclusions.

Gate (per block): scaffold iff the block is low-judgment; densify iff it carries
design judgment a wrong draft could **anchor**.

| Block | Scaffold (Composer) | Densify (reasoner) |
|---|---|---|
| `<scope>` | path list, git SHA, selection mode | — (mechanical) |
| `<corpus>` | changed-file manifest (paths only) | — (mechanical) |
| `<invariants>` | skill-ref lines, SLOC/no-bc boilerplate | per-task narrowing |
| `<output_format>` | finding/closeout shape boilerplate | — (mechanical) |
| `<task_guidance>` | section headers / question stubs | **all judgment** — questions, criteria, acceptance |
| `<mcp_capabilities>` | tool list boilerplate | evidence-format specifics |

**Anchoring caveat:** the pattern is upside-only where the scaffold is structural.
Where a preliminary artifact would embed a *design decision* (e.g. proposed module
boundaries), a cheap draft can anchor the reasoner into elaborating a flawed
structure rather than reasoning fresh. There, prefer either/or tiers with
escalation, ¬ scaffold→densify chaining — unless the draft is explicitly labeled
"candidate, re-derive don't elaborate." See `.cursor/commands/overhaul.md` § 2
(split planning) for the worked exclusion.

## Naming + delivery

- **Default implement transport = `cursor-sdk` generate** (`team_dispatch(op=generate, role=cursor-sdk, packet_path=…, contract=implement)`) — auto Composer, no IDE pickup. `cursor-implement` handoff is the operator-attended **fallback**, not a peer default. The packet must be **dense** (Composer executes mechanically). Full policy: `agent-skills/consult-routing.md` § Dispatch targets.
- Packet path: `tmp/reviews/<task>-<seat>-packet.md` (write the file **before** the handoff call).
- **`packet_path` root**: Stargate resolves `packet_path` relative to `PROJECT_ROOT`
  (`/mnt/torus/projects/universal-llm-gateway`). Use `tmp/reviews/<file>.md` — **no repo prefix**.
  `fs(sandbox="workspaces")` uses a different root (`/mnt/torus/projects`) and needs the
  `universal-llm-gateway/` prefix. These are different; conflating them gives `handoff_packet_missing`.
- Web boot-gate fields (when target is `claude-web`): ensure `<invariants>` carries the
  `architecture-invariants` + `ulg-architecture` skill-ref lines — web has no IDE `*_ws.mdc` backstop.
- **Cursor + cursor-sdk arch-layer note** (when target is `claude-cursor` or `cursor-sdk`
  implement via `team_dispatch(op=generate, role=cursor-sdk, …)`): keep the same skill-ref
  lines + task narrowing + Block 5 item 0 as for `claude-web`. Both seats load project rules
  via `setting_sources=all`, but only the engineering-discipline layer (`alwaysApply: true`)
  auto-attaches; the arch layer (`topology_ws`, `mcp-integration_ws`, `event-debugging_ws`, …)
  is description-gated and does NOT reliably attach in a packet-booted thread, so Block 2
  skill-refs (`architecture-invariants`, `ulg-architecture`) and Block 5 item 0 (`fs(cortex, …)`
  reads before findings/edits) are **load-bearing**, not a backstop. **cursor-sdk** also
  receives a mechanical implement preamble enforcing these reads — packet authors must still
  include skill-refs and narrowing so the executor loads task-specific cortex skills beyond
  the universal pair. Only genuine trim: ¬ re-inline the auto-loaded engineering-discipline
  layer (SLOC/modularization, code style, scope, `no-bc`, logging).
- Only a ≤25-line pointer is posted to the bus; the packet stays on disk.
- **Web-consult pointer (B′, server-enforced):** default `build_pointer_body` for
  `handoff_contract=consult` appends a mechanical arch-layer read reminder (same
  substance as cursor-sdk implement preamble) — packet authors must still include
  Block 2 skill-refs, Block 5 item 0 in `<mcp_capabilities>`, and web boot-gate
  frontmatter (`active_project_tag`, `cortex_boot_confirmed`, `related_thread_ids`).

## Authority

| Topic | Source |
|---|---|
| Six-block contract | project `.cursor/rules/architecture-handoff-protocol.mdc` |
| Dispatcher matrix | project `.cursor/rules/handoff-dispatchers.mdc` |
| Transport routing | `agent-skills/consult-routing.md` (cortex) — §Dispatch lane |
| Dispatch metadata on todos | `universal-llm-gateway/.cursor/rules/todo_ws.mdc` §Dispatch metadata |
| Admission lint | `services/universal-stargate/systems/frontier_consult/handoff.py` |
