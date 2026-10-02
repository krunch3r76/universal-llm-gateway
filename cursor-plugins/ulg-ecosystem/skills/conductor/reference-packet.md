## Packet
<!-- width-seat:v1:start -->
> **Width seat** (generated from `libs/implement_admission/conductor_width_seat.py`; do not hand-edit): ACTIVE = `cdp/opus-5.5` · `reasoning_effort=high` · `effort_when_bind_gates_wave=max` only when a bind gates a wave · CLI `--model opus-5.5` · usage channel **Opus**. SKETCH (G1) = `cdp/opus-5.5` · `reasoning_effort=extra`. SKEPTIC (G4) = `cdp/opus-5.5` · `reasoning_effort=extra` (one rung above ACTIVE; cross-family review stays on this effort). RESTORE = `cdp/fable-5.1` · `reasoning_effort=high` (only when Kaywan asks; set `ACTIVE = RESTORE`).
<!-- width-seat:v1:end -->


Six-block handoff packet (`architecture-handoff-protocol`), **materialized by
Stargate from the todo** on `job=conductor` + `source_ref` (front-matter
`role_name: conductor`, the Use-line, entry gate, `summon_mode`, pins). The lead
shapes the **todo**, not the file; `packet_kind` is retired on the wire (the
contract carries it). The list below is what the materialized packet must end up
saying — audit the materialized file against it, and put anything missing on the
todo (`scope`, `problem`, attrs), not in a hand-edited copy.

**Optional frontmatter `sdk_mode:` (conductor admit vs nested legs):**

| Field | Value | When |
|---|---|---|
| *(omit)* | resolves **`agent`** | Standing conductor admit — orchestrates, nests, may land |
| `sdk_mode: agent` | explicit agent | Same; use when documenting intent in the packet file |
| `sdk_mode: plan` | **forbidden** on `job=conductor` | Plan mode is for **nested** G3 recon/bind legs only — worker 422 |

Nested G3 packets (not the top-level conductor admit) MAY carry `sdk_mode: plan`
when the row is sparse recon before `implement_ready`. SoT:
`cursor_sdk_mode.py` · `docs/agent-guides/cursor-sdk-conversation-mode.md`.

Required in `<scope>` / `<invariants>`:
- **`Use the conductor skill — …`** (continuity-lead required-skill gate — see Audience)
- Root thread id + charter + scoreboard URIs
- Checkout regime: **Lane B is the standing default**, including cortex-only
  work (a throwaway worktree is enough). State `lane="B"` explicitly; an
  omitted `lane=` still resolves to Lane A at GIW, so default ≠ "leave the
  param off". Lane A is one write slot. A seat does not raise that cap.
  `lane="A"` only when lane B is refused for paths outside the repo
- Incident/sibling lanes (cite ≠ convert)
- Forbidden verbs (e.g. no `request` on a stood-down lane)
- Judgment vs human rule (above)
- **Run-to-completion restated** — state plainly that this admit already
  authorizes landing the mission's own Lane-B branch on green; no
  plan-review or merge-ack round trip is needed unless *this* packet names a
  specific hold-merge exception in this same list
- **Bound conductor model + effort** (or "lead picks at admit from tier table")
- **G-row contract honesty** — do not mark a G-row conductor-direct / `owner: cursor-sdk` when `files_expected` includes production code+tests. Conductor binds; Composer implements.
- **Class reservation (A1 §13′ #2)** — under `work_key=todo:{slug}`, only a seated conductor (`job=conductor`, cursor-sdk) may author G-rows and mutate the scoreboard. A liaison admits that conductor for a played todo (§ Play in `liaison`) and does not author G-rows, Sketch, or `job=implement`. The conductor nests Composer implement after its own Compose.
- **Scoreboard G6/G7 (binding)** — **`review harvest ≺ land ≺ DONE`**. After G5
  implement, **G6** = `cdp/opus-5.5` `job=delivery-review` `reasoning_effort="high"`
  (**`extra`/`xhigh` floor, `max` if invariant-touching, under
  `conductor_profile=fable-scarce`**) on the **lane branch diff** (sidecar **R1**).
  **G7** = merge/land (sidecar **L1**).
  Witness fold must clear G6 before G7; terminal DONE requires both. **¬**
  land-then-background-review; **¬** terminal DONE without R1 harvest
  (`a:32146` · `a:32226`). **review harvest ≺ land ≺ DONE** means
  evidence-before-dispatch and one dispatch per gate: the evidence block
  (diff stat, suite summary on base and tip, catalog check when a docstring
  feeds a generated region, else `evidence: absent — <name>`) goes into the
  single G6 `team_dispatch` before it is fired; one `poll_hint` wait, then
  `stop: ROW_HOP` if still running. Same three steps as the materialized
  packet and `reference-run-to-completion.md`.

Continuity sidecar during run: `cortex://notes/system/threads/{root}-conductor.md`
(G-row table, nested `execution_id`s, `NEXT_ADMIT`, judgment calls) — same shape
as `7286-off-tick-conductor.md`.

Worked packet example: `tmp/reviews/7310-conductor-packet.md` /
`cortex://notes/system/threads/7310-conductor-packet.md` (early dogfood used
Opus — not the standing default).

### First-utterance spawn (standing path)

IDE mints todo identity (S4a); Stargate materializes the packet:

```text
team_dispatch(
  op=generate,
  seat=cursor-sdk,
  job=conductor,
  lane="B",
  source_ref="todo:{slug}",
  dispatch_thread_id="{root}",   # shape 1 when root is not an operator lane; else shape 2 child
)
```

First-utterance **mint** only. Harvested score / `NEXT_ADMIT: none` is the
rematerialize trap (§ Liaison-decide), not this recipe.

`{root}` = continuity root that already has turns and is not an operator lane
(shape 1), **or** a `lifecycle_state=pending` ∧ `turn_count==0` child of that
root. When that root is an operator lane, the admit 422s
`conductor_summoning_operator_lane`. Shape 2: the caller pre-creates a
pending-empty child (`bus_lifecycle_state=pending`, `turn_count=0`,
`parent_thread=<lane>`, `cse_registration_id` null) and passes that child as
`dispatch_thread_id` and `reuse_thread`. Lifecycle-null
pre-create 422s (`conductor_coord_split_refused`). Resume-after-terminal:
`reuse_thread=<work thread>`. Receipt quotes the **admitted** thread +
`branch_current=cursor-sdk/lane-{that id}` + `dispatch_id` + `scoreboard_uri`.
Ledger holds
`work_key=todo:{slug}` (no `todo:` packet front-matter — nested G5 uses
`nest_under`). Top-level `job=implement` on the same todo while conductor
is open → 409.

### Scoreboard `sdk_mode` column (plan vs implement legs)

Bind a **`Mode`** column (or inline `sdk_mode:` on each G-row) on the scoreboard
tip and continuity sidecar. Values: **`plan`** · **`agent`** · **`—`** (CDP / non-sdk).

| G-row | Mode | Nested contract | Closeout witness |
|---|---|---|---|
| G3 recon / bind (pre-ready) | `plan` | `none` \| `recon` \| `seed` \| `consult` | `plan:closeout_verdict=PLAN_COMPLETE` or `PARTIAL` + artifact URIs — **no** land |
| G5 implement | `agent` | `implement` \| `pure-mechanical` | **Attended:** `SCORE_RESURFACE` (slug in subject; body cites CDP exec + review sha when the tip recorded them) **∧** implement (`ledger:nested_implement` ∨ `git:lane_head`). ¬ tip `DONE` · ¬ steer-inject · ¬ harvest alone · ¬ nested_implement without resurface. **Away:** implement only. Path-explicit commit / `land_disposition` are G7 |
| G1 · G2 · G4 · G6 | `—` | CDP transport | harvest URI (G6 = one `team_dispatch(op=generate, model=cdp/opus-5.5, contract=freeform, job=delivery-review, dispatch_thread_id=<worker>, parent_thread=<summoning or worker>, prompt=<body with job=delivery-review and retrieval_report: lines>)`, one `poll_hint` wait, `NEXT_ADMIT: harvest <execution_id>` in the hop CHECKPOINT; distinct from the attended G5 closer) |
| Conductor (top-level) | `agent` | `conductor` | scoreboard drive |

**Scoreboard tip template (worked example — sparse birth):**

```markdown
| ID | Deliverable | Mode | Status | Stops |
|---|---|---|---|---|
| G1 | Architecture / recon | — | OPEN | |
| G2 | Frame | — | OPEN | |
| G3 | Densify | plan | OPEN | |
| G4 | Skeptic / gate-6 | — | OPEN | |
| G5 | Implement | agent | OPEN | |
| G6 | Pre-land review | — | OPEN | |
| G7 | Ship / land | — | OPEN | |
```

Rewrite `Mode` when densifying: G3 `plan` → `agent` after `implement_ready` and a
separate implement nest (or skip plan hop when packet is already dense).

**`NEXT_ADMIT` after `PLAN_COMPLETE`:** when a nested plan leg closes with
`plan:closeout_verdict=PLAN_COMPLETE`, rewrite scoreboard `NEXT_ADMIT` to nest
implement:

```text
team_dispatch(
  op=generate,
  seat=cursor-sdk,
  job=implement,
  nest_under=<plan_dispatch_id>,
  lane=,                          # inherit parent isolation
  sdk_mode=agent,                 # omit ok — implement-class defaults agent
  packet_path=…,                  # or source_ref when implement_ready stamped
)
```

Harvest plan sidecar URIs into the implement packet `<corpus>` before admit.
Plan closeout **forbids** `landed` / path-explicit commit claims
(`apply_plan_mode_closeout_gate`).

### Score journal + stops

- Tip: `cortex://notes/system/scoreboards/{slug}-scoreboard.md`
- Journal: `cortex://notes/system/scoreboards/{slug}-score-journal.md` (append-only)
- **Witnessed DONE:** Status cells are a **projection** folded from witnesses (cortex
  relationships, bus turns, git land). Nobody writes `DONE` — hang witnesses; the fold
  renders `DONE`. Self-marked `DONE` without a witness renders **`CLAIMED`** (not a
  rewind of a closed row).
- **Conductor duty:** attach witnesses (G1 `derived_from` edge, G5 `SCORE_RESURFACE`
  when attended, etc.); do not re-derive work already in sidecar artifacts.
- **CDP-ratified G5 witness — one form (`a:37198`).** G3→G5 in-process CDP
  score-ratify (LIKELY_OPTIMAL / do-not-fight) is a **transition**: fire, record the
  harvest URI, continue. It does **not** hang the G5 fold witness by itself.
  **Required attended witness:** post one bus turn on `summoning_thread_id` whose
  subject starts with `SCORE_RESURFACE` and includes the mission **slug**, and
  whose body cites (1) CDP exec id and (2) review sha when the tip recorded
  them. Fold source: `bus:SCORE_RESURFACE` only when that turn **and**
  implement completeness (`ledger:nested_implement` ∨ `git:lane_head`) both
  hang. Bus read is `found` / `not_found` / `unknown` — `unknown` (HTTP/token
  miss) is not a closer and is not a license to re-post. **Forbidden:** tip
  Status prose, operator steer-inject, CDP harvest URI alone, nested implement
  without resurface on attended. **Successor refuse-redo:** when the fold
  already shows G5 `DONE` with `bus:SCORE_RESURFACE` (or away
  `ledger:nested_implement` / `git:lane_head`), do **not** re-fire score-ratify
  and do **not** re-post `SCORE_RESURFACE` — drive from the next entry gate. If
  Status is `CLAIMED` and a matching resurface already exists on the summoning
  thread, file friction (fold miss) — ¬ re-post. Specimen falsifier: 14010 row1
  `376ec0a9cb90-9c691bb0` (steer/harvest claimed hung; fold still open) → row2
  `59032116` burned a full hop posting `#73` (`tmp/reviews/closeouts/…`,
  `agent-bus:12286#1912`).
- **G5 ≠ attended-door only.** `SCORE_RESURFACE` witnesses the attended resurface
  after score-ratify, ¬ implement completeness. `G4.withhold ∨ G4.AC_red ∨
  G4.says(remainder is mechanical) ⇒ ¬ close G5` on Composer land ∧ empty-template
  green. Hang G5 until a *read* independent check (overlay quoted at harvest) ∨ a
  seeded-ladder fixture witnesses the named remainder. Fold: a G4 URI whose body
  withholds/FAIL G5 is **not** a G4 witness (v1 URI-resolve alone was the 9655
  collapse).
- **`SCORE_RESURFACE` thread:** post on `summoning_thread_id` (parent/root —
  9582/9638-class), **never** the leftover worker thread. Packet scope names
  `summoning_thread_id:`; GIW attended preamble repeats it.
- Stops:
  - **Wait (do not terminate):** `CONSULT_PENDING` — harvest → document →
    `derived_from` → next row. Honest wrapper is `partial:consult` +
    `NEXT_ADMIT`, never `gate_d` / `work`.
  - **Exit-and-persist:** `ROW_PINNED` · `HOLD_MERGE` · `OPERATOR_GATE` ·
    `PARKED_TRANSPORT` — persist, then exit. These stop **progression past the
    unpaid gate** (no nest of later G-rows, no land, no terminal `DONE` while
    the owed stronger-model harvest is missing). `PARKED_TRANSPORT` is not
    “park the gate as debt and keep driving.” `ROW_PINNED` after honest
    `SCORE_RESURFACE` on the summoning thread is `partial:consult`, not work
    failure.
  - **Exit-and-continue:** `ROW_HOP` — persist (journal → CHECKPOINT), then
    exit; the substrate admits the successor immediately. Nobody consulted;
    not a page; not visible in the liaison register. Seventh token in
    `STOP_TOKENS`; new class `CHAIN_STOPS`.
  - **Open mission, no `land_disposition`.** At any stop other than `DONE`,
    do not declare `land_disposition`. The substrate retains the lane for the
    open conductor mission: no discharge, no branch debt, no abandoned mark.
    It stamps disposition reason `retained_for_mission`, which the reap
    reads, so a later reap still sees the retention after the closeout text
    is gone. `conductor_mission_open` rereads row key `lane_retained_for_mission`. If a line is declared anyway,
    GIW ignores it while the mission is open. The closeout that carries
    `DONE` is what settles the branch. Nested executor closeouts that end
    `land_disposition: unlanded <tip>` are not conductor rows; they still
    declare it.
  - Also: `CONFIRM_PENDING` · `DONE` (stop token only — not row Status)
- **`CONSULT_PENDING` wait:** the generate session waits or hands off — it does
  not end. `agent_bus.wait` until `archive_uri` or `from=web-anthropic` harvest
  turn; chrome-only continues wait (bounded under remaining wall).
- **Literal closeout wrapper (mandatory):** emit exactly this block (fill
  placeholders; GIW preamble `_CONDUCTOR_CONSULT_PENDING_TEMPLATE` repeats at
  admit):

  ```text
  status: partial
  consult: <what you waited for>
  execution_id: <this dispatch_id>
  NEXT_ADMIT: <web-anthropic|none>
  stop: CONSULT_PENDING
  ```

  Score journal appends require real UTC timestamps (`datetime.now(UTC)` on
  write — authority: `implement_admission.conductor_score_io`).
- G3→G5 default: in-process CDP score-ratify (do-not-fight / likely-optimal) via
  `team_dispatch(op=generate, model=cdp/opus-5.5, contract=freeform, session=ask, dispatch_thread_id=<this worker thread id>)`.
  Explicit see-score → `ROW_PINNED` + ping.
- **Stronger-model gates (a:32146 · a:32226):** Conductor **MUST break** (halt /
  refuse nest of the next gated G-row · refuse land · refuse terminal `DONE`)
  without the owed stronger-model **harvest**. Two pickers, not one:
  **G4 Skeptic** (pre-implement, on the G3 **spec**) = ACTIVE (reasoning_effort
  and effort_when_bind_gates_wave per ACTIVE when a bind gates a wave).
  **Under `conductor_profile=fable-scarce`:**
  G4 still reads ACTIVE, and the ≤1 `cdp/fable-5.1` leg-per-arc allowance is
  removed. **After-ship code review**
  (post-implement, on the **lane branch**, before land) = `cdp/opus-5.5`
  (xhigh/Extra). Skeptic ≠ code review. Composer cannot self-certify either.
  Same-family effort bumps are **not** the gate.
  **Transport failure ≡ missing harvest (BINDING):** `cdp-ask` down · Chrome
  CDP listen timeout · empty/FAILED bus body · `stall_stage=mark_terminal` ·
  unreaped CSE ⇒ treat as **no harvest**. Required act: `PARKED_TRANSPORT` or
  `OPERATOR_GATE` (scoreboard gate row stays OPEN) and **stop progression past
  that gate**. Forbidden: stamp DEFERRED / “cert unpaid — CDP” / “transport debt”
  and still nest G5, land, or claim DONE; silent seat swap to Cursor/Composer;
  land-then-background-review. Resume only after (1) CDP restored **and** the
  owed picker harvests a quotable verdict URI, or (2) the operator **explicitly**
  rebinds that gate’s seat (e.g. Cursor skeptic) — inform-then-proceed, never
  silent. Liaison / IDE stand-in enforces the same stop (9638 hop3; 10013).
- Attended IDE spawn: at G3→G5 fire the same in-process CDP score-ratify
  on ACTIVE (reasoning_effort and effort_when_bind_gates_wave per ACTIVE when a bind gates a wave) as an away summon, and post `SCORE_RESURFACE` on the
  summoning thread as a report whose body cites CDP exec id + review sha
  (`a:37198` — required attended G5 fold witness; ¬ implement completeness).
  The report is not a stop. A human gate is only an explicit see-score or
  `OPERATOR_GATE`.
- No live summoning chat = confer-and-finish via `team_dispatch` (Q2 unchanged).
- `ROW_PINNED` / stall / QWA pages the operator when away, when see-score is
  explicit, **or** when the summoning IDE is liaison (human not in that chat).
  No pager only when the live summoning chat **is the human operator**.
  Liaison IDE ≠ operator-present. Bus `Quiet with work in flight` is not a page
  (`qwa-*` on the worker thread → `to=cursor` is the named miss; 9638#187).
- Mode B admit-proof on CHECKPOINT when `CONSULT_PENDING`: `execution_id`+
  `poll_hint` or honest halt.

### Hop CHECKPOINT contract

Authority split: the **scoreboard tip + witness fold** decides which row is
next (`entry_gate` = first non-DONE row; a successor never rewinds a witnessed
row). The hop CHECKPOINT is the reconstitution index for everything the fold
cannot see. Written as read-modify-write (`expected_sha256`) of the mission's
conductor sidecar (`cortex://notes/system/threads/<worker>-conductor.md`) plus
a `CHECKPOINT — hop <n>` turn on the **worker** thread (`supersedes_turn` =
prior hop CHECKPOINT, `mark_read=true`, self→self). Order: journal append ≺
sidecar write ≺ bus tip ≺ closeout.

**Density hop (leg boundary):** work-item tip delta via `forward_mutate_tip`
(stop noted, row Status unchanged) ≺ ten-field hop CHECKPOINT ≺ closeout
(`stop: ROW_HOP`). `Next-pickup` names the same open G-row as entry gate — not
a rewind.

Ten fields, index-thin: `Anchor` · `Hop` · `Mission` · `Rows` · `In-flight` ·
`Judgment` · `Next-pickup` · `NEXT_ADMIT` · `Stop` · RESUME footer. Optional row
field under profile **`cdp_fable_legs_arc`** no longer caps legs: the ≤1
`cdp/fable-5.1` leg-per-arc allowance is removed, and G4 reads ACTIVE.
Empty / `FAILED body_len=0` leg ⇒ record `cdp_fail_route` on the scoreboard row and escalate at once. Do not retry the CDP pool. `nested-grok` or `operator` as in the after-ship gate above. Field-level
content and the why-a-cold-successor-needs-it column:
`cortex://notes/system/threads/9638-hop-architecture-bind-web-anthropic-20260901T13.md`
§3. **Not** in it: prose narrative of the row's work (that is the journal), the
scoreboard table itself (pointer + sha), hand-written `DONE` (witnesses render
DONE). Exceeding roughly `9831-conductor.md` size ⇒ it is carrying journal.

**`Rows` inline `Mode=` (cursor-sdk G3/G5 legs):** append mode on active nested
legs so cold successors need not infer from ledger:

```text
Rows: G3 open — Mode=plan — dispatch_id=<id> — artifact=<uri>
```

After plan harvest:

```text
Rows: G3 done (PLAN_COMPLETE) — NEXT_ADMIT: nest implement under <plan_dispatch_id>
Rows: G5 open — Mode=agent — nest_under=<plan_dispatch_id>
```

### Resume-if-dead (binding)

Planned hops and crash-resume are **one substrate path**: conductor row
terminal ∧ mission open ∧ no exit-and-persist/`DONE` token ⇒ successor on the
same thread. `ROW_HOP` = immediate; token-less terminal (crash, silent exit) =
same successor under the crash / no-progress budgets, then `PARKED_TRANSPORT` +
page. The liaison fires a resume only when the reactor has parked — never as
the first responder.

`CONSULT_PENDING` is **not** a designed-stop terminate. Exit-and-persist stops
(`ROW_PINNED`, `HOLD_MERGE`, `OPERATOR_GATE`, `PARKED_TRANSPORT`) retain store +
worktree (`resume_retain`).

**Substrate-written `PARKED_TRANSPORT` (GIW restart park):** when manage restarts
GIW with `park_live=true` (or `recycle_giw` parks at idle), GIW cancels your bridge
run and posts the PARKED turn itself with the wake record
`PARKED_TRANSPORT wake=giw_restart:{intent_id}` — a designed stop, not a crash
(no hop budget charge). The wake is GIW's own `resume_of` child after restart
(PARK-RESUME preamble + your unchanged packet, `hop_successor` stamped); no CDP
harvest is owed and the liaison fires nothing. On resume: treat the last tool
call as unverified, continue from your CHECKPOINT/journal, never restart the row.

**Seat-initiated park (`team_dispatch op=steer`):** IDE / Auto may park one live
dispatch without arming manage restart — `team_dispatch(op="steer",
steer="park_for_restart", dispatch_id=…, reason=…)` → Stargate → GIW park route.
Same PARKED turn shape and `poll_hint` continuity as substrate park above; wake
record may read `giw_restart:none` when no restart intent is bound.

**Verb split (resume vs hop):**

| Conductor stop | Continuation | Mechanism | Who initiates |
|---|---|---|---|
| `ROW_PINNED`, `HOLD_MERGE`, `OPERATOR_GATE` (when gate clears) | **same agent** | `team_dispatch(… resume_of=<pinned dispatch_id>, reuse_thread=<worker thread>, source_ref=todo:<slug>, dispatch_thread_id=<coord>)`, lane omitted | liaison / IDE lead when the pin lifts |
| `ROW_HOP` (planned), crash, silent, watchdog | **fresh agent** | GIW hop reactor → Stargate generate with `reuse_thread` + `hop_*`, **no `resume_of`** | substrate |

**Designed-stop CLOSEOUT identity (binding — a:32548):** exit-and-persist
tokens (`ROW_PINNED`, `HOLD_MERGE`, …) terminate the **parent** dispatch that
carried the conductor packet and scoreboard. When the pin lifts,
`resume_of=<that parent dispatch_id>` re-admits the **same agent** on the same
worker thread. Scoreboard `NEXT_ADMIT` / nested `nest_under` targets the
**child** dispatch id minted by that resume — never the terminal parent.
Multi-hop `resume_of` chains walk lineage to the store-bearing ancestor HOME
(GIW `resolve_sdk_store_dir`); intermediate hop rows may carry empty
`bridge-state` while the sqlite store lives under an earlier dispatch HOME.

`resume_of` **requires** `reuse_thread` (same mailbox + Lane-B isolation). XOR
`nest_under`. GIW is sole eligibility authority; resume within
`cursor_sdk_timeout_retain_s` after parent terminal.

If the worker thread is **still live**: do not second-generate on it —
`422 CURSOR_WORKER_THREAD_OCCUPIED`. Poll or `nest_under` the live holder.

**Nest 422 (binding):** when a nested `team_dispatch` returns **422** (transport
refusal, schema violation, occupied thread, etc.), stop **`PARKED_TRANSPORT`** —
persist scoreboard + journal, then exit. **¬** absorb the G-row in-seat as G5;
**¬** ghost-admit under a dead parent. Retry only after fixing the wire (keep
`nest_under=<conductor dispatch_id>`).

If the worker is **terminal** on a **hop** path, the substrate fires the hop
successor (fresh agent). On **`ROW_PINNED`**, the liaison fires **`resume_of`**
when the pin lifts — do not mint a sibling worker while the pinned agent is
reusable. New `dispatch_id` is required (dead execution ≠ dead thread). House
folds (pager, nest-orphan, scoreboard) ride **on** that resume — they do not
postpone it.

**G5 mechanical child:** `nest_under=<live resumed conductor dispatch_id>` (never
the terminal parent).

```text
team_dispatch(
  op=generate,
  seat=cursor-sdk,
  model_knobs={"fast":"true"},
  job=conductor,
  source_ref=todo:{slug},
  resume_of=<terminal parent dispatch_id>,  # ROW_PINNED lift — same agent
  reuse_thread=<parent worker thread>,      # REQUIRED with resume_of
  dispatch_thread_id=<coord root>,          # SCORE_RESURFACE + coord target
  # lane omitted — inherit parent isolation
  # hop path (substrate): reuse_thread + hop_* only, no resume_of
)
```

**Packet carry is mandatory.** GIW preambles extract from **packet text only**.
Re-admit with `source_ref` materialize (or the same packet + Use-line) only
when the conductor is **unfinished**. Harvested / `NEXT_ADMIT: none` is not
resume — rematerialize trap (§ Liaison-decide).

**Identity rewrite (binding on the new seat):**

| Artifact | After re-admit, name the **new** `dispatch_id` |
|---|---|
| Sidecar `NEXT_ADMIT` / nested Composer | `nest_under=<new dispatch_id>` |
| Seat-identity preamble | new `dispatch_id` supersedes the dead parent |
| Scoreboard tip (when present) | rewrite `NEXT_ADMIT` nest target to the new id |

Pager only when `summon_mode` is absent/away or the packet names see-score-page —
not on every attended `ROW_PINNED` (attended floor = bus `SCORE_RESURFACE` on the
summoning thread + summoning lead relay).

