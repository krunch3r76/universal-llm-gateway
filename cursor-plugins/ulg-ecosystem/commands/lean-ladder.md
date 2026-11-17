Lean ladder — architect confer → plan → Composer implement, planning externalized to files, every dispatch authored under retrieval.

## What this command does

Runs one work item through three dispatched legs while the orchestrating seat stays lean:

1. **Confer** — a CDP architect (default `cdp/fable-5.2`) reads a packet and returns a verdict.
2. **Plan** — a planner (this tab, or a glass tab on `:8`) reads the confer by section and writes a plan file: wire/design, `files_expected`, numbered AC, pytest matrix.
3. **Implement** — Composer on `seat=cursor-sdk` lane B executes the plan file and quotes pytest in its closeout.

The seat holds only ids and URIs. Packets, confer output and plan live in `cortex://` files; closeouts are read by section with `md_list` / `md_read`. Each packet the seat authors goes through `/retrieval-before-authoring` first.

**Template SOT:** `cortex://notes/system/templates/lean-dispatch-ladder.md` (read it with `fs(op="read", path=…)`; fill the `{{slots}}` from the parameters). This command is the wrapper — ¬ re-derive the legs, closeout-reading rules, or completion block here.

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
| `planner` | no | `here` | `here` = this tab runs legs 1–3. `glass:8` = spawn a planning tab on `:8` |
| `planner_model` | no | `claude-sonnet-5-5` | Only with `planner=glass:<n>`. Model picked in the spawned tab |
| `planner_effort` | no | `high` | Only with `planner=glass:<n>` |
| `wake` | no | `subscribe` | Only with `planner=glass:<n>`. `subscribe` registers the spawned tab for `BRIDGE_WAKE` on the arc thread before Leg 1 (template § Wake registration). `none` skips it; the tab then never wakes and must not be used to wait on a closeout |
| `skip_confer` | no | `false` | `true` = start at Leg 2 |

## Slug inference

`slug` is optional. When omitted:

1. Lower-case `question`, drop stopwords, keep the 3–5 most specific nouns, join with `-` (≤40 chars). Example: "bind a Cowork project per team_dispatch hop" → `cowork-project-team-dispatch-hop`.
2. `cortex(tool="entities", type=todo, query=<slug words>)`. If a matching open todo exists, **reuse its slug** instead.
3. Show it as `slug (recommended): <slug>` in the echo and proceed. The operator overrides by passing `slug=` or replying with a different slug before Leg 1 fires; after Leg 1 is admitted the slug is fixed for the arc.
4. If the registry shows a live row on `todo:<slug>`, stop and coordinate on that thread; do not suffix the slug to dodge the claim.

## Procedure

1. **Parse** the parameters, apply defaults, infer `slug`. Echo the resolved set in ≤8 lines.
2. **Preflight** (template § Preflight): registry read on `todo:<slug>`; ensure the todo with `density_triage`; fresh non-`role:root` arc thread.
3. **Retrieval gate (seat duty, per artifact).** Retrieval comes first and the draft comes after it, for every artifact a model will read. Each artifact is its own authoring job with its own `rag` calls, report and `search_id`s (a new job inherits none): (a) the seed `packet` (reader `architect_model`, Claude), (b) the planner-tab prompt when `planner=glass:<n>` (reader `planner_model`, Claude; report `<slug>-spawn-retrieval-report.md`), (c) the plan, authored by the planner (reader `implement_model`; report `<slug>-plan-retrieval-report.md`), and (d) any redispatch after a red closeout, plus any `prompt=` on any leg. Use the `retrieval-before-authoring` skill: one `rag(op=search)` per required scope (`llm_prompting`, `claude_api`, `prompt_injection`, `agent_skills_research`; add `constitutional_ai` when the reader is Claude, `skill_compilation` on skill/rule bodies), `top_k=10`, compact then `projection=full` for scopes cited, one reformulation on `weak_match: true`. Write each report with `target: todo:<slug>` at column 0 and put `retrieval_report:` on its own line in the authored body. An artifact re-fired unchanged is not re-retrieved. Do not fire a leg whose artifact has no report.
4. **Fill and run**
   - `planner=here` → read the template, substitute the slots, run the legs in order.
   - `planner=glass:<n>` → follow template § Wake registration in order: mint `agent_id` (UUID) → `agent_bus(op=subscribe, agent=cursor, thread_scope=<arc>, surface=cursor, display=<n>, agent_id=<agent_id>, title="<arc> <slug>")` → Leg 1 → `cursor_bridge(op="spawn_tab", gui_host=":<n>", agent_id=<agent_id>, model=<planner_model>, effort=<planner_effort>, work_key="todo:<slug>", thread=<arc>, prompt=<filled template>)` → verify with `agent_bus(op=subscriptions, agent=cursor, thread_scope=<arc>)`. Legs 1 and 3 carry `caller_agent="cursor"`. Spawn failure ⇒ unsubscribe and stop. `spawn_tab` alone registers nothing, so an unsubscribed tab never wakes on the Fable reply. The spawned tab runs the same retrieval gate. Do not hand the operator a manual handoff.
5. **Harvest** closeouts by section only (template § Selective reading): `md_list`, then `md_read` of `executive_envelope`.
6. **Close** with the template's Completion block: quote ids, URIs, hashes, pytest lines, liveness class.

## Typical dispatch flow

```
operator:  /lean-ladder "bind a Cowork project per team_dispatch hop" planner=glass:8

seat       echo: slug (recommended)=cowork-project-team-dispatch-hop · architect=cdp/fable-5.2 high
           · implement=cursor/composer-2.5 lane B · planner=glass:8 sonnet-5-5 high
seat       registry(todo:…) → empty · todo ensured (density_triage=mechanical) · arc thread 24xxx
seat       retrieval gate (packet) → rag × scopes → retrieval-report → author packet (target: todo:…)
seat       Leg 1  team_dispatch(model=cdp/fable-5.2, contract=confer, sidecar_ref=packet) → execution_id, poll_hint
seat       retrieval gate (spawn prompt) → report → author filled prompt
seat       agent_bus.subscribe(cursor, thread_scope=<arc>, display=8, agent_id=<uuid>) → row id
seat       cursor_bridge.spawn_tab(:8, claude-sonnet-5-5, agent_id=<uuid>, prompt=filled template)
seat       agent_bus.subscriptions(thread_scope=<arc>) → row carries <uuid>   (seat ends its turn)

glass tab  rename_chat "<arc> <slug>" → ends turn idle (no agent_bus wait, no watcher)
bus        Fable reply on <arc> → endpoint wake → BRIDGE_WAKE thread=<arc> turn=<n> pasted into the tab
glass tab  wakes: Fable reply landed
glass tab  md_list(confer) → md_read(Verdict, Risks)           ← sections only
glass tab  retrieval gate (plan; new job, own report) → write …-plan.md
glass tab  Leg 3 team_dispatch(seat=cursor-sdk, lane=B, model=cursor/composer-2.5,
           sidecar_ref=…-plan.md, caller_agent=cursor) → dispatch_id; ends turn idle

composer   implements plan → closeout

bus        closeout on <arc> → BRIDGE_WAKE → tab resumes
glass tab  md_read(closeouts/<dispatch_id>.md, [executive_envelope, executive_envelope/effects_manifest])
glass tab  bus turn on arc: verdict section id · plan URI+sha · dispatch_id · pytest lines · liveness class
seat       wakes → follow-up to operator: what returned, what it changes, next move
```

## Examples

```
/lean-ladder "bind a Cowork project per team_dispatch hop"
/lean-ladder "bind a Cowork project per team_dispatch hop" planner=glass:8
/lean-ladder "fix X" slug=fix-x density_triage=judgment_required architect_effort=max
/lean-ladder "fix Y" skip_confer=true implement_model=cursor/grok-4.7
```

## Guards

- `seat=cursor-sdk` pairs only with a `cursor/*` model. Pairing it with `anthropic/*` raises `TypeError … FrontierGenerateRequest … 'seat'` instead of a 422 (already filed: `a:43058`, `a:42950`, narrowed in `a:43066` on `service:stargate`). For an `anthropic/*` consult use `role=` (for example `role=synthesizer`), not `seat=`.
- On cursor-sdk pass `model_knobs={"effort":…}`, never `reasoning_effort`.
- `planner=glass:<n>` without a verified subscription row ⇒ stop before Leg 1. A wake aimed at a mid-turn tab is held, not queued or steered: the gate returns `agent_running` (classified `pending`, retriable) and pastes nothing. There is no steer override for wakes, because the submit path's `submitChatMaybeAbortCurrent` could abort the running turn. A tab that polls `agent_bus(wait)` stays mid-turn and keeps its wake deferred, so end the turn idle.
- A live registry row on `todo:<slug>` ⇒ stop and coordinate; ¬ duplicate.
- Same input failed twice ⇒ no third retry; change the input once, then report.
- `¬dispatch_primitive(surface)` ⇒ do the leg and say so in one line.
