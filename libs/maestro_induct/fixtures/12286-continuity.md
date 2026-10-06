# 12286 operator-ear-house — continuity

## Stance
Use the `ulg-for-llms` skill.

## Why this house
Kaywan (2026-09-21): "we need you to be an operator that works with a liaison on a cursor_sdk dispatch that continually hops until its task is complete… Expand cursor_auto as much as you please. Create pipelines as well (preferred over new MCP tools because you have a tool count limit)." The Cursor IDE is not reaching its primary pool models tonight; cursor-sdk is fine. This house is where the web seat learns to drive the hopping conductor by token, and where the tooling that makes that cheap gets built. Skills on this seat may lag the liaison workflows — when a skill and the live code/spec disagree, the live surface wins and the drift gets filed.

## Objective
Mission: make web-anthropic a working ear on a Plane A conductor — harvest each `CONSULT_PENDING`, bind forks, write `NEXT_ADMIT:`, and see the conductor continue — and ship `operator-hop-harvest` so one pipeline call shows the operator a hop.
In: `todo:operator-ear-by-token` (G1–G7 in the spec). Out: new MCP tools · GIW hop plane · Plane B · lexicon migration · charter-runner.

## Rows

| Row | Status | Evidence |
|---|---|---|
| Friction a:36242 harvest returns dormant with empty turns | OPEN | `cse_session` harvest of `https://claude.ai/cowork/cse_01AoM9mfdhGSHrbPoikuwobw` returned `outcome=dormant`, `turns=[]`, `multiple_current_hosts`. Owner `service:cdp-ask`. Resolution note a:36243: open a Chrome window, scrape, and close it. |

## Rules
See card `## Rules`.

# Current

## Settled
- Root 12286 minted 2026-09-22T01:34Z; spec written (sha256 5a8cff64…); todo minted with seed floor.
- Bind (operator seat, hypothesize-simulate): ear-by-token (Rival A) with liaison talk on the root (Rival C transport). Kills: stream-held ear from this seat. Falsifier: a CONSULT_PENDING that does not resume after a `from_agent=web-anthropic` harvest turn.
- Drift noted: `directive-authoring-standard` D4 and `cdp-operator-proxy` inv 37 say the conductor recipe lives in the `cursor_request` docstring under `COMMISSION_CONDUCTOR`; the live docstring (services/mcp-server/tools/cursor_request.py) carries only "Conductor commission: `investigate` + `lane=B` — packet/nest table: `agent_skill:conductor`". Recipe of record is the conductor skill § First-utterance spawn. To file as skill friction after the first hop.

## Live
- Commission fired 01:37:33Z: lane `agent-bus:12289 12286-conductor-operator-ear-by-token` (child of 12286, sub_mission), job `e6dd72a4-5ac0-4e54-a5d8-70bc829dd7c5`, request `6a720a82…`; admitted 01:37:36Z (turn 2): composer-2.5 honored, contract=investigate, effort xhigh, gate_plan=dispatch_now. Waiting on its CLOSEOUT (status:done) which must quote the conductor worker thread + the CONSULT_PENDING wait locus (AC-C4).
- Durable wake armed: trig_01SP2fQfzDw6BQp71NQjgfQV fires 01:54Z.

## Pattern studied — 12088 crcl-leftover-book-sync (operator note 2026-09-22)
The house root receives every `cursor-sdk generate admitted` from `conductor-hop`; the ear (web-anthropic on a `cdp generate` gate) posts its bind **on the root, to conductor-hop** (12088 turn 126: subject `todo:<slug> — G2 Frame DONE: <so-what>`; body = Bind · scope effect on G3/G5 `files_expected` · new finding N4 · Out held · Sidecar). The IDE liaison harvests judgment turns (`HARVEST judgment — 12088` listing turn ids), posts `SCORE_RESURFACE G3→G5`, LANE CLOSEOUT rows per child, and CHECKPOINT `to: web`. That ear turn is the template for this house's harvest turns; difference here: the wait is cleared by token (`from_agent: web-anthropic`), not by a CDP stream ending.

## Leg 1 — settled 01:45Z
- Hop 1 (dispatch 219be9039b88-bfee1f79, worker `agent-bus:12291 cursor-sdk-generate-a721e2cd`): G1 recon DONE in 144s / 82 tool calls / 5.9M tokens (composer-2.5); stop CONSULT_PENDING; G1-R sidecar `cortex://notes/system/recon/operator-ear-by-token/consult-pending-loop.md`.
- Ear harvest = 12291#4 (sidecar `cortex://notes/system/threads/12291-12291-g2-ear-bind.md` sha256 d1e088e6…) + pointer 12286#3. Rulings R1–R6 (both threads; recency NEXT_ADMIT; **no auto-continue on bare CONSULT_PENDING at a2beb8c — verified**; after_turn baselines; G4/G6 = this seat; pipeline field contract).
- Resume: same-lane `cursor_request(thread=12289)` refused `seat.identity_unresolvable` (census N=0) → child-lane fallback `agent-bus:12297 12286-resume-hop2-operator-ear-by-token` → hop 2 admitted on 12291#5 (execution fb1bdb95-ad9b-493e-9beb-26893e100d9d, 01:45:31Z).
- Frictions filed: a:36195 (directive-authoring-standard COMMISSION_CONDUCTOR pointer drift), a:36196 (lead-seat-boot quality_gate not on life mount). Assertion a:… on the todo (G2 DONE).

## Leg 2 — settled 01:51Z
- Hop 2 (dispatch ab509b4759d0-b75179c3, 309s, 41 tools, 2.96M tokens): folded G1/G2 witnesses, **copied the harvested NEXT_ADMIT into the tip (R2 applied)**, nested G3 densify (eac33471425a-9e63709e on worker `agent-bus:12299`, 120s, 56 tools) → S4b spec `cortex://notes/system/specs/operator-ear-by-token-g3-densify.md` (sha 137cfd0a…). CHECKPOINT 12291#6 (01:50:35) then CLOSEOUT 12291#8 (01:50:50) `degraded_reason: conductor_consult_handoff_missing` — the session did not wait for the ear; wrapper lacked NEXT_ADMIT.
- Ear G4 skeptic = 12291#7 (sidecar `cortex://notes/system/threads/12291-12291-g4-skeptic.md` sha 37ee7c58…): PASS + binding S1 (wait predicate may be False on real closeout — test it; widen D3 step 2 to degraded_reason), S3 (ledger over GIW HTTP, no foreign SQLite), S4 (4KB trim), S5 (journal written_at fabricated 01:50/02:50 — real clocks 01:45:31Z/01:48:40Z); summarize fork closed (omit). `density_triage=implement_ready` set on the todo by this seat (implement_spec attrs).
- Resume hop 3 via child lane `agent-bus:12304 12286-resume-hop3-operator-ear-by-token` (job 443d4c7d…, 01:51:25Z).
- Pipeline dir (from G3 recon): Stargate hot-reloads `pipelines/` (`config/stargate_config.golem-master.yaml` search_paths[0]); `config/pipelines/` is documentation only.

## Leg 3 — settled 02:16Z
- Hop pickup f16d9cf03f5e-90c3eecc (admitted 12291#9 01:52:19Z via lane 12304): folded G4, nested two implements — 12307 (172cdd42…) **orphaned** 02:02:36Z, 12308 (ec50cfa69e01) closed 01:57:08Z "G5 implement closeout — operator-hop-harvest + D3": lane-B commits e72b1241d → HEAD 5840f54b5, pytest 12 passed, ruff clean, GIW restart intent 8a0e0ebf-126b-4594-b029-7617898c7621 drain_pending. Conductor then posted CONSULT_PENDING 12291#12 (to web-anthropic, with execution_id + NEXT_ADMIT — wrapper fixed), CHECKPOINT #13, CLOSEOUT #14 (02:07:03Z).
- Ear G6 review = 12291#15 (sidecar `cortex://notes/system/threads/12291-12291-g6-review-r1.md` sha 5abe23fc…): **AMEND**. C1 S3 violated (`libs/operator_hop_harvest/ledger.py` opens GIW SQLite via `CursorDispatchLedger._connect()` from Stargate; libs→services imports) → GIW read route + HTTP. C2 D3 fixture is synthetic, not the ledger-stored closeout_body → real fixture + widened predicate if `is_consult_pending_wait` is False on it. C3 journal rows 01:50:00 / 02:50:00 / 01:55:00 hand-written → correction row. C4 orphan 12307 unaccounted.
- Hop 4 resume via `agent-bus:12323 12286-resume-hop4-operator-ear-by-token` (job 51293ff0…, 02:16:17Z).
- Substrate defect observed twice: root 12286 `summary` gets overwritten to "Dispatch orphaned — probe_status_null" when a child lane posts an orphan notice — `resolve_so_what_summary` adopting a child's notice; corrected by hand both times; file as friction at close.

## Leg 4 — 02:27Z — second conductor (settle)
- Operator rule: settle frictions as part of complete house work. Frictions filed: a:36195 (directive-authoring-standard pointer), a:36196 (lead-seat-boot quality_gate), a:36210 (agent-bus root summary overwritten by orphan notice — twice), a:36207 (stargate false "Dispatch orphaned" on reuse_thread — 12286#6 and #10), a:36209 (conductor wrapper/journal-clock drift), a:36208 (identity_unresolvable → resolved-by D3 or option b).
- `todo:operator-ear-house-settle` minted (spec `cortex://notes/system/specs/operator-ear-house-settle.md` sha 606246dc…; AC1–AC7; every row ends in friction_close or explicit hold). Commissioned via `agent-bus:12332 12286-conductor-operator-ear-house-settle` (job 673ddbff…, 02:27:19Z). Two conductors now run under this root with distinct work_keys and worktrees.

## Leg 5 — 02:35Z — three conductors playing
- Operator: "You can launch another conductor. Play the music for the world to hear all over." → third score commissioned: `todo:operator-observe-pipeline` via `agent-bus:12337 12286-conductor-operator-observe-pipeline`, worker `agent-bus:12338` (execution 5b1d99bb…, admitted 02:33:22Z, 12286#16).
- Score B (settle) hop 1 done in 211s (dispatch b384ec0b038d-aa6ba8fe): G1 recon `cortex://notes/system/specs/operator-ear-house-settle-g1-recon.md`. Ear G2 harvest 12334#5: AC1=B (retarget SOT to conductor § First-utterance spawn; drop the docstring token assertion), AC2=B (fix `_orientation_blocks.py:275–278` + lead-seat-boot annex; verify via cursor_request contract=verify), AC3–AC5 shapes as recon, AC6 hold. Resume hop 2 via `agent-bus:12340 12286-resume-settle-hop2`.
- Score A (12291): hop 4d6fd5ba2399-8ee297c1 landed corrections 209f4cfe1 (C1 GIW route `/api/v1/cursor/dispatch/latest-terminal-conductor` + httpx client, no services imports; C2 real ledger fixture + `test_real_closeout_body_is_consult_pending_wait`; C3 correction row; C4 12307 accounted; 31 passed). Ear G6 re-review 12291#22 = **PASS**. Resume hop 5 (G7 land) via `agent-bus:12341 12286-resume-hop5-operator-ear-by-token`. GIW restart intent now a8f55cf6-81c4-4caa-bccf-42b19a35233e (drain_pending).
- Pipelines as live tooling (operator emphasis): `pipeline(op=validate|run)` works from this seat; `operator-hop-harvest` becomes callable here the moment G7 lands (hot-reload); `diff-review`/`code-review` pipelines live — use as second reader at G6; `team-dispatch` pipeline is a live generate door — NOT used to bypass the cursor-auto lane unless the operator says so.

## Leg 6 — 02:46Z — Sketch outsourced to cdp/opus-5
- Operator: "Auto lane doesn't have to be serial. Outsource to architect solutions (save context here). Call cdp/opus-5 to recon and generate ideas on leveraging cursor_auto." → Sketch hop fired: `agent-bus:12352 12286-sketch-cursor-auto-leverage` (contract=confer, desired_model=cdp/opus-5, xhigh), admitted 02:46:28Z as producer `cdp-generate` execution e4f453d7-449c-4a44-8d85-c99804f6ecb9. Questions: Q1 serialization point + smallest concurrency change; Q2 which legs the ear outsources and the exact wire per leg; Q3 bind the consult-stop mechanism (ROW_HOP+successor-holds-wait vs D3 vs bus-native resume identity). Deliverable: shape-bind sidecar `cortex://notes/system/threads/12286-sketch-cursor-auto-leverage.md` + ≤3 seedable todo stubs. Ear ratifies at DISPOSITION.
- Score C G2 harvested (12338#9 + 12344#2); C's successor-holds-the-wait pattern needs no manual resume — specimen for Q3.

## Leg 7 — 02:58Z — Sketch ratified; scores D and E; C at G5; B at G5
- Sketch 12352 closed 02:48Z (Composer ran it; cdp/opus-5 pin silently substituted → a:36211). Ratified with one correction (12352#7): Q1 serialization = exclusive `claim_next` (`auto_worker_loop.py:110-114`), only `lease_free_propagate` concurrent (`execution_mode.py:18-23`), concurrent loop exists (`:166-170`) → smallest change = a concurrent execution class for conductor commissions gated on distinct work_key + lane=B; Q2 outsource table → card § Outsource; Q3 bind = D3 (ROW_HOP for gear motion; option (b) fallback). Sidecar sha 63522a37….
- Score D `todo:maestro-induct-pipeline` (operator prefers pipeline induction): commissioned 12357 (job 34a110b1…, 02:53:09Z); trigger `MAESTRO 12286`. Journal `12286-journal.md` + `runbook:12286-house` written; card carries § Runbooks/§ Journal/§ Outsource.
- Score E `todo:auto-conductor-parallel-admit` (Sketch Q1): commissioned 12362 (job fe982366…, 02:55:28Z).
- Score C: successor-CSE web-anthropic (birth 72a317e75f19 via 12344) posted a G2 addendum 12338#15 (byte budget 50/30 ≈13KB vs 4KB cap; per-slice deadlines) — a peer instance of this seat, correct. cdp/fable G4 leg FAILED (19579bfc, 12338#21); ear G4 skeptic 12338#23 PASS + K1–K3; implement_ready set. Entry gate G5.
- Score B: G4 PASSed by a CDP web-anthropic leg (96f5f3f0); SCORE_RESURFACE 12286#29 → ear PLAY 12334#21 + P1 (emit the wrapper, not a comment) P2 (friction_close per row with evidence) P3 (propagate rows for mcp/agent-bus/stargate/GIW); resume via `agent-bus:12364 12286-resume-settle-hop5`.
- Score A: hop 5 (787d633e, 02:38:59Z) still landing G7 — no closeout at 02:58Z.
- CDP door tonight: one success (96f5f3f0), one failure (19579bfc), one silent substitution (12352) — pattern worth C's observe pipeline.

## Leg 8 — 03:17Z — outage mid-play
- 03:08:03Z a false "Dispatch orphaned" relay (12286#42) **closed the root** (status closed, lifecycle failed) — reopened 03:09:53Z; a:36213 (escalation of a:36210/a:36207; settle B AC3/AC4 must cover the close path).
- A's G7 land blocked: resume 12341 got 409 — stale work_key holder 6e328862 on thread 12326 (DISCARDED). Clear+re-admit commissioned on 12376 (03:10:12Z) — never admitted.
- D G2 harvested (12359#8 + 12366#2, 7 forks + packet/trigger contract). C G4 harvested earlier (12338#23). E hopped past its G2 via a CDP peer bind (12365 at hop 3, G3 nest). B hop 5 nested G5 (12334#25, 03:03Z).
- **03:16:47Z Auto handler down**: resumes 12381 (D hop 3) and 12382 (C hop 5) → no-auto-handler, liveness 503, terminal_park. `fleet_liveness` from this seat: Manage API connection refused. Stargate + agent-bus up. Possibly the drain-gated GIW restart executing (would make D3 live). LIAISON commission to the IDE seat on 12286#44. Wake 03:26Z.
- Cost check for the operator: route_policy routes every Auto contract to cursor/composer-2.5; no Grok in this house; per hop ≈ $0.65 on Composer vs ≈ $1.7–2.0 Grok 4.7 medium vs ≈ $3.5–4 Grok fast. Cache-read volume (~2.9M/hop) is the real lever.
- Other-house load: `liaison-ticker` on 11876 fires a `contract:none` generate every ~2 min (12356…12378), many "Quiet with work in flight" — shares the SDK gate with this house.

## Leg 9 — 03:30Z — the premium check, by the direct door
- Kaywan: Other Models pool spent → review on `cursor/muse-spark-1.3`; Sketch must be on cdp/opus-5 or cdp/fable, "address now, maybe even directly".
- Door found and verified: `pipeline(op=async, pipeline_id="chat-dispatch", options={model:"cdp/opus-5", mcp:false})` — probe exec 95a1c5fb returned `PROBE-OK Claude Opus 5 (claude-opus-5)`, 0 API tokens. Doors that fail from life: team-dispatch pipeline (top-level `dispatch_thread_id` not exposed), `dispatch(tool="team_dispatch")` (not in overflow), `cursor_request(desired_model=cdp/*)` (substituted, a:36211). Recorded on card § Seats tonight.
- Sketch 12352 second read on cdp/opus-5 (exec 3f639e0b, MODEL_ACTUAL claude-opus-5): VERDICT AMEND, 7 binding, all accepted — gate raise dropped, todo 2 (ROW_HOP vocabulary) before todo 1, FORK 2 narrowed, R1 scoped to non-ear consults until dwell measured, Sketch simulation relabelled projection. Harvest 12352#12, sidecar `12352-opus-second-read.md` (19635aa6…); pointer 12286#45.
- Score E: G2 frame F1 (`auto-conductor-parallel-admit-g2-frame.md`, now 35ce9032…) amended — FORK 2 = `{investigate, recon, verify}` (confer dropped: judgment legs left the Auto lane), falsifiers 7–8, G7 entry gate = row-hop rehearsal. HARVEST on 12365#16. E is hopping by itself (hop 3 at 03:07, generate 12d6f6e8 in flight) — no ear resume needed.
- Near-miss: I almost overwrote F1 with a thinner draft; the shared-doc `expected_sha256` refusal is what saved it. Rule → journal: read the path before writing to a spec path, even one you think is empty.
- Substrate at 03:27Z: Manage API still `Errno 111`; IDE LIAISON commission (12286#44) unanswered; IDE-plane loop tapes (11667/10534/10479) still emitting DIGESTs, so GIW hop reactors are alive — only cursor-auto + manage are down. 12376/12381/12382 remain parked.

## Leg 10 — 03:47Z — GIW back; cost leg fired; A landed while the ear was away
- `fleet_liveness` 03:44:57Z: git_integration_worker running (PID 452647, started 03:40:16Z), all 11 services up, checkout master@209f4cfe1. Auto handler `auto-handler-live`; 89 queued jobs `failed_on_restart` (the parked 12376/12381/12382 among them).
- **Score A landed without the ear:** 12291#26 CHECKPOINT "G7 DONE — operator-ear-by-token landed", master@209f4cfe1 FF merge from cursor-sdk/lane-12291; SCORE_RESURFACE 12286#56. Residual: `operator-hop-harvest` not in Stargate registry until hot-reload; GIW propagate drain-gated.
- **Score F fired** (Kaywan's cost-leg-first): lane 12412 `12286-cost-guardrails`, job dfeb2913, admitted 03:46:04 — `requested_model=cursor/grok-4.7 resolved=cursor/grok-4.7 model_honored=True`, effort medium, contract=implement, dispatch auto-9166b9070e90. Admit-plane pin only; served model verified on CLOSEOUT.
- **Resumes re-fired as new child lanes:** 12414 (D hop 3, re-fire of 12381) job 634974e5; 12415 (C hop 5, re-fire of 12382) job ee941bc1. Both `queued` behind F — cursor-auto runs one claimed job at a time tonight. 12376 not re-fired (A is DONE; its closeout was degraded `empty_assistant_turn`).
- **Sweep:** 12334 (score B worker) hopping alone — conductor 23521c3d CLOSEOUT 03:43:54, diff-review d00fff10 "completed" 03:44:49 in **0.012 s with 0 tokens** (not a review; friction candidate for G6), successor admitted 03:45:19. 12365 (score E) generate 2c5e498f admitted 03:41. 12286#53 "Dispatch orphaned" exec 17d0c752 = a pre-restart worker killed by GIW boot reconcile — expected per a:36215(b).
- No CONSULT_PENDING addressed to web-anthropic since the 03:44Z checkpoint (fetch_unread digest: 12365#5, 12359#5, 12334#13, 12291#19 are all already harvested).
- Tool note: on /mcp/life cortex, section edits are `md_rewrite_section` / `md_insert` / `md_replace` — there is no `md_write`. The MCP endpoint threw Cloudflare 502s 03:49:45–03:49:50Z; a write that 502s may or may not have landed — md_list before retrying.

- **03:50–03:58Z addendum.** Score E resurfaced (12286#58) with G4 skeptic OPEN; conductor closeout 12365#20 partial/consult, no CONSULT_PENDING wrapper (standing defect). Ear ran the skeptic on the cdp door (`chat-dispatch`, exec 43947747, 139 s, MODEL_ACTUAL claude-opus-5, 0 API tokens): VERDICT AMEND, 9 binding, 8 test gaps — adjudicated and posted as HARVEST 12365#21 (sidecar ae4f89cf…). Resume fired → 12426. Resumes 12414 → 12359 successor e76da8e6; 12415 → 12338 successor e2e4890c (both composer-2.5). Score F closed partial (`checks_failed`, 18 pre-existing catalog failures, lane 711ad4ffe) — **a second web-anthropic seat reviewed it (12412#8 PASS) and commissioned the land (12425 → 12427)** while this scheduled run was working; this run did not touch F after firing it. Two ears on one house is itself a coordination risk — recorded at the top of Next-pickup.

## Next-pickup
- **Seat state 04:08Z:** original seat (1ca3ffe0) and successor-1 both yielded on 12286#69/#71; successor-2 (cse_01VoRM…, fired by hand 04:01:41Z) is the live maestro and re-arms its own +20 min successor. Before any action, `fetch_unread` and read the tails of 12286 / the lanes below — if a web-anthropic turn newer than the latest CHECKPOINT exists on any child lane, stop and yield. If Kaywan is present, his word overrides.
- **Score F (12412):** 12427 stopped honestly at AC1 (grok: master `test_cdp_model_endpoint.py` = 50 passed; the "18 failed, 84 passed" was a multi-file run). Ear rebind: the 18 are `CatalogValidationError … missing SOT at <worktree>/.claude/skills/…` (catalog.py:288–309) — `.claude/skills` exists only in the hub checkout, so the proof is (a) every lane failure is missing-SOT and (b) the suite is green on the hub. Re-fired as **12440 `12286-cost-guardrails-land-3`** (job 0d941898, grok-4.7 medium, 04:06:50Z). Next ear stop: 12440 CLOSEOUT → if landed, propagate cdp_ask/stargate/GIW only in a quiet window (no lane-B dispatch in flight) → `friction_close` a:36215(a) + a:36214.
- **Score B (12334):** conductor hop 7 merged lane-12371 → master **ccab90b2** (03:49Z) on its own harvest, skipping the ear's G6 CONSULT_PENDING. Ear ran G6 post-hoc at 12334#35: PASS (test fixture fix, not assertion weakening; cursor_request docstring synced). Friction filed **a:36221** (agent_skill:conductor, protocol). Residue: mcp restarted 03:49:47Z at ccab90b2 (live); **git_integration_worker NOT live** for pager_notify/tick.py — fold into the quiet-window GIW restart shared with F. G7 friction_close ×5 (a:36195 · a:36196 · a:36210 · a:36207 · a:36209) + a:36208 disposition still house work.
- **Score E (12365):** conductor cb6aa5748134 hop 1 closed 04:04:38Z with G5 implement nest live on **12431** (dispatch 656dfde959ab, execution b81c2f6a). Next ear stop: when 12431 terminal → resume conductor (new child lane, investigate, reuse_thread=12365, resume_of=cb6aa5748134-2a21ecde) → G6 on the cdp door (demand MODEL_ACTUAL). Per-hop context datum for score G: E hop cost 4.03M in / 3.92M cache-read (12365#20).
- **Score D (12359):** G3 nest 12422 terminal 03:56:57Z (densify spec sha 7cfa7b2f…); conductor 72b78c7170e4 closed partial. Resume fired **12434 `12286-resume-maestro-induct-hop4`** (job 808d6062, admitted 04:04:26Z). Next ear stop: successor CHECKPOINT on 12359 → G4 skeptic CONSULT_PENDING → cdp door.
- **Score C (12338):** conductor-hop auto-resurfaced hop 6 (039b4af6, 03:59:27Z); G5 implement nest admitted on **12432** (04:02:17Z). Next ear stop: G6 on 12338 — run the ear's own review (diff-review pipeline returned 0 tokens on B; not a review).
- **Score A DONE** (12291#26). Residual `operator-hop-harvest` registry hot-reload + GIW propagate → first quiet-window restart (shared with B and F).
- Then score G `conductor-hop-context-gate` (Sketch R5 steps 2+3); seed `todo:conductor-consult-row-hop-vocabulary` before E's G7.
- Friction candidates unfiled: (1) 59× `work_key_unparseable` on `row-bind:` (liaison-ticker-10479); (2) diff-review pipeline completed/0 tokens in 12 ms (12334#31); (3) conductor `PLAN_COMPLETE` over a partial nested plan leg (12365#20); (4) `CURSOR_LANE_B_READ_ONLY` blocks `sdk_mode=plan` on lane B (12286#58); (5) hop provenance reports lineage_unresolved / HTTP 500 for a session that is executing (12286#71, a:36213-adjacent); (6) `landed_resolution_reason: hub_master_head_recovered:lane_meter_zero` on 12334#34 — landed=true derived from hub head, not from the dispatch commit.
- 11876 budget block = never-resetting estimate; never book as spend. Judgment on the cdp door only; demand `MODEL_ACTUAL:`. Grok for mechanical; Composer for conductors. Conductor CONSULT_PENDING wrappers must carry `NEXT_ADMIT:` + `execution_id:`. Never restart GIW while any lane-B dispatch is in flight.

## Follow up
_None yet._
