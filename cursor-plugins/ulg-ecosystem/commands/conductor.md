Conductor — cursor-sdk as **mission operator** of a continuity root.

Interactive setup: bring this chat up to speed, ask establishing questions, then
author the six-block conductor packet and (on confirm) admit it.

**Skill SOT:** Use the `conductor` skill. This command is the attended wrapper —
¬ re-derive nesting / Lane A·B / judgment ladder / cost tiers here.

## When

| Condition | Route |
|---|---|
| Operator `/conductor` (optional ring / objective) | This command |
| Continuity root already live; just admit | Skip Qs already bound; author/admit packet |
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
conductor tier** (T0 Composer / T1 Grok / T2 terra·sol / T3 Opus) with why.

### 2 — Establishing questions (ask only unknowns)

Ask in one batch; skip any already bound in chat:

1. **Objective** (one sentence) — what does "done" mean for this ring?
2. **Root** — existing `agent-bus:N` or birth new `orchestrator_continuity` root?
3. **Incident / sibling lanes** — cite-only ids (e.g. stood-down proxy lane); any
   `¬ request` / pause markers?
4. **Checkout regime** — Lane A (shared master) or Lane B (`cursor-sdk/lane-*`)?
5. **G-rows** — paste scoreboard or list OPEN rows + Next-pickup.
6. **Human gates** — anything that must stay operator-only (tabs, creds)?
7. **Conductor model tier** — accept the standing default, or pin
   `composer` / `gpt-5.6-terra` / `claude-opus-5` (+ effort). Standing default:
   **T1 `cursor/claude-sonnet-5` @ `effort=max`** (`thinking=true`, `context=1m`).
   Opus is expensive — require a named T3 trigger. ¬ Grok, ¬ Sonnet 4.6 on this seat.
8. **Admit now?** — draft packet only vs admit after confirm.

Do **not** invent Lane B for an arc that historically ran Lane A (or vice versa)
without an explicit answer to Q4.

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

Validate: line-anchored six tags + `acceptance` in `<task_guidance>`.

### 5 — Confirm → admit

Show packet path + sha256 + **model tier** + admit knobs. On operator **go** /
**admit**:

```text
# Example: T1 default — substitute model/effort from Q7
team_dispatch(
  op=generate,
  seat=cursor-sdk,
  model=cursor/claude-sonnet-5,
  contract=light-bounded,
  packet_path=tmp/reviews/{slug}-conductor-packet.md,
  dispatch_thread_id={root},
  model_knobs={effort: max, thinking: "true", context: "1m"},
  lane="B",                    # when Q4 = Lane B — REQUIRED; see skill Gotchas
)
```

If `CURSOR_LANE_B_SCOPE_REFUSED`: **¬ omit `lane=`** — fix packet scope paths, then
re-admit (skill § Gotchas). After admit, confirm `busy_status.active_by_lane`
matches the bound regime.

If T3 Opus: announce inform-then-proceed trigger line. Post root CHECKPOINT with
`execution_id` / `dispatch_id` / worker thread / queue holder if queued / **model**.

### 6 — Hand back

Poll hint for the worker. Lead does not steal G-rows while conductor is active
(`dispatch-in-flight-supremacy`). Codify residuals on the root entity.

## Worked example

Ring **7310** (`7244-ide-resolution`): Lane A finish of 7186 residual; packet
`tmp/reviews/7310-conductor-packet.md`; sibling 7281 cite-only. Early dogfood
admitted Opus — standing default is **T1 Grok** unless a T3 trigger fires.
