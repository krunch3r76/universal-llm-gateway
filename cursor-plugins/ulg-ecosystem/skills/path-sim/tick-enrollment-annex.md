# Path-sim L3 annex — tick enrollment + autonomous charter procession

**Parent SOT:** `cursor-plugins/ulg-ecosystem/skills/path-sim/SKILL.md`.
Open this annex when **enrolling a path-sim todo on the charter runner** or running the
**autonomous** attendance arc. Attended, in-session path-sim consults do not need it.

## Tick enrollment — typed admit (BINDING — charter-runner-alt-arch)

**When:** standing path-sim / charter work must become runnable on the charter
runner without seat SeedConfirm or ``PHASE1_SEEDS`` midwifery.

**Profile:** typed work-item admit on the durable ledger is the **control-plane
SoT**. CHECKPOINT tip remains optional progress/resume journal — **not** admit
SoT. Malformed or absent tip does **not** block admit when the typed record is
valid.

**Order:** (1) mint scoreboard when chartered; (2) stamp todo attrs
(§ Todo lifecycle bind); (3) **typed admit** the root via ledger
``admit_work_item`` (required fields: ``root_id``, ``pickup_gid``,
``pickup_lane``, ``attendance``, ``scoreboard_uri``, optional
``pickup_executor``); (4) optional CHECKPOINT on the root for human resume.

**Drive (R3):** wake signal → dirty work-item → harvest → dispatch → advance on
the same typed record. Ledger-query floor covers stale open items; roster poll
is not the primary admitter.

**Launch gate (fail-closed on typed record):** invalid/missing typed fields →
fail closed with migrate hint. Tip schema skips apply only on the legacy path
when no typed row exists.

**Legacy enrollment tag:** tag ``charter-runner`` + ``enroll_charter_runner=true``
may still appear on bus threads for visibility, but dual-key enroll is **not**
required for admit→dispatch on the typed path.

## Tick enrollment — initial CHECKPOINT (legacy journal template)

**When:** minting a new `charter-runner` root (`agent_bus` `send` with `enroll_charter_runner=true`) to hang a path-sim todo on the charter runner — including friction follow-ons and operator “enroll on charter runner” requests.

**Profile:** `tick_charter` only (machine consumer). Full field contract + runtime skips: Use the `checkpoint-discipline` skill. **This block is the copy-paste enrollment template** — do not paraphrase the RESUME line.

**Order:** (1) mint scoreboard at `cortex://notes/system/threads/<slug>-scoreboard.md` from `charter-scoreboard.md` template when chartered; (2) stamp todo attrs (§ Todo lifecycle bind — dispatch-cascade-annex); (3) post CHECKPOINT below on a **new** root slug `path-sim-<todo-slug>`.

**Launch gate (fail-closed):** missing `##` sections, non-gated Next pickup, or RESUME prefix ≠ `— RESUME (any seat, no command):` → charter runner skips launch, emits a one-line fix hint, and **keeps skipping**. Phase 3 deleted `schema_skip_heal` — there is **no** machine repair CHECKPOINT after N skips in any mode; a schema-skipped enrollment stays skipped until an author reseeds the tip. `agent_bus send` does **not** validate — author correctly here.

**Executor must be substrate-compatible with the admit path (BINDING — a:26659).** The kernel reads `executor=` off the **first gated** `## Next pickup` row (later rows are invisible until that one clears) and refuses `ADMIT_WORKER` when the named family cannot ride the worker generate seat → `skipped_reason=executor_mismatch`. `cursor/*` is worker-native; **`cdp/*` is not** — a `cdp/opus` row parks the root indefinitely, because no CDP admit path exists yet (queueing such a tip as a consult is designed but unlanded). Consequences for authors: a judgment leg that genuinely needs CDP Opus belongs in a **consult** stop (`CONSULT_PENDING` + `consult_role`), ¬ a worker row; and never let a `cdp/*` row sit **first** ahead of generate-compatible work you want processed — order the gated rows so the runnable one leads. `executor=pending` / absent leaves the attended→generate path open.

**Template** — replace `{…}`; keep section headings and RESUME **byte-identical**:

```
TYPE: CHECKPOINT

## Profile
`tick_charter` (charter-runner enrolled)

## Anchor
- Thread: agent-bus:{ROOT_ID}
- Window: 1 · path-sim bundled arc
- Todo: {TODO_SLUG}
- Scoreboard: {SCOREBOARD_URI}

## State
**Primary OPEN:** G1–G6 (path-sim cascade).
**WIP:** none.

## Steps
1. [ ] G1 — path-sim L0 Q · {TODO_SLUG}
2. [ ] G2 — Grok A + Gate-2 densify
3. [ ] G3 — R-admit (CDP Opus)
4. [ ] G4 — implement (Composer)
5. [ ] G5 — R-after (/work-item-review)
6. [ ] G6 — closeout + friction_close when applicable

## WIP / In-flight
_None this window._

## Next pickup
1. G1 — path-sim L0 Q · {TODO_SLUG} · executor_lane: judgment · detent=standard

## Frictions
_None this window._

## Sidecars
- Scoreboard: {SCOREBOARD_URI}
- Dense spec / stub: {SPEC_URI}

## Precedents / Implications
_None this window._

## BLOCKED
None.

## Scoreboard URI
{SCOREBOARD_URI}

— RESUME (any seat, no command): load checkpoint-discipline (tip resume + author workflow; done/close claims also load agent-bus-discipline § R12 completeness gate; cursor coding arc may add orchestrator-workflow) → read {SCOREBOARD_URI} → this is the latest CHECKPOINT (wave/in-flight/next above). Do not read the thread linearly. empty Next-pickup ≠ arc complete.
```

**Operator-framed todos:** add to the G1 Next pickup row: `operator_framed=true · frame_uri=<cortex://…> · pinned_question=<verbatim>`.

**Autonomous attendance:** set todo attr `attendance=autonomous` and include tag `attendance:autonomous` on the root when `/path-sim … autonomous` was invoked.

**Post-arc (lead):** densify projection; cite R-admit sidecar + CDP harvest (or allowed-skip evidence); **fire R-after** (`/work-item-review todo:{slug}` · `cursor/grok-4.6`, default-on — same closed skip set) and cite its verdict URI; apply REVISE or seed follow-up; **docstring-quality scan on touched paths — criticals=0** (cite evidence); if concentrated warnings on new public surface ∨ arch-doc feedstock needed → `/docstring-enhance` CDP Sonnet then re-scan; event-instrumentation closeout one-liner when applicable; stamp `recon_waived` with `reason_code=path_sim_self_certify` **only** when R-admit **or** R-after was skipped under the closed set above **and** friction already filed — ¬ as a routine substitute for R; `friction_close` if arc started from friction; `todo-close` with evidence URIs [recon, Q, A, R-admit, implement, R-after, docstring-scan, closeout].

## Autonomous charter procession (attendance axis)

**v0 — landed 2026-07-21** (`autonomous-path-sim-charter`). Design SOT:
`cortex://notes/system/specs/autonomous-path-sim-charter.md`. This section makes the
autonomous arc **first-class** (charter-runner-invocable, not prose-pasted each run).

### The attendance axis (attended | autonomous)

Attendance is a knob **orthogonal to the detent** (`closed|standard|wide|frontier`),
selected at `/path-sim` call time:

| Axis | Values | Meaning |
|---|---|---|
| **detent** | closed \| standard \| wide \| frontier | aperture / reasoning heaviness (unchanged) |
| **attendance** | **attended** (default) \| **autonomous** | attended = lead present, CDP R-admit (current default cascade). autonomous = charter runner launches a background-lead run, satellite R-admit, capped revise loop |

**Call-time token (command Invocation):** `/path-sim … autonomous` (aliases
`attendance=autonomous`, `--attendance=autonomous`). Omit = attended.

**Selection = todo attr `attendance=autonomous`** (durable across windows — an
autonomous arc spans many charter windows; a transient command flag alone dies at the
first window boundary — the lead **copies** the call-time token onto the todo attr).
Attended arcs set nothing new — the default cascade (§ Dispatch bindings) is unchanged.
`autonomous` still declares a detent for its Q/A legs. ¬ prose-paste a charter brief
when the token was given.

**Arming the runner (per-root — no env var).** `CHARTER_ADMISSION_MODE` is
**retired**; production no longer reads it. The kernel resolves attendance
**per enrollment, once per pass** — `resolve_attendance(root)` precedence: **todo attr**
`attendance=autonomous` → **bus thread tag** `attendance:autonomous` /
`attendance:operator_proxy` → default `attended`. Mode follows from attendance:
`autonomous → autonomous`, `operator_proxy → operator_proxy`,
`attended → generate`. Attended and autonomous roots therefore coexist under one
runner (the deferred `charter-autonomous-per-root-attendance` follow-up **landed**;
its overlay defect was friction 26571). Arming an autonomous arc = stamp the todo
attr and/or tag the root — ¬ set an env var, ¬ restart to change mode.

**Enrollment dual-key (legacy visibility — not control-plane):** newly adding tag
``charter-runner`` on ``agent_bus`` ``send`` / ``create_thread`` / ``update_thread``
still requires ``enroll_charter_runner=true`` when the tag is newly added. Typed
ledger admit replaces enroll tag as the control path — keeping or removing an
already-enrolled tag never needs the flag. Worker/window threads use
``charter-window`` + ``root:`` / ``window:`` — not the enrollment tag.

### Autonomous cascade shape (same arc, unattended, across windows)

```
G1 Q (lead CDP Fable L0) → G2 A+Gate-2 (cursor-sdk Grok, dense spec) →
[CONSULT_PENDING ⇒ consult seat (depth-1) ⇒ resume] →
G3 R-admit (team_dispatch model=cdp/opus-5 → web-anthropic Opus; project_ask escape) → G4 implement (Composer) + deploy-verify →
[G4a/G4b/G4c revise, cap 3] → G5 R-after (cursor-sdk Grok /work-item-review) → G6 close
```

**Nest+wait+park (default under limit=1):** a live cursor-sdk parent that needs a
nested same-repo cursor-sdk child MUST park (`nest_under=<parent_dispatch_id>` /
GIW park path) then wait for child terminal and restore — ¬ in-seat collapse as
the default answer to nesting. In-seat remains fallback when park is unavailable
or the work is trivial/bounded. CONSULT depth-1 (never nested *consult*) is a
separate rule and does not forbid nest+wait for Q/A limbs.

**Consult-stop awareness (G7 bind — thin pointer, not stop machinery):**
`CONSULT_PENDING` and depth-1 are shared stop vocabulary with one textual SOT —
`cortex://notes/system/specs/autonomous-path-sim-charter.md` § Stop vocabulary /
§ Target architecture. The charter runner **enforces** those verbs; this skill **cites** them.
A consult launch is a path-sim leg under charter-runner launch: pinned corpus uses the
consult scope-lock template (Question verbatim / OOS / detent / layers / deliverable
gate); returned verdicts use path-sim verdict grammar (`ADMIT` | `ADMIT_WITH_AMENDMENTS`
| `RATIFY` | `RATIFY_WITH_CONDITIONS` | `RETURN` | `SCOPE-DRIFT`). Default G3 R-admit
hosting is **consult-seat** (`consult_role: r_admit`); holder-fired is IF6 emergency
only (G5 dogfood 2026-07-23). R-admit and judgment-gap replies write the **same**
`implement_ready` provenance schema (`consult_thread`, `verdict`, `consultant_family`,
`consultant_substrate`) — see design doc § Where R-admit runs. This section
does **not** own admission, caps, or eligibility prose.

Each charter window advances **one gated step then posts a CHECKPOINT and stops**;
the charter runner launches the next gated Next-pickup. The arc processes *across* runs
— never an immortal loop inside one window. The background-lead window is authorized
(unlike the one-step generate packet) to decompose, dispatch sub-legs, fire satellite
R-admit, restart services for deploy-verify, and revise.

### Satellite R-admit (dissolves the operator pin)

Attended R-admit fires from the lead IDE seat's CDP browser lane — which a headless
worker lacks (so a headless worker "running R" collapses to self-certify). Autonomous
R-admit fires the **same web-anthropic Opus** via the **primary** `team_dispatch(model=cdp/opus-5)`
model-endpoint (MCP `project_ask` = escape only), invocable from any vortex-code seat:

```
# Primary
team_dispatch(op=generate, model=cdp/opus-5, contract=light-bounded,
              sidecar_ref=cortex://…, dispatch_thread_id=…)
agent_bus.wait(… from_agent=web-anthropic)   # reply OR DELIVERY FAILED; long running ≠ stalled

# Escape (IF6 / satellite-direct)
project_ask(op=submit, prompt_uri=cortex://…, converse=true,
            no_project_uuid=true, model=opus-5, purpose=ask)
project_ask(op=poll, execution_id=…)
```

Poll discipline is identical to § R-admit poll (dispatch-cascade-annex). If the window
boundary arrives before R completes, CHECKPOINT the live `poll_hint` / `from=web-anthropic`
bus-turn (primary) or `execution_id` (escape) as the next-pickup so the next window
resumes (¬ re-fire).

### Revise-via-clean-CHECKPOINT (BINDING)

`charter-runner._recover_worker_failure` STOPS the root on a worker crash/timeout (no
auto-retry). So a **failed deploy-verify probe is a REVISE, not a failure**: post a
clean, success-shaped CHECKPOINT whose gated Next-pickup is the next revise step
(`G4a`…), and exit the window cleanly. NEVER exit with a failure status on a
recoverable probe fail. Capped at **3** cycles (read the count off the scoreboard); on
exhaustion post a **BLOCKED** line (not done) — a bounded stop, not a crash, not a
false done.

### `checkpoint_missing` — no runner heal (BINDING — Phase 3 retired self-heal)

**Nothing repairs a missing window terminal.** If the worker exits
`complete`/`partial` without posting one (`CHECKPOINT` \| `CONSULT_PENDING` \|
`BLOCKED` \| `PACKAGING_DEFICIT`), the window is simply **not** a successful window:
it goes unharvested and the enrollment's gated pickup does not advance. The charter runner does
**not** author a replacement CHECKPOINT in any attendance mode.

Spec §A5 — *"Supervisor never authors CHECKPOINTs"* — and §B — *"never a new
`*_heal.py`"* — retired the heal path at the Phase 3 cutover; `self_heal.py` is
deleted. **The remedy is author reseed, never runner heal** (the same rule
`checkpoint_schema/footer.py` enforces for an invalid `wip` field). So: post your
window terminal, and validate its footer **before** posting — no grace period and no
heal budget will cover you.

Empty Next-pickup on an enrolled root is a different exit: **`no_gated_pickup`**
(admission precondition — the kernel refuses `ADMIT_WORKER`/`ADMIT_CONSULT` when the
tip has no live gated row) → state-close, the silent-starve class. Operational digest
for CHECKPOINT authors: Use the `checkpoint-discipline` skill § Autonomous tick runtime.

### restart-auth (BINDING — overrides ask-before-restart)

The materialized autonomous packet AC **explicitly authorizes** deploy-verify
restart during the worker window, overriding `implement-work-item` §4B:
`quality_gate → manage(sync_restart) → wait_healthy (manage busy_status) → live probe`.
**Only the `manage` MCP** — never systemctl / pkill / docker / raw shell kill.

**Charter harvest propagation (BINDING — standard for all charter windows):**
when the worker closeout carries ``propagation_residue`` (landed≠live), the
charter runner **executes** ``sync_restart`` for each mapped service at harvest
(after the worker exits), **blocking on drain** for ``git_integration_worker``
and polling ``wait_healthy`` for others. Workers still run deploy-verify in-window
when they can; harvest propagation is the safety net so landed code goes live
without a lead-seat follow-up. ``install_plugin`` lines remain manual.

**`charter_reload` is not a code-pickup path (Phase 3).** It restarts the charter runner
loop class in-place and returns ``count=0`` / ``reloaded_modules=[]`` — Phase 3
retired ``importlib.reload``, so it does **not** re-import Python modules. A
long-lived ``manage`` process keeps the charter-runner code it booted with.
Therefore: **edits under ``…/controller/charter_runner/`` require a manage
quit/start** before they are live; the charter runner still schedules ``charter_reload``
on that path, but treat it as a loop bounce, not a deploy. **Recipe SOT
(seat op, tmux ``0:0``):** hub rule ``services_ws.mdc`` § Manage process recycle
— ``charter_pause`` → ``safe_to_quit`` → ``./manage q`` → ``./manage`` →
``charter_resume``. ¬ operator gate.

**Per-root hold (≠ recycle):** to stop admits on **one** root, use
``manage(action="charter_block_root", root_id=…)`` — not a bus NOTE/WAKE tip.
Clear with ``charter_unblock_root`` (``reenroll=true`` when returning to the roster).
Global ``charter_pause`` remains the fleet stop for quit/start. Teaching SOT:
``services_ws.mdc`` § Charter control — global vs per-root.

### The one guardrail (restated)

**Substrate separation, not operator presence, keeps autonomous R honest.**
Q/A run on cursor-sdk Grok; implement on cursor-sdk Composer; R-admit runs on web-anthropic Opus
via `team_dispatch(model=cdp/opus-5)` (MCP `project_ask` escape) — a different family on a
different transport. The background lead firing its own R is legitimate **only** because
the reviewer model/family differs. Collapsing R-admit into the cursor-sdk window's
self-assessment = self-certify = invalid. R-admit stays web Opus; R-after keeps its
documented cursor-sdk partial-independence trade (§ R positions).
