Conductor — cursor-sdk as **mission operator** of a continuity root.

Interactive setup: bring this chat up to speed, ask establishing questions, then
author the **conductor score** (six-block packet + scoreboard) and (on confirm)
admit the packet.

**Default posture (binding): run to completion, merge rubber-stamped.** Once
admitted, the conductor drives every open G-row to completion in one
commission and lands its own verified Lane-B branch without a second merge
ask — the admit itself is the standing authorization for both (skill § Run to
completion). Q6 below is where an operator names an exception, not where they
grant the default.

**Skill SOT:** Use the `conductor` skill. This command is the attended wrapper —
¬ re-derive nesting / Lane A·B / judgment ladder / cost tiers here.

## When

| Condition | Route |
|---|---|
| Operator `/conductor` (optional ring / objective) | This command |
| Continuity root already live; just admit | Skip Qs already bound; author/admit packet |
| Pinned worker terminal; retained store | Skill § Resume-if-dead — `resume_of` + `reuse_thread` (same agent continues) |
| Formal CDP `operator_proxy` mission | `mission-operator` + `cdp-operator-proxy` — not this |
| Single dense implement | `/todo` / wrap — not conductor |

## Procedure (attended IDE)

### 1 — Orient (read before asking)

Load in order (stop early if operator already pasted handles):

1. If `ring:` / thread id given → `thread_get` + tip CHECKPOINT + named charter/scoreboard.
2. Else scan recent roots / operator paste for objective.
3. `manage(busy_status)` — note Lane A/B write leases (queue risk).
4. Use the `conductor` skill (role split + **model/effort tier** + admit shape).

Brief the operator in Been→Are→Going (≤6 lines). Include a one-line **recommended
conductor seat** (Composer default — omit `model=`; CDP for judgment; Other-Models pin
only with a named trigger) with why.

### 2 — Establishing questions (ask only unknowns)

Ask in one batch; skip any already bound in chat:

1. **Objective** (one sentence) — what does "done" mean for this ring?
2. **Root** — existing `agent-bus:N` or birth new `orchestrator_continuity` root?
3. **Incident / sibling lanes** — cite-only ids (e.g. stood-down proxy lane); any
   `¬ request` / pause markers?
4. **Checkout regime** — default is **Lane B** (`cursor-sdk/lane-*`);
   confirm, or override to Lane A (shared master) with a named reason.
5. **G-rows** — paste scoreboard or list OPEN rows + Next-pickup.
6. **Human gates** — anything that must stay operator-only (tabs, creds,
   genuinely irreversible acts)? Default is **none beyond that** — the
   conductor drives every G-row and lands its own verified merge without a
   separate ask (skill § Run to completion). Name an exception here if this
   mission needs its merge held for review.
7. **Explicit seat pin?** — default none (Composer — omit `model=`, `{fast:true}`);
   judgment nests `cdp/fable` / `cdp/opus-5` escalation or an Other-Models pin only
   with a named trigger. Sonnet/Opus/Terra remain explicit-pin facts only — never implicit defaults.
8. **Admit now?** — draft packet only vs admit after confirm.

Default to Lane B on Q4 absent an operator override; do **not** silently carry
Lane A forward just because a prior ring in this arc happened to run Lane A —
that still needs a named reason, not historical inertia.

### 3 — Establish surfaces

If root missing: mint charter + scoreboard → birth CHECKPOINT → `role:root`
(`orchestration-lanes` + `checkpoint-discipline`). Operator confirms objective
before stamp when material.

If root exists: update scoreboard Next-pickup to "conductor admit" if needed.

### 4 — Author packet

Write `tmp/reviews/{slug}-conductor-packet.md` (six blocks). Mirror to
`cortex://notes/system/threads/{id}-conductor-packet.md` when ring id known.

Front-matter: `packet_kind: conductor`, `role_name: conductor`, checkout regime,
bound `model` / `effort` (or "admit-time tier"), `off_tick: true` unless
tick-enrolled.

**Required skill on the dispatch (binding):** first line of `<invariants>` MUST be
`Use the conductor skill — nest specialists; ¬ hand-code mechanical G-rows; cost
tier from this skill.` (`team_dispatch(skills=["conductor"])` mounts too, but the
Use-line is what survives a replayed or `skills=`-less admit — send both.)

Validate: line-anchored six tags + `acceptance` in `<task_guidance>` + conductor
Use-line present.
### 5 — Confirm → admit

Show packet path + sha256 + **model tier** + admit knobs + confirm conductor
Use-line in `<invariants>`. On operator **go** / **admit**:

```text
# Example: Composer standing seat — substitute pin from Q7 if any
# Packet MUST already carry: Use the conductor skill — …
team_dispatch(
  op=generate,
  seat=cursor-sdk,
  contract=light-bounded,
  packet_path=tmp/reviews/{slug}-conductor-packet.md,
  dispatch_thread_id={root},  # continuity root with turns, or pending-empty child of root
  model_knobs={"fast":"true"},
  lane="B",                    # DEFAULT (Q4) — pass explicitly even when
                                # unopposed; omitting resolves to Lane A at GIW
)
```

If `CURSOR_LANE_B_SCOPE_REFUSED`: **¬ omit `lane=`** — fix packet scope paths, then
re-admit (skill § Gotchas). After admit, confirm `busy_status.active_by_lane`
matches the bound regime.

If explicit `claude-opus-5` pin: announce inform-then-proceed trigger line. Post root CHECKPOINT with
`execution_id` / `dispatch_id` / worker thread / queue holder if queued / **model**.

### 6 — Hand back

Poll hint for the worker. Lead does not steal G-rows while conductor is active
(`dispatch-in-flight-supremacy`). **Do not expect an interim check-in** — the
conductor runs every G-row to completion and lands its own verified merge by
default (skill § Run to completion); poll for the terminal CHECKPOINT /
closeout, not a mid-mission pause. Codify residuals on the root entity.

## Worked example

Ring **7310** (`7244-ide-resolution`): Lane A finish of 7186 residual; packet
`tmp/reviews/7310-conductor-packet.md`; sibling 7281 cite-only. Early dogfood
admitted Opus — standing seat is Composer (omit `model=`); judgment nests CDP
(`cdp/fable` / `cdp/opus-5`) unless an Other-Models pin names a trigger.
