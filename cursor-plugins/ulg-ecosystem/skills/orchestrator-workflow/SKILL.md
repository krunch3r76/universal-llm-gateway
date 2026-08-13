---
name: orchestrator-workflow
description: Lead/orchestrator session — decompose, fan-out, fork protocol, adjudicate, close-back. Load before dispatch fan-out or fork handoffs.
trigger_short: "lead/orchestrator session ∨ fan-out ∨ before dispatch"
skill_category: dispatch-delegation
skill_binding: {skill_class: workflow}
provisional: true
related_skills: ["consult-routing", "dispatch-workflow", "lead-seat-boot", "handoff-packet-authoring"]
trigger_match_terms: ["orchestrator-workflow", "orchestrate", "orchestration", "lead", "fan-out", "fanout", "dispatch", "fork", "sidecar", "adjudicate", "close-back", "closeback", "steelman", "multi-doc-review", "research-synthesis", "decompose", "work-stream", "sub-agent"]
---

# Orchestrator Workflow (lead dispatch composition)

> **PROVISIONAL (n=1).** Hardens on reuse. Single lived use case: arc 2466 (orchestrator) -> 2467 / 2468 (forks) -> close-back, re-exercised by the 2490 codification consult itself. Authored as artifact A of the 2490 consult; R1-R6 wording verified against the two fork feedback sidecars, deviations flagged inline.

## Justify existence (author iff recurring ∧ past judgment failed)

- **Recurring**: `recurring ⇐ this_arc(2466/2467/2468) ∧ standing_lead_dispatch_pattern`. Generalized from one coding arc to a standing invariant for *every* lead session (see Thesis).
- **Past failure (demonstrated, not speculative)**: implement-admission onion-peel bounces (F1 friction 19852; F2 4x serial bounce); SDK self-report trust failure (`files_modified:[]` falsely reported); packet contract self-inconsistency (F1 front-matter/body/turn mismatch); near-forced false dichotomy (F2 where-it-lives). `competent_lead absent this skill still repeats these ⇒ skill earns its place`.

## skill_class + overlap scope

`skill_class = workflow` (standing lead invariant). Overlap scan (`entities type=agent_skill`): **no** skill owns the `orchestrat*` trigger. Adjacent owners — do not re-own their mechanics:
- `dispatch-shape` — MCP call shape (`arguments` = JSON string).
- `dispatch-workflow` — per-dispatch hygiene + verification discipline (filesystem ground truth, not metadata self-report).
- `consult-routing` — lane / transport authority.

`scope(this_skill) = the multi-fork COMPOSITION (decompose -> fan-out -> adjudicate -> close-back) ∧ lead-context-conservation`, NOT the atomic mechanics above.

## Thesis

`orchestration = how a lead conserves context`. A lead's scarce resource is its own context window. Dispatch pushes heavy reading and authoring onto forks; the lead holds the goal, the arc, and the synthesis. Every rule below serves that conservation.

`∀ lead_session : lead_session = orchestrator_session` (coding ∨ non-coding). Treat this skill as a STANDING LEAD INVARIANT — written as if auto-injected on every lead boot (deployment intent below).

## When to read

Read when any holds:
- Entering a lead/orchestrator session (you hold the operator goal ∧ can dispatch).
- Before fanning out work to forks/sub-agents (coding ∨ non-coding).
- Before authoring fork handoff packets, or before adjudicating fork closeouts.

Do NOT read when:
- `role = fork ∧ deliverable = single_bounded_item` — read the packet `<invariants>` skills instead.
- single-seat work with no dispatch.

## Workflow phases

**Default execution model — simple session-driven.** The chat session IS the orchestrator. By DEFAULT there is NO dedicated 'outside' orchestrator agent-bus coordination thread: the session decomposes, fans out, and reads results directly. Forks are DELIBERATELY BLIND to the orchestration — they carry no orchestration-awareness and do NOT read this skill (`role = fork` already excludes it under "When to read"). The orchestrator's MINIMUM obligation to a fork is a CLOSE CONTRACT (see Result contract): the packet's `<output_format>` instructs the fork to write its deliverable to a cortex sidecar and post a closing pointer on its OWN per-dispatch result thread; the session-orchestrator reads that thread + sidecar to collect results. Per-dispatch result threads are INHERENT to dispatching — not the thing being dropped. RESERVE the alternative (Model 1: forks post onto one shared orchestrator thread) for carve-outs only: orchestration spanning multiple sessions, or many forks needing a single shared collection thread. The gap "forks don't know the orchestration" closes by SYSTEMATIZING the close contract in every packet's `<output_format>`, NOT by making forks orchestration-aware.

- **P1 Decompose.** `operator_goal ⇒ (this chat session as orchestrator) + N fork work-streams`; `∀ fork : |deliverable(fork)| = 1` (one bounded deliverable each). No dedicated orchestrator thread by default (see Default execution model).
- **P2 Fan-out (web-seat economy).** Orchestrator posts six-block fork handoff packets (per `handoff-packet-authoring`). `lead_context := minimize ⇒ push(heavy_reading ∧ authoring ∧ sidecar_writes, forks)`. Orchestrator stays lean; forks carry the load.
- **P3 Fork protocol.** Each fork: boot (lean dispatch profile — R2) -> priming checklist (native skill stubs + boot index -> fetch related threads -> load packet `<invariants>` task-class skill BODIES) -> consult/investigate -> (optionally) implement -> verify -> write sidecar -> post a closing pointer on its OWN per-dispatch result thread. `fork ⇏ close(own_dispatch_thread)` unless instructed — the session-orchestrator reads the dispatch thread + sidecar, adjudicates, and closes.
- **P4 Adjudicate.** Lead navigates fork sidecars (see md-navigation), applies independent verification (R4), assembles the synthesis.
- **P5 Close-back.** The session-orchestrator collects sidecars from each fork's per-dispatch result thread -> adjudicates -> authors synthesis + close-back -> closes fork threads.

**Ordering invariant:** `investigate/consult ⇒ implement ⇒ verify`. Never implement before the judgment-heavy investigate is landed on the lead.

## Result contract

`fork.MCP_enabled ⇒ canonical_deliverable(dispatch) = durable_cortex_sidecar(markdown)`. The bus reply = short pointer + summary, `¬full_result_inline`. This is the RESULT contract, not merely the bus body-size convention: the orchestrator collects sidecars, never inline walls. The dispatch packet SHOULD pin the expected sidecar sandbox+path (a cursor-sdk leg has been observed writing to the repo checkout instead of the cortex mount).

## cursor-sdk Q/R calling contract — thread consolidation

**Applies to:** `op=generate, seat=cursor-sdk` dispatches from lead/orchestrator seats.

The platform (`resolve_cursor_sdk_thread_targets`) auto-consolidates cursor-sdk Q/R — routing the result turn back to the dispatch shell instead of minting a new thread — **only** when `dispatch_thread_id` is **numeric, pending, and empty** (no prior turns). When those three conditions are not met, Stargate auto-provisions a separate result thread, producing the thread-doubling symptom the contract below prevents.

### Decision table

| Shape | Condition | Platform behavior |
|---|---|---|
| `dispatch_thread_id=<numeric pending empty shell>` | Pre-staged shell; no prior turns | Auto-consolidation fires; result lands on the shell; no new thread minted |
| `reuse_thread=<id>` (explicit) | Active arc thread must receive the result | Platform routes result to the named thread; `dispatch_thread_id` remains the context/compaction key |
| Neither, or `dispatch_thread_id` names an active non-empty arc | Fresh dispatch or mistaken arc pointer | Stargate auto-provisions a new result thread; lead polls `poll_hint` on the new thread |

### Anti-pattern — pre-created shell pointing at a different active arc (2672 failure mode)

**Never** pre-create a pending shell thread **and** point `dispatch_thread_id` at a different active arc. The shell is orphaned; Stargate sees the active `dispatch_thread_id` as non-consolidatable and mints a third result thread silently.

```
# WRONG — three threads: arc-coordination (A), orphaned shell (B), auto-minted result (C)
create_thread(slug="sdk-result-shell") → thread B  (pending, empty)
team_dispatch(op=generate, seat=cursor-sdk, …, dispatch_thread_id=A)
# A is active/non-empty → consolidation does NOT fire → Stargate mints C

# RIGHT — two threads: arc-coordination (A) stays lean; shell (B) serves as context + result
create_thread(slug="sdk-result-shell") → thread B  (pending, empty)
team_dispatch(op=generate, seat=cursor-sdk, …, dispatch_thread_id=B)
# B is pending+empty → consolidation fires → result lands on B; lead polls B via poll_hint
```

Since the `consolidation_split_warning` ship (commit 10112551), `team_dispatch(op=generate, seat=cursor-sdk)` emits a warning in the response when `dispatch_thread_id` names an active non-pending arc and `reuse_thread` is absent. The warning is advisory — dispatch proceeds — but treat it as an immediate signal to audit the thread binding before reading results.

### Pre-dispatch preflight (mandatory gate — verify BEFORE calling `team_dispatch`)

**Read before you fire.** Both consolidation conditions must be confirmed by a live probe, not assumed. Performing this check after `team_dispatch` returns is too late — the fork is already admitted by the time `consolidation_split_warning` surfaces, and the thread-doubling outcome cannot be unwound without manual cleanup.

```
agent_bus(tool="fetch", arguments='{"thread": "<dispatch_thread_id>", "compact": true, "last": 1}')
```

| Gate | Required value | Fail → halt and correct BEFORE dispatching |
|---|---|---|
| `bus_lifecycle_state` | `"pending"` | Thread was activated (turns staged or status changed). Close it; create a fresh pending shell; re-point `dispatch_thread_id`. |
| `turn_count` | `0` (zero prior turns) | A staging turn was posted to the shell, defeating the empty condition. Delete the errant turn(s) to restore empty state — OR explicitly pass `reuse_thread=<id>` when delivery to an existing thread is intentional and understood as non-consolidating. |

**Halt-on-fail invariant:** stop before calling `team_dispatch` when either gate is red. The two-thread split that results is not cosmetic — it produces a coordination thread (A) and an orphaned result thread (B) that the lead must track separately, defeating the purpose of consolidation.

*Grounded in friction 20133 — lesson_gap, service:mcp-server, 2026-06-19. Thread 2729 was created active with a staged instruction turn before dispatch; both consolidation conditions were defeated; `consolidation_split_warning` fired but the lead did not act on it, accepting threads 2729 + 2730 instead of halting and correcting.*

### Lead-seat obligation

| Thread role | Owner | Contains |
|---|---|---|
| **Arc coordination** | Lead (pre-created pending shell) | Context pre-staging turns; briefings; resumptions across the arc lifecycle |
| **SDK result / closeout** | Stargate (on-behalf delivery) | Single closeout turn carrying the sidecar pointer and acceptance evidence |

For consolidated Q/R, these must resolve to **one thread**. Stage context on the pending shell and pass that shell as `dispatch_thread_id`; Stargate delivers the result turn to the same thread, closing the loop without extra threads to track or poll.

Cross-links: `consult-routing` § Implement lane — source_ref (dispatch shape authority); `dispatch-shape` § Handoff poll hints (use `poll_hint.arguments_json` to poll the consolidated shell thread post-dispatch); `agent-bus-discipline` § Dispatch polling (cursor-sdk closeout matcher); `agent-bus-discipline` § Thread lifecycle (close the consolidated thread after reading the closeout — reduces false-unread counts at next boot).

### Post-dispatch poll recipe (cursor-sdk — mandatory)

After `team_dispatch(op=generate, seat=cursor-sdk)`, poll completion from `poll_hint` — **not** subject substring matching.

**Canonical poll (preferred):**

```python
agent_bus(tool="wait", arguments=poll_hint.arguments_json)
# wire-equivalent: wait(thread=N, after_turn=T, completion="first_reply_from", from_agent="cursor-sdk")
```

Re-call until `complete=true`. The machine closeout turn always posts with `from_agent="cursor-sdk"` regardless of subject wording.

**Anti-pattern — subject-regex polling:**

`¬` match on subject containing `closeout|complete`. Machine closeout subjects are `cursor-sdk dispatch {dispatch_id}` (and `FAILED`/`DELIVERY FAILED` variants) — `status` lives in the JSON body, not the subject. Human-authored closeouts may happen to include `closeout` in the subject (thread 4878 matched incidentally); that is not a reliable completion signal. A subject-regex poll loop on thread 4879 slept forever while turn 2 already carried `status=partial` (~18:34Z).

**Body JSON adjudication (after `complete=true`):**

`get` the qualifying turn and parse the body as JSON. Terminal states: `status ∈ {complete, partial, failed}`; inspect `effects_manifest`, `evidence_uris`, and sidecar pointers. `partial` = work landed with deviations — lead adjudicates before treating as done.

*Grounded in friction 23595 — orchestrator poll matcher bug on cursor-sdk implement shells, 2026-07-11.*

## md-navigation (adjudication mechanic)

`adjudicate ⇒ fs(md_list, sidecar)` for the section tree, then `fs(md_read, section=...) ∨ fs(md_to_dict)` to pull ONLY needed sections. `¬full_file_read`. Composes with R4 (verify) and the R2 boot-profile context thesis — md-navigation is how the lead reads fork output without re-spending its context.

## Rules (R1-R14 — R1-R6 convergent lessons from 2467/2468; R7-R8 operator directives 2026-06-18; R9 operator directive 2026-06-20; R10-R11 operator directives 2026-06-22; R12 operator directive 2026-06-29; R13 operator directive 2026-06-30; R14 2026-07-04 / assertion 22313)

- **R1 — implement-admission preflight. [TOP operational dependency.]** `before(implement_dispatch) ⇒ run non_writing_dry_run returning ALL unmet gates at once`. `¬onion_peel` (one call, not serial single-gap rejections). Mirror `session_close(dry_run=True)`. Gate set observed live: canonical spec path; pinned committed assertion citing the spec at `source_uri`; `spec_sha256` match; dense-spec schema (8 regex sections) + `<reasoning_trace>` attestation; `content_hash` resolvability. *Mechanism (the dry-run tool itself) is tracked as `todo:implement-admission-dryrun-preflight`; here: EXPECT the full gate set, do not discover it serially.*
- **R2 — dispatch-scoped boot profile.** `dispatch_boot ⇒ thin_card = orientation + arc_digest + operator-posture + MCP-binding/capability-verify + dispatch/consult-routing + cortex-orientation + target_entity + INJECT(packet_named_task_class_skill_BODIES)`. `DROP {personal-domain clusters, Deadlines, Dropbox-pending, Temporally-Active, Recent-Mentions, full 97-skill manifest}` — native discovery + lean boot profile replaces the manifest. *Mechanism = `decision:dispatch-boot-profile-shape` + `todo:dispatch-scoped-boot-profile`.*
- **R3 — revision->dispatch trap.** `mutating_consult ⇒ reland(approved_revisions -> todo_attrs) ∨ dispatch(via=packet_path)`. `source_ref` materializes from the todo's distilled attrs; `stale(attrs) after revision ⇒ silently_drops(approved_revisions)`.
- **R4 — independent lead verification (mandatory).** `¬trust(fork_self_report.files_modified)`. `lead verifies via direct_read + service_wide_grep + quality_gate`. Mechanic owned by `dispatch-workflow` — composed by reference; the point here is the LEAD (not the fork) performs it at adjudication. *FLAG (inference): grounded in the todo digest (cursor-sdk falsely reported `files_modified:[]`), not in the 2467/2468 feedback sidecars directly.*
- **R5 — packet contract self-consistency.** `packet.front_matter.contract ⇔ packet.body_shape ⇔ thread_turn_lane`. `inconsistent ⇒ routing_error`. A consult packet carrying an implement body (acceptance criteria + implement step) without an explicit lane declaration is malformed.
- **R6 — verify fork decision options vs LIVE architecture.** `fork_author presents decision_options ⇒ verify(each_option, live_architecture) first`. `unchecked ⇒ forced/false_dichotomy`.
- **R7 — dispatch cost asymmetry (route by capability × cost-to-operator).** `cursor-sdk dispatches = autonomous (zero operator burden) ⇒ use LIBERALLY` — including staging/recon and verification legs; the autonomous fork is the default workhorse. `web-claude handoffs = manual per-thread operator push (nonzero cost) ⇒ RESERVE for substantial, context-heavy work ∧ COMBINE multiple tasks onto ONE web-claude thread` to amortize the push. Never spawn N piecemeal web-claude handoffs where one batched thread serves. The lead routes by `capability × cost_to_operator`, not capability alone. *(Operator directive 2026-06-18.)*
- **R8 — todo pickup fires a cursor-sdk recon/staging pass first.** `todo lifecycle STARTED in a web session ⇒ dispatch a cursor-sdk staging/recon pass BEFORE the web lead engages design` — scour the repo for relevant files, surface candidate touch-points, gather code anchors — so the web lead's context stays lean (direct application of the Thesis + R2 + R7). **Model-tier selector:** retrieval/scaffolding-only inventory → Composer OK (`model=cursor/composer-2.5` or role default); investigate-emphasis staging (judgment / suggest / densify inputs) → `model=cursor/grok-4.6` (`contract=light-bounded`). Overlap theme (deferred, do not expand here): `todo:gatherer-role-reimplement-and-wire`. SCOPE GUARD: the recon pass is `RETRIEVAL/SCAFFOLDING ONLY` — it MUST NOT resolve design forks, select implementation shape, or mark implement-ready; judgment stays with the web reasoner. Label recon outputs "candidate — re-derive, don't elaborate" (a cheap recon that embeds a design decision can anchor the reasoner into elaborating a flawed structure). KNOWN GUARD: cursor-sdk closeout has been observed writing to the repo checkout, not the cortex mount (friction 19916) — the recon packet MUST pin AND verify the cortex sidecar path. *(Operator directive 2026-06-18; sibling todo:web-pickup-cursor-sdk-recon-pass. Companion edits to `handoff-packet-authoring` § Staging and `implement-todo` web-pickup convention are separate follow-ons, not in this file.)*
- **R9 — investigate = reasoning work; reasoning seat + cursor-sdk LIMB, never cursor-sdk-direct for open design.** `open_design_investigate (prove_linchpin ∧ design_schema ∧ HALT-or-proceed) ⇒ dispatch to a REASONING tier`, default `web-consult` (the default handoff seat — no explicit-auth, Opus-pickable); `cursor-consult` only on EXPLICIT operator selection (`cursor` is explicit-only/attended, #19925). `¬ route(open_design_investigate, cursor-sdk-direct)`: Composer is a mechanical executor that *optimises for appearing done*, so open-design judgment routed there risks a rubber-stamped verdict. The reasoning seat stays ABOVE the conformance line; when it hits an environment wall it cannot cross (run the worker, inspect a live on-disk artifact, terminal grep, execute a harness) it dispatches `cursor-sdk` (`pure-mechanical`/`light-bounded`, pre-trusted, autonomous — an R7 leg) as a mechanical LIMB and consumes the structured result. Limb model: pure inventory → Composer; investigate-shaped limb work (judgment/suggest densify inputs) → `model=cursor/grok-4.6`. False binary to avoid: *the autonomous/pre-trusted lane is mechanical, so put the whole investigate there* — COMPOSE (reasoning seat + limb), don't collapse. This IS the `task:reasoning-limb-delegation-workflow` shape: reasoning/orchestration → conformance limb → substrate; R8 (cursor-sdk recon pass) is its sibling — the limb for retrieval. *(Operator directive 2026-06-20; grounded in friction 20207 — the #20174 double-error class: auto-routing cursor without authorization, and nearly collapsing an open-design investigate onto cursor-sdk-direct.)*

- **R10 — offload context-bloating recon to the limb; pull only the compact verdict.** `lead would run a repo-wide grep / multi-file search / harness probe ⇒ dispatch it to a cursor-sdk limb on ONE consolidated thread (pending shell + packet_path), then pull the verdict sidecar — NEVER run the noisy breadth-search in the lead's own context`. Rationale: a service-wide grep dumps dozens of low-signal matches into the lead window (each match a context tax that compounds across a long arc); the limb greps in ITS context and returns `file:line + verdict`, so the lead's window holds the conclusion, not the search residue. Mechanic: author the packet to `cursor-sdk-instruction-standard` (determinate steps + mandatory self-check); pure inventory limbs → Composer OK; investigate-emphasis limbs → `model=cursor/grok-4.6`; poll once via `agent_bus(tool=wait)` from `poll_hint`, read the cortex sidecar (pin+verify its path per R8 KNOWN GUARD). This is R7×R8 applied to the lead's OWN verification legs (R4): keep the independent re-read on-seat for the LOAD-BEARING hit, but route the breadth sweep to the limb. *(Operator directive 2026-06-22; grounded in the mcp-toolresult consumer audit, thread 2977 — grep sweep + Cursor structured_content canary offloaded to one cursor-sdk worker, two one-line verdicts pulled back, ~zero search residue in the lead window.)*

- **R11 — one-off frontier CONSULT/REVIEW goes to a forked thread; pull only the verdict (the reasoning-consult analog of R10).** `lead needs a one-off second opinion (reviewer ∨ skeptic ∨ artisan) mid-arc ⇒ ¬run as synchronous inline generate` — the full model output AND the poll-loop both tax the lead window. Instead: stage the consult corpus as ONE turn on a fresh agent-bus thread, then `team_dispatch(op=to_thread, role=…, thread=N, dispatch_thread_id=N)` so the role's reply posts BACK to that SAME thread (one-thread pattern, directive a19869 — `¬sprawl` onto a separate result thread), and pull the single reply. Shape: `agent_bus(send, new_slug=…, allow_long_body=true, body=<consult prompt>)` → the body becomes the model prompt VERBATIM (do NOT instruct it to "reply on this thread" — Stargate delivers on-behalf; friction 17396) → `team_dispatch(op=to_thread, role=reviewer|skeptic, contract=light-bounded, thread=N, dispatch_thread_id=N)` → `agent_bus(wait, thread=N, after_turn=1, completion=first_reply_from)` → `get` the reply turn. `role=reviewer ⇒ gpt-5.5`, `role=skeptic ⇒ grok` (no `model=` needed). The model's full reasoning lands in a Stargate on-behalf sidecar; the lead's window holds the VERDICT, not the reasoning. R10 offloads a mechanical breadth-search to a cursor-sdk limb; R11 offloads a reasoning consult to a frontier role — same Thesis (context conservation), different fork class. GOTCHA: the on-behalf reply posts under the ROLE seat label (`from="reviewer"`), NOT the model/family name — `wait(…, from_agent="gpt-5.5")` will NOT match (wait alias-awareness does not bridge role↔model); poll `from_agent="reviewer"` (the role) or omit `from_agent` and take the next turn after the pointer (friction 20435). A/B confirmation of the saving is OPTIONAL, not gating — codified on Max-Effort reasoning. *(Operator directive 2026-06-22; grounded in the 13633 durable-identity wording consult, thread 2978 — corpus staged, gpt-5.5 reviewer verdict pulled back on the same thread, full reasoning in the on-behalf sidecar, ~lean lead window.)*

- **R12 — standing root bus thread + cross-session continuity (the unified orchestration path).** `orchestration spans (multi_session ∨ multi_seat ∨ multi_wave) ⇒ EITHER birth the arc on a standing agent-bus thread OR migrate onto one when context densifies` — birth-on-thread is OPTIONAL; an initial threadless session is fine. The binding moment is the PROMOTE: `dense(lead_context) ⇒ post a state-snapshot turn to a (new ∨ existing) root thread, THEN continue there` (the thread is a continuity device, not a birth artifact). Continuity then lives in THREE seat-agnostic surfaces, `¬ in chat context`: (1) a durable roadmap/plan FILE (cross-arc state), (2) the root bus thread (chronological pickup/closeout ledger; compacts on read; `agent_bus(fetch, thread=N)`), (3) cortex entity state (per-item authoritative `workflow_state` + assertions). Resolution: `cortex wins per-item; file = sequencer; thread = audit trail`. BINDING once a root thread exists: (a) **flush at inflection points, `¬ at exhaustion`** — `finish(item ∨ wave) ⇒ post one-line turn (subject = `<verb> <item-id>`, e.g. "DONE 0.1") ∧ flip roadmap row BEFORE context tightens` (a half-finished item whose only record sat in a dead context window is the exact failure this prevents); emit a `CHECKPOINT wave N` snapshot turn at each wave boundary so the thread is SELF-INDEXING — `readers reconstitute state from latest-checkpoint + roadmap in O(1), ¬ linear history` (nav mechanics → `agent-bus-discipline` § Standing root threads); (b) **one in-flight item per seat** — `mark WIP(<seat>)` (prevents cross-seat double-grab); (c) **close with a handoff pointer, `¬ fade-out`** — `session_close.handoff_prompt = "resume <arc> — read thread N + <roadmap>; wave=X; in-flight=<rows>"`. NESTED ORCHESTRATION: `an orchestrator MAY launch child orchestrators on their own root threads` — each child thread is itself an R12 root for its sub-arc; the parent links the child by thread id and treats the child's closeout turn as the fork deliverable (recursive; depth unbounded). SUPERVISION + HANDOFF MODEL: orchestration threads are `human-supervised` — the operator is the carrier who shuttles a thread between seats (a handoff = operator hands the thread number to the next seat). RESUME `= LOAD the orchestration skill set THEN READ the three surfaces, ¬ recall` — every pickup turn is read by a FRESH session, so `step 0 = load orchestrator-workflow body + agent-bus-discipline body` if not resident (cursor: always-applied stubs give the minimum, lead boot loads bodies; web: `InjectScope.LEAD`; a bare mid-arc resume must load them explicitly), `EXCEPT a pure closing turn` (close mechanics only — no further pickup). resume is IDENTICAL across seats (surfaces are seat-agnostic — web picks up a cursor-rooted arc and vice versa). Orchestrator seats today = `{cursor, web}`; an `API lead` is a future handoff target that slots in with NO protocol change — `just another from_agent seat` (acknowledge it, `¬ special-case` it). Address the waiting seat explicitly (`to=<seat>`) when a handoff expects its next turn so its unread digest pings. CALIBRATION (`¬ absolute mandate`): a single-step same-session fan-out needs no thread; the trigger is `continuity_across_context_windows matters`, not orchestration-in-general. *(Operator directive 2026-06-29, refined same day: migrate-on-density + nested-orchestrators + seat-extensibility; grounded in agent-bus thread 3624 `backlog-resolution-orchestrator` + `universal-llm-gateway/tasks/roadmaps/backlog-resolution-roadmap.md`.)*


### R12 — continuity hardening (doctrine review 2026-06-29)

Post cross-family review (threads 3626/3627/3628). Guidance-first; Tier-3 lint deferred.

Domain-neutral floor (birth trigger + charter-carried floor: lessons & frictions, pertinent skills, session-effectiveness hints — no separate per-arc workflow file) lives at `orchestrator-core` §7 (promoted 2026-06-30, revised same day to match live practice on threads 3870/3877). This section is the CODING specialization on top: scoped SOT reconciliation, CHECKPOINT-as-index, roadmap/Cortex-card mechanics, and the friction-mirroring contract (R13) below.

**Scoped source-of-truth model.** There is no single global SOT for an orchestration:

| Surface | Authoritative for | Not authoritative for |
|---|---|---|
| Cortex entity state | current per-item workflow state, ownership metadata, durable item facts | wave order, full audit history |
| Roadmap/plan file | sequencing, wave structure, decomposition, parent/child registry | current item state unless backed by Cortex |
| Root bus thread | audit trail, handoff index, checkpoint snapshots, child closeout reports | current item state when Cortex differs; intended sequence when roadmap differs |
| Charter scoreboard (when charter/brief exists) | deliverable-sequence completeness vs live `todo:`/`decision:` cards | wave audit history; in-flight seat WIP (CHECKPOINT indexes that) |

A `CHECKPOINT` is a **reconstitution index**, not state authority. It should name the roadmap path, active Cortex item IDs/states, child threads, unresolved discrepancies, and (when present) the charter-scoreboard path. `empty(Next-pickup) ⇏ arc_complete` — empty pickup only means no sequenced next row; charter/brief open deliverables still govern completeness.

**Surface disagreement recovery.** When the three surfaces disagree, reconcile by scope — do not silently pick one globally:
1. **Per-item state:** Cortex wins unless direct evidence it is stale; update Cortex or mark the discrepancy explicitly.
2. **Sequencing / wave order:** roadmap wins unless a later CHECKPOINT explicitly supersedes it.
3. **Audit / causality:** root thread wins for "what was reported when," not for current item state.
4. **Reconciliation turn:** after any mismatch, post `CHECKPOINT wave N — reconciliation` naming roadmap path, affected Cortex IDs/states, child thread IDs, and reason for roadmap edits.

**Checkpoint freshness.** A checkpoint is stale if: a child root closed after it; a Cortex item in the wave changed state after it; the roadmap file changed after it; or a seat posted root-thread work after it without a newer checkpoint. A stale checkpoint is an index only — reconcile before acting.

**Concurrent writes.** Root-thread turns are append-only. Multiple seats may post status; only the orchestrating lead posts wave-level `CHECKPOINT` unless delegated. Conflicting seat reports ⇒ reconciliation, not execution.

**Child-thread registry.** Every child orchestrator must be listed in the parent roadmap **and** (when a charter/endeavor scoreboard exists) in the scoreboard child/Pointers table, and in the next parent checkpoint as `child <thread-id> → scope → expected closeout`. Parent treats child closeout as deliverable only after the child's result is summarized back to the parent root. Depth is unbounded, but unregistered children are a protocol violation. Substrate/outliving work branched from an endeavor root MUST be a registered child (`agent-bus-discipline` § Deliverable vs substrate) — ¬ silent chat, ¬ stuffing onto the endeavor root.

**Stalled-child signal.** If a child root has no new turn within an operator-visible idle window (default: flag in parent checkpoint when child idle >48h), the parent posts `BLOCKED child <id> (idle)` on the parent root — do not wait silently.

**Write-back duty.** When a root or child closeout **or mid-arc retraction** changes deliverable state: (1) update the charter/endeavor scoreboard immediately, (2) update the relevant Cortex item or mark `Cortex write-back pending` in the checkpoint. A CHECKPOINT that still indexes DONE after a later retraction is stale until reconciled.

**Endeavor-root plumbing.** `¬ dispatch_thread_id=<endeavor_root>` for API-role `generate`/`to_thread`. One-off consults use R11 forked threads; generate/reply pairs on the endeavor root are fetch noise (128KB ceiling / mid-tier unreadability — grounded agent-bus:5129).

**Calibration — birth vs migrate.** Triggers + exemption: `orchestrator-core` §7 (domain-neutral; moved there 2026-06-30).

**Resume step 0 (ordered read).** Load orchestration skill bodies, then: latest checkpoint (index) → charter scoreboard if named/present (completeness) → roadmap (sequence) → Cortex cards (item state). Reconcile before execution. Mid-tier seats: stop after scoreboard + checkpoint unless a named operator question requires one further targeted read (`agent-bus-discipline` § Standing root threads — mid-tier budget).

**Charter scoreboard + done-claim gate (friction 23944).** **Life/web SOT:** `agent-bus-discipline` § R12 completeness gate (Customize Skills on claude.ai; `/mcp/life` only). **This skill** adds coding-orchestration specialization (cursor-sdk dispatch, source_ref, repo recon) on top — do not fork parallel doctrine.

**Empty-hopper standing tip forbid (friction 26710).** Forbid **admit-shaped** forever-open gated tips on an empty hopper (concrete-work dress with `executor=pending` / standing G-row and no in-flight WIP) — they thrash the charter-runner sole admitter with repeated empty re-verify admits. **Legal wait** = a **marked** gated standing tip (`executor=pending` or empty `executor=` on the gated Next-pickup row) fenced by kernel `empty_hopper` NOOP — the root stays enrolled across ticks. **Birth-shape checklist:** an admit tip needs **concrete work and a concrete executor** (`cursor/*` or rebind-eligible family); a marked standing wait uses explicit `executor=pending` (or empty value) and expects NOOP, not admit. **`empty(Next-pickup) ⇏ arc_complete` (a:23944) is the done-claim gate only** — it does **not** license admit-shaped empty-hopper tips; done-claim ≠ tip-admit license. **Do not** steer authors to empty/ungated Next-pickup as a standing wait — that path closes the root via `no_gated_pickup` → state_close (a:26596).

Cross-link only (full gate text lives in agent-bus-discipline): scoreboard SOT · done/next/close-arc gate · mid-tier budget · resume/checkpoint operator verbs · birth/none-forbid writer detail → § Standing root threads + § R12 completeness gate there.

**Countable failure mode (coding arc).** Incomplete operator-facing replies under root-thread multi-SoT load ⇒ log `friction` on `agent_skill:orchestrator-workflow` (`protocol`).

**API-lead nuance.** Protocol is seat-agnostic for bus/thread mechanics (`from_agent` is a label), but boot inject is not: a future API lead requires `InjectScope.LEAD` (or equivalent) plus explicit step-0 skill load — not the cursor always-applied stub alone. Non-interactive close may omit pasteable `handoff_prompt` when a machine-readable checkpoint assertion on Cortex suffices.

- **R13 — friction reporting on close-back (the close-back report is corrective, not just descriptive).** `arc_close_back ⇒ surface(tooling/process frictions hit during fan-out) as structural friction() observations on the OWNING skill/tool, ¬ narrative-only`. Every gap that cost a retry, a guess-and-fail loop, or a silently-wrong write (wrong param name, undocumented gate input, schema/doc mismatch) gets a `cortex(tool="friction", owner=<owning agent_skill/service>, category=..., note=..., suggestion=...)` call BEFORE the close-back narrative is written — this is what makes "frictions to address structurally" durable and queryable instead of evaporating in chat context. The arc's charter — specifically its `⚠ Lessons & frictions` section (micro-lifecycle live→recurring→resolved[structural|behavioral]→retire, per `orchestrator-core` §7) — is the mirror target: mirror the same items there, each entry a directive instruction + friction-id citation, `¬` a passive "watch out for" — so the next session reading that charter doesn't retrip the same gap before the structural fix lands. PRUNE DISCIPLINE: delete a bullet once its cited friction resolves upstream — this is a deliberate, bounded patch zone, the same "lean workflow" principle Known gotchas already follows, not a place for gotchas to accumulate. SELF-CHECK BEFORE LOGGING (mandatory, not optional): verify the claim against the actual tool schema/skill body FIRST — a friction logged from an unverified assumption is itself a new friction, and is strictly worse than no friction (it pollutes the fix queue with a non-bug). For Cortex-mechanic gotchas specifically (a tool signature that drops a field, an op requiring an undocumented co-field, a 422 needing an unstated param), prefer asserting on `service:cortex` per `agent_skill:cortex-orientation` § Operational gotchas — check `analyze_impact`/`write_discipline` for near-duplicates before asserting, since this surface already carries 180+ accumulated lessons. *(Operator directive 2026-06-30; grounded in thread 3867 `cursor-substrate-unification-slice1` close-back — 8 frictions logged (21655–21662) and mirrored into `workflow:todo-lifecycle` § Session frictions; this rule's own first application caught and retracted exactly the failure mode it warns against — friction 21660 initially claimed `fs` had "no append op," which was false (`append`/`md_append`/`md_insert` exist, documented in `agent_skill:fs`); root cause was skipping the mandatory schema-check before logging, not a tool gap — retracted via `assertion_update(valid_until=...)` per the retract-don't-supersede convention.)*

### R14 — fork close-contract + lead-voice provenance (2026-07-04, assertion 22313)
Source: assertion **22313** (thread 4286 densify fork, 2026-07-04) — a fork closed its own dispatch thread and appended a lead-voice narrative ("Lead has reviewed and is closing this thread now") before the lead adjudicated. Substance was fine (13/13 ratified), but a later reader would have trusted an adjudication that never happened. Same completion-provenance family as `completion-provenance-discipline`.

- **Forks do not close the dispatch thread when the packet reserves closure for the lead** (`<output_format>`: "Do not close the thread — lead adjudicates and closes"). A fork reporting DONE leaves the thread OPEN.
- **Completion attribution belongs to the seat that performed it.** A fork MUST NOT write lead-attributed text — close summaries, "lead reviewed / closing" narratives, or any first-person-lead voice. Report in the fork's own seat voice; the lead authors the close.
- **Lead checklist on a pre-closed dispatch thread:** treat the closure as unverified. Independently verify the substance, then patch the thread summary with corrected provenance (who actually closed; whether adjudication occurred) BEFORE reporting up — do not inherit the fork's lead-voice summary.
- Structural hardening (`agent_bus` close op recording the closing `from_agent`/session in `ThreadDetail`, so provenance is structural not narrative) is a separate code enhancement — out of arc scope; logged as an arc secondary finding.

## Seat cost terms — R7 parameterized by seat

`R7 (dispatch cost asymmetry) is SEAT-PARAMETERIZED`. The principle is constant — route by
`capability × cost-to-operator`, keep the lead's context lean — but the executor menu and the
DEFAULT differ by seat:

- **web-claude lead — dispatch-FORCED.** No local code execution. Every code touch is a
  dispatch: cursor-sdk `generate` (autonomous, zero operator burden — the workhorse) or
  web-claude `handoff` (manual per-thread operator push — reserve + batch). There is no
  "do it myself" option; orchestration is structurally forced.
- **cursor lead — dispatch-by-CHOICE, three options.** The keyboard seat CAN edit directly,
  so R7 gains a third term:

  | Option | Cost profile | When |
  |---|---|---|
  | direct in-seat edit | zero dispatch overhead; spends the EXPENSIVE resident lead's context + bloats the window | trivial/local one-touch only |
  | `team_dispatch(op=generate, seat=cursor-sdk)` | offloads to cheaper Composer; lead context stays lean; autonomous | **DEFAULT for all non-trivial implements** |
  | handoff (web/cursor) | manual operator push | substantial context-heavy reasoning; reserve + batch |

  **Uniform-default invariant (operator lock 2026-06-29):** the cursor lead DEFAULTS to
  `generate` for non-trivial implements — the keyboard seat ORCHESTRATES, it does not
  hand-code. This yields ONE cost/effectiveness model to optimize across both seats. Resist
  "I'm right here, I'll just edit it" — that is the in-seat analog of the R10 anti-pattern
  (running the breadth-search in your own window). Direct edit is reserved for genuinely
  trivial/local touches where dispatch overhead exceeds the edit itself.

**Isomorphism:** both seats' coding sessions run the SAME arc — recon (cursor-sdk paralegal)
→ investigate/adjudicate (lead) → review → densify → `generate` implement → lead-verify. The
ONLY seat difference is this skill's boot-residence path (web: server inject via
`InjectScope.LEAD`/`CODING`; cursor: always-applied stub + native `<available_skills>` trigger inject), not the
workflow. *(Operator directive 2026-06-29 — agent-workflow-parity, posture parity prompt/rule
level; infra-level boot-inject parity + the persistent orchestrator-bus-thread continuity
substrate are the deferred follow-on.)*

## Composes (by reference — do NOT restate bodies)

- `consult-routing` — lane / transport authority the workflow rides.
- `dispatch-shape` — MCP call shape.
- `dispatch-workflow` — per-dispatch hygiene + verification (R4 mechanic).
- `handoff-packet-authoring` — the six-block packet contract (P2).
- `agent-bus-discipline` — thread / reply / sidecar mechanics (P3/P5).
- `operator-posture` — held-for-confirm + briefing shape (R6 disposition).
- `session-close` — close ritual + the `dry_run` pattern R1 mirrors.
- `lead-seat-boot` — lead boot orientation.

## Deployment intent

This skill SHOULD be auto-injected on lead boots (invariant-skill channel) so the discipline is always present, even for non-coding leads. The server change to do so is tracked as `todo:auto-inject-orchestrator-workflow-skill` (bound mechanism: the unified scoped inject registry, `decision:dispatch-boot-profile-shape` D3). `lifecycle = active` (this file) is the precondition that unblocks it — `/skills/body` withholds an inactive skill's body.


## Coding specialization — composes onto `orchestrator-core`

> **This skill is the CODING SPECIALIZATION of lead orchestration.** The domain-neutral PRINCIPLES now live in `orchestrator-core` (auto-injected on every lead boot; read it first). The rules below (R1–R11) are the CODING INSTANTIATIONS — they bind the core's principles to the cursor-sdk / implement-admission / source_ref / repo-recon substrate. A non-coding lead inherits `orchestrator-core` ALONE, without this machinery. Ratified + split: Tier-5 domain-abstraction arc (panel thread 3303; `cortex:notes/system/threads/3303-panel-adjudication.md`; spec `cortex:notes/system/specs/orchestrator-domain-abstraction.md`).

### R1–R11 → core-family map

| Rule (coding instantiation, retained below) | Core principle it instantiates (see `orchestrator-core`) |
|---|---|
| R1 implement-admission preflight (dense-spec schema, spec_sha256) | F4 intake-preflight returns ALL gates at once + acceptance-contract density |
| R2 dispatch-scoped boot profile (packet task-class skill bodies) | F3 dispatch-boot economy (thin profile) |
| R3 revision→dispatch trap (source_ref materialization) | F4 commit approved output to the authoritative surface before re-dispatch |
| R4 independent lead verification (grep, quality_gate) | F4 independent lead verification (¬trust fork self-report) |
| R5 packet contract self-consistency (front-matter/body/lane) | F4 declared ⇔ actual ⇔ delivery-channel must agree |
| R6 verify fork options vs LIVE architecture | F4 verify presented options against live state |
| R7 dispatch cost asymmetry (cursor-sdk vs web-claude) | F3 route by capability × cost-to-operator |
| R8 todo pickup fires a cursor-sdk recon pass first | F3 recon-first with scope-guard |
| R9 reasoning seat + cursor-sdk LIMB | F3 reasoning/conformance line — compose, don't collapse |
| R10 offload repo-grep breadth-search to the limb | F2 context-conservation (mechanical-fork offload) |
| R11 one-off frontier CONSULT/REVIEW to a forked thread | F2 context-conservation (reasoning-fork offload) |

(F2 = context-conservation thesis; F3 = delegation grammar; F4 = adjudication discipline.) The cursor-sdk thread-consolidation contract, the dense-spec section schema, source_ref, grep + quality_gate, and `cursor-sdk-instruction-standard` D1–D4 are this specialization's coding FILLERS for the core's binding-table slots and its low-judgment-executor instruction standard. The evidence-curator binding-table slot is filled here by cursor-sdk tiered up for provenance-disciplined curation.
