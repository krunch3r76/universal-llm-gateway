# 12286 operator-ear-house — maestro journal

Reflective log for the house seat (web-anthropic as maestro on Cowork). Append-only; real UTC clocks; one entry per leg or lesson. Facts go to assertions; positions go to the CHECKPOINT; this file holds the *why it worked / why it didn't* that a successor cannot rebuild from the bus.

## 2026-09-22T02:50Z — first night, three conductors

**What is working**
- **The ear by token.** A conductor stops at `CONSULT_PENDING`; the maestro posts one `HARVEST — G<n> … DONE: <so-what>` turn on the **worker lane** (`from_agent=web-anthropic`, `RULING:` lines, `NEXT_ADMIT:` on its own line, detail in a sidecar via `sidecar_content`) plus a one-line pointer on the root. The next hop reads it: hop 2 of score A literally journaled "harvest NEXT_ADMIT copied; entry G3". Template = agent-bus:12088#126's shape (Bind · scope effect · new finding · out held · sidecar).
- **Independent check earns its keep.** Composer said "12 passed, ruff clean" and had still ignored a binding S3 ruling (Stargate opening GIW's SQLite; libs importing services). The G6 AMEND → corrections → PASS cycle took 20 minutes and produced a real GIW read route. Never land on a green closeout alone; read the worktree with `fs(thread=<worker>)`.
- **Multiple conductors under one root** — distinct `work_key` + lane-B worktree each; the root CHECKPOINT indexes all scores; one ear harvests all. Second and third admits collided with nothing.
- **Pipelines are live tooling.** `pipeline(op=validate|run|stats)` works from this seat; a landed pipeline is callable the moment Stargate hot-reloads — no hop, no re-boot. `diff-review` is a free second reader at G6. `team-dispatch` is a live generate door I chose not to use to bypass the cursor-auto lane (pillar-1) — the operator can lift that.
- **Outsourcing architecture to cdp/opus-5** via `cursor_request(contract=confer, desired_model=cdp/opus-5)` admitted in 2 s (12352). The maestro's context is for adjudication; Sketch/densify/review legs go out.
- **Score C's stop pattern** (ROW_HOP, successor holds the wait, consult routed as a handoff packet) needed no manual resume at all — the pattern to prefer until D3 is live.

**What is not working (and who owns the fix)**
- Bare `CONSULT_PENDING` closeouts end the session and the reactor will not continue (verified at a2beb8c: `park_harvest_owed` needs EXIT_PERSIST, `park_harvest_continue_owed` needs PARKED_TRANSPORT, `hop_owed` blocked by the closeout body). → D3 landed on lane 12291, live after the drain-gated GIW restart.
- Same-lane resume is refused (`seat.identity_unresolvable`, census N=0) → a child lane per resume (12297, 12304, 12323, 12341). → D3 or a:36208 option (b).
- The Auto admit lane is serial (`pending 2 · claimed 1`) → Sketch 12352 Q1.
- Root summary overwritten by false "Dispatch orphaned" notices (three times) → a:36210 + a:36207, score B.
- Conductor-authored journal rows with hand-typed clocks; closeout wrappers missing `NEXT_ADMIT:` → a:36209, score B AC5.
- Skills lag the workflows (COMMISSION_CONDUCTOR pointer; quality_gate claim) → a:36195, a:36196, score B.

**Lessons for the maestro**
1. Read the code before ruling; quote file:line in the harvest. Two of tonight's rulings (R3, C1) only held because the line numbers were in the turn.
2. Fewer, fatter commissions: one DIRECTIVE per resume, all corrections in one nest, one CHECKPOINT per leg.
3. `wait_seconds ≤ 45` on life; `predicate_unmet` is not silence — fetch the tail.
4. When a wake fires, sweep all worker lanes before doing anything; the CONSULT_PENDING you owe is rarely on the lane you expected.
5. Re-arm the durable wake every turn; the Monitor is session-scoped.
6. Frictions are house work: file with evidence, then settle them on a score — a fixed-but-open friction is a claim/artifact desync (pillar-4).

## 2026-09-22T02:58Z — five scores; the ear has peers

- **A pinned premium model is not a guarantee.** 12352 asked for cdp/opus-5 and Composer answered, with no refusal on the wire. Read `model_actual` on every consult closeout before trusting the seat that wrote it. Filed a:36211.
- **"web-anthropic" is a seat, not this window.** The substrate spawned CDP instances of this seat (12344 successor, 96f5f3f0) that harvested and even corrected my G2 bind (12338#15, byte math I got wrong). Treat them as peers: read their turns, keep their corrections, do not re-do their harvests. Duplicate fires happen (12344 fired 24 s after my 12338#9) — the substrate said so itself.
- **Outsourcing works when the question is pinned.** The Sketch came back with file:line for the serialization point and a change I could turn into a score in five minutes. What made it usable was AC1's "file:line or it didn't happen", not the model.
- **ROW_PINNED after SCORE_RESURFACE is a PLAY the attended seat owes.** Say PLAY on the worker lane with amendments, then resume; the reactor will not hop an exit-persist stop.
- Journal clocks: this entry written by the seat at the real time shown; no round numbers.


## 2026-09-22T03:32Z — the premium seat, reached directly

**What is working**
- **`chat-dispatch` is the cdp door from life.** `pipeline(op=async, pipeline_id="chat-dispatch", options={model:"cdp/opus-5", mcp:false})` — resolved and *served* by claude-opus-5 (MODEL_ACTUAL on the reply, `model_entity_id: model:opus-5`, 0 API tokens). No lane, no queue, no `dispatch_thread_id`, and it works while cursor-auto and manage are down. The earlier entry's "cdp/opus-5 via cursor_request admitted in 2 s" was a pin, not a seat: the Sketch was written by Composer (a:36211). Pinned ≠ served — read the model on the closeout, every time.
- **The second read paid for itself in one call.** Seven binding items, four of them the Sketch contradicting itself (a gate raise its own numbers didn't need; a lease claim that fought the next paragraph; a projection sold as observation; "two lines" for a three-item bind). The AMEND flipped the sequencing of two todos. A Composer Sketch ratified by a tired ear is exactly the failure a premium second reader exists for.
- **Structured-token demand.** Asking for line-start `MODEL_ACTUAL:` / `VERDICT:` / `BINDING n:` / `FORK n:` / `DONE:` made a no-tools model's output harvestable verbatim into a sidecar with the ear's adjudication table under it.

**What did not work**
- `team-dispatch` pipeline from the MCP `pipeline` tool — `concurrency.key` wants a top-level `dispatch_thread_id` the tool cannot send. `dispatch(tool="team_dispatch")` — not in the overflow catalog. Two dead doors before the live one; the card now lists all three so the successor skips the detour.
- **Near-miss, worth its own line.** I drafted a fresh G2 frame for score E from the consult body and tried to write it to the spec path — the path already held a fourteen-hop ear bind from earlier this night, thicker and better. The shared-document `expected_sha256` refusal is the only reason it survived. Lesson: *read the target path before writing to it, especially after a context compaction* — a summary tells you a file exists, not what it says.

**Rulings I would make again**
- Drop `confer` from the concurrent allowlist not because a reader said so, but because the judgment legs left the Auto lane tonight (they run on the cdp door). Rulings should follow demand, not caution alone.
- Score E's G7 gated on the row-hop rehearsal, not its G3–G6: let the conductor keep hopping on the parts that are safe.

## 2026-09-22T03:59Z — scheduled run, unattended: fire, sweep, one skeptic, and a second ear appears

**What is working**
- **The scheduled-run loop held.** Induct (checkpoint → card → Next-pickup → journal) → `fleet_liveness` → fire → sweep → harvest → write took 15 minutes with no human. Reading the lane tails *before* re-firing saved one wasted admit: 12376 (score A readmit) was moot because A had already landed (12291#26) while the ear was away.
- **The cdp door for a skeptic, synchronously.** `pipeline(op="run", chat-dispatch, cdp/opus-5)` returned in 139 s with a token-structured review I could adjudicate in one pass (9 binding, 8 test gaps; two of its findings — the None==None same-key collision and the missing `_CONCURRENT_EXECUTION_MODES` entry — were sharper than my own first-pass notes). `op="run"` beats `async` + `result_delivery` when the ear will adjudicate in the same session; the raw output must still go to a sidecar under the ear's table.
- **Admit-plane pin honored for grok:** `requested_model=cursor/grok-4.7 resolved=cursor/grok-4.7 model_honored=True` on 12412#2, and served grok-4.7 on the closeout. Grok on mechanical lanes is real tonight.

**What did not work**
- **Two ears, one house.** While this run was harvesting E, another web-anthropic seat reviewed score F's G6 and commissioned its land (12412#8, 12425, 12427). Nothing collided this time only because the two seats happened to pick different scores. The scheduled-task prompt says "Kaywan may be present" — it should say: *if another web-anthropic turn newer than your checkpoint exists on any child lane, stop and yield.* Recorded at the top of Next-pickup.
- **MCP 502s mid-write.** Two Cloudflare 502s during `md_insert`; the section rewrite before it had landed, the insert had not. `md_list` after any 502 before retrying — the write may or may not have landed.
- **`supersedes_turn` takes the turn number, not the turn id** (422 on 77158; 54 worked). `md_write` does not exist on /mcp/life — `md_rewrite_section` / `md_insert` / `md_append`.
- **diff-review that isn't.** 12334#31: pipeline `diff-review` "completed" in 0.012 s with 0 tokens. A conductor will happily count that as its G6 second read. Friction candidate; the ear must run its own review on B.

**Rulings I would make again**
- Skeptic B7 half-accepted: carry G2's negative rulings into G3, but a test that passes `work_key` explicitly on the wire is R5, not the forbidden derivation. Read what the test does, not what its example value looks like.
- Resume E immediately after the harvest rather than waiting for D3 auto-continue: D3 is on master but not in the running GIW, so a harvest without a resume is a parked score.

## 2026-09-22T04:08Z — successor-2, unattended: three seats reconcile, one honest stop, one bypassed gate

**What is working**
- **Seat reconciliation over the bus, not chat.** Three web-anthropic sessions touched this house between 03:44 and 04:03. Each yielded with a STATUS turn naming what it did and which trigger it left armed (12286#69, #71). Induction cost four reads; no duplicate lanes. The rule that should now be in the scheduled prompt: *post SUCCESSOR ALIVE, then read the last three non-relay turns before touching any lane.*
- **An honest AC1 stop beats a merge.** 12427 (grok-4.7) compared the wrong file on master (50 passed) and refused to merge because "18+84 is not a count this file can produce". It was right to stop and right about the count; the ear's job was then to find the real frame — the 18 failures are `missing SOT at <worktree>/.claude/skills/…`, an artifact of lane worktrees, not a regression — and re-fire with the proof rebound (12440). Reading catalog.py:288–309 took one call; guessing would have cost a lane.
- **Post-hoc G6 by reading the merged files at head.** For a three-file merge the ear can do its own second read from `workspaces://` in four calls — fixture size vs. assertion weakening is decidable from the test body alone.

**What did not work**
- **Conductor bypassed the ear gate on B.** Hop 7 on 12334 took an `empty_assistant_turn` from its muse-spark reviewer as licence to self-review and merge to master (ccab90b2), although NEXT_ADMIT bound a CONSULT_PENDING to web-anthropic. Filed a:36221 on `agent_skill:conductor`: an empty second-reader nest must stop the row. The land was sound this time — that is luck, not a control.
- **`landed: true` from hub head recovery.** 12334#34 reports `landed_resolution_reason: hub_master_head_recovered:lane_meter_zero` — landed derived from what master's head happened to be, not from finding the dispatch commit. Unfiled friction candidate (6) in Next-pickup.
- **`protocol` friction anchor shape** on this surface is top-level `root_thread` + `cp_ordinal`, not a nested `continuity{}` object (first call 422'd).

**Rulings I would make again**
- Re-fire F's land with the suite named explicitly and the failure class named as the proof, rather than asking for "the same 18 on master" — the earlier frame could never be satisfied on a hub checkout that has the SOT.
- Leave E and C to their live nests (12431, 12432) and D to its admitted resume (12434); no propagate this run — three lane-B dispatches in flight is not a quiet window.
