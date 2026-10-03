## Interactive entry
<!-- width-seat:v1:start -->
> **Width seat** (generated from `libs/implement_admission/conductor_width_seat.py`; do not hand-edit): ACTIVE = `cdp/opus-5.5` · `reasoning_effort=high` · `effort_when_bind_gates_wave=max` only when a bind gates a wave · CLI `--model opus-5.5` · usage channel **Opus**. SKETCH (G1) = `cdp/opus-5.5` · `reasoning_effort=extra`. SKEPTIC (G4) = `cdp/opus-5.5` · `reasoning_effort=extra` (one rung above ACTIVE; cross-family review stays on this effort). RESTORE = `cdp/fable-5.1` · `reasoning_effort=high` (only when Kaywan asks; set `ACTIVE = RESTORE`).
<!-- width-seat:v1:end -->


Command `/conductor` (plugin): orient → ask establishing questions (incl. **model
tier**; checkout regime pre-filled **Lane B**, confirm or override to Lane A) →
draft charter/scoreboard/packet → confirm → admit. Skill body does not re-ask
when the operator already bound the answers in chat. Scoreboard mint includes
stronger-model gates (a:32146 · a:32226): G4 Skeptic = ACTIVE; after-ship code review = `cdp/opus-5.5` — hard, ¬
optional, ¬ Composer self-cert; CDP transport fail ≡ stop past that gate
(¬ DEFERRED-and-proceed). Profile
`fable-scarce` ⇒ see § Profile fable-scarce (width rows stay on ACTIVE).

## Composes with

| Skill / rule | Boundary |
|---|---|
| `orchestration-lanes` | Root/mission birth; conductor **runs** after birth |
| `mission-operator` | Formal operator_proxy turn schema — different lane kind |
| `checkpoint-discipline` | Root CHECKPOINT tip hygiene |
| `handoff-packet-authoring` | Six-block authoring |
| `bind-then-compose-dispatch` | Mechanical nest after a pick (any G-row) |
| `judgment-escalation-ladder` | Unsure → binder, not human |
| `git-posture` | Lane-B branch land = merge/`git_land`; ¬ path-copy onto master. Conductor admit = standing "operator directs a merge" for its own branch (§ Run to completion) — ¬ a second gate |
| `lean-context-dispatch-first` | Tier ladder + Opus inform-then-proceed |
| `consult-routing` | Model split / non-primary gate · **cursor-sdk `lane=` caller recipe** (this skill does not own omit semantics) |
| `life-operator-do-chain` | Hop names + products (Sketch → shape bind · Mission Composer → conductor score · this seat plays it) — named hop is direction, not a recipe when harvest conflicts |
| `reasoning-posture` | Liaison decide-before-admit — pin Question / OOS / detent |
| `runbook:extraordinary-aperture` | Named hop vs harvested state — in-seat widen; ¬ path-sim cascade |
| `todo:conductor-hop-wait-protocol` | GIW hop reactor occupancy + witness fold — this skill names seat doctrine; substrate detail in R2 review |

## Anti-patterns

| Bad | Good |
|---|---|
| Admit conductor packet without `Use the conductor skill` in `<invariants>` | Continuity-lead required-skill gate (Audience) |
| Hand-author `tmp/reviews/{slug}-conductor-packet.md` and pass `packet_path=` on `contract=conductor` | Shape the **todo**; `source_ref=todo:` — the materializer writes the packet and the scoreboard (§ Admit from an existing plan) |
| Spawn with `contract="freeform"` + `source_ref=todo:` (post-`19ab1566a` residue) | `contract="conductor"` — `none_with_source_ref` is a 422, not a lighter admit |
| Stamp `derived_from` attribute only (or edge only) to skip G1 | Both attribute and `consult_kind=architecture` edge, or neither — a `consult_kind: sketch` doc is not a G1 witness |
| Seed `implement_ready` / `files_modified` / `acceptance` on the todo | Readiness is a Gate-2 assertion; canonical keys are `files_expected` / `acceptance_criteria` / `required_skills` (non-empty `list[str]`) |
| Reconstruct the admit path from source + 422s because the plan “isn't a sketch” | Register the plan as the substitute sketch (`consult_kind: sketch`), then follow § Admit from an existing plan |
| Drop the packet Use-line because `skills=["conductor"]` now mounts | Both — the Use-line is what survives a replayed or `skills=`-less admit |
| One flat `implement` "does the whole mission" | Conductor + nested contracts per G-row |
| Page human "which remedy?" | Nest binder; `needs-attended` only for operator-only |
| Conductor hand-codes any G-row whose remainder is files+tests after a pick | Nest Composer |
| nest_under sibling mission lease | Queue or wait; record holder |
| Convert incident lane into root mid-flight | Cite incident; root stays continuity |
| Wait forever on sibling merge without bind | Scoreboard cite-only vs explicit wait criterion |
| Drop `lane="B"` after `CURSOR_LANE_B_SCOPE_REFUSED` | Fix scope paths; re-admit with `lane="B"` |
| Treat missing Lane-B worktree as a shared-master admit | Expect `422 CURSOR_LANE_B_WORKTREE_MISSING`; mint/inherit a tree or name Lane A |
| Conductor on Lane A while G-row code is on `cursor-sdk/lane-*` | One regime: conductor + nests share the Lane-B worktree/branch |
| Opus-by-default for every conductor | Seat table; Composer standing; CDP escalation; Opus only with explicit pin |
| Omit `lane=` assuming that means "no preference" | Lane B is the default — pass `lane="B"` explicitly; name Lane A only with a reason |
| Conductor pauses after a G-row to ask "continue?" | Drive to completion in one commission; report via CHECKPOINT, don't wait for a reply |
| Call `team_dispatch(reuse_thread=<own thread>)` from inside the running conductor | Journal → hop CHECKPOINT → `stop: ROW_HOP`; the substrate admits the successor after your terminal (§ Run to completion) |
| End with the mission open and no token ("done for now") | `ROW_HOP` at a boundary, or the owed designed stop — silence is a budgeted re-admit, then a park |
| Treat the mission's own `git_land` as a second approval gate | Admit is the standing merge ack; land on green + AC met (§ Run to completion) |
| Escalate "ok to merge?" to the human mid-mission | Land it; escalate only genuinely operator-only acts |
| Conductor judges the mission "too big"/risky and stops before any G-row, unasked — or verifies the mission is genuine then refuses it over a later step's scale (7419) | Nest Composer, drive to green; only a **named** packet exception holds the merge — scale/blast-radius/"verified legitimate" alone are never an implicit one. Execute the current step, raise the concern in the closeout, reassess only at the flagged step under standing authorization (reasoning-posture rule 6 mirror) |
| Closes `status: partial`/`checks_failed` with zero files touched because it wanted to flag the plan first | Flag the concern on the CHECKPOINT while still driving — flagging is commentary, not a hold |
| Independent `team_dispatch` (no `nest_under`) for mechanical G-row landing work | `nest_under=<conductor dispatch_id>` + Composer `contract=implement` — independent dispatch is judgment/spec-only |
| Nest 422 then in-seat G5 absorb | `PARKED_TRANSPORT` + persist; fix wire and re-nest under live `dispatch_id` |
| Close G5 because G4 said “remainder is mechanical” + empty-template green | Hang G5; read the overlay or seed a fixture — G4 withhold is not a G5 witness |
| Fire Opus after-ship / Fable Skeptic and never read it | Summoning-thread lead quotes the harvest; unread ⇒ ¬ DONE |
| Terminal `DONE` / land / nest G5 after Composer work with cdp-ask down / CDP stall / empty FAILED body / no owed stronger-model harvest | Escalate at once. Record `cdp_fail_route` on the scoreboard: `nested-grok` or `operator`. Do not wait and do not retry the CDP pool. ¬ DEFERRED-and-proceed; ¬ land-then-background-review; ¬ Composer substitute |
| Stamp G4 / after-ship `DEFERRED (transport)` and keep driving later G-rows | Same break — transport fail ≡ no harvest ≡ stop past that gate |
| Treat G4 Skeptic as code review (or collapse both onto one picker) | Skeptic = ACTIVE on the spec; code review stays `cdp/opus-5.5` on the lane branch diff, before land (G6) |
| Author a nested CDP width prompt without `retrieval_report: cortex://…` (or claim RAG in-stream with no sidecar) | Run `retrieval-before-authoring` for real; write the report sidecar (Queries / Yields / Choice-to-evidence); cite it on the prompt body — substrate refuses admit without it (a:37183) |
| Open a G4 / `gate_path=SKEPTIC` / adversarial-spec prompt with delivery/code-review chrome ("packet carries the code under review", checkout-pytest waive) | Spec-skeptic genre only; job=delivery-review delivery charter is for G6 / `contract=delivery-review`, never G4 — substrate refuses chrome-on-SKEPTIC |
| Hop closeout lists nested prompt URIs with no report bundle | Closeout check: every nested CDP prompt authored this hop has a resolvable report bundle (`hop_prompts_missing_report_bundles`) |
| Treat named hop / `` + `source_ref=todo:X` as a recipe when the score is harvested / `NEXT_ADMIT: none` | Liaison-decide; park remints; new remit → sibling todo + Composer implement |
| Land then stay silent on recycle (or write LAND-LIVE as only “not live”) | Path-explicit commit of session paths, then prompt go-live for each serving process, or announce skip in the same turn; LAND-LIVE names the skipped recycle |
| Land a named `todo:` and leave the card `open` | Stamp `todo-close` / LANDED on that entity in the same turn |
| Assume `ROW_HOP` ⇒ immediate successor while a CDP gate still streams on the mission lane | Reactor skip `live_external_gate` is correct — harvest the gate first |
| Self-report "gate clear" in closeout to unblock hop | Occupancy is a substrate read from the CDP lane snap, not closeout prose |
| Fire a second `cdp/opus-5.5` review while the first gate is still live on the lane | 409 `cdp_external_gate_live` — wait for harvest |

**Work-complete finishing (last).** Default: land → path-explicit commit of session paths → recycle serving processes. Recycle-from-dirty-tree is exceptional. `work_complete` / terminal DONE without quoted commit + recycle (or named skip) is invalid.
