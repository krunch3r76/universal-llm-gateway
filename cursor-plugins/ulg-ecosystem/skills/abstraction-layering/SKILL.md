---
name: abstraction-layering
description: "Codework idea→implement peer to path-sim: G1–G6 layering on charter tick. Use on codebase changes, friction fixes, DIRECTIVEs. Layers inherit binds; ¬ re-reason closed."
lifecycle: active
trigger_match_terms:
  - abstraction-layering
  - abstraction layering
  - /layer
  - layer command
  - layer contract
  - frame layer
  - codework lane
  - supersedes path-sim
  - architecture then frame
  - layer charter
  - layer tick enrollment
  - idea to implementation
  - densify
related_skills:
  - consult-routing
  - path-sim
  - claude-ai-cdp-navigation
  - handoff-packet-authoring
  - implement-todo
  - cheap-recon-before-escalation
  - agent-bus-discipline
  - entity-lifecycle-discipline
  - work-item-seed-path
---

# Abstraction Layering

**Codework idea→shipped.** Peer to `path-sim` for codebase changes
(`decision:abstraction-layering`). Path-sim pays R-admit/R-after; this lane
**layers** seats and inherits ratification downward.

Lane brand = **layering** / `/layer`. The G3 stage token remains **`densify`**
(dense-spec hop only — ¬ the lane name).

## Invariant

`∀ layering:` high abstraction → low concreteness; inherit binds above; ¬ re-reason
closed layers. `Fable CDP → Opus frame → Grok densify → GPT check → Composer → verify`.
No standing R-admit/R-after. G6 = mechanical verify.

## When

| Condition | Entry |
|---|---|
| Codebase change (DIRECTIVE, friction, charter) | Highest open G1–G6 |
| **G1 skip (graph)** — active structural `derived_from` from the work item → a `document:` with `consult_kind=architecture` whose `source_uri` resolves | **G2** (mark G1 `[x]` with that URI) |
| Architecture rival live (detent ≥ wide) ∧ ¬ G1 skip | G1 |
| No frame yet | G2 |
| Frame exists | G3 |
| Dense spec unchecked | G4 |
| `implement_ready` or mechanical job | G5 |
| Non-codework / fat-packet deepen | `path-sim` |
| Settled bind, ship only | `/address` |

Mark gates above entry `[x]` with artifact URI at enrollment.

### G1 skip — architecture-consult edge (BINDING)

`/layer` does **not** skip G1 on chat inform alone. Skip authority is the Cortex graph:

```
∃ relationship(
  source = todo:{slug},
  type   = derived_from,
  target = document:{…},
  active
) ∧ attrs(target).consult_kind = "architecture"
  ∧ attrs(target).source_uri resolves to the G1 answer sidecar
⇒ G1 closed; enter G2; ¬ re-fire Fable
```

**Check at entry:** `cortex(relationships, entity_id=todo:{slug})` (or traverse `derived_from`).
If the edge is missing, treat architecture as open even if a Fable answer exists on disk —
**seeding duty** is to stamp the edge (below). Assertion `evidence_uris` may complement;
they are **not** the skip signal.

## Entry

| Caller | How |
|---|---|
| Cursor IDE | `/layer` |
| Dispatch / charter / cursor-auto | `Use the abstraction-layering skill` |
| Charter tick | G1–G6 CHECKPOINT (L3 `tick-enrollment-annex.md`) |

`layer todo:{slug}` · `layer friction {id}` · `layer=architecture|frame|densify|check|implement`

## Arc (G1–G6)

Tick recognizes `[GR]\d+` only — layer names never replace G-ordinals in Steps.

| G | Layer | Seat | Token | Exit |
|---|---|---|---|---|
| 1 | Architecture | Fable/wide CDP | `[consult:judgment_gap]` | `fable-answer.md` |
| 2 | Frame | Opus, minimal `skills=` | `[consult:judgment_gap]` | `opus-grok-instructions.md` ≤120L |
| 3 | Densify | `cursor/grok-4.5` | `[judgment]` | `specs/{slug}.md` + Gate-2 |
| 4 | Check | `cursor/gpt-5.6-terra` | `[judgment]` | check sidecar |
| 5 | Implement | `cursor/composer-2.5` | `[implement]` | code + quality gate |
| 6 | Verify | inline | `[inline]` | ACs, docstrings, close |

**G4 Check diversity (6524 R4, BINDING):** substrate diversity is discharged **upstream**
at G1/G2 via ``independence_ok`` branch (A) cross-substrate consult or architecture
``derived_from`` edge — not a per-gate seat requirement at G4. G4 Check owes **family**
diversity from G3 Densify (`family(G3) ≠ family(G4)`); both seats may share
cursor-sdk substrate by design. Under ``attendance=operator_proxy``, branch (C)
``operator_proxy_attends`` satisfies the upstream arm live (R3a: web-anthropic
operator vs cursor executor). Seat bind locus:
``charter_runner/window_exec/materializer_layer.py`` (`LAYER_G3_SEAT` /
``LAYER_G4_SEAT``); ``layer_g4_check_family_diverse()`` enforces the floor.

Web corpus: `cortex://` only.

## Stage 0 — todo first

`¬ dispatch before todo.` **Mint path SOT:** Use the `work-item-seed-path` skill
(`/work-item-seed`) when no closable work item exists yet — recon?, architecture order A/B,
rich-seed|backlog|hold, then hand off into this lane. This section keeps **attrs + attach
duty** only; ¬ re-derive seed-path stages here.

Friction → `todo:{slug}` + attrs (via seed path or equivalent). Key attrs: `density_triage`
(`mechanical` ⇒ G5), `executor_harness=cursor-sdk`, `required_skills` includes this
slug + `cheap-recon-before-escalation` + docstring/event floors, `dispatch_lane=path-sim-admit-gate`,
`arc_lane=layer` (recommended explicit stamp for layer charter enrollments; unset defaults
to `layer` per G6), `source_uri=cortex://notes/system/specs/{slug}.md`. Bus root `layer-{todo-slug}`
(new enrollments; legacy `densify-{todo-slug}` roots remain valid if already live).

### Architecture-consult attach (seeding duty — BINDING)

When a G1 architecture consult has produced (or will produce) an answer the work item
binds against — including **Fable-before-seed** (operator/architecture-open carve-out of
todo-first dispatch; seed-path S3 mode B) — stamp the graph before `/layer` proceeds past G1:

1. **Answer sidecar** at `cortex://notes/system/threads/…` (or consultations/) — durable text.
2. **Document entity** for that answer: `document:{slug}-architecture-consult` (or stable
   equivalent) with attrs:
   - `consult_kind=architecture`
   - `source_uri=<answer cortex URI>`
   - optional: `envelope_kind=architecture-consult` (R1)
3. **Structural relationship:** `relationship_create(
     source_id=todo:{slug},
     target_id=document:{…},
     type_id=derived_from
   )` — work item **derived from** the consult. Prefer structural relationships over
   session `edge_create` (cross-seat / cross-session skip must survive).
4. Optional complement: G1 RATIFIED assertion with `evidence_uris` → same answer URI.

**Order variants (equivalent once the edge exists):**

| Sequence | When |
|---|---|
| todo → `/layer` G1 Fable → harvest → document + `derived_from` → G2 | Default Stage 0 |
| Fable first → harvest → seed todo + document + `derived_from` → `/layer` enters G2 | Architecture-open / nebulous; operator asked consult-before-seed |

¬ leave a harvested Fable answer unlinked on a seeded todo — that forces tribal “inform the
agent” and re-risks a duplicate G1.

## Recon

Use the `cheap-recon-before-escalation` skill. Tier-1 breadth → **Explore subagent**
(`Task(subagent_type="explore")`); ¬ Composer as recon worker; ¬ lead-as-recon-worker.
Tier-1 → `cortex://notes/system/recon/{slug}/tier1-anchors.md`.

## Gates

1. **G1** — rival-shape only; verdict sidecar, not spec. Envelope template (four parts, pair
   rule): `cortex://notes/system/specs/contract-envelope-v0.md` — ¬ a freestanding inline template.
   Answer shape = R1 `output_envelope` (8 core + 5 conditional-by-kind + quality bar; §8 Falsifiers
   MUST open with the **Adjudication check** line):
   `cortex://notes/system/specs/lane-architecture-consult-brief-template-v2.md` — specializes
   envelope **R1** (semantic locator: registry URI + row id; ¬ row sha — W7); ¬ restate envelope vocabulary.
   **Exit also stamps** the architecture-consult document + `derived_from` edge (§ Stage 0 attach)
   before G1→2 — that edge is the standing skip signal for later `/layer` entry.
2. **G2** — Opus → densifier instructions ≤120L; ¬ dense spec.
3. **G3** — Grok dense spec; Gate-2 (`doc_validate`, attrs, `implement_ready`, STOP).
4. **G4** — merged GPT check; fold amendments + refresh `spec_sha256`.
5. **G5** — Composer `contract=implement`, `source_ref=todo:{slug}`.
6. **G6** — mechanical: gates · `files_expected` · ACs · docstrings · `friction_close` · `implement-todo` §5.

**Mechanical leg:** G5+G6 when no gate above open. **Escalate** (re-enter highest gate):
architecture re-opens · ≥2 rivals · invariant/cross-agent · failure ≥2×.

## Auto-advance

| Hop | When |
|---|---|
| G1→2 | Verdict sidecar; **lead-fired, advise-only** — adjudication duties AD1–AD5, typed closeout block, block list: `cortex://notes/system/specs/g1-g2-adjudication-transfer-gate-v1.md` (auto-advance retired as a category) |
| G2→3 | Frame ≤120L |
| G3→4 | `doc_validate` · zero `OPEN:` · `implement_ready` |
| G4→5 | check done · preflight admitted |
| G5→6 | implement ∩ `files_expected` |
| G6→✓ | gates green · AC ledger |

G1→2 is **advise-only** — judgment content, no validator (`decision:verifier-detent`;
envelope advise-vs-reject). G3→4 / G5→6 keep their reject-mode checkers.

## Tick enrollment

Scoreboard → todo attrs → CHECKPOINT on `layer-{slug}` + `enroll_charter_runner=true`.
Attended default (leave `attendance` unset); `attendance=autonomous` allowed with
`arc_lane=layer` (recommended) or unset (defaults layer). Stamp `arc_lane=path_sim`
only for deepen / non-layer arcs. Annotate every G-row. Template + mechanics:
L3 `tick-enrollment-annex.md`.

## Forbidden / anti-patterns

¬ Opus dense spec · ¬ G2+G3 merge · ¬ R-windows on codework · ¬ dispatch w/o todo ·
¬ non-G tokens · ¬ claim autonomous without layer when `arc_lane=path_sim` was intended
(stamp explicit `path_sim` for deepen arcs) · ¬ G3
implements · ¬ G6 review consult · frame >120L · bare tick w/o G-rows ·
¬ claim G1 closed from chat inform / sidecar path alone without `derived_from` →
`consult_kind=architecture` document · ¬ mint a second Fable G1 when that edge already
resolves.

## Conformance

Entry declared · todo w/ `required_skills` · durable exit artifact · auto-advance met ·
closed gates `[x]` · ratification inherited. Miss ⇒
`friction(owner=agent_skill:abstraction-layering)`.

## SoT

`decision:abstraction-layering` · `consult-routing` § Abstraction layering ·
`agent-bus-discipline` § CHECKPOINT · L3 `tick-enrollment-annex.md`. Codework only;
fat-packet deepen stays `path-sim`. Former slug/command: `densify-abstraction-layering` /
`/densify` (retired aliases — prefer this skill + `/layer`).

## Skills

`work-item-seed-path` (pre-lane mint) · `consult-routing` · `cheap-recon-before-escalation` ·
`handoff-packet-authoring` · `claude-ai-cdp-navigation` · `agent-bus-discipline` ·
`implement-todo` · `event-instrumentation-discipline`
