---
name: friction-review
description: "On friction triage, feature-request vs todo classify, or investigate→execute — friction {id}, type:bug pickup, MCP failure, or file-as-feature."
---

# Friction review

Friction rows are **assertions** on an allowed friction-owner entity (`service:`/`agent_skill:`/`ai_agent:`) with claims like `[tool_error] …`.
They record tool/schema/boot/protocol gaps (F5 funnel) **and** feature asks (`category=feature`).

**Critical split:** `friction()` = **observation log**. A fix cycle = **codified bug ticket**
via the investigate→execute lifecycle; once the investigate close distills attributes, the
execute default is server materialization via
`team_dispatch(op=generate, seat=cursor-sdk, contract=implement, source_ref=todo:{slug})` —
`cursor-implement` / `web-implement` + `packet_path` are the named fallback. Operator says
"dispatch / address / fix the friction" → open the ticket; do not stop at logging.

Full transport matrix: Use the `consult-routing` skill § Codified bug reports.

## Classify before park (feature vs todo vs residual)

Triage **before** choosing a transport or parking a leftover. This section owns
the channel (`friction()` / `todo:` / residual-on-matter / neither). The
dispatch lane is in § Codified bug ticket below and `consult-routing.md` §
Codified bug reports — do not re-derive it here.

| Class | Signals | Park | `todo:`? |
|---|---|---|---|
| **gap** | broke / wrong / tool-schema-boot-protocol miss | `friction(existing category)` | Only if a fix is asked or nameable — then seed path |
| **feature** | "feature request" / "add X" / new capability / "file this" / design still open / "not now" on a *new* ask | `friction(category=feature)` on the owner service/skill; optional edge to a related decision | **No** — substrate defaults `actionable=false` + `defer_enqueue=true` |
| **residual-on-matter** | deferral of an *already-bound* decision/todo/spec | residual-imprint `DEFERRED` on that entity | No |
| **commissioned work** | "address / fix / seed / do this" on a gap or feature | seed path → `todo:` (cite `a:{id}` when a friction exists) | Yes |

`¬` park a feature ask as `DEFERRED` on a decision as the only memory — that
row never appears in `frictions`. `¬` seed a `todo:` because the ask is
memorable; seed when work is commissioned.

| Operator input | `friction()`? | `todo:`? | Then |
|---|---|---|---|
| "Report this as a friction" **and** it names a tool/schema/boot/protocol gap | Yes — log first | Yes if actionable — cite the friction assertion as context | Mint via `work-item-seed-path` (`/work-item-seed friction a:{id}`) when no todo yet → then spawn conductor; existing todo → re-admit |
| "This broke / is wrong / fix this" (defect, clear fix) | Yes if not already logged and it fits a category | Yes — fix-cycle `todo:` unless one exists | Same mint path if no todo; else investigate→execute below |
| "Feature request: add X" / "file this as a feature" — no defect, design may be open | Yes — `category=feature` | **No** unless the operator commissions work | Reopen via `frictions(category=feature)`. Identity-punch only on commission; architecture-open ⇒ Mode B (Fable-before-S4b) |
| "Maybe this is a friction" / ambiguous root cause | Yes only if symptom maps to a category | Yes only if there is an actionable change | Prefer investigate; ask only if it changes the next action; mint via seed path when actionable |
| Pure observation of a real tool/protocol gap, no requested change | Yes — records the gap | No unless a fix is asked for or nameable | None |
| Neither — not a gap, not a feature ask, no commissioned change | No | No | Acknowledge |

**Mint pointer:** when the next act is creating a closable work item (not log-only), Use the
`work-item-seed-path` skill — ¬ invent Stage 0 mint order inside this skill.

> `friction()` is the observation log (gaps *and* feature asks) — never a fix
> ticket by itself. `todo:` tracks commissioned work. A feature ask that is
> not commissioned stays `[feature]` with `actionable=false`; do not substitute
> a backlog `todo:` or a decision residual.

Examples: "report this as a friction, the boot card is stale" → `friction(boot_drift)` +
`todo:` fix cycle. "Feature request: add a dark-mode toggle" → `friction(feature)` only.
"This MCP call just errored out" with no fix asked → `friction(tool_error)` only.

## When to read

- After `cortex(tool="friction", …)` when the defect needs a fix cycle (not log-only).
- Operator: "review frictions", "address the friction", "dispatch to fix friction {id}".
- Before picking up a `type:bug` agent-bus thread about tooling.
- Unexpected MCP/cortex tool failures (log via `friction` if needed, then ticket if actionable).

## Codified bug ticket (investigate→execute fix cycle)

A codified bug ticket follows **recon → investigate/settle → densify → check → execute**.
**Recon is the named default first hop** for feature/bug frictions: load `cheap-recon-before-escalation` and run the axis-1 cost ladder (**Tier-1 breadth → Explore subagent** `Task(subagent_type="explore")` — ¬ tool, ¬ UI Exploring, ¬ Composer-as-recon; judgment residual → `team_dispatch(op=generate, seat=cursor-sdk, model=cursor/grok-4.6, contract=light-bounded)`; Task unavailable / pure mechanical inventory-only → Composer fallback → GPT cross-family filter → Opus/Fable for hard residual).

**Spine vs attended fork (bind before transport):** After a code-lane bug-fix `todo:` exists with `density_triage=judgment_required`, the default is the **autonomous work-item spine** (`decision:autonomous-work-item-spine`; consult-routing § Autonomous work-item spine; todo-lifecycle Gates 3–4):

```
recon → settlement/escalate → densify (Grok) → GPT merged check → Composer implement
```

`web-consult` / attended densify is **escalate / opt-in only** — operator explicitly chooses attended/web, or settlement hits `authority_fork` / confirmed deadlock. Mid-pipeline web densify/check/implement is **forbid**. ¬ let this skill's older web-first tables override the spine after the todo exists (incident: friction 23712 → voided thread 4901).

**Skip recon when:** `density_triage == "mechanical"` or `"trivial"`, a dense implement spec already exists (`files_expected` + `acceptance_criteria` distilled), or explicit `recon_waived="<reason>"` attribute on the todo (waives axis-2 skeptic only — dense-spec checks still apply).

**Default heuristic:** a friction/bug filed → **assume recon+investigate** unless mechanical-only or a dense implement spec already exists. Axis-2 skeptic remains binding on material (`judgment_required`) decisions before `implement_ready`.

**Entity scope vs dispatch lane (do not conflate):** Friction subsumption
(`do NOT open a standalone fix arc`; fold under an existing `todo:`/`task:`) governs
**entity scoping**. It does **not** waive investigate-before-execute. After a
`judgment_required` code-lane todo exists, investigate/settle/densify rides the
**spine** (not automatic `web-consult`) unless the operator opts into attended/web
or `authority_fork` escalates. A friction claim that names an interim remedy is
input to investigate, not authorization to skip investigate and self-execute.

| Stage | When (default) | Transport | Tier |
|---|---|---|---|
| **Investigate + decide** | Root cause unknown / design open; most `friction()` categories except mechanical-only | **Code-lane `judgment_required` todo:** autonomous spine (recon→settle→Grok densify→GPT check). **Attended/web:** only on operator opt-in or `authority_fork` escalate. **Self-contained corpus (no spine todo yet):** GPT generate OK. **`cursor-consult`:** Cursor-seat need or operator ask | spine / GPT / attended opt-in |
| **Execute** | Dense implement spec exists OR mechanical-only | default `team_dispatch(op=generate, seat=cursor-sdk, contract=implement, source_ref=todo:{slug})`; web-native inline `fs` fix when already on web seat; `cursor-implement` / `web-implement` + `packet_path` = named fallback (§ Fallback triggers) | Composer / web |

| Initiator / seat | Investigate (default) | Execute |
|------------------|----------------------|-----------|
| **cursor** (IDE) | Spine for code-lane `judgment_required` todo; GPT generate when corpus self-contained; `web-consult` / `cursor-consult` only on operator opt-in or Cursor-seat need / `authority_fork` | Default: `cursor-sdk` + `source_ref=todo:{slug}` after densify-close + GPT check; fallback: `cursor-implement` handoff |
| **web-anthropic** | May **orchestrate** the spine (dispatch recon/settle/densify/check) — orchestration ≠ mid-pipeline insertion. Routine densify/check/implement still spine/API; `web-consult` packet only when opted-in or escalated | Default: spine → `cursor-sdk` implement, or web-native inline `fs` when already fixing on web; fallback: `web-implement` handoff |
| Operator names different transport | Obey operator or stop and ask — never substitute `agent_bus` for named `team_dispatch` | — |

**Lifecycle (order matters):**

1. **Investigate + decide** — spine default for code-lane `judgment_required`; reproduce, trace root cause, inventory touch points, resolve design choice → dense spec (Grok densify under spine; attended/web only if opted-in / escalated)
2. **Investigate/densify close** — dense spec at `cortex://notes/system/specs/{slug}.md` + `ready-for-Composer-implement` +
   **distill `files_expected` / `acceptance_criteria` (+ `required_skills`) onto the bug-fix `todo:`** +
   implement-ready assertion citing the spec + `spec_sha256` (cross-ref `handoff-packet-authoring.md`
   § Gate 2 step 6 + consult-routing § Densify lane) + spine GPT merged check
3. **Execute** — default `team_dispatch(op=generate, seat=cursor-sdk, contract=implement, source_ref=todo:{slug})`;
   web-native inline `fs` fix on web seat; patch, verify, restart if substrate change
4. **Report** — bus closeout: root cause, paths, verification evidence
5. **Secondary findings** — issues found during investigation → labeled in closeout;
   spin separate friction/handoff if they need their own cycle

**Inline execution — todo entity is optional.** When the operator says "investigate and fix"
and both stages complete in the same session, a friction claim's embedded "promote to investigate
todo" suggestion is satisfied by the inline completion. ¬ create a `todo:` entity for work already
done — use a friction closure assertion (resolution row on `service:*`) as provenance instead.
`todo:` is warranted only when the work spans sessions, requires handoff, or has residual open
items. See lesson `tasks/lessons/tooling-inline-todo-redundancy.md`.

## Fallback triggers (named exceptions)

`cursor-implement` / `web-implement` handoff and hand-authored `packet_path` remain correct only in the
closed wrap-exception set — see `handoff-packet-authoring.md` § Gate 3 ("Wrap — four senses" + branch
table) and `handoff-dispatchers.mdc`. Do not re-state that table here.

## Pass zoom-out duty

∀ `type:bug` agent-bus pickup or bug handoff to **web-anthropic** / **cursor**: **zoom out in the pass** — widen beyond the filed symptom before the investigate stage completes or the execute stage is declared done. SOT detail: consult-routing § Codified bug reports → Pass zoom-out duty.

| Pass stage | Mandatory zoom-out |
|---|---|
| **Investigate** | Touch-point inventory; grep bug-class pattern service-wide; audit sibling call sites in the same subsystem |
| **Execute** | `[quality:bug-class-sweep]` before `declare_complete` — fix every instance or defer with `SF{n}` |
| **Closeout** | `## Secondary findings (labeled — separate cycle if pursued)` — `None observed.` if empty |

Per finding: disposition `verify-now` | `flag-deferred` | `spin-ticket`. Zoom-out runs inside the bug cycle; it does not block the primary fix with an open-ended redesign.

**NOT** a codified bug ticket: `agent_bus`-only thin ping; `friction()` without handoff when
fix is required; redesign/graph-walk **before** investigate.

**Anti-pattern (premature execute):** `cursor-implement` as the **first** dispatch on a bug that
still needs a touch-point inventory or design resolution (incident: friction 13571 → thread 1377).
Default to recon/investigate (spine for code-lane `judgment_required`); reach for
`cursor-implement` only against a dense spec or operator-confirmed mechanical-only scope.

**Anti-pattern (spine override):** after a `judgment_required` code-lane todo exists, dispatching
`web-consult` densify/investigate as the default because this skill historically preferred web
(incident: friction 23712 → voided thread 4901 / friction 23719). Bind the spine-vs-attended
fork; keep web as named non-default.

Web handoff preflight (when web is opted-in/escalated): `agent-guides/web-agent-orientation.md` § Handoff preflight.

## Lookup paths

### Cross-service (default)

```
cortex(tool="frictions", arguments='{"limit": 30}')
cortex(tool="frictions", arguments='{"category": "tool_error", "seeded_by": "web-anthropic", "limit": 20}')
```

### One service

```
cortex(tool="assertions", arguments='{"entity_id": "service:mcp-server", "filter": "tool_error", "superseded": false, "limit": 20}')
```

### Bus queue

```
agent_bus(tool="threads", arguments='{"tags": ["type:bug"], "status": "active"}')
```

Filing agents may post bus threads separately from `friction()` — use both queues.

## Friction ID preflight (cursor — before `team_dispatch`)

**Incident:** friction 16849 / thread 1576 — operator said "friction 16737" (not a friction row);
Composer dispatched a densify consult anyway. Web burned a full investigate pass reconciling stale
packet claims vs live cortex (16724 on `service:universal-stargate`, task already `done`).

Before firing `team_dispatch(op=handoff)` on a friction arc:

1. **Resolve the ID** — friction rows are assertions on an allowed friction-owner entity (`service:`/`agent_skill:`/`ai_agent:`) with `[category]` claims.
   An assertion on `task:`/`decision:` is **not** a friction ID even if the number matches.
   ```
   cortex(tool="assertions", arguments='{"entity_id_prefix":"service:","filter":"[{category}]","limit":50}')
   ```
   Or direct lookup: confirm `entity_id` starts with an allowed owner prefix (`service:`/`agent_skill:`/`ai_agent:`) and claim starts with `[` (omit the prefix filter, or pass `owner_type=`, to span all owner types).

2. **Check disposition** — if the bound task is `workflow_state: done` or a resolution assertion
   already exists (`[resolved:…] Friction #N closed`), do **not** dispatch investigate;
   close the friction row or tell the operator the arc is complete.

3. **Stamp the packet** — corpus MUST include exact `entity_id` (e.g. `service:universal-stargate`,
   not `service:stargate`) and a **live-read timestamp** or instruct web to re-fetch state first.

4. **Operator intent** — if the operator's message is ambiguous (wrong ID, "typo", "don't dispatch"),
   **stop** — do not fire `team_dispatch`. Confirm the friction ID and intent in chat first.

## Void / recall (accidental dispatch)

When a handoff was fired by mistake (operator: "typo", "don't dispatch", "cancel thread N"):

**Cursor (dispatching seat)** — before web picks up:
```
agent_bus(tool="update_thread", arguments='{"thread":"<id>","tags":["dispatch:void"],"from_agent":"cursor"}')
agent_bus(tool="close", arguments='{"thread":"<id>","summary":"Void: accidental dispatch — operator typo. No work required."}')
```

**Web (receiving seat)** — turn-1 pickup gate (before loading packet / tier check / investigate):
- Thread tag `dispatch:void` → close immediately, one-line ack, **no** packet read, **no** findings.
- Turn body or operator chat contains void/recall/typo for this thread → same.
- Corpus "open friction" contradicts live `frictions`/`assertions` → **trust live state**, note
  stale packet in closeout, do not spend a pass reconciling unless operator confirms investigate.

Log the misfire: `cortex(tool="friction", service="agent-bus", category="tool_error", ...)`.

## Close (after fix)

```
cortex(tool="friction_close", arguments='{"assertion_id": ID, "resolution_kind": "agent_skill:slug"}')
```

Valid `resolution_kind`: `agent_skill:{slug}`, `workflow:{slug}`, `todo:{slug}`, `commit:{sha}`, `superseded`, `wontfix`.

## Log new friction (observation only)

```
cortex(tool="friction", arguments='{"service": "mcp-server", "category": "tool_error", "note": "...", "agent": "web-anthropic"}')
```

Categories: `tool_error`, `tool_mismatch`, `tool_absent`, `schema_gap`, `boot_drift`,
`lesson_gap`, `lesson_conflict`, `stale_context`, `doc_drift`, `protocol`,
`regression`, `feature`.

## Protocol anchor variants (step-7)

Sweep-eligible `[protocol]` rows require exactly one anchor variant:

| Variant | Fields | Actioning |
|---|---|---|
| **charter** | `charter_root` + `window_index` | `reconcile_charter_frictions` auto-mints follow-on todos |
| **continuity** | `root_thread` + `cp_ordinal` | Manual friction-review lane + root CHECKPOINT `## Frictions` line |

**Down-cast is a filing error:** once continuity filing exists, observing a protocol-class
defect on a non-enrolled root as `[schema_gap]` instead of `[protocol]` with a continuity
anchor is wrong — file the correct variant. `checkpoint_turn` (bus turn pointer) ≠
`cp_ordinal` (checkpoint ordinal counter).

Continuity rows are **not** swept by `reconcile_charter_frictions(charter_root=…)`.
Follow-on automation tracked under `todo:continuity-friction-sweep`.

## Avoid

- Fixing only the filed symptom without pass zoom-out (no touch-point sweep, no labeled secondary findings).
- Treating `friction()` as submitting a fix-cycle ticket.
- `cortex(tool="search", …)` for `[tool_error]` alone — prefer `frictions` / `assertions`.
- Scraping `review_status=staged` — friction tickets are normal service assertions.
