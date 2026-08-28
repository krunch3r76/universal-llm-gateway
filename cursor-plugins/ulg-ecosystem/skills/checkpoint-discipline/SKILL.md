---
name: checkpoint-discipline
description: "Author/resume CHECKPOINTs on standing roots (spine=root): profile pick tick_charter vs orchestrator_continuity, tip supersede + lean resume, role:root stamp, RESUME footer, scoreboard birth."
skill_category: orchestration
trigger_match_terms: ["checkpoint-discipline", "CHECKPOINT", "tick_charter", "orchestrator_continuity", "tip supersede", "lean resume", "role:root", "RESUME footer", "scoreboard birth", "standing root", "Windows log", "myelinate"]
---

# Checkpoint Discipline

`∀ spine=root: tip_hygiene ∧ lean_resume`; enrollment picks **body profile only**.

**Schema SOT (field IDs):** `cortex://notes/system/specs/checkpoint-schema-profiles.md` — B1–B5 / T1–T9 / §3.5 Windows log live there; ¬ copy defs into this skill.

## When to load

About to post, supersede, or resume a CHECKPOINT; standing-root continuity; charter-runner enroll; operator `resume`/`checkpoint <thread#>`.

**Continuity stance (first-class trait — operator 2026-08-25):** `∀ orchestrator_continuity` root: Use the `ulg-for-llms` skill ∧ `## Why this house` on the continuity-doc before Anchor. Birth CHECKPOINT indexes `## Stance` (Use-line + pointer; speech stays on the catch-up file). Resume loads the skill then the preamble, then the index. Footer stays §3.1.1. `tick_charter` skips. Substrate: `root_missing_stance` on birth/bootstrap when either half is missing. `thin_kickoff ∧ starving(why)` is a defect.

## Invariant

```
CHECKPOINT := reconstitution index ∧ deliberative steering
¬ completeness authority
empty(Next-pickup) ⇏ arc_complete
```

Done/close-arc claims: also load `agent-bus-discipline` § R12.

## Spine vs enrollment

| Axis | Values | Note |
|---|---|---|
| Spine | `root` \| `work` | Tip discipline binds **root** only |
| Enrollment | `charter-runner` \| none | Enrolled ⇒ root (auto-stamp). Profile: enrolled → `tick_charter`; else → `orchestrator_continuity` |

`profile=` HTML is descriptive only. Classification recognition: `agent-bus-discipline` § Thread classification (thin).

## Profile discriminator

Enrollment tag is SOT. `tick_charter` = machine consumer (base + T extras; malformed → tick skip). `orchestrator_continuity` = index-thin human/agent index + handoff pointer. ¬ full tick ceremony on non-enrolled roots. Field presence → schema §3/§5/§6.

## Writer conventions

| Rule | Binding |
|---|---|
| Subject | Prefix `CHECKPOINT` (wave/seam). Other verbs: WIP/DONE/BLOCKED/SPAWN/reconcile |
| Body | WIP + pointers; Next-pickup = gated G-rows only; tangent by pointer |
| Primary OPEN vs WIP | Name G-rows separately from seat WIP; divergence without bind/child = named fork |
| Delta gate board | Carry settled `[x]` by reference; emit open/delta rows; evidence in sidecar |
| Scoreboard birth | **Chartered root** (`charter-runner` enrolled): mint `cortex://notes/system/threads/<id>-charter-scoreboard.md` from template **before** first CP if absent (includes empty `## Windows`). `Scoreboard: none` = violation. **Unchartered orchestrator_continuity root**: omit scoreboard birth — mint `## Windows` on the continuity-doc instead |
| Scoreboard write-back | `retract ∨ flip(status) ⇒ update scoreboard BEFORE next CP` (chartered roots only; unchartered roots have no standing scoreboard file) |
| G-row prefix order | `DONE(Gₙ) ⇒ ∀k<n: DONE(Gₖ)` — gated IDs are an ordered prefix, not birth stickers. Skip / out-of-order land ⇒ **renumber** open rows to the end (or retract to Tangentials) in the **same** scoreboard write before the next CP. `OPEN` mid-table above later `DONE` = hygiene defect (operator bind 2026-08-10 · agent-bus:7059). Distinct from mission-roadmap permanent ordinals (`cdp-operator-proxy` inv 29). |
| Terminal-block coherence | Appending a terminal verdict block (`ARC COMPLETE`, closure table) ⇒ refresh the file's `RESUME` / next-actor footer in the **same** write. A footer naming actors the block just closed makes the file contradict itself, and R12 computes against the **file**, not the newest block — either verdict is pickable (friction `27099`) |
| Side-quest | Multi-step ∉ OPEN G-row ⇒ operator bind or child thread before act |
| Residual sweep | Checkpoint-time: named residuals still in chat only? → Use the `residual-imprint` skill |
| Myelinate | Checkpoint-time graph: both-ends-known missing links → `relationship_create` / `edge_create` now; load-bearing existing edges → `edge_update` if judgment changed. `¬` defer to session-close (close is not automatic). Detail: `cortex-orientation` § Myelinate |
| Windows append | On each CHECKPOINT **post**, append one row to `## Windows` on the charter surface (scoreboard if chartered, continuity-doc if unchartered): `cp_ordinal`, turn, `session_id` if known, State one-liner if Arc empty. Tip **pointers** at that table — ¬ paste it. Schema §3.5. `/session-end` fills Arc later. |

## Tip hygiene (spine=root)

| Rule | Binding |
|---|---|
| `role:root` stamp | CP **author** stamps via `update_thread` / `add_tags` on **first** continuity CP if absent when ``AGENT_BUS_CHECKPOINT_AUTO_STAMP`` is on (default off). Manual: `add_tags` / `remove_tags` (or CLI `--add-tag` / `--remove-tag`). Enrolled roots auto-stamp. Legacy read: `CHECKPOINT ∧ ¬type:monitor` ⇒ treat as root until stamped |
| `supersedes_turn` | Target prior **CHECKPOINT** **turn_number** (same thread). Index-level — prior CPs remain audit. Response echoes `{superseded_turn_number, superseded_turn_id}`. Deprecated alias `supersedes_turn_id` (row id) one release cycle |
| `mark_read` | `true` only for **self→self** continuity tips. ¬ mark CPs addressed to another seat/operator |

## Resume (lean default)

1. Detect root: `role:root` ∨ legacy CHECKPOINT read ∨ enrollment.
2. Tip body: `fetch(thread, compact=true, last=K)` → subject index → `get(turn_number=<latest subject starting with CHECKPOINT>)`. **¬** `last=1` as tip (latest turn may be closeout).
3. Other unread: compact subjects only; ¬ auto-widen on `has_earlier_turns`.
4. Child lanes (`lane_bind` → append-only `thread_lane_associations`; CHECKPOINT's **Child lanes** derived zone = depth-1 substantiated, per `agent-bus-discipline` § Lane parentage): pointer IDs only — ¬ fetch child history on parent resume. Leftover conductor workers that landed as grandchildren of a coord stub (9676/9677 class) are **not** Child lanes — cite `agent-bus:{worker}` on the root CHECKPOINT or `lane_bind` the worker to the root. `conductor_coord_split_refused` retires the class going forward.
5. Then: tip → Cortex hub (`continuity-thread-shaping`) → scoreboard if named → roadmap if named → Cortex cards. Mid-tier: ≤3 further sidecars before drafting. **¬** load `## Windows` on resume. Catch-up *body* shape + hub wrap: Use the `continuity-thread-shaping` skill.

| Widen when | Fetch |
|---|---|
| Operator asks / `--all` / `--context N` | As asked |
| Unread `from≠self` ∧ subject ¬CHECKPOINT | That body (+ optional context) |
| Tip lacks Next-pickup / Anchor / stale vs known child activity | Prior 2–3 CPs or named sidecar |
| Review / audit of whole-history | Charter-surface `## Windows` (schema §3.5) — not linear thread read |
| Execute on child lane | Open **that** thread separately |

**Operator-facing:** Mission + In/Out first (`operator-posture` Rule 3 · `decision:continuity-resume-mission-open`). Then `orchestrator_continuity` → Been→Are→Going → `In one line:` → settled·live·next · next. `tick_charter` → Mission + Scope, then wave · in-flight · next pickup.

## MONITOR / mission resume — fast successor (BINDING — 2026-08-02)

Waiting for `live_cse=0` × idle confirmations after a CHECKPOINT that already names a
**rewritten commission_seq** is a stall, not discipline. On tip resume / CP authoring when:

`commission_seq > last_summoned_seq ∧ running_count=0 ∧ arc_complete=false`

⇒ **fire the successor now** (`team_dispatch(model=cdp/…, purpose=operator-proxy,
sidecar_ref=commission)` or re-arm watchdog with the fast path). Do **not** wait for a
lingering CSE to die. Prefer warm follow-up into a live CSE when the departing seat is
still correspondent; otherwise a new window on the **same** private lane.

Fold gates that wait on passive traffic (e.g. N≥20) ⇒ prefer the named **backfill /
instrument** shortcut in the commission before parking another episode on accrual.

Watchdog SOT: `scripts/opus-summons-watchdog.py` (`successor_fast` when seq advanced +
`running_count=0`).

**Vocabulary:** `resume <n>` → this section; `checkpoint <n>` → post per profile + tip hygiene.

**Mid-tier / B6:** scoreboard gated lane + tip first. Charter-health dispatch only when densified Next/WIP ∉ OPEN G-row or operator asks how-are-we-doing (≤15 lines).

## Autonomous tick runtime (compressed)

Enrolled roots only. Design SOT: `cortex://notes/system/specs/autonomous-path-sim-charter.md` · `charter-runner-tick`.

| Contract | Consequence |
|---|---|
| `no_gated_pickup` | `root_skipped` → state-close → unenroll (≤1/tick) |
| `checkpoint_missing` | No heal — author reseed |
| `executor_mismatch` | Refuse ADMIT_WORKER until tip reauthored |
| `stale_r_corpus_sha` | R-admit fail-closed — refresh Sidecars `spec_sha256:` |
| Arc-close | Sidecar ≠ done — `workflow_state=done` on parent+children |

## RESUME footer (canonical — paste byte-identical)

Prefix must stay `— RESUME (any seat, no command):` (parser T8).

```
— RESUME (any seat, no command): load checkpoint-discipline (tip resume + author workflow; done/close claims also load agent-bus-discipline § R12 completeness gate; cursor coding arc may add orchestrator-workflow) → read <continuity-source URI/path> [+ scoreboard gated lane if named] → this is the latest CHECKPOINT (wave/in-flight/next above). Do not read the thread linearly. empty Next-pickup ≠ arc complete.
```

`<continuity-source URI/path>` = the durable roadmap or reconstitution index named in the CHECKPOINT body (e.g. `cortex://notes/system/roadmaps/<slug>.md` or a workspaces share path). Parameterize per arc — do not hardcode a single global path.

## Related

- `ulg-for-llms` — standing why; first-class continuity stance trait (not in the RESUME footer string)
- `agent-bus-discipline` — send/reply/lifecycle; R12 done/close; thread classification
- `orchestrator-workflow` — coding-arc R12
- `operator-posture` — Rule 3 resume ceremony
- `continuity-thread-shaping` — catch-up body shape, Cortex hub wrap, resume slot fill sources (`role:root` house only; ¬ work/conductor-worker/MONITOR)
- path-sim `tick-enrollment-annex` — enroll template
- Schema URI above · §3.5 Windows log
- `cortex-orientation` § Myelinate — checkpoint-time associations + strength rating
