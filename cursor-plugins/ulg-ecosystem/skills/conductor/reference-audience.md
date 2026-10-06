## Audience (binding)

| Seat | Duty |
|---|---|
| **Continuity lead** (IDE / `/conductor`) | Read this skill to author/admit; **require** it on the conductor dispatch (below). **Harvest reader** of any after-ship overlay the conductor fires — quote the sidecar or name why unread |
| **Liaison** (IDE continuity lead when not the conductor) | **Default register** = liaison register (BINDING — operator 2026-08-27). Speak so the human does not need the bus, scoreboard, `SCORE_RESURFACE`, or closeout open. Gloss IDs only when they change what he does next. Skill `audience-register` plain half is the closest written match; the duty name is **liaison register**, not that skill. Off when this seat *is* the conductor. **Decide** = § Liaison-decide |
| **Conductor** (cursor-sdk) | Load this skill on pickup — nest, tier, scoreboard, ¬ hand-code any G-row whose remainder is files+tests after a pick |

### Liaison-decide (binding)

Operator direction ≠ hop recipe. Anticipate harvested score / `NEXT_ADMIT: none` /
spawn-stale materialize / in-flight orphan; choose the next useful move. Inform;
do not wait for a rewritten recipe.

**Attended in-flight (binding — operator 2026-09-01):**
After admit, when the wait is event-gated, arm **in-session wake** (three legs,
atomic) and exit — unless already armed or named decline. SoT:
`runbook:bus-consult-watcher` + `runbook:bus-consult-watcher-terminal-harness`
(IDE terminal ghosts — always leg 2; no slot limit). Watcher: Use `runbook:bus-consult-watcher` (cursor-sdk → `--dispatch-id`; CDP/bus-consult → `--execution-id`; tail; relay) — never restate here. ¬ start-only; ¬ tail-skipped; ¬ hold-turn on `wait`; ¬ hang-tail after complete. Cheap = legs 1–2 /
`SCORE_RESURFACE` / `poll_hint` + lean heartbeat (`loop` skill). Costly =
short-cadence `/loop` or holding this turn. Complements a:31104. ¬ a:31024 liaison.

**Decide-before-admit:** Use the `reasoning-posture` skill — pin Question / OOS /
detent. Question is whether the leftover score is still the remit. Resident
alwaysApply does **not** substitute for this cue.

**Named hop vs harvested state:** `runbook:extraordinary-aperture`
(`cortex://notes/runbooks/extraordinary-aperture.md`) — in-seat widen that can
kill the conventional “replay that hop” frame. ¬ `/path-sim` cascade. ¬ remint
a harvested conductor. ¬ leftover-execute.

**Rematerialize trap:** `` + `source_ref=todo:X` forbids
`packet_path` and rematerializes the **old** conductor packet. Harvested score /
`NEXT_ADMIT: none` ⇒ park remints. New remit ⇒ **new sibling todo** +
`job=implement` (Composer). Never replay the harvested conductor todo.
W5 (`reuse_thread` + same `source_ref`) is unfinished-conductor only.

**After land (binding):** path-explicit commit of session paths, then prompt
go-live (recycle) for each serving process that loaded those paths (hub GIW vs
satellite — name the process and what “live” means). Do not recycle-and-call-
finished while session paths are uncommitted. OR if this liaison chooses not to
recycle: announce skip in the same turn — process, why skipped, what stays
landed-not-live. When the land was a named
`todo:`: stamp that entity (`todo-close` or LANDED assertion) in the same
turn — recycle without stamp leaves the next resume walking an open card.
Code-live ≠ trading-live (recycle claudeburst ≠ activate
`LIGHTER_LIVE_TRADING`). Continuity `status` LAND-LIVE names **skipped
recycles** and **unstamped matter entities**, not only “not live.”

### Follow up (operator phrases)

Joint convention — the human-visible box lives on the continuity card
(`## Follow up`). Any one phrase is enough. Distinct from page-on-stall and
from CHECKPOINT (reconstitution index, not a follow-up ask).

| Phrase | Liaison |
|---|---|
| `follow up` | Harvest what's terminal. One Been / Are / Going paragraph in the summoning IDE chat. ¬ new hop unless the last bind already said to admit. |
| `follow up on the pager` · `page me when done` · `I'm leaving — follow up` | Same harvest + Use the `pager-notify` skill (awareness; ¬ `COME TO IDE` unless they said come to IDE). Aligns to that skill's “ping me when X”. |
| `status` | **HOPS / BLOCKERS / PENDING / SCORES / BREAKS / LAND-LIVE**. PENDING = leftovers this liaison already named or still dirty on this arc (Mission/resume WIP, in-seat files, landed-not-live, branch-debt). Land → path-explicit commit → recycle is the default sequence to report. After any land: commit session paths, then prompt recycle of each serving process, or announce skip (process · why · landed-not-live). LAND-LIVE names skipped recycles, not only “not live.” Recon-spawn fills the continuity `## Scores / breaks / land-live` box — ¬ dump tables into this skill. ¬ decide-and-admit. |

Named subject (`follow up when hedges land`) is the trigger. Hops still in
flight → say so; do not invent done. `follow up` ≠ remint conductor ≠
leftover-execute. Pin Question (`reasoning-posture`) then report.

**Identity (operator-facing):** never a bare `Qn` / `Gn` / hop id **or bare
bus thread / child-lane number**. One-line identity: what question or score
or hop, whose thread as `{id} {slug}` (`thread_get`). Same for G7 (never a
commissioned live-arm row unless the scoreboard says so) and hop numbers.

**Continuity-lead required-skill gate (BINDING):** before
`team_dispatch(op=generate, seat=cursor-sdk, …)` for a conductor packet, the
lead MUST put `conductor` on the dispatch as a required skill:

1. Packet `<invariants>` MUST include a line:
   `Use the conductor skill — nest specialists; ¬ hand-code mechanical G-rows; cost tier from this skill.`
2. Treat slug `conductor` as `required_skills` for the admit (same catalog duty as
   Gate-2 — catalog-registered in `config/skills.yaml`).

`team_dispatch(skills=["conductor"])` now mounts on cursor-sdk — the body is
staged into the dispatch HOME and the Use-line is emitted. Keep the packet
`<invariants>` line anyway: it is the one channel that survives a packet replayed
by hand, pasted into another seat, or admitted without `skills=`, and step 1 is
what the Gate-2 audit reads. Belt and suspenders, not redundancy to prune.
Reasoning-posture auto-prepend for `conductor` does **not** substitute for
the `conductor` skill itself.

