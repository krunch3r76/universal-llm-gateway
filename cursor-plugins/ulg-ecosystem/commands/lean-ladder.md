Lean ladder — architect confer → plan → Composer implement, planning externalized to files, every dispatch authored under retrieval.

## What this command does

Runs one work item through four legs while the orchestrating seat stays lean:

1. **Confer** — a CDP architect (default `cdp/fable-5.2`) reads a packet and returns a verdict.
2. **Plan** — a planner (this tab, or a glass tab on `:8`) reads the confer by section and writes a plan file: wire/design, `files_expected`, numbered AC, pytest matrix, falsification table, and confer-risk accounting (procedure in the template).
3. **Implement** — Composer on `seat=cursor-sdk` lane B executes the plan file and quotes pytest in its closeout; it does not land.
4. **Pre-land review** — after Leg 3, `cursor/composer-2.5` (fresh context) and a one-job Grok Bot on display `:9` review the lane commit at whole files; both must pass before merge (procedure in the template).

**Reviewers by stage (operator).** When the caller runs a plan review, it is read by `cursor/grok-4.7` at `effort=high` (optional between Leg 2 and Leg 3; it does not edit the plan). A code review is read by a one-job Grok Bot; Leg 4 keeps Composer in a fresh context plus that Grok Bot, both counting.

The seat holds only ids and URIs. Packets, confer output and plan live in `cortex://` files; closeouts are read by section with `md_list` / `md_read`. Each packet the seat authors goes through `/retrieval-before-authoring` first.

**Template SOT:** `cortex://notes/system/templates/lean-dispatch-ladder.md` (read it by section: `fs md_list`, then `md_read` the sections you need; plain `fs(op="read")` returns only a manifest; fill the `{{slots}}` from the parameters). This command is the wrapper — ¬ re-derive the legs, closeout-reading rules, or completion block here.

Use the `lean-dispatch` skill. Use the `consult-routing` skill. Use the `retrieval-before-authoring` skill.

## Usage

```
/lean-ladder "<question>" [slug=<slug>] [key=value ...]
```

## Parameters

| Param | Required | Default | Meaning |
|---|---|---|---|
| `question` | yes | — | One line, operator's words. Fills `{{question}}` |
| `slug` | no | **inferred** (see Slug inference) | `work_key=todo:<slug>`; the todo is created if missing |
| `packet` | no | `cortex://notes/tmp/packets/<slug>-packet.md` | Problem/seed sidecar. Authored (after retrieval) if absent |
| `retrieval_report` | no | `cortex://notes/tmp/packets/<slug>-retrieval-report.md` | Seed-packet report only. Must carry `target: todo:<slug>` at column 0. The spawn and plan artifacts get `<slug>-spawn-retrieval-report.md` and `<slug>-plan-retrieval-report.md` |
| `architect_model` | no | `cdp/fable-5.2` | Leg 1 wire. `cdp/fable-5.5` is not in `route_policy` |
| `architect_effort` | no | `high` | Leg 1 `reasoning_effort` |
| `implement_model` | no | `cursor/composer-2.5` | Leg 3 wire. Must be a `cursor/*` slug (seat constraint) |
| `lane` | no | `B` | Leg 3 checkout lane. Lane A is refused at admit |
| `density_triage` | no | `mechanical` | Todo attribute and Leg 3 flag. Use `judgment_required` for open forks |
| `planner` | no | `here` | `here` = this tab runs legs 1–4. `glass:8` = spawn a planning tab on `:8` |
| `planner_model` | no | `claude-sonnet-5-5` | Only with `planner=glass:<n>`. Must not be `cursor/grok-4.7` (it does not plan; it may run the optional plan review between Leg 2 and Leg 3 only) |
| `planner_effort` | no | `high` | Only with `planner=glass:<n>` |
| `wake` | no | `subscribe` | `subscribe` registers the planner tab (this tab for `planner=here`, the spawned tab for `planner=glass:<n>`) for `BRIDGE_WAKE` on the arc thread under the per-arc mailbox `cursor-<slug>`, before any dispatch (template § Wake registration). `none` skips subscription: the fire check does not require a row, the caller ends its turn idle after each admit, and it resumes on one bounded scheduled check per named leg or at the operator's next message — no polling loop and no `BRIDGE_WAKE` wait (reason: claude.ai seats have no working wake path yet) |
| `skip_confer` | no | `false` | `true` = omit Leg 1; Leg 2 still reads `cortex://notes/tmp/packets/<slug>-confer.md`, which must exist before planning (hand-seeded or from a prior confer) |

## Slug inference

`slug` is optional. When omitted:

1. Lower-case `question`, drop stopwords, keep the 3–5 most specific nouns, join with `-` (≤40 chars). Example: "bind a Cowork project per team_dispatch hop" → `cowork-project-team-dispatch-hop`.
2. `cortex(tool="entities", type=todo, query=<slug words>)`. If a matching open todo exists, **reuse its slug** instead.
3. Show it as `slug (recommended): <slug>` in the echo and proceed. The operator overrides by passing `slug=` or replying with a different slug before Leg 1 fires; after Leg 1 is admitted the slug is fixed for the arc.
4. If the registry shows a live row on `todo:<slug>`, stop and coordinate on that thread; do not suffix the slug to dodge the claim.

## Procedure

1. **Parse** the parameters, apply defaults, infer `slug`. Echo the resolved set in ≤8 lines.
2. **Preflight** (template § Preflight): registry read on `todo:<slug>`; ensure the todo with `density_triage`; fresh non-`role:root` arc thread; when `skip_confer=true`, confirm the confer sidecar exists before Leg 2.
3. **Retrieval gate (seat duty, per artifact).** Retrieval comes first and the draft comes after it, for every artifact a model will read. Each artifact is its own authoring job with its own `rag` calls, report and `search_id`s (a new job inherits none): (a) the seed `packet` (reader `architect_model`, Claude), (b) the planner-tab prompt when `planner=glass:<n>` (reader `planner_model`, Claude; report `<slug>-spawn-retrieval-report.md`), (c) the plan, authored by the planner (reader `implement_model`; report `<slug>-plan-retrieval-report.md`), and (d) any redispatch after a red closeout, plus any `prompt=` on any leg. Use the `retrieval-before-authoring` skill: one `rag(op=search)` per required scope (`llm_prompting`, `claude_api`, `prompt_injection`, `agent_skills_research`; add `constitutional_ai` when the reader is Claude, `skill_compilation` on skill/rule bodies), `top_k=10`, compact then `projection=full` for scopes cited, one reformulation on `weak_match: true`. Write each report with `target: todo:<slug>` at column 0 and put `retrieval_report:` on its own line in the authored body. An artifact re-fired unchanged is not re-retrieved. Do not fire a leg whose artifact has no report.
4. **Fill and run**
   - Mailbox for both modes: `<mailbox>` = `cursor-<slug>`, one per arc, never the shared name `cursor` (`unsubscribe` takes only `agent` + `thread_scope`, so a shared name lets cleanup remove another tab's row). Every leg carries `caller_agent="<mailbox>"`. When `wake=subscribe`, turns addressed `to` `<mailbox>` wake the tab. When `wake=none`, `<mailbox>` is bus routing only (replies land on the arc thread; it does not `BRIDGE_WAKE` this tab — reason: claude.ai seats have no working wake path yet). `thread_scope` is one string.
   - `planner=here` → when `wake=subscribe`, subscribe this tab first (nothing subscribes it automatically): `agent_id` is this tab's own id (the `tab=` of its last `BRIDGE_WAKE`, or the uuid in its agent-store path), `display=<this tab's display>`; verify with `subscriptions`. When `wake=none`, skip subscribe and agent-id resolution; still pass `caller_agent="<mailbox>"` on every leg; after each admit end the turn idle and resume on this platform's scheduled check for the named leg, or on the operator's next message on `<arc>`. Then read the template, substitute the slots, run the legs in order.
   - `planner=glass:<n>` → follow template § Wake registration in order: mint `agent_id` (UUID) → retrieval gate for the spawn prompt → when `wake=subscribe`, `agent_bus(tool=subscribe, …)` as in the template → `cursor_bridge(op="spawn_tab", gui_host=":<n>", agent_id=<agent_id>, model=<planner_model>, effort=<planner_effort>, work_key="todo:<slug>", thread=<arc>, prompt=<filled template>)` → when `wake=subscribe`, verify with `agent_bus(tool=subscriptions, agent=<mailbox>, thread_scope=<arc>)`. **The seat fires no leg:** the spawned tab re-checks its row when `wake=subscribe` (when `wake=none`, no row is required), then fires Legs 1, 3, and (after Leg 3 closeout) Leg 4 itself with `caller_agent="<mailbox>"`, so replies are addressed to the mailbox that wakes it when subscribed. Spawn failure ⇒ `unsubscribe(agent=<mailbox>, thread_scope=<arc>)` when a row exists and stop. The spawned tab runs the same retrieval gate. Do not hand the operator a manual handoff.
5. **Harvest** Leg 3 closeout by section only (template § Selective reading): `md_list`, then `md_read` of `executive_envelope`.
6. **Leg 4 gate** (template § Leg 4 — Pre-land review (before merge) / After a failed gate): parallel Composer + Grok Bot reviews at the lane commit; split or blocking verdict ⇒ do not land; follow that section's failed-gate steps (fix rounds or BLOCKED).
7. **Close** with the template's Completion block: quote ids, URIs, hashes, pytest lines, Leg 4 verdicts, git land state, liveness class.

## Light path and fork to heavy

This command is the **light path**: an impresario may dispatch a Cursor tab and call `/lean-ladder` as written, call it with modifications (parameters, `skip_confer`, the optional `cursor/grok-4.7` plan read), or start on it and **fork to the heavy path** when difficulties arise. **Carry over at the fork:** the plan file and its hash, the lane branch and commit, the fix-round count, and the review files. **Examples of difficulty signals** (weigh; not exhaustive): a split or blocking Leg 4 verdict; a second red closeout on the same criterion; a confer or a plan that re-triages the work to `judgment_required`; a falsification row that cannot be fixed inside `files_expected`.

> Heavy path (the grand-impresario default workflow). 1. Composer drafts the plan against the repository and a Claude architect, default `cdp/fable`, extends the same plan. 2. Plan review: Composer in a fresh context plus `cursor/grok-4.7` at effort=high, both counting. 3. `cdp/opus` densifies the approved plan into the implementation packet, and the same pair reviews the packet. 4. Composer implements. 5. Implementation review: Composer in a fresh context plus a new one-job Grok Bot, both counting. No writer starts before both reviewers approve one hash; a split is a failed gate; a plan or packet gets at most two revisions and an implementation at most three fix rounds that commit. Canonical text: standing rules R5, `cortex://notes/system/rules/seat-coordination-standing-rules.md`.

## Typical dispatch flow

```
operator:  /lean-ladder "bind a Cowork project per team_dispatch hop" planner=glass:8

seat       echo: slug (recommended)=cowork-project-team-dispatch-hop · architect=cdp/fable-5.2 high
           · implement=cursor/composer-2.5 lane B · planner=glass:8 claude-sonnet-5-5 high
seat       registry(todo:…) → empty · todo ensured (density_triage=mechanical) · arc thread 24xxx
seat       retrieval gate (packet) → rag × scopes → retrieval-report → author packet (target: todo:…)
seat       retrieval gate (spawn prompt) → report → author filled prompt
seat       agent_bus.subscribe(agent=cursor-<slug>, thread_scope=<arc>, display=8, agent_id=<uuid>) → row id
seat       cursor_bridge.spawn_tab(:8, claude-sonnet-5-5, agent_id=<uuid>, prompt=filled template)
seat       agent_bus.subscriptions(agent=cursor-<slug>, thread_scope=<arc>) → row carries <uuid>   (seat ends its turn; fires no leg)

glass tab  rename_chat "<arc> <slug>" → subscriptions(agent=cursor-<slug>) → row present (else BLOCKED on <arc>, stop)
glass tab  Leg 1  team_dispatch(op=generate, model=cdp/fable-5.2, contract=confer,
           reasoning_effort=high, dispatch_thread_id=<arc>, sidecar_ref=packet,
           work_key=todo:<slug>, caller_agent=cursor-<slug>) → execution_id, poll_hint; idle (¬ wait)
bus        Fable reply on <arc> to=cursor-<slug> → endpoint wake → BRIDGE_WAKE thread=<arc> turn=<n> pasted into the tab
glass tab  wakes: Fable reply landed
glass tab  md_list(confer) → md_read(Verdict, Risks)           ← sections only
glass tab  retrieval gate (plan; new job, own report) → write …-plan.md
glass tab  Leg 3 team_dispatch(seat=cursor-sdk, lane=B, model=cursor/composer-2.5,
           sidecar_ref=…-plan.md, caller_agent=cursor-<slug>) → dispatch_id; ends turn idle

composer   implements plan → closeout (lane commit; no land)

bus        closeout pointer on <arc> to=cursor-<slug> → BRIDGE_WAKE → tab resumes
glass tab  md_read(closeouts/<dispatch_id>.md, [executive_envelope, executive_envelope/effects_manifest])
glass tab  Leg 4 parallel: Composer fresh-context review + Grok Bot on :9 at lane commit (whole files) → both APPROVE, or fix round / operator escalation per template
glass tab  bus turn on arc: verdict · plan URI+sha · dispatch_id · pytest · Leg 4 verdicts · git land state · liveness
seat       not subscribed (only the planner mailbox is): the Completion turn on <arc> is the record
```

## Examples

```
/lean-ladder "bind a Cowork project per team_dispatch hop"
/lean-ladder "bind a Cowork project per team_dispatch hop" planner=glass:8
/lean-ladder "fix X" slug=fix-x density_triage=judgment_required architect_effort=max
/lean-ladder "fix Y" skip_confer=true
```

## Guards

- `seat=cursor-sdk` pairs only with a `cursor/*` model. Pairing it with `anthropic/*` raises `TypeError … FrontierGenerateRequest … 'seat'` instead of a 422 (already filed: `a:43058`, `a:42950`, narrowed in `a:43066` on `service:stargate`). For an `anthropic/*` consult use `role=` (for example `role=synthesizer`), not `seat=`.
- On cursor-sdk pass `model_knobs={"effort":…}`, never `reasoning_effort`.
- `planner=glass:<n>` with `wake=subscribe` and no verified subscription row ⇒ stop before Leg 1. With `wake=none`, no row is required before Leg 1. A wake aimed at a mid-turn tab is held, not queued or steered: the gate returns `agent_running` (classified `pending`, retriable) and pastes nothing. There is no steer override for wakes, because the submit path's `submitChatMaybeAbortCurrent` could abort the running turn. A tab that polls `agent_bus(wait)` stays mid-turn and keeps its wake deferred, so end the turn idle.
- A live registry row on `todo:<slug>` ⇒ stop and coordinate; ¬ duplicate.
- Same Leg 1 and Leg 3 `team_dispatch` input failed twice ⇒ no third retry; change the input once, then report (covers confer and implement only; not review legs).
- A review `team_dispatch` that errors is not retried: post BLOCKED on `<arc>` with the dispatch id (reason: standing rule R5; Leg 2b and Leg 4 alike — template § Leg 2b and Leg 4).
- `¬dispatch_primitive(surface)` ⇒ do the leg and say so in one line.
- Leg 2 plan must not carry `land: silent` or merge-without-review AC; Leg 4 is mandatory before land.
- `cursor/grok-4.7` does not plan (`planner_model` must stay Claude). On cursor-sdk, `model_knobs={"effort":"high"}` on `cursor/grok-4.7` is the plan-review default when the caller runs that optional read. Leg 4 code review is Composer plus a Grok Bot only.

## Rules

> Diff reviews are prohibited universally (Kaywan, 2026-11-16). Review complete relevant files at the exact commit SHA or content hash, with the surrounding source and architectural context needed to judge the artifact. Do not commission or accept patch-, diff-, or cleanup-delta reviews, including for formatting, generated-file cleanup or follow-up rounds. A diff fingerprint identifies the land artifact; it is not review input or review proof. Preserve actual reviewer provenance, independent reviewers and the three-round cap. One exception (Kaywan, 2026-11-17 13:24 and 13:42 PT): `cursor/composer` may be given a diff read as an additional read to find gaps; it is cheap and encouraged. It never replaces a whole-file review, and a counting verdict still rests on complete files. Diff reads on `cdp/*` and on a Grok Bot are explicitly prohibited: those reviewers read whole files only. Every other reviewer, `cursor/grok-4.7` included, stays under the prohibition.
