<!-- target:* -->
# Todo Lifecycle

## Architecture

Todos are Cortex entities (`type: "todo"`) in `~/.cortex/cortex.db`.

**Entity schema**: `id: todo:{slug}`,
`workflow_state: open|in_progress|blocked|done|deferred|cancelled` (typed column,
enum-validated against the `workflow_schemas` registry),
`attributes: {priority, domain, required_skills: [...], backlog?, seed_contract_ack?,
dispatch_lane?, density?, density_triage?, density_triaged_by?,
density_triage_evidence_uri?, executor_harness?, artifact_uri?, block_reason?}`.
Rich todos have `source_uri: tasks/specs/{slug}.md` — spec files are RAG-indexed
in the `todo_specs` scope for semantic search.

**Priority**: `high` | `medium` | `backlog`
**Domain**: `rag` | `pipeline` | `mcp` | `tooling` | `cortex` | `infra` | `agent`

## Two-Tier Todo Contract

∀ todo: it is **one of exactly two tiers** at creation:

| Tier | When | Required fields |
|---|---|---|
| **Implementation-intent** (default) | Intent to implement (any non-deferred/backlog creation) | `source_uri` (stub spec), `required_skills` (≥1), `priority`, `domain`, ≥1 context edge (`references→decision:*`, `relates_to→service:*`, or `evidence_uris→agent-bus:N`) |
| **Backlog-only** (explicit) | `workflow_state=deferred` OR `attributes.backlog=true` OR operator says "not yet" / "backlog" | `priority`, `domain` only — no stub spec required |

**Prose mentions in description do not satisfy the implementation-intent contract.**
Only graph-traversable edges count. The `todo_implementation_seed_incomplete` audit
gate (WARNING severity) fires on open/in_progress todos missing any of the three
required fields. Suppressed via `attributes.backlog=true` or
`attributes.seed_contract_ack='<reason>'` (documented escape hatch).

**Suppression scope — audit only, never implement admission.** `backlog` and
`seed_contract_ack` silence the *structural audit* finding; they do **not** grant
implement-dispatch admission. ∀ implement dispatch: the precondition is an **active
implement-ready assertion** citing a dense spec — a generic ack cannot substitute
(it would re-open the gap an ack was never meant to cover).

**Handoff invariant**: ∀ implementation-intent todo: a fresh agent picking up the
todo should produce dense phase docs with zero external session memory —
`entity_get(todo:{slug})` + `source_uri` (stub spec) + edge traversal ⟹ complete
context.

## Domain → default `required_skills`

∀ todo: `required_skills` is the skill set a pickup loads **before** gauging
readiness. At creation, attach skills by domain so pickup is never skill-blind.
The defaults below are the floor — add domain-specific skills on top.

| Domain | Default `required_skills` |
|---|---|
| *(any repo work — the floor)* | the repo's core architecture-invariants skill |
| `pipeline` | + build-pipeline (new pipeline) or refine-pipeline (eval/iterate) |
| `consult` / dispatch / review | + consult-routing |
| plan arc (multi-phase) | + implementation-plan-workflow |
| rag / corpus | + research-article-ingest |

**Tier rules:**

- **Implementation-intent todos** (default tier) MUST carry ≥1 skill — the repo's
  core pair at minimum for any repo work. This is the `required_skills` field of
  the rich-seed contract above; the `todo_implementation_seed_incomplete` gate
  fires without it.
- **Backlog-only todos** (`backlog=true` / `deferred`) are exempt from the gate,
  but SHOULD still carry the predicted skills when the domain is known — it costs
  nothing at creation and saves a checkpoint at pickup.

The pickup-side fallback (empty set ∧ known repo → default-load the core pair) is
a safety net, not a substitute for seeding the attribute. Seed at creation; let the
fallback catch only genuinely domain-unknown todos.

**Skill read path at pickup**: ∀ slug in `required_skills`,
`entity_get(agent_skill:{slug})` → read `source_uri`. Seed-time `requires` edges
(one per slug) are mandatory alongside the attribute — an audit fires when they
diverge. After the floor, agents scan the skills index + boot manifest triggers
for task-relevant extras.

## Grouping

When ≥2 todos share one deliverable arc, group under a **`task:`** container (spec +
`child_of` edges) — **not** a master-`todo:`. A `todo:` is always a **leaf**
(closable unit of work).

The `{slug}` is canonical: entity slug ≡ spec basename ≡ phase_dir ≡ plan slug —
tools derive the name; they do not ask for it.

### Container: `task:` vs `plan:` vs `project:`

| Container | When | Leaf unit |
|---|---|---|
| **`task:{slug}`** + leaf `todo:` | Bounded arc of ≥2 serial/parallel leaf todos; phase-free (no formal phase deck); optional `related_to` → umbrella `project:` | Always `todo:{slug}` — never a master todo |
| **`plan:{slug}`** + **`plan_phase:`** | Phased implementation deck workflow; phase docs per slug; expected executor per phase | One `todo:` per closable leaf (may mirror phases) |
| **`project:{slug}`** | Long-horizon umbrella track; vision-level; holds multiple `task:` / `plan:` / freestanding `todo:` children | Never a closable execution container itself |

**Pick `task:`** for a bounded multi-todo arc without a formal plan deck.

**Pick `plan:`** when work is authored as a **phased implementation deck** with
per-phase density and executor in spec headers.

**Pick `project:`** for umbrella tracks only. A `task:` MAY `related_to` (or
`child_of`) an umbrella `project:` for portfolio association — promotion from
`task:` → `project:` is optional and rare (arc outgrows bounded scope).

A single umbrella `project:` may hold both `task:` arcs and `plan:` decks.

### Dispatch metadata (`dispatch_lane`, `density`, `executor_harness`)

Encode **who produces the next artifact** and **expected execution seat** on
implementation-intent todos. Keeps `workflow_state` for execution progress only
(`open` · `in_progress` · `blocked` · `done` · `deferred`).

| Attribute | Values | Purpose |
|---|---|---|
| `dispatch_lane` | dispatch-transport specific values | Who authors the next artifact |
| `density` | `sparse` · `dense` · `mechanical` · `exploration` | Plan-density classification |
| `density_triage` | `mechanical` · `judgment_required` · `unknown` | **Declared** staging-tier verdict — who may stage how (set by an authorized reasoner/operator, ¬ inferred by the stager) |
| `density_triaged_by` | agent/operator id | Who recorded the triage verdict |
| `density_triage_evidence_uri` | spec / assertion / thread URI | Cited basis for the verdict |
| `executor_harness` | expected execution seat identifier | Expected execution seat |
| `artifact_uri` | canonical URI | Canonical implement packet or phase doc when known |
| `block_reason` | string | When `workflow_state: blocked` — e.g. falsifier, packet accept, operator assert |

**Staging-tier triage (declared-state, ¬ inferred).** Deciding "densify by a
reasoning tier vs stage mechanically" is an **escalation** decision — the tier
that would need to escalate empirically is the one that won't. So it is
**declared**, not heuristically inferred: an authorized reasoner/operator sets
`density_triage` before staging.
- `judgment_required` ⟹ reasoning-authorized tier densifies; ¬ mechanical stage.
- `mechanical` ⟹ bounded mechanical path admissible iff preconditions hold (dense
  source, `required_skills`, context edge, no open forks).
- `unknown` / unset ⟹ implement dispatch **blocked**; only consult/densify
  admissible.

## Access

Query open todos via the entities API (`type=todo`, `workflow_state=open`) or the
equivalent SQL read-model.

## Completion

**Preferred**: use the atomic todo-close pipeline, which writes the closure
**sidecar** (a human-readable index at the todo's notes location + a
`closure_summary_uri` attribute), the closure assertion (citing the sidecar URI
in `evidence_uris`), references, `depends_on` edges, and the `workflow_state=done`
flip in one call. Structured per-step results keep partial failures visible.

**Closure payload fields**: `todo_id` (required), `summary` (required, becomes the
closure assertion's claim), `evidence_text`, `evidence_uris`, `reasoning_summary`,
`references` (`[{target, type_id?, role?, evidence?, strength?}]` → relationships),
`depends_on_resolved` (assertion ids → `depends_on` edges), `edges` (escape hatch),
`skip_workflow_update` (backfill mode), `agent`, `session_id`.

**Closure sidecar convention**: on success, the closure writes a human-readable
index (sections: Summary, Reasoning, Evidence, References — built from the
closure payload), sets `closure_summary_uri` on the todo (merge — existing
attributes preserved), and the closure assertion cites that URI in
`evidence_uris`. The `done` todo card surfaces `closure_summary_uri`. This is the
todo analogue of an agent-bus thread-sidecar convention. The assertion remains
the audit trail; the sidecar is the human-readable index. Sidecar write is
best-effort — a failure is recorded in per-step results but does not abort the
audit-trail writes.

∀ priority=high todo: close via the todo-close pipeline. For lower-stakes todos,
manual entity update + assertion is permitted, but a closure-gap signal fires when
a todo transitions to `done` with zero assertions on the entity. Surface the gap,
do not ignore it.

**Session-close gate**: session-close protocols require todo **reconciliation**
before closing a session: inventory touched todos → live `entity_get` each →
determine completion → close via the todo-close pipeline when acceptance is met
but `workflow_state` is still open/in_progress → re-verify done → then close the
session. Session close does not mark todos done by itself.

**Manual fallback** (only if the pipeline is unavailable): entity update to
`workflow_state: done` + seed a completion assertion with what was done and why.
The write path validates `workflow_state` against the registered enum for `todo`.

## Vision Awareness

∀ discussion involving architecture decisions, new subsystems, or strategic
direction: consult the project vision document to check alignment with the
long-term roadmap. Surface misalignment or synergy when relevant — don't force it
into every conversation.

## Discoveries

∀ discussion that surfaces a non-obvious architectural insight: check the
discoveries archive for existing related insights before proposing new ones.
<!-- /target:* -->
