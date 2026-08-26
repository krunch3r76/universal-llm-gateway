# `dispatch_monitor_core` — portable Model (G4 deliverable)

**Arc:** agent-bus:5735 · `project:dispatch-supervisor-monitor` · G4 (Opus CDP tier)
**Authority:** Fable G3 bind (P2 + S4 + S5) · MC playbook §3–4.3, §6 · transport doctrine · projection-libs gap
**Status:** complete for G4 scope. Controller I/O is interface-only; the graft is G5.

Stdlib only. Zero `libs.` / `services.` / third-party imports, asserted by
`tests/test_contract.py::test_core_imports_stdlib_only` rather than promised.

---

## 1. What this is, and what it deliberately is not

```
dispatch_monitor_core  (this package — stdlib only)
  Model          apply(EventRecord) -> None
                 derive(now_ms, previous=None) -> SupervisorProjection   # PURE
  DTOs           frozen dataclasses
  folds/         CharterFold · SdkFold · CdpFold
  correlation    CorrelationIndex (evidence-only joins)
  attention      every threshold in the system
  ProjectionCodec  frame schema — no socket
  watch          text sink (pure render)
  replay/__main__  the only two modules that touch a filesystem

dispatch_monitor_ulg  (G5 graft — NOT in this deliverable)
  Controller     sole owner of I/O: 4× ES WS subscribe + resume_from, reconnect/
                 re-seed, 1 s clock, hosts libs/projection.BroadcastHub,
                 command RPC (manage.sock / admin HTTP), click-time ReconcilePort
  Adapters       UlgEventSource · ColdStartSeeder · ReloadCommand · ReconcileOnClick
```

The invariant to break last, from Fable §3.1:

> `derive` is a pure function of `(folded_state, now_ms)`. Any datum the View needs
> that the Controller obtained by I/O **must enter the Model as an `EventRecord`.**
> The Controller never derives; the Model never does I/O.

That one line closes P6 (no inline bus enrichment at fold time), keeps P1's socket
out of the Model, and makes the fixture suite total coverage of derivation rather
than partial.

Out of scope by construction, not by omission: `dispatch_monitor_ulg`, Event
Service wiring, `libs/projection/` itself, `rag.yaml`, agent-bus schema,
`thread.orchestration` emission, the SDL View.

---

## 2. Grounding gaps — read before trusting a field name

Declared up front because they change how much of this you should believe.

| # | Gap | Effect on this deliverable |
|---|---|---|
| **A** | **Corpus staged 2026-07-26; repo↔cortex fixtures synced slice 4.** `event-payloads.md` and `replay-fixtures/` live at `cortex://notes/system/specs/dispatch-supervisor-monitor/`. Repo `core/fixtures/*.jsonl` is the pytest authority; cortex mirror is byte-identical (verified by `CORTEX_MIRROR.sha256`). | Payload field sets reconciled slice 4; residual drift is named in `v3-gap-taxonomy.md`, not silently coerced. |
| **B** | **v3 is unreadable from a CDP seat** and is `ratify pending (operator)` anyway. Fable recorded the same gap as G-a. | DTO **names** are v3's (via playbook §2/§8 + Fable §3.3). DTO **field sets** are this pass's proposal. §5 lists what G5 reconciles. |
| **C** | **GS1/GS3/GS4/GX1/GP1/GP2 are label-only in the readable corpus.** Only GS2 could be reconstructed from independent evidence (§4). | The other gap labels are not implemented and not faked. `arcs` stays empty pending GP1. |
| **D** | **The CDP lane emits zero events today** (thread 5718 review). | `cdp.generate.*` is a *prospective* family — the consumer half of a contract the emitter side has yet to honour. `cdp` derives empty until it does, which is correct. |

None of these block G4: the folds, purity, determinism and wire schema are all
testable against authored fixtures. They do mean **field names are the first thing
to check at graft**, and that is where §5 points.

---

## 3. Handler table — every signal this core folds

Provenance is per-signal because it varies. The registry lives in `signals.py`;
`tests/test_contract.py` asserts this table, that module, and `Model.handled_signals`
all agree, so none of the three can drift silently.

### 3.1 Charter family — VERIFIED in Cortex

| Signal | Folded to | Provenance |
|---|---|---|
| `manage.charter.tick.scanned` | tick counts, `skipped_by_reason` histogram, lease/queue/WIP | `specs/charter-runner-state-close-on-no-gated-pickup.md` (A6) |
| `manage.charter.tick.admitted` | root → `in_flight`; links `worker_thread`; mirrors `path_sim_g_step`; optional `objective` from scoreboard | `specs/charter-runner-handoff-admission-live-dogfood.md` S3 |
| `manage.charter.tick.closed` | harvest **window** close — `window_index`, `checkpoint_turn` | same spec, S7 |
| `manage.charter.tick.root_skipped` | skip reason + streak | `charter-runner-state-close-on-no-gated-pickup.md` |
| `manage.charter.tick.root_closed` | the **only** root-closing signal | same |
| `manage.charter.tick.waiting_open` | `waiting_open_since_ms`, soft remind | `specs/charter-runner-tick.md` |
| `manage.charter.tick.error` | last tick error | `recon/charter-admission-environment-contract/tier1-anchors.md` |
| `manage.charter.tick.intent_healed` | clears an orphaned admit intent — **historical: no live emitter** (Phase 3 retired the heal path); fold kept for replaying archived events | `specs/charter-runner-admit-intent-orphan-self-heal.md` |
| `manage.charter.tick.audit` | **cold-start seed only** | tier1-anchors (8-min window, admitted/closed/failed) |
| `manage.charter.tick.started` | tick loop → running | v3 §4 lifecycle |
| `manage.charter.tick.stopped` | tick loop → stopped | v3 §4 lifecycle |
| `manage.charter.tick.reloaded` | reload command resolved — **historical: no live emitter**; `charter_reload` now bounces the tick loop silently (`count=0`, no module re-import) | v3 §4 |
| `manage.charter.tick.window_failed` | root → failed + attention | v3 §4/§9 |
| `manage.charter.tick.paused` | durable hold armed (`hold=yes` + reason) | `kernel/hold.py` / manage `charter_pause` |
| `manage.charter.tick.held` | hold heartbeat while ticks skip | `emit_held_if_due` |
| `manage.charter.tick.resumed` | durable hold cleared | manage `charter_resume` |
| `manage.charter.root.blocked` | per-root operator hold armed (ledger BLOCKED) | `root_control.block_root` / manage `charter_block_root` |
| `manage.charter.root.unblocked` | per-root hold cleared (BLOCKED → IDLE) | `root_control.unblock_root` / manage `charter_unblock_root` |
| `manage.charter.tick.frictions_audit_passed` | window harvest friction audit passed | `window_terminal_harvest.py` |
| `manage.charter.tick.transition` | per-root status transition (informational) | `charter_runner/telemetry.py` |
| `manage.charter.tick.shadow.diff` | ack only (high-volume; ¬ mint root rows) | `charter_runner/telemetry.py` |
| `manage.charter.tick.shadow.starved` | ack only | `charter_runner/telemetry.py` |
| `manage.charter.tick.consult.queued` | consult tip queued for root (informational) | `charter_runner/telemetry.py` |
| `manage.charter.tick.consult.deferred` | consult tip deferred (informational) | `charter_runner/telemetry.py` |
| `manage.charter.tick.enrollment.filtered` | ledger-migrated root blocked on old path | `charter_runner/telemetry.py` |
| `manage.charter.conveyor.enrolled` | friction-belt item enqueued (`FRICTION BELT` frame) | `observation_event_conveyor.py` |
| `manage.charter.conveyor.stale` | belt enrollment demoted after idle ticks | `conveyor.sweep_stale_enrollments` |
| `manage.charter.conveyor.disenrolled` | belt enrollment removed (operator cancel / root close) | `conveyor.disenroll_frictions` |
| `manage.charter.conveyor.enroll_failed` | harvest minted follow-ons but enroll raised | `window_terminal_harvest.py` |
| `manage.charter.conveyor.identical_work_refire_refused` | belt refused an identical-work refire | `observation_event_conveyor.py` |

### 3.2 SDK family — two lanes, one dispatch (GS2)

| Signal | Emitter | Provenance |
|---|---|---|
| `monitor.meta.sdk_started` | graft synthetic | D2 — no live `frontier.sdk.worker.started` (GS3) |
| `frontier.sdk.worker.progress` | `worker` | **VERIFIED** — `cursor_sdk_events.py` |
| `frontier.sdk.worker.toolcall` | `worker` | **VERIFIED** — `cursor_sdk_stream_capture.py` (last tool column) |
| `frontier.sdk.worker.completed` | `worker` | **VERIFIED** — `cursor_sdk_events.py`, token-cost specs |
| `frontier.sdk.worker.failed` | `worker` | **VERIFIED** — `cursor_sdk_events.py` |
| `pipeline.frontier.dispatch.started` | `pipeline` | **VERIFIED** — `frontier_lifecycle.py` |
| `pipeline.frontier.dispatch.completed` | `pipeline` | **VERIFIED** — pipeline dispatch lifecycle |
| `pipeline.frontier.dispatch.failed` | `pipeline` | **VERIFIED** — pipeline dispatch lifecycle |
| `frontier.sdk.worker.queued` | `worker` | **VERIFIED** — GS2 branch stargate vs git_worker |
| `frontier.sdk.generate.requested` | `worker` | **VERIFIED** — stamps `resolved_model` before/while queued |
| `frontier.sdk.worker.timeout` | `worker` | **VERIFIED** — `cursor_sdk_events.py` |
| `frontier.sdk.worker.orphaned` | `worker` | **VERIFIED** — `cursor_sdk_events.py` |
| `frontier.sdk.worker.cancelled` | `worker` | **VERIFIED** — `cursor_sdk_cancel_events.py` (supersede) |
| `frontier.sdk.worker.delivery_failed` | `worker` | **VERIFIED** — non-terminal (run ok, bus fail) |
| `frontier.sdk.implement.source_ref_unresolved` | `worker` | **VERIFIED** — implement admit without `source_ref` (gate bypass) |
| `frontier.sdk.worker.lease.promoted` | `worker` | **VERIFIED** — FIFO advance |
| `frontier.sdk.worker.lease.acquired` | `worker` | **VERIFIED** — write lease acquired; start/admit clock |
| `frontier.sdk.worker.lease.released` | `worker` | **VERIFIED** — write lease release |
| `frontier.sdk.worker.lease.park_enter` | `worker` | **VERIFIED** — parent parked_waiting |
| `frontier.sdk.worker.lease.park_restore` | `worker` | **VERIFIED** — restore parent state |
| `frontier.sdk.closeout.relocated` | `worker` | **VERIFIED** — durable closeout URI |
| `frontier.sdk.closeout.reconciled` | `worker` | **VERIFIED** — FS ground truth suppressed closeout degrade |
| `frontier.sdk.closeout.relayed` | `worker` | **VERIFIED** — GIW closeout relay |
| `frontier.sdk.worker.dispatched` | `worker` | **VERIFIED** — dispatch accepted (opens row, GS2 lane A); GIW emits only when `admitted_via=cursor-auto`; stamps `topic` / `nest_under` |
| `frontier.sdk.worker.resumed` | `worker` | **VERIFIED** — child resume; `resume_of` aliases onto a live parent (no second LIVE identity) |
| `frontier.sdk.admit.duplicate_refused` | `worker` | **VERIFIED** — attention item, never a live row |
| `frontier.sdk.closeout.partial_work.production_specimen` | `worker` | **VERIFIED** — production `partial:work` specimen; identity only, never terminal |
| `frontier.sdk.review_child.spawned` | `worker` | **VERIFIED** — auto-review child nested under parent execution |
| `sdk.lane.selected` | — | **VERIFIED** — checkout lane A/B; stash/stamp only (no row mint) |
| `sdk.lane_b.minted` | — | **VERIFIED** — Lane-B branch mint; stash/stamp only (no row mint) |
| `mcp.team.dispatch.dispatched` | `mcp` | **VERIFIED** — stamps `from=`/`via=` (seat/surface); stash/stamp only (no row mint) |

**Endpoint provenance (agent-bus 6164):** `SdkDispatchRow.admitted_via` / `asked_by` /
`purpose` / `story_id` / `topic` / `nest_under` / `resume_of` fold first-writer-wins
from `worker.queued`, `worker.dispatched`, and terminal payloads. Live paint
appends ``topic=`` after model when present and ``{admitted_via or '?'}←{asked_by}``
on `sdk_live_line` when `provenance=signal` (reconciled rows abstain).
`purpose` is story-wire `intent:` (or `(unstated)`), not operator mission prose.

**Phantom (documented, not handled):** `frontier.sdk.worker.started` — GS3 gap; fold opens rows on `pipeline.frontier.dispatch.started`, `frontier.sdk.worker.dispatched`, or graft `monitor.meta.sdk_started` instead.

### 3.3 CDP family — live v3 §6 (G5.2 slice 1)

**Handled (live emitters, keyed on `request_id`):** `cdp.generate.admitted` ·
`cdp.generate.submitted` · `cdp.generate.proof` · `cdp.generate.stalled` ·
`cdp.generate.delivery_failed` · `frontier.poll.hint.issued` (filter
`reply_from_agent == 'cdp'`).

**Handled (stamp-only — join on thread; never open a leg):**
`mcp.agentbus.thread.cse.bound` · `cdp.provenance.bound`. These stash or stamp
`chat_url` on an existing CDP leg keyed by `thread_id` / `lane_thread`; alone
they leave `cdp` empty.

**Removed from handler table (G4 phantom — never emitted live):**
`cdp.generate.running` · `cdp.generate.progress` · `cdp.generate.completed` ·
`cdp.generate.failed` · `cdp.generate.aborted`.

**Declared observation (live emitters, ignored, not handled):**
`cdp.generate.compose_attested` · `cdp.generate.reconciled` ·
`cdp.generate.horizon.unverifiable`. These are in `CDP_OBSERVATION_SIGNALS` only —
not in `CDP_FAMILY`, `ALL_HANDLED`, or `CdpFold.handlers()`. They must not
increment `unhandled_signals` and must not set `terminal_ms`.

See `v3-gap-taxonomy.md` GP3a/GP3b and slice-4 drift table.

### 3.4 Realtime-plane counters — VERIFIED (doctrine §2)

`events.dropped.ingest` · `events.dropped.subscribe`

### 3.5 Cold-start seed meta — G5 graft only (not on live bus)

`monitor.seed.fold_status` — fold posture after lease-snapshot reconcile (`seeded` /
`suspect`). Injected by `ColdStartSeeder`; never emitted on the Event Service.

`monitor.meta.sdk_started` — synthetic SDK row from reconcile / lease-snapshot
(D2: no live `frontier.sdk.worker.started` emitter). `_provenance=reconciled`.

`monitor.meta.charter_objective` — scoreboard ``## Original objective`` grafted
onto a root row (cold-start seeder, click-time cortex reconcile, or mirrored on
live ``manage.charter.tick.admitted`` when the charter-runner reads the scoreboard
at admit time). TICK rows render ``obj: …`` inline when present.

`monitor.reconcile.source_failed` — click-time reconcile source failure; drives
`attention[]`, never a steady-state poll input.

`monitor.transport.replay_truncated` — GX1 subscribe replay window could not be
satisfied; drives `attention[]` and triggers cold re-seed via the Controller.

`system.started` — Stargate session boundary; SdkFold terminalizes review-child
rows stranded before the watermark (restart orphan clear).

Asymmetric on purpose: subscribe drops are **correct** under overload and do not
degrade health; ingest drops mean fold inputs were lost and folded state may be
incomplete, so they do.

---

## 4. Negative space — what each handler must NOT infer

The expensive defects in a monitor are not missing rows. They are confidently
wrong rows. Each of these has a test.

| Rule | Why | Test |
|---|---|---|
| `.closed` does **not** close a root | it is window-shaped; overloading it was considered and killed in Cortex | `test_window_close_does_not_close_the_root` |
| `admitted=0` is **not** a fault | tick health ≠ admission progress | `test_admitted_zero_is_not_a_health_fault` |
| a root vanishing from `scanned` is **not** closed | absence carries no disposition | — (state simply persists) |
| **CDP silence is not failure** | G3 is a black box until wall ceiling; admitted ≠ failed | `test_cdp_silence_is_not_failure` |
| `stalled` / `delivery_failed` are **terminals** | v3 §6 live emitters | `test_cdp_stalled_raises_attention` |
| a **judgment gap** yields no SDK row | "admitted" and "the worker started" are different claims | `test_admission_alone_creates_no_sdk_row` |
| correlation never guesses | a wrong link is invisible and corrupts every panel; an unlinked row is honest | `test_parked_state_needs_the_correlation_edge` |
| `arc_g_step` has exactly one source | there is no CHECKPOINT parser and no second route | `test_arc_g_step_mirrors_admission_payload_only` |
| an unknown signal never raises | a monitor that crashes on drift is worse than one that reports it | `test_unknown_signal_is_counted_never_raised` |
| **observation ≠ unhandled ≠ handled** | live `compose_attested` / `reconciled` / `horizon.unverifiable` are declared and ignored at the Model gate | `test_cdp_observation_signals_not_unhandled` |

### GS2 — reconstructed, flag for confirmation

The v3 definition of GS2 is unreadable from here, so its shape was rebuilt from two
independent attested sources: Fable §3.4 ("`source`… required to disambiguate the
GS2 dual-emitter case") and friction 22940, where dual-emitter divergence between
a stream-fold path and a manifest path was a real shipped defect whose ratified fix
**kept** the second emitter as a cross-check rather than deleting it.

Reconstruction: one cursor-sdk dispatch is terminal-observable from both
`frontier.sdk.worker.completed` and `pipeline.frontier.dispatch.completed`. Both
are attested live signals and both carry `execution_id`. The fold therefore:

1. keys on `execution_id`, so a `resume_from` overlap replaying a terminal is a
   no-op — **idempotent**;
2. lets the **first terminal win** every timing and status field;
3. records disagreement in `divergent_fields` and raises a **crit** attention item,
   and **picks no winner**. Reconciling would hide the exact defect class 22940 was.

**G5: confirm this against v3 §GS2 before trusting it.** If v3 means something
else by GS2, the fold logic is still sound but the label is wrong.

---

## 5. Graft checklist for G5

### 5.1 Wire the adapters

| Adapter | Satisfies | Wire to |
|---|---|---|
| `UlgEventSource` | `protocols.EventSource` | `libs/event_store/client.py` — 4 subscribes + `resume_from` |
| clock | `protocols.Clock` | 1 s tick |
| `ColdStartSeeder` | replay into `Model.apply` | `manage.charter.tick.audit`, `signal-events`, `lease-snapshot` |
| `ReloadCommand` | command channel | manage.sock `charter_reload` |
| `ReconcileOnClick` | `protocols.ReconcilePort` | agent-bus read, admin HTTP — **click-time only** |
| projection host | `ProjectionCodec` frames | `libs/projection.BroadcastHub` over UDS (S5) |

An `EventRecord` needs only `signal` / `ts_unix_ms` / `seq` / `payload`. If the
envelope gains CloudEvents `id` / `source` / `subject` (Fable §3.4), the core picks
them up automatically via `envelope_source` / `envelope_subject` / `envelope_id`
with no core change — a pre-addition four-field record stays valid.

### 5.2 The Controller loop is already written, twice

`__main__.py` is a degenerate Controller with the production shape:

```python
now  = self.clock.now_ms()
proj = self.model.derive(now)          # pure, no I/O
if proj.fingerprint != self._last:
    self.hub.publish(proj)             # drop-oldest, no ack, never blocks
    self._last = proj.fingerprint
```

Two rules the Controller owns and the core cannot enforce:

* **Post-drop hints.** After any subscriber drop, stamp the next delivery to that
  subscriber with `("*",)` — call `model.hints_after_drop(frame)`. Falsifier F4.
  Hints are advisory, per-subscriber, and relative to the last *delivered* frame.
* **Never derive.** If the Controller needs a datum the Model lacks, the fix is a
  new `EventRecord`, never a Controller-side computation.

### 5.3 Reconcile against v3 first — highest-value checks

1. **Payload field names** on the SDK and charter families. Everything else rests
   on these, and gap A means they were reconstructed from specs, not captured.
2. **`frontier.sdk.worker.{started,progress,failed}`** and
   `pipeline.frontier.dispatch.failed` — INFERRED names. Grep the emitters.
3. **GS2's actual v3 definition** (§4 above).
4. **v3 §2.2 DTO field sets** against `dtos.py`; additive-only within schema 1.
5. **Attention thresholds** in `Thresholds` against v3 §9. Defaults here are
   deliberate placeholders, all of them idle windows.
6. **Whether the real fixtures exist somewhere** (gap A). If they do, replace the
   authored ones and re-run; a fold that survives real capture is worth more than
   one that survives its author's fixtures.

### 5.4 Two invariant tensions to disposition, not code around

* **Signal grammar.** `[universal:events]` fixes signals at
  `^[a-z]+(\.[a-z]+){1,4}$` — no underscores. Four **live** charter signals violate
  it: `root_skipped`, `root_closed`, `waiting_open`, `intent_healed`. The core is a
  consumer and accepts what the bus emits. The divergence belongs to the
  charter-runner owner: either the grammar or the emitters should move.
* **`bus_lifecycle_state` / `dispatch_links`.** Fable §4.1 observed both declared
  and unpopulated on live threads. Nothing here depends on them. Wire or retire.

---

## 6. Fixture provenance

**Repo authority; cortex mirror byte-identical.** Sync direction: repo → cortex
(slice 4). Hashes in `fixtures/CORTEX_MIRROR.sha256`; `test_fixture_cortex_mirror.py`
pins repo bytes.

| Fixture | Exercises |
|---|---|
| `charter-admit-run-terminal.jsonl` | cold-start audit → scan → admit → pipeline start → progress → terminal → window close → root close. Carries one deliberate **duplicate** `scanned` (a `resume_from` re-delivery) that must not move the fingerprint. |
| `cdp-leg.jsonl` | three legs using G4 contract signal names (`completed` not live `proof`); clean completion with proof; completion **without** proof; silence stays `running`. Plus a subscribe drop. |
| `parked-parent.jsonl` | cross-family parked parent, five-tick skip streak, `waiting_open`, `path_sim_g_step` mirror. |
| `gs2-dual-emitter.jsonl` | GS2 agree/disagree/single-emitter; tick `error` with live `reason` field; ingest drop; unknown signal. |

---

## 7. Running it

```bash
# text sink — the v1 View, and the gate: if --watch is not trustworthy, SDL is not ready
python -m dispatch_monitor_core --watch fixtures/parked-parent.jsonl

# canonical JSON frames (handshake + snapshot) — what the projection channel carries
python -m dispatch_monitor_core --watch fixtures/cdp-leg.jsonl --format json

# per-record frames with fingerprint suppression — the Controller loop, visible
python -m dispatch_monitor_core --watch fixtures/charter-admit-run-terminal.jsonl \
    --frames each --suppress-unchanged

python -m pytest tests -q     # 81 passed
```

`--now-ms` freezes the clock (default: the fixture's own high-water timestamp), which
is what makes every age-derived assertion reproducible.

## 8. Test pyramid

| Layer | File | Asserts |
|---|---|---|
| Determinism | `test_determinism.py` | F2 replay-twice-same-hash; age exclusion suppresses; threshold crossing still publishes |
| Fold behaviour | `test_folds.py` | the §4 negative space, GS2, parked parent, idempotence |
| Wire + View | `test_codec_and_watch.py` | round-trip fidelity, version refusal, unknown-field tolerance, F4 post-drop hints, the `--watch` entry |
| Contract | `test_contract.py` | stdlib-only, no clock, no I/O outside the harness, no text parsing, README↔registry↔handler-table agreement, frozen DTOs, total attention order |

The contract layer is the one that matters over time: it fails if a later edit
quietly imports `libs.`, adds a CHECKPOINT parser, or lets this README drift away
from the code it documents.
