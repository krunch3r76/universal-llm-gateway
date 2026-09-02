# Handoff packet authoring — detail annex (L3)

Load on demand for templates, web priming checklists, and block-by-block primers relocated from L2.

## Framing house style (merged from dispatch-prompt-house-style)

1. `dispatch_prompt ⇒ anchors_before_prose` — ids/paths/errors first; unverified ⇒ `hypothesis; re-derive`.
2. `scope ⇒ fence_in ∧ fence_out`.
3. `grant(fix_authority) ⇒ bind(scope ∧ no-bc ∧ live-verify ∧ no-repro ∧ provenance)`.
4. `output_body > 8000_chars ⇒ sidecar_first`.
5. **Corpus posture** — web/life: cortex pointers; code seats: pointer_first when MCP-on.
6. Match ceremony to leg: full six blocks for implement/consult; trimmed light-bounded; 7-part kickoff via
   `handoff-prompt-authoring`.
7. `refer_to_skill ⇒ canonical_name`; web-anthropic exception: skill-inline gate (full bodies).

## Gate 2 — expanded sequence

1. **Verify lane** — `dispatch_lane ∈ {web-spec, web-implement-packet}`; wrong lane ⇒ stop.
2. **Seed stub spec** — `doc_template(implement_dense_spec)`; layer verdict + forks; `entity_update(source_uri)`.
3. **Author consult brief** — `tmp/reviews/{slug}-harden-web-consult-packet.md`; `contract: consult`; scaffold
   non-authoritative.
4. **Dispatch** — `team_dispatch(op=handoff, role=web-consult, packet_path=…)`; ¬ `contract=consult` param (422).
5. **Hand back** thread + `push_reminder`.
6. **Gate-2 close distillation** — `files_expected`, `acceptance_criteria`, `required_skills` (catalog-registered only).
7. **Entity hygiene** — implement-ready assertion + `spec_sha256`.

### Dense-spec heading phrases (friction 21659)

`validate_dense_spec` matches heading **text** by regex:

| Section | Heading must contain |
|---|---|
| problem | `problem` |
| non_goals | `non-goal` or `scope exclusion` |
| provenance | `source-of-truth` or `provenance` |
| touch_points | `touch-point` / `touchpoint` |
| forks | `bound design`, `fork table`, `design decision`, `resolved fork` |
| implementation | `implementation guidance` or `implementation steps` |
| acceptance | `acceptance` |
| verification | `verification` or `quality gate` |

Plus `<reasoning_trace>` with literal `no fork remains open`.

### Skeptic ratification bindings (frictions 21656, 22008)

- `spec_sha256:<hex>` MUST be in `skeptic_ratified` assertion `evidence_uris`.
- Cite ratifying bus turn `agent-bus:{id}#turn-{N}`, not orchestration root.
- Bus reply MUST carry `FILE_EVIDENCE_PATHS:` block (one path per line). Parser reads turn body only.
- `<output_format>` must demand fs-verify before citing paths (friction 23526).

## Gate 3 — reject branches (full)

| Condition | Verdict | Action |
|---|---|---|
| Missing attrs | `implement_attrs_unpopulated` | distill/backfill |
| Bad `required_skills` slug | `required_skills_uncatalogued` (write-time 422) / `SkillCatalogResolveError` (materialize) | catalog-registered skill slug only — rule `*_ulg.mdc` stems (e.g. `skill-surface`) are not valid `required_skills` |
| No implement-ready | not ready | return Gate 2 |
| Open fork | not ready | resolve first |
| Spec/attrs drift | `implement_spec_drifted_since_ready` | refresh assertion |
| Inspect artifact | W4 `contract=wrap` | materialize only |
| Manual transport | W1 `packet_path`/handoff | exception |

Wrap triggers: W4 inspection; W1 manual/alternate; W1 non-projectable corpus; break-glass materializer incident.
NOT wrap: missing attrs, open fork, no assertion, batch without plan route.

## Wrap vocabulary

- **W1** lifecycle Gate-3 wrap: hand-authored packet, no dispatch.
- **W2** act split: artifact-gen vs implement dispatch.
- **W3** `prepare_implement_packet`: server gate+materialize.
- **W4** `contract=wrap`: materialize without Composer.

Legacy inline wrap: read todo + assertion → verify spec → write `tmp/reviews/{slug}-implement-packet.md` → dispatch.
¬ dispatch wrap step to cursor-sdk.

## CONFORM / CONVERSE lanes

**CONFORM:** loose intent → conforming todo → wrap. Envelope fields + `light-bounded` generate; Layer 1+2 verify.
Blocked until N≥5 runs.

**CONVERSE:** latent forks → dialogue → envelope → CONFORM. 3-round budget; lead-run only. Blocked until N≥8 episodes.

## General execution without packet

```text
team_dispatch(op=generate, seat=cursor-sdk, dispatch_thread_id=…, contract=light-bounded|pure-mechanical)
```

Load `cursor-sdk-instruction-standard` (D1–D4). Model split: recon+investigate → **`seat=cursor-sdk` `contract=investigate`**; implement → Composer.

## Friction-ticket packet preflight

| Check | Why |
|---|---|
| Friction ID → `service:*` assertion | avoid ID mixups |
| Bound task not `done` | avoid closed-arc investigate |
| Corpus names exact `entity_id` | no slug drift |
| `<mcp_capabilities>` uses same service slug | no guessed service |
| Operator confirmed intent | typo ⇒ void |

See `friction-review` § Friction ID preflight.

## Web-receiver priming checklist

For `team_dispatch(op=handoff, role=web-consult|web-implement)`: web attaches **life only** — no workspaces fs,
no IDE rules/skills/terminals.

### Web-anthropic skill-inline gate (binding)

Slug-only `Use the <slug>` is **not reliable** on web-anthropic. Exactly one channel:

1. Inline full bodies at enrich (`<!-- skill-inline:<slug> digest:sha256:… -->`), budget-gated.
2. Treat inlined text as binding; ¬ fs-read skill paths for allowlist slugs.
3. Block-2 may name slugs for orientation only.
4. Happy path: inline before operator push.

Manual cortex sidecar + `allow_long_body` = fallback for hand-assembled dispatches.

### Life-surface cortex-mirror gate (binding)

**SOT shared:** `life-handoff-corpus` skill.

**Mirror prefix:** `cortex://ephemeral/handoffs/…` — preferred packaged corpus (fewer tool calls); not durable product docs.

Before dispatch:

1. `packet_path` may resolve from checkout `tmp/reviews/…`.
2. **Prefer:** mirror verbatim to `cortex://ephemeral/handoffs/<thread-or-slug>-<subject>.md`; cite in bus pointer — packaging beats open-ended browse for speed/targeting.
3. Life/web `<corpus>`: `cortex://`, entities, `agent-bus:` preferred; `workspaces://` is readable — name it explicitly when exploration is encouraged.
4. Hot checkout evidence ⇒ mirror to `ephemeral/handoffs/` pre-dispatch when the packet should stay lean.
5. Required skills ⇒ skill-inline gate (full bodies).

Code-surface receivers may keep `workspaces://` pointers as routine.

### Life/web post-implement code review

Composed path: `web-consult` + cortex mirrors + CDP bus-nudge. Checklist:
`cortex://notes/system/threads/web-anthropic-code-review-practice.md`.

Binding deltas: lean packet ≤~15KB; corpus-completeness; full files + untruncated sha256; Verdict trichotomy +
Amendments + Code-surface follow-ons in `<output_format>`.

### Frontmatter boot gate

Required: `active_project_tag`, `cortex_brief_confirmed: true`, `related_thread_ids`, bound `todo:`/`plan:`.

### Block 2 `<invariants>` skill refs

ULG code consults: include `reasoning-posture`, `architecture-invariants`, `ulg-architecture`, `docstring-quality`. Minimum web set:
`lead-seat-boot` or `cortex_brief_confirmed`; `life-handoff-corpus`; `consult-routing` when implement-ready possible;
≥1 task-class skill; all `required_skills`.

### Block 4 `<corpus>` repo pointers

| Receiver | Allowed pointers |
|---|---|
| Life / web-anthropic | `cortex://`, entities, `agent-bus:` preferred; `workspaces://` readable — name when exploration is encouraged |
| Code / workspaces MCP | + `workspaces://` routine |

Skill delivery ≠ corpus pointers. Inline-only receivers (`mcp=false`) keep full inline packet.

### Block 5 `<mcp_capabilities>` — life/code split

**Life/web default:** `LIFE/CORTEX MCP: ON` + `CODE/VORTEX MCP: OFF`. Default steps: `cortex(entity_get/search)`,
`agent_bus(fetch/reply)`, `fs(sandbox="cortex", op="read")`; `fs(sandbox="workspaces")` when the packet
encourages exploration. ¬ team_dispatch, pipeline, manage, observability on life-only. ¬ invent `model=` on handoff.

**Code seats:** numbered plan with boot, bus/cortex fetches, `fs(read)` on openable sandboxes, live probes.

### Pre-dispatch self-check

frontmatter gate · task-class skill · arch pair when ULG · required_skills mirrored · life/web: mirror + inline
skills · code MCP: thread fetches · behavior-touching spec has event vocabulary · scaffolds carry no design judgment.

## Block 6 output format by worker tier

- **Life/web:** deliverable = bus reply default; cortex writes only when task/output authorizes.
- **Code MCP:** cortex sidecar `notes/system/threads/…` + brief bus pointer ≤2KB (8KB limit; 64KB w/
  `allow_long_body`).
- **Inline/no-MCP:** full closeout inline; Stargate may set `allow_long_body`.

Skeptic packets: `<output_format>` MUST demand `FILE_EVIDENCE_PATHS:` in bus reply turn.

`ruff check` on `.py` only (friction 20766).

## Skeleton (paste template)

```markdown
---
contract: consult   # required on consult/light-bounded; implement uses implement
---
<scope>
Goal: <one-line>. Selection mode: <targeted|branch|path>.
Primary artifacts: <paths>. Out of scope: <...>.
</scope>

<invariants>
Read before editing — ULG/code packets ONLY:
- Use the `architecture-invariants` skill
- Use the `ulg-architecture` skill (when ULG)
- Use the `docstring-quality` skill (when ULG code)
Per-task narrowing:
| Tag | Rule |
|---|---|
| [universal:no-bc] | delete old surfaces; update consumers |
| [scope] | every changed line traces to task |
| [quality] | SLOC gates on code change |
</invariants>

<task_guidance>
For implement: ## Acceptance criteria (numbered, all required).
</task_guidance>

<corpus>
Pointers / artifacts / incident context.
</corpus>

<mcp_capabilities>
LIFE/CORTEX MCP: ON — cortex(entity_get/search), agent_bus(fetch/reply), fs(sandbox="cortex", op="read").
CODE/VORTEX MCP: OFF — no workspaces or code-only tools.
</mcp_capabilities>

<output_format>
Findings or closeout table.
</output_format>
```

## Preliminary scaffold → densification

| Block | Scaffold | Densify |
|---|---|---|
| `<scope>` | path list/git SHA | — |
| `<corpus>` | changed-file manifest | — |
| `<invariants>` | skill refs/boilerplate | task narrowing |
| `<output_format>` | closeout boilerplate | — |
| `<task_guidance>` | headers/stubs | judgment/questions/ACs |
| `<mcp_capabilities>` | tool list | evidence specifics |

Reasoner treats scaffold as fallible; re-derive from primary artifacts.

## Skill load resolution — implementation detail

Handoff route auto-materializes inline bodies for `inline_authoritative` web receivers (`skill_delivery` from
`WEB_RECIPIENT_REACHABILITY`). Budget: `handoff_inline_budget_bytes`. Emits `<!-- skill-inline:<slug> … -->` blocks;
rewrites pointer lines for inlined slugs.

Anti-patterns:

```text
✗ fs(cortex, agent-skills/consult-routing.md)
✗ fs(workspaces, .cursor/skills/…/SKILL.md)
✗ entity_get → source_uri → fs for skill bodies
```

Packet-wired ≠ session-loaded. All skill-ref lines in packet turn 1; no bus supplement after dispatch.
