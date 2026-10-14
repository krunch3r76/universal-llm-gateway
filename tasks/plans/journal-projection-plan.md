# Plan — todo:journal-projection

target: todo:journal-projection

Retrieval: `tmp/plans/journal-projection-retrieval-report.md` (orchestrator). Design placement below cites that report. File:line cites are from this worktree.

Rulings bound, not reopened: a:39215 (Q1–Q4), K27 a:39211, K28 a:39213, K29 a:39214. Fable review c52a033d (agent-bus:16671 turn 9) must-fixes are applied in this revision.

## 1. Aim

Arrival on the maestro hop shows a bounded, facet-matched `RULE:` projection and a register card, and no longer depends on reading `cortex://notes/system/maestro/journal.md` at offset 0 limit 40. A DISPOSITION that carries a line-start `lesson:` produces one journal entry; one that does not produces none. One writer is live at a time. Until a line-start `projector_live:` is in the journal, only the seat appends. The interim `team_dispatch` and the pipeline admit do not append. The seat's append of the first verified interim closeout includes that marker. After the marker, only `append_journal_from_closeout` appends, reading the cursor-sdk closeout file, not the 202 body. The source bus turn id is the dedupe key. The seat appends again only when that completion has failed. A pipeline definition can pin `cursor/composer-2.5` with `model_knobs {"fast":"true"}` and that step runs on cursor_sdk; the former cloud-only error no longer fires for that model. Induction cites one rule and one specimen from the maestro journal, and a missing citation is observable as `register_not_carried` in tests. Acceptance 4 and K29 (b) ship test-only: no production caller passes an in-flight projection id or a `SEAT_REGISTRATION` body into the new checks. Runbook steps 2/6/9/9a and the 12286 handoff head change only as proposed text in this file, after H3 PLAN, a lean-verifier VERDICT, and ACK by web-anthropic. `anthropic/*` is not a projector model, and neither is `cursor/claude-*`. Acceptance 1–6 of `todo:journal-projection` map onto L1, L0, L2, and L3 below. K29 (a)–(d) are acceptance on L1 and L2; (e) stays an open follow-up.

Retrieval placement (report yields): the hop body keeps longform data (`## Lessons in force`, `## Register`) above the first-act instructions, and hard refusals stay last (`claude_api` longform-at-top; `prompt_injection` hierarchy / post-prompting, weak but kept). The register card is the short surface; the journal specimen is loaded only when a projected RULE needs it (`agent_skills_research` progressive disclosure). K is bounded so a fixed window cannot swallow later lessons (`llm_prompting` lost-in-the-middle / Laban). The file:line rule is the brief's invariant (specimen 16660#10), not a corpus finding.

## 2. Ordered slices

Order is mandatory: L1, then L0, then L2 (cursor_sdk branch, then the pipeline), then L3. Do not start L2 model dispatch before L1's hop render is green. Do not start L3's checker before L2 can write a real entry.

### L1 — Deterministic hop render

No model call. No fixed offset-0 journal window.

**files_expected**

- `libs/claude_bundles/operator_proxy_hop_status.py` — compute the two sections inside `hop_successor_fields` (`libs/claude_bundles/operator_proxy_hop_status.py:117`).
- `libs/claude_bundles/operator_proxy_mission.py` — the arrival instruction that still reads the journal at offset 0 is the template, not the renderer. Act 4 is `libs/claude_bundles/operator_proxy_mission.py:136`. Field substitution is `_FIELD_ORDER` / `_LATE_FIELD_ORDER` at `libs/claude_bundles/operator_proxy_mission.py:184-190` and `render_hop_successor_briefing` at `libs/claude_bundles/operator_proxy_mission.py:202`.
- `libs/claude_bundles/test_operator_proxy_hop_status.py` — `_SECTION_ORDER` at `libs/claude_bundles/test_operator_proxy_hop_status.py:25-35`; `test_rendered_prompt_section_order` at `:128`; `test_first_acts_are_numbered_in_order` at `:146`.
- `libs/claude_bundles/test_operator_proxy_mission.py` — `_SECTION_ORDER` at `libs/claude_bundles/test_operator_proxy_mission.py:40-50`; `test_ensure_injects_briefing_without_slash_prefix` asserts that order at `:65-70`. Update in the same slice when the template grows headings. The hop-status file's `test_mission_ensure_opens_on_successor_template` (`test_operator_proxy_hop_status.py:163-170`) also asserts "What is running now" before "Hard refusals".

Mint `files_expected` omitted `operator_proxy_mission.py`. Bound here: acceptance 1 is false if act 4 still says `Read cortex://notes/system/maestro/journal.md with offset 0, limit 40`. Rejected alternative: leave the template and hope the seat ignores it.

**Behavior**

- Add `## Lessons in force` and `## Register` to the hop body after `running_now_line` / HOUSE RULES and before `## Live leg`. Hard refusals stay the suffix (`_HOP_SUCCESSOR_SUFFIX`, `libs/claude_bundles/operator_proxy_mission.py:175-180`).
- Lessons: newest K lines, K starts at 5. Match a lesson line only when, after optional whitespace, an optional `-`/`*` bullet, and optional `**`, the line begins with `RULE:`. Facet tags are bracket tokens on that line. Keep the line when its facets intersect the role's facet set. Maestro's set is the seed's fixed set and nothing else: `route`, `restart`, `review`, `brief`, `bus`, `land`, `register`. Any other role gets an empty set until that role's first real entry defines facets (Q2: no invented content). Each kept line carries its entry heading and date.
- Do not match `## STANDING RULE` headings. Do not scrape a `**RULE:**` that sits mid-bullet. Observed: entry 157 (`cortex://notes/system/maestro/journal.md`, read_sha256 `dd50d7065087218be3ba5defab5f3de184cb9c605162cf1904820f4ef896bbd9`) puts `**RULE:**` inside a bullet that starts `- **A tool…`. A line-start matcher yields no lessons from that entry. That empty projection is correct. Do not rewrite entry 157.
- Journal path the renderer reads: `cortex://notes/system/maestro/journal.md`. `cortex://notes/system/threads/12286-journal.md` is marked superseded (its own first lines). `MaestroInductFetchJournalHandler.execute` still reads `cortex://notes/system/threads/{root}-journal.md` (`pipelines/maestro_induct/v1/handlers/fetch_journal.py:23-24`). That induct path is not this slice.
- Register: copy `cortex://notes/system/maestro/register.md` whole, capped at 8 lines. Observed this turn: that path is file-not-found. Render one line that the card is absent and name the path. Do not author card prose.
- Replace template act 4 with: the Lessons and Register sections in this hop body are the arrival projection; open the journal file only to read the specimen of one named RULE. Keep acts 1–3 and 5–7 numbered as they are (`test_first_acts_are_numbered_in_order` checks `1. Use`, `2. Read`, `7. Act`).
- K29 (b) ships test-only. `hop_successor_fields` does not call `pipeline` `op=result` and does not call a model. An injected callable may name an in-flight projection id. When the test passes an id, render it beside the fixture's durable RULE lines. When it returns nothing, or the journal read raises `OSError`, render the durable text that was supplied (or "journal unavailable") and do not wait. Same failure posture as `standing_handoff_text_for_prompt` (`libs/claude_bundles/operator_proxy_hop_status.py:76-80`). Production hop render does not pass that callable. The callers are `ensure_operator_proxy_mission_prompt` at `libs/cdp_ask/runner.py:469` and `libs/claude_bundles/cdp_model_endpoint_staging.py:495`; both pass handoff text and `execution_id` only. `filter_cp_projection_producer_links` (`libs/agent_bus_store/checkpoint_projection_producers.py:150`) is reached from checkpoint wiring (`libs/agent_bus_store/checkpoint_projection_wiring.py:85`), not from the hop. This todo does not add a probe argument at those call sites. A live in-flight name on the hop is a follow-up.

**Acceptance criteria**

| Criterion | Test |
|---|---|
| Acceptance 1. Facet-matched `RULE:` lines, at most K, plus a Register card, and the rendered prompt does not contain `journal.md with offset 0, limit 40`. | `test_lessons_in_force_bounds_k_and_facets` and `test_first_act_does_not_read_journal_offset_0` in `test_operator_proxy_hop_status.py`. Fixture journal text passed through the existing `standing_handoff_text` / a new `journal_text` seam. No cortex client. |
| A `[fleet]` line is omitted for role `maestro`. Seven matching maestro lines yield 5, newest first. | Same test, fixture lines. |
| Mid-bullet `**RULE:**` and `## STANDING RULE` headings are not projected. | `test_lessons_ignore_standing_rule_headings_and_midline_rule`. |
| Absent register path renders the absence line and no invented voice. | `test_register_absent_is_named`. |
| K29 (b), test-only. With an injected callable, an in-flight id is named and lessons still come from the fixture journal. The render does not call a wait or a model. Production `runner.py:469` and `cdp_model_endpoint_staging.py:495` are not changed and do not name an in-flight id. | `test_inflight_projection_does_not_block`. |
| Journal read `OSError` renders "journal unavailable" and still returns a prompt. | `test_journal_read_oserror_renders_unavailable`. |
| Section order: Lessons and Register sit after "What is running now" and before "Hard refusals". | Update `_SECTION_ORDER` and `test_rendered_prompt_section_order` (`test_operator_proxy_hop_status.py:128`). |

**Breakage matrix**

| Change | Breaks when | Test |
|---|---|---|
| New template fields | A brace in HOUSE RULES or caller body is interpreted as a format field. | Existing substitute-then-verbatim order (`render_hop_successor_briefing`, `operator_proxy_mission.py:214-216`). `test_house_rules_extracted_verbatim` (`test_operator_proxy_hop_status.py:69`) plus a fixture rule line that contains `{lane}`. |
| Act 4 rewrite | Numbering drift; `test_first_acts_are_numbered_in_order` (`:146`) fails on `2. Read` / `7. Act`. | That test, updated to the new act-4 sentence, still asserts order. |
| Journal seam hits disk | Production path or a live cortex root is read. | Tests pass `journal_text=` / a raising callable. `test_loader_seam_does_not_touch_disk` (`:53`) stays green. |
| Wrong journal path | Renderer follows induct and reads the superseded `threads/12286-journal.md`. | Fixture assertion that the production default URI string is `cortex://notes/system/maestro/journal.md`. |
| In-flight probe | Stargate or the tracker is down, or the probe waits. | `test_inflight_projection_does_not_block`: callable raises, render still returns. |
| Empty projection | Implementer "fixes" emptiness by falling back to offset 0. | `test_first_act_does_not_read_journal_offset_0` forbids that string. |
| Install / staging | Hop text is injected by GIW / cdp_ask from this source. A landed file is not what a running process serves. | This slice does not restart. The implement closeout says `not_live` until the lead recycles the injector. No test signals a process. |

**Live surface**

| Call | file:line | Role in this slice |
|---|---|---|
| `hop_successor_fields` | `libs/claude_bundles/operator_proxy_hop_status.py:117` | Add lessons + register fields. |
| `house_rules_section` | `libs/claude_bundles/operator_proxy_hop_status.py:104` | Unchanged. Live handoff has no heading containing `HOUSE RULES` (search of `12286-standing-handoff.md` returned SUNDAY RESUME, CURRENT LEG, CURRENT STATE, First acts). Empty HOUSE RULES stays empty. |
| `standing_handoff_text_for_prompt` | `libs/claude_bundles/operator_proxy_hop_status.py:60` | Pattern for the journal read seam (`OSError` → None at `:76-80`). |
| `ensure_operator_proxy_mission_prompt` | `libs/claude_bundles/operator_proxy_mission.py:230` | Calls `hop_successor_fields` at `:253`. |
| `render_hop_successor_briefing` | `libs/claude_bundles/operator_proxy_mission.py:202` | Must substitute the new fields before verbatim blocks. |
| `project_live_leg` / `render_live_leg` | imported `libs/claude_bundles/operator_proxy_hop_status.py:18-24`; called at `:139-140` | Unchanged. Tests keep `fetch_turns` injected (`test_operator_proxy_hop_status.py:38`). |
| `parse_journal_entries` | `libs/maestro_induct/parse.py:230` | Do not use it for lessons. It returns whole `##` sections, newest by file offset, which is how a window swallows entries. |

### L0 — Lesson capture on DISPOSITION

**Hook site:** `body_has_disposition_type` (`libs/agent_bus_store/disposition.py:33-43`), which uses `first_line_is_disposition_type` (`:25-30`) and `_DISPOSITION_TURN_TYPE_RE` (`:19-22`). That regex matches `TYPE: DISPOSITION`, `TYPE: LEG DISPOSITION`, and `TYPE: DISPOSITION+…`, and stops at the first other `TYPE:` line. Do not hook `pager_notify.bus_scan._turn_is_pageable` (`libs/pager_notify/bus_scan.py:40`) — that pages. Do not hook `is_cease_to_act` (`libs/claude_bundles/pickup_awaits.py:101`) — that is the pickup refuse.

**files_expected**

- New `libs/claude_bundles/journal_lesson.py` — `capture_lesson(body, *, turn_id) -> LessonCapture | None`. Calls `body_has_disposition_type`. Returns None unless the body is a disposition and has at least one line-start `lesson:` (same bullet/bold prefix rule as `RULE:`). One disposition turn → one capture, even if several `lesson:` lines exist (acceptance 2: one entry).
- `libs/claude_bundles/test_journal_lesson.py`
- Template sentence in `operator_proxy_mission.py` (the "Report each leg as a DISPOSITION" line at `:149`): until the journal contains a line-start `projector_live:`, the seat is the only appender, in the same step as the standing-handoff replace. Interim `team_dispatch` and `append_journal_from_closeout` do not append in that window. The seat's append of the first verified interim closeout includes `projector_live: <dispatch_id>` in that same write. After the marker, the seat does not append. Dedupe key is the source bus turn id. The seat appends again only when `append_journal_from_closeout` has failed (see L2), not while a poll is still `predicate_unmet`.

The seat append itself is the hop instruction. This slice does not subscribe to the bus and does not write cortex.

**Acceptance criteria**

| Criterion | Test |
|---|---|
| Acceptance 2. Disposition with line-start `lesson:` → one capture. Without → None. | `test_lesson_line_captures_one` / `test_disposition_without_lesson_captures_none`. |
| `TYPE: DIRECTIVE` whose body contains `lesson:` → None. | `test_non_disposition_type_ignored`. |
| Second capture with the same `turn_id` against a journal fixture that already has that id → no second entry. | `test_dedupe_on_source_turn_id`. |
| `projector_live:` present → `writer_mode` is `projector`. Absent → `seat`. | `test_writer_mode_marker`. |

**Breakage matrix**

| Change | Breaks when | Test |
|---|---|---|
| Classifier too wide | A DIRECTIVE or CLOSEOUT that mentions `lesson:` becomes an entry. | `test_non_disposition_type_ignored`. |
| Two writers | Seat and L2 both append. | `test_writer_mode_marker` plus L2's dedupe test. Marker is the switch; turn id is the dedupe. |
| Concurrent dispositions | Two turns append without a shared id and duplicate. | Dedupe key is the bus turn id, not the lesson text. |
| Partial line | `lesson:` mid-sentence, not line-start. | Same matcher as RULE. `test_midline_lesson_ignored`. |
| Ordering | L0 ships before L1, and the template still orders an offset-0 journal read above the new sentence. | L0's template edit lands only after L1's act-4 test is green. |
| Cortex down | Nothing in this slice calls cortex. | Tests are pure functions on strings. |

**Live surface**

| Call | file:line | Role |
|---|---|---|
| `body_has_disposition_type` | `libs/agent_bus_store/disposition.py:33` | Gate. |
| `first_line_is_disposition_type` | `libs/agent_bus_store/disposition.py:25` | Line match. |
| Hop DISPOSITION instruction | `libs/claude_bundles/operator_proxy_mission.py:149` | Where the seat-append sentence is added. |
| Runbook step 4 (not edited here) | `cortex://notes/runbooks/maestro-loop.md` step 4 | The seat already posts `TYPE: DISPOSITION` via `agent_bus(send, thread=<lane>, to=cursor)`. Proposed step 6/9 text points at the marker. |

### L2 — cursor_sdk branch, then journal-projector

Q1: the `cursor/*` → `cursor_sdk` branch is this slice, after L1. Team_dispatch is interim only until that branch's test is green. Q3: model `cursor/composer-2.5` plus `model_knobs {"fast":"true"}`. Do not use `cursor/composer-2.5-fast`.

**Catalog bind (observed).** `CURSOR_MODEL_CAPABILITIES["composer-2.5"]` has knob `fast` accepted `("false","true")`, default `"true"` (`libs/cursor_capabilities/cursor_capabilities.py:141-151`). There is no `"composer-2.5-fast"` key. `CURSOR_BARE_ALIASES` (`:420-429`) does not fold that id. `canonical_cursor_bare_id("cursor/composer-2.5-fast")` therefore stays `composer-2.5-fast` (`:445-455`), and `_valid_models_block_key("composer-2.5-fast")` is False (`libs/implement_admission/test_workflow_registry.py:49-55`). `config/model_rates.yaml` has a rate row `cursor/composer-2.5-fast`; that file is not the cursor model catalog. `align` fills `fast=true` when the wire omits it (`services/universal-stargate/systems/frontier_consult/cursor_sdk_alignment.py:240-246`). The pipeline still passes the knob explicitly, matching the team_dispatch wire (`services/mcp-server/tools/frontier.py:332` and the relay at `:1095`).

**Branch shape.** Mirror the CDP early return. Do not weaken `require_cloud_api_backend`.

1. `normalize_frontier_wire_model` (`services/universal-stargate/systems/pipeline/core/handlers/frontier_dispatch/request.py:76-93`) already returns the wire id when `backend_type == "cdp"` (`:88-89`) and otherwise calls `require_cloud_api_backend` (`:90-93`), which raises `SubstrateCapabilityUnimplementedError` for `cursor_sdk` (`libs/model_id/wire_resolve.py:239-257`). Add a `cursor_sdk` return beside the `cdp` return. `resolve_model` calls this at `request.py:152`.
2. `run_admission_gate` (`admission_gate.py:74`) calls `resolve_model` at `:90` and returns early for CDP at `:91-99`. Add the same early return for a cursor-sdk model **before** the cloud tool loop. The comment at `admission_gate.py:133-134` ("cursor-sdk / cursor/* agents are unbounded and never enter this admission path") becomes true only if that return exists. Passing cursor through `normalize` without the return drops the model into `resolve_dispatch_tool_set` (`:153`).
3. `FrontierDispatchHandler.execute` (`handler.py:68-78`) branches to `run_cdp_dispatch` at `:74-75`. Add `run_cursor_sdk_dispatch` in a new sibling module, same package. It calls `dispatch_cursor_sdk_generate` (`services/universal-stargate/systems/frontier_consult/cursor_sdk_generate.py:303`), the function the route awaits at `generate_wrap.py:483`. It does not call `dispatch_cursor_sdk_generate_route` (`generate_wrap.py:226`). That route's signature takes a FastAPI `Response` (`:231`). At `:611-612` it sets `response.status_code = 202` and returns `result`, the admit envelope from `:483`, not model text. The sibling must not construct a `Response` to satisfy that signature: the 202 body still has nothing to append. Seat `cursor-sdk`, model and knobs as above. The sibling passes `lane="B"` (`cursor_sdk_generate.py:339`) and `contract="freeform"`. That `contract` Literal (`:310-317`) does not list `freeform`; the route already passes `contract=body.job` at `generate_wrap.py:489`, and the interim `team_dispatch` uses the same string. Do not edit the Literal in this slice. The sibling does not run `require_cursor_sdk_checkout_lane` (`generate_wrap.py:241`), because that check is inside the route it does not call. Tests monkeypatch `dispatch_cursor_sdk_generate` and never build a `Response`.
4. `anthropic/*` stays `backend_type == "cloud_api"` (`libs/model_id/model_id.py:150-154`, test `libs/model_id/tests/test_model_id.py:81`). It still goes through `require_cloud_api_backend` and is not on the cursor branch. `CURSOR_DENIED_MODELS` is an empty frozenset (`libs/cursor_capabilities/cursor_capabilities.py:41`), so `cursor/claude-*` is a real catalog id and `is_other_models_pool` is True for it (`libs/cursor_capabilities/model_pools.py:6-32`). The projector's first deterministic step rejects provider `anthropic` and any id `is_other_models_pool` accepts, including a `pipeline_options.model` override (`resolve_model` precedence 1 is the runtime model, `request.py:105-125`). Rejection happens before `dispatch_cursor_sdk_generate`.

**Pipeline definition** (after the branch test is green)

- Path: `pipelines/journal_projector/v1/journal-projector-v1.yaml`. `id: journal-projector` so `pipeline(op=async, pipeline_id=journal-projector)` matches the seed. Layout follows `pipelines/{domain}/v1/` (`pipeline_ws` directory rule).
- Steps in one DAG execution: (1) deterministic parse — no `lesson:` → completed skip, no model call; (2) `frontier_dispatch_v1` with `model: cursor/composer-2.5` and domain field `model_knobs: {fast: "true"}` only when a lesson exists. Step 2's output is the admit envelope (`execution_id`, `poll_hint`) from `dispatch_cursor_sdk_generate`. It is not a journal entry. There is no step 3 in that same execution: `reads_from` step 2 would append the 202 body.
- Completion path, after `poll_hint` returns `complete=true` and `projector_live:` is already in the journal: pipeline id `journal-projector-commit` has one step, handler `append_journal_from_closeout` in `pipelines/journal_projector/v1/handlers/append_closeout.py`. The seat starts it with `pipeline(op=run)` and passes `dispatch_id`. It is not a step of `journal-projector` and it does not `reads_from` the admit envelope. The handler reads the closeout file `write_repo_sidecar` writes (`services/git_integration_worker/cursor_sdk_deliverables.py:71`). The relative path is `_sidecar_rel_path` (`:57-58`), `tmp/reviews/closeouts/{dispatch_id}.md`. The production write of that file is `services/git_integration_worker/cursor_sdk_closeout/delivery_assembly/change_set_resolution.py:146`. The handler extracts one line-start `RULE:` entry (source turn id, facet tags) and appends it to `cortex://notes/system/maestro/journal.md`, newest first. A lane-B cursor-sdk seat does not write that cortex path. It writes the repo closeout only. The async tracker is not the store. The seat writes the marker; this handler does not (see §3).
- Add `model_knobs` to `FrontierDispatchHandler._ACCEPTED_RUNTIME_OPTION_KEYS` (`handler.py:45-66`) or the runtime override is `UnknownPipelineOptionsError` before execute. YAML domain fields do not need that set; a caller `pipeline_options.model_knobs` does.
- Durable write uses the filesystem root `read_cortex_file` already uses (`pipelines/maestro_induct/v1/handlers/_clients.py:107-121`): path under `CORTEX_FILES_ROOT`, compare-and-swap on the sha256 that function returns. `CORTEX_FILES_ROOT` unset → `files_root_unset` (`:109-110`) → step failed → seat fallback. Do not call `cortex_dispatch` (`_clients.py:76`), which POSTs `/dispatch` on the cortex URL.
- Dedupe: if the turn id is already in the file, `append_journal_from_closeout` is a no-op and still successful. It does not write `projector_live:`. The seat writes that marker once, inside its append of the first verified interim closeout, before this function is allowed to append.
- K29 (a): the durable destination is the journal file written by `append_journal_from_closeout` from the closeout. Hop and checkpoint read that file (and, for in-flight, `thread_dispatch_links`), never tracker `result` and never the 202 body.
- K29 (c): today `admit_dispatch` returns `execution_id`, `pipeline`, `started_at`, `status` (`services/universal-stargate/systems/proxy/routers/api/pipelines_dispatch.py:474-479`) and does not return `poll_hint`. A link row is inserted only when `result_delivery.bus_thread` is set: `register_execution` calls `_schedule_dispatch_admit` at `lifecycle.py:123-124`, which POSTs `/threads/{id}/dispatch-admit` (`dispatch_admit.py:48-68`) into `admit_dispatch` (`libs/agent_bus_store/db/threads_atomic.py:408`). The POST is skipped when `agent_bus_token` is unset (`dispatch_admit.py:36-37`) and a non-200 is swallowed (`:70-79`). Checkpoint "In-flight producers" renders those link rows (`libs/agent_bus_store/checkpoint_projection_producers.py:212-218`). Gap to close in this slice: journal-projector `pipeline(op=async)` passes `result_delivery` with `bus_thread` set to the maestro lane, and the 202 body gains the same `poll_hint` object `build_poll_hint_wait` builds (`services/universal-stargate/systems/frontier_consult/handoff_response.py:116`) — `tool=wait`, `arguments.thread`, `arguments.from_agent`, `arguments.completion=first_reply_from`, `arguments.execution_id`. Team_dispatch already returns that shape (`test_team_handoff.py` asserts `poll_hint.tool == "wait"`). Without `result_delivery.bus_thread`, the run stays out of `thread_dispatch_links` and out of the checkpoint section.
- K29 (d): delay is not failure. `derive_status` returns `predicate_unmet` / `no_new_turn` while the predicate is open, and the comment at `libs/agent_bus_store/wait_status.py:507-508` says SDK `failed` must stay `predicate_unmet` / `no_new_turn` rather than being treated as proof. `pipeline(op=result)` `wait_seconds` is clamped to 60 (`services/mcp-server/tools/pipeline.py:50`, `:565-568`). A still-running result after that clamp is a poll to repeat, not a failed entry. Provider error is failed: a cursor-sdk closeout whose first lines are `provider_error` (a:39212) is a failed projection even if `terminal_status=completed`. The seat fallback appends only on that failure, on `TrackerCapacityError`, or on `files_root_unset` — not on `predicate_unmet`.
- Tracker facts the projector must not depend on: `max_records` 256 and `retention_seconds` 86400 (`services/universal-stargate/systems/pipeline/core/execution/async_tracker/constants.py:14-19`). At 256 records, `register_execution` drops the oldest terminal whether or not anything has read it (`lifecycle.py:66-76`). If all 256 are running, it raises `TrackerCapacityError` (`:77-87`). Age prune drops terminals past retention (`prune.py:22-37`) and does not age-evict running records. Stargate restart drops the in-process tracker (`constants.py:16-18`). The journal file survives all three.

**Interim.** Until `test_cursor_model_skips_cloud_backend` is green, the lesson trigger is `team_dispatch(op=generate, seat=cursor-sdk, lane=B, contract=freeform, model=cursor/composer-2.5, model_knobs={"fast":"true"})` via MCP `team_dispatch` (`services/mcp-server/tools/frontier.py:294`). That call does not append. The pipeline id is not the production trigger in that window. After the test is green and `projector_live:` is in the journal, the trigger is `pipeline(op=async, pipeline_id=journal-projector, …)` and the writer is `append_journal_from_closeout`. See §3.

**Acceptance criteria**

| Criterion | Test |
|---|---|
| Acceptance 3. `cursor/composer-2.5` on `frontier_dispatch_v1` does not raise `SubstrateCapabilityUnimplementedError`. The handler calls `dispatch_cursor_sdk_generate` and does not call `run_dispatch_loop` or `dispatch_cursor_sdk_generate_route`. | `test_cursor_model_skips_cloud_backend` in `services/universal-stargate/systems/pipeline/core/handlers/frontier_dispatch/test_cursor_sdk_dispatch.py`. Monkeypatch `dispatch_cursor_sdk_generate` to return `{execution_id, poll_hint}`. Assert `Response` was not constructed. Do not bind a socket and do not construct a GIW client. |
| Former reject path still rejects when the cloud helper is invoked. | Existing `test_cursor_and_cdp_substrates_admitted` (`libs/model_id/tests/test_wire_resolve.py:80-105`) stays: `require_cloud_api_backend` still raises for a cursor resolution. This slice does not edit that function. |
| Acceptance 6. `anthropic/claude-sonnet-5` and `cursor/claude-opus-5` on journal-projector fail the guard before `dispatch_cursor_sdk_generate`. | `test_projector_rejects_anthropic_and_other_models_pool`. Pure function on the model string. |
| A disposition with no `lesson:` completes without a model call. | `test_no_lesson_skips_model`. Assert the patched `dispatch_cursor_sdk_generate` was not called. |
| K29 (a). `append_journal_from_closeout` given a closeout fixture that contains one `RULE:` line writes that entry under `tmp_path`. Given only an admit envelope `{execution_id, poll_hint}`, it writes nothing. The hop renderer does not read the tracker. | `test_append_reads_closeout_not_admit_envelope` with `CORTEX_FILES_ROOT=tmp_path`. |
| K29 (c). Async admit body includes `poll_hint.tool == "wait"` and `execution_id` when `result_delivery.bus_thread` is set. A call without `bus_thread` does not invoke `admit_dispatch`. | `test_async_admit_poll_hint_shape` builds the response dict with the same helper `build_poll_hint_wait` (`handoff_response.py:116`). `test_dispatch_admit_scheduled_only_with_bus_thread` uses the existing tracker unit seam (in-memory tracker, no Stargate). |
| K29 (d). Status `running` after `wait_seconds` clamp is not a failed capture. A fixture closeout starting `provider_error` is failed and `writer_mode` allows one seat fallback. | `test_running_is_not_failure` / `test_provider_error_is_failure`. |
| Dedupe. Second append of the same turn id does not duplicate the `RULE:` line. | `test_append_dedupes_turn_id`. |
| Capacity. When the tracker raises `TrackerCapacityError`, the capture result is failed (seat may append). | `test_capacity_error_is_failed` constructs `TrackerCapacityError` (`lifecycle.py:85`) in-process. It does not fill 256 live executions. |

**Breakage matrix**

| Change | Breaks when | Test |
|---|---|---|
| Pass-through without admission return | Cursor model enters the cloud tool loop (`admission_gate.py:133` onward) and `run_dispatch_loop` (`handler.py:77`). | `test_cursor_model_skips_cloud_backend` asserts `run_dispatch_loop` was not called. |
| `require_cloud_api_backend` edited to allow cursor | Other cloud-only callers admit cursor_sdk. | Do not edit it. `test_wire_resolve.py:99-105` still raises. |
| `model_knobs` omitted from the allowlist | Runtime knobs die as `UnknownPipelineOptionsError` before execute (`reject_unknown_runtime_options` is first in `run_admission_gate`, `admission_gate.py:87`). | `test_model_knobs_accepted` with options `{fast: "true"}`. |
| Synthetic id `cursor/composer-2.5-fast` | Not a catalog key; alignment will not fill `fast` on that bare id (`cursor_sdk_alignment.py:240` compares `composer-2.5`). | Guard test rejects that wire id for the projector. |
| `lane` omitted on the sibling call | `dispatch_cursor_sdk_generate` is invoked with `lane=None` (`cursor_sdk_generate.py:339`). The route's `require_cursor_sdk_checkout_lane` (`generate_wrap.py:241`) does not run, because the sibling does not call the route. | `test_cursor_model_skips_cloud_backend` asserts the monkeypatched call received `lane="B"`. |
| Stargate down | `pipeline(op=async)` returns `stargate_unreachable` from `_pipeline_async` (`services/mcp-server/tools/pipeline.py:347-352`). | Not a unit-test target. The seat fallback treats connect failure as L2 failed. Document in the trigger; do not open port 9999. |
| `result_delivery` omitted | No `thread_dispatch_links` row (`lifecycle.py:123`). Checkpoint producers stay `_none linked_` (`checkpoint_projection_producers.py:220-221`). | `test_dispatch_admit_scheduled_only_with_bus_thread`. |
| dispatch-admit swallowed | Token missing or non-200 (`dispatch_admit.py:36-37`, `:70-79`). The 202 still looks successful while the checkpoint omits the producer. | The pipeline 202 includes `poll_hint` from `build_poll_hint_wait` (`handoff_response.py:116`), not from `dispatch_cursor_sdk_generate_route`. A unit test of `_do_dispatch_admit` is out of this slice if it needs a live bus; the plan's test stubs the scheduler and asserts it was invoked with `bus_thread`. |
| Concurrent appends | Two writers, same file, lost update. | Compare-and-swap on `read_cortex_file`'s sha. Conflict → one retry → fail → seat fallback. `test_append_conflict_fails_closed` uses two sha values in a fake reader. |
| Tracker eviction / restart | Result bytes disappear (`lifecycle.py:66-76`, `constants.py:16-18`) while the hop still needs them. | Hop tests read only the file fixture. `test_append_reads_closeout_not_admit_envelope`. |
| Wrong order | Pipeline YAML calls cursor before the branch exists; every run hits `require_cloud_api_backend`. | Branch test is the gate. YAML `model:` is added in the same slice only after that test is part of the diff. Interim uses team_dispatch (§3). |
| Install / hot reload | A new YAML is invisible until Stargate reloads pipelines. | No restart in this todo's implement closeout. Claim `landed`, not `live`. Validate with a schema/unit load of the YAML object, not `pipeline(op=validate)` against `:9999`. |

**Live surface**

| Call | file:line | Role |
|---|---|---|
| `is_cdp_dispatch_model` | `cdp_dispatch.py:63` | Pattern only. Cursor gets its own predicate; do not overload this one. |
| `normalize_frontier_wire_model` | `request.py:76` | cursor_sdk returns wire id beside cdp (`:88-89`). |
| `require_cloud_api_backend` | `libs/model_id/wire_resolve.py:239` | Still the cloud-only reject. Not modified. |
| `resolve_model` | `request.py:96` | Calls normalize at `:152`. |
| `run_admission_gate` | `admission_gate.py:74` | Early return beside `:91`. |
| `FrontierDispatchHandler.execute` | `handler.py:68` | Branch beside `:74`. |
| `run_cdp_dispatch` | `cdp_dispatch.py:366` | Pattern for the sibling. Do not send cursor models through CDP. |
| `run_dispatch_loop` | `native_loop.py:142` | Must not run for cursor_sdk. |
| `is_cursor_sdk_generate_admission` | `admission.py:816` | Admits `model=cursor/…`. |
| `dispatch_cursor_sdk_generate` | `cursor_sdk_generate.py:303` | The sibling's callee. Tests monkeypatch it. Returns an admit envelope, not journal text. |
| `dispatch_cursor_sdk_generate_route` | `generate_wrap.py:226` | Not called by the sibling. Sets `response.status_code = 202` and returns `result` at `:611-612`. Requires `response: Response` at `:231`. |
| `write_repo_sidecar` | `cursor_sdk_deliverables.py:71` | Writes the closeout the completion path reads. Call site `cursor_sdk_closeout/delivery_assembly/change_set_resolution.py:146`. Path `tmp/reviews/closeouts/{dispatch_id}.md` (`:57-58`). |
| `team_dispatch` (HTTP) | `route.py:661` | Calls the generate route at `:887`. |
| `team_dispatch` (MCP) | `services/mcp-server/tools/frontier.py:294` | Interim only. Relays `model` and `model_knobs` at `:1088-1109` to `/api/v1/team/dispatch` (`:1118`). |
| `pipeline` tool | `services/mcp-server/tools/pipeline.py:690` | `op` enum includes `run`, `async`, `result`, `cancel`, `stats` (`:691`). |
| `_pipeline_async` | `pipeline.py:285` | Returns the Stargate JSON body (`:339-346`). |
| `_pipeline_result` | `pipeline.py:565` | `wait_seconds` clamped at `:568` to `_RESULT_MAX_WAIT` (`:50`, 60). |
| `admit_dispatch` (tracker register + 202) | `pipelines_dispatch.py:301` and response `:474-491` | Add `poll_hint` here. |
| `register_execution` | `lifecycle.py:43` | Capacity `:66-87`; link schedule `:123-124`. |
| `_schedule_dispatch_admit` / `_do_dispatch_admit` | `dispatch_admit.py:27` and `:48` | Link insert condition and swallow. |
| `admit_dispatch` (bus row) | `libs/agent_bus_store/db/threads_atomic.py:408` | Insert at `:448`. |
| `build_poll_hint_wait` | `handoff_response.py:116` | Shape to copy onto the 202. |
| `derive_status` | `wait_status.py:521` | Delay vs terminal. |
| `read_cortex_file` | `pipelines/maestro_induct/v1/handlers/_clients.py:107` | Durable read/sha. Writer is the same root, new function, not `cortex_dispatch` (`:76`). |
| `render_producers_section` | `checkpoint_projection_producers.py:212` | What the checkpoint shows after a link exists. |
| `is_other_models_pool` | `model_pools.py:25` | Projector reject for `cursor/claude-*`. |

### L3 — Induction

**files_expected**

- `libs/claude_bundles/journal_lesson.py` — `check_induction(text, *, journal_text) -> ok | register_not_carried`.
- `libs/claude_bundles/test_journal_lesson.py`
- Hop template act 6 (`operator_proxy_mission.py:138`): the seat's `TYPE: SEAT_REGISTRATION` gains one line `induction: RULE <entry id> — <one sentence in the seat's own words> · specimen <turn or sha>`. The GIW stamp `build_seat_registration_stamp` (`libs/hop_handoff/body.py:135-165`) stays as it is. That stamp is a registry projection (`source: cursor-auto`), not the seat's own words. Putting `induction:` there would invent a sentence the seat did not write.

**Behavior**

- Parse `induction:` from the seat's registration body.
- The cited entry id must be a `##` heading in **this role's** journal (`cortex://notes/system/maestro/journal.md` for maestro). The specimen token must occur in that entry's body.
- Failure returns `register_not_carried` from `check_induction`. Do not call `cortex(tool=friction)`. The friction category enum does not include `register_not_carried`. Mapping this token onto `lesson_gap` is an H3 follow-up, not this slice.
- Acceptance 4 ships test-only. `check_induction` has no production caller. `seat_registration_gate._is_seat_registration` (`libs/agent_bus_store/seat_registration_gate.py:61`) classifies `TYPE: SEAT_REGISTRATION` for a 422 gate; it does not read `induction:` and this todo does not add that read. The hop template still tells the seat to write the line. A missing registration line is `register_not_carried` when a test calls `check_induction`, not a crash, and not a 422 from the gate.

**Acceptance criteria**

| Criterion | Test |
|---|---|
| Acceptance 4, test-only. Citation whose entry id and specimen are in the maestro fixture → ok. No production caller is added. | `test_induction_ok`. |
| Missing entry id, or specimen absent from that entry → `register_not_carried`. | `test_induction_missing_entry` / `test_induction_missing_specimen`. |
| A line that only exists in another role's journal text does not satisfy a maestro check. | `test_induction_does_not_read_other_role_journal` passes two fixtures and asserts the other is unused. |
| Acceptance 5. This slice does not write `runbook:maestro-loop` or the handoff. | No cortex client in the test module. The proposed text is §4 of this plan. |

**Breakage matrix**

| Change | Breaks when | Test |
|---|---|---|
| Checker uses the whole file as one blob | A specimen from an older entry satisfies a newer id. | `test_induction_missing_specimen` puts the specimen under a different heading. |
| GIW stamp required to carry induction | Every hop fails closed because `build_seat_registration_stamp` has no seat sentence. | Test the seat-authored body, not `build_seat_registration_stamp`. |
| Friction enum | `friction(category=register_not_carried)` is rejected by the tool schema. | Observable is the line token. No friction call in the test. |
| No production caller | A bad induction is silent on the live hop, because `runner.py:469` does not call `check_induction`. | Stated: acceptance 4 is test-only. `test_induction_ok` covers the function. This todo does not wire the gate or the hop renderer to it. |
| Ordering | L3 runs before any `RULE:` entry exists, so every induction fails. | L3 ships after L2. The test uses a fixture entry, not the live journal. |

**Live surface**

| Call | file:line | Role |
|---|---|---|
| Seat registration instruction | `operator_proxy_mission.py:138` | Add the `induction:` line to the instruction. Not a checker. |
| `_is_seat_registration` | `libs/agent_bus_store/seat_registration_gate.py:61` | Production classifier. Not extended in this todo. |
| `build_seat_registration_stamp` | `libs/hop_handoff/body.py:135` | Not modified. |
| `body_has_disposition_type` | `disposition.py:33` | Not the induction gate. Induction is on `TYPE: SEAT_REGISTRATION`. |
| `parse_journal_entries` | `libs/maestro_induct/parse.py:230` | Usable to split entries by `##` heading for the specimen check. It does not know facets. |

## 3. L2 interim vs pipeline

Until `test_cursor_model_skips_cloud_backend` is green, a `lesson:` DISPOSITION is handed to one `team_dispatch` generate:

- `op=generate`, `seat=cursor-sdk`, `lane=B`, `contract=freeform`
- `model=cursor/composer-2.5`
- `model_knobs={"fast":"true"}` (`frontier.py:332`, relay `:1095`)
- `work_key=todo:journal-projection`
- one call per disposition turn id

That call does not append. The seat reads its closeout and is the only writer until the marker exists. The seat's one append of the first verified interim closeout includes line-start `projector_live: <dispatch_id>` in that same write. `append_journal_from_closeout` does not run in this window. Turn-id dedupe is not a second writer.

After that test is green and the marker is present, the production trigger is `pipeline(op="async", pipeline_id="journal-projector", result_delivery={bus_thread: <maestro lane>, …}, messages=[…])` (`pipeline.py:773-793`). The 202 carries `execution_id` and `poll_hint` and is not a journal entry (§2 L2). The seat polls with that hint unchanged. On `complete=true` the seat starts `journal-projector-commit` and does not append. `append_journal_from_closeout` reads the closeout and writes the entry. It does not write `projector_live:`.

One appender at a time (Q4). Before the marker: the seat. After the marker: `append_journal_from_closeout`. The seat appends again only when that handler has failed.

## 4. Cortex changes (proposed text, H3)

Not written by this leg. Apply only after H3 PLAN, lean-verifier VERDICT, and ACK by web-anthropic (acceptance 5). Quotes are from `cortex://notes/runbooks/maestro-loop.md` read_sha256 `e758de6dfe94d7371c9ea6dbd3f9d3d6c3097ba52fb434c8257e72a524cec815` and the handoff/journal shas in §2.

### Step 2

**Carries today** (whole step): poll completion, `get` the turn (`tool_call_count`, `usage`, `source_ref`), closeout path `workspaces://…/tmp/reviews/closeouts/<dispatch_id>.md` offset 0 limit 40, the 2026-10-02 note that the older `cortex://notes/system/threads/<child>-cursor-sdk-closeout-<dispatch_id>.md` path 404'd, propagate reads only `executions[].status` and sha fields, overflow files offset 0 limit 40, lean verifier when the verdict is not in those lines, never paginate past line 40.

**After:** the paragraph above, unchanged, plus this sentence at the end: "A journal-projector run is polled with `pipeline(op=result, execution_id, wait_seconds)` taken from its `poll_hint` (clamp 60, same as `pipeline.py:568`). `predicate_unmet`, `no_new_turn`, and status `running` are not failure; re-call the hint. `provider_error` in the closeout's first lines, `TrackerCapacityError`, and a failed durable append are failure. The hop reads the journal file, not the tracker result."

**Drops:** none.

### Step 6

**Carries today:** `friction_close` only when the fix is LIVE, with `assertion_id` and `changed_paths`. When a leg breaks a rule this file states, append that trajectory under Appendix and leave one specimen id on the rule. "The induction question asks for the rule plus one specimen from this handoff's revision chain." REPLACE the standing handoff CURRENT STATE block at every leg boundary (`fs(replace)`, never prepend; head grew 166→482 lines by 2026-10-08). HOUSE RULES stay at the top of the head. Pass `expected_sha256` from the last `written_sha256`. The hop body does not copy this block: it renders HOUSE RULES, one live leg projected from bus state, and pointers (12286#2881 point 3).

**After:** same paragraph, with the induction sentence replaced by: "The induction line on `TYPE: SEAT_REGISTRATION` cites one `RULE:` entry id and one specimen from this role's journal (`cortex://notes/system/maestro/journal.md`). A missing entry or specimen is `register_not_carried`." And the hop-body sentence extended by: "it also renders `## Lessons in force` and `## Register`." Add: "Until the journal contains `projector_live:`, this seat is the only appender, in the same step as the handoff replace, when the DISPOSITION has a line-start `lesson:`. Interim team_dispatch does not append. This seat writes `projector_live:` inside the append of the first verified interim closeout. After that line, this seat does not append; `append_journal_from_closeout` does, from the closeout file. This seat appends only when that handler failed."

**Drops:** the specimen source "this handoff's revision chain". Specimens move to the journal entry. `friction_close`, the appendix-specimen rule, the replace-never-prepend rule, `expected_sha256`, and "do not copy the CURRENT block" stay.

### Step 9

**Not replaced wholesale.** The step carries KERNEL-H counters (closeouts ≥6, skill reload, spill, summarize-not-quote, tool-call thresholds, 3rd result over 5 KB), idle definition, hop model `cdp/opus-5.5-extra`, replace CURRENT STATE before the hop, idle hop-and-stop, PARK plus one send_later, compact fetch, fleet_liveness filters, closeout offset 0 limit 40, E8/R3 prompt authorship, 290s sleep, Cowork bash timeout, the Arms paragraph, and the falsifier "a second fetch_unread while nothing is in flight".

**Span that changes** — the no-house sentence: "When no house is live (specimen: 2026-10-08 11:46Z onward), the seat-side rules above apply. On wake with a live house, read its brief first; read the handoff head and journal only if the brief names a gap (a:38890)."

**After:** "When no house is live (specimen: 2026-10-08 11:46Z onward), the seat-side rules above apply. Until `projector_live:` is in the journal, a DISPOSITION with a line-start `lesson:` gets one seat-appended entry in the same step as the handoff replace. The seat writes `projector_live:` inside the first verified interim closeout append. Interim team_dispatch does not append. After that marker, `append_journal_from_closeout` writes the entry from the closeout file and this seat does not, unless that handler failed (`provider_error`, capacity, or durable-append failure) — not because a poll is still `predicate_unmet`. On wake with a live house, read its brief first; read the handoff head and the rendered lessons only if the brief names a gap (a:38890)."

**Drops:** "read the handoff head and journal only if the brief names a gap" loses the bare "journal" read. The gap read becomes the handoff head plus the rendered lessons. a:38890 stays.

### Step 9a

**Carries today:** hop call `agent_bus(hop, …, desired_model=cdp/opus-5.5-extra)` between legs; K22/K18/K21 history; successor first acts `lane-act-gates`: skill reloads → runbook Refuse and Steps via `fs md_read` (quote read_sha256; Appendix only for a cited K-row) → handoff head offset 0 limit 50 → journal offset 0 limit 40 ("standing rules and the newest entry") → `fetch(last=3)` → `mark_read` → `send` `TYPE: SEAT_REGISTRATION` with the birth record and `seat = Cowork CSE https://claude.ai/cowork/cse_<id> · registration_id <reg>`, plus the cse_ / `cse_session` / CURRENT STATE / `thread_get.cse_chat_url` / `resolve_attended` rules (a:37788).

**After:** the same paragraph, with the journal clause replaced by: "read `## Lessons in force` and `## Register` in the hop body (they are computed at render). Open `cortex://notes/system/maestro/journal.md` only to read the specimen of one named RULE. Do not read offset 0 limit 40." The `SEAT_REGISTRATION` sentence gains: "and one `induction:` line citing a RULE entry id and a specimen from that journal."

**Drops:** "journal (`offset=0 limit=40`: standing rules and the newest entry; E-4)" as an arrival read. E-4's standing-rules-in-the-window behavior is the bug this todo removes. Skill reloads, runbook md_read, handoff head offset 0 limit 50, fetch, mark_read, the `seat =` line, and the cse_ resolution rules stay.

**Not edited, still contradictory until a later ACK:** step 8 still says the wake reads `cortex://notes/system/maestro/journal.md` ("loop + latest entry"), and it names `render_operator_wake_body` in `services/git_integration_worker/cursor_auto/operator_wake_body.py`. That step is outside the named keep-set for this proposal. Flag it on the ACK; do not silently rewrite step 8 or that renderer in the implement diff.

### 12286 standing-handoff head

**Carries today** (read_sha256 `1c6b8df814301f6862b2809bc6277722d07bfec0a8315e118149242461c6bb72`, first lines):

```
## SUNDAY RESUME — LEAN PROTOCOL (read first; gc1 2026-10-09 11:2x PT per Kaywan 11:22/11:23 PT)
1. Skills, then runbook § Refuse + § Steps via md_read (not the full file); handoff offset=0 limit=50; journal offset=0 limit=40.
```

There is no `## HOUSE RULES` heading. `house_rules_section` (`operator_proxy_hop_status.py:104-109`) therefore returns None on the live file.

**After:** insert `## HOUSE RULES` as the first heading. Item 1's journal clause becomes: "Lessons and Register are already in the hop body; open the journal only for one named specimen." Keep items 2–8 of SUNDAY RESUME (gc1 queue, lean dispatch models, VERDICT+shas, closeout limit 40, fetch_unread compact, no thread_get, hop desired_model, houses through gc1). CURRENT LEG / CURRENT STATE / First acts stay where they are and are still replaced as a block (step 6), never prepended.

**HOUSE RULES content:** move, byte-for-byte, the four journal sections whose headings are:

- `## STANDING RULE — lean verification (Kaywan 2026-10-09 11:22/11:23 PT; …)`
- `## STANDING RULE — fleet actions and permission refusals (Kaywan, 2026-09-29; …)`
- `## STANDING RULE — truncation watch (Kaywan, 2026-10-02 ~20:35Z; …)`
- `## STANDING RULE — master is safe to go live (Kaywan, 2026-10-05 ~03:45Z; …)`

Source file sha `dd50d7065087218be3ba5defab5f3de184cb9c605162cf1904820f4ef896bbd9`. This plan does not paste those bodies and does not reword them.

**Drops from the handoff head:** the arrival instruction `journal offset=0 limit=40` on line 2. Items 2–8 are not dropped.

### Journal restructure

**Carries today at the head:** purpose paragraph (Kaywan 2026-09-27 16:33Z; newest entry first; each entry dated, observed, then the rule), companion-file pointer, then the four STANDING RULE sections, then `## 2026-10-14 — entry 157 …`.

**After:** purpose paragraph kept, plus one sentence: "Standing rules live in the handoff HOUSE RULES. This file holds lesson entries only." The four STANDING RULE sections are removed from this file because they now live in HOUSE RULES. Entry 157 and older entries stay, unmodified (no rewrite of mid-bullet `**RULE:**`). New entries are prepended, with line-start `RULE:` and the source turn id. `projector_live:` is written by the seat inside the first verified interim closeout append, not by this H3 edit and not by `append_journal_from_closeout`.

**Drops:** the four standing-rule sections' presence in the journal. Their text is not dropped; it moves. Entry 157 is not dropped.

### Register card

`cortex://notes/system/maestro/register.md` is absent. Do not invent one in the H3 edit. The hop renders the absence until a later observed register delta (Kaywan correction, new vocabulary, changed refusal) produces a card of at most 8 lines.

### SEAT_REGISTRATION

Proposed addition is the hop instruction in step 9a and act 6, not a new field on `build_seat_registration_stamp`.

## 5. Test hazards

No test may construct a manager or HTTP client aimed at a production service or port. That includes Stargate `:9999`, `manage.sock`, `git_integration_worker`, the agent-bus socket, and `DEFAULT_CORTEX_URL` via `cortex_dispatch` (`_clients.py:76`). No test may signal a live process, call `manage`, or call `list_live_sdk_models` (`services/git_integration_worker/cursor_models.py:147`), which launches a bridge.

Hermit patterns that already exist and that new tests copy:

- `@pytest.mark.offline` and injected `fetch_turns` / `standing_handoff_text` (`test_operator_proxy_hop_status.py:18`, `:38`).
- `monkeypatch.setenv("DATA_DIR", tmp_path)` as in `test_cdp_dispatch.py:30-35`, if a cursor dispatch helper touches the inflight ledger. Prefer not calling the helper's I/O at all: monkeypatch `dispatch_cursor_sdk_generate`. Do not construct a FastAPI `Response`.
- `monkeypatch.setenv("CORTEX_FILES_ROOT", tmp_path)` for the append test. Never point it at the real files root.

`pipeline(op=validate)` and `pipeline(op=run)` against a running Stargate are out of the test plan.

## 6. Out of scope / follow-ups

- Widening `dispatch_cursor_sdk_generate`'s `contract` Literal (`cursor_sdk_generate.py:310-317`) to include `freeform`. Proposal only if a type checker rejects the sibling call. Not in the implement diff unless that call fails.
- Fleet, reviewer, and author journals and register cards. The mechanism is role-keyed. This todo populates the maestro journal only. Another specialist's journal starts at that role's first `lesson:` entry. No invented entries (Q2).
- `maestro-induct-v1` still reads the superseded thread journal (`fetch_journal.py:24`). Follow-up: point it at `cortex://notes/system/maestro/journal.md`. Not required for acceptance 1, which is the hop body.
- Step 8 and `render_operator_wake_body` still tell a wake to read the journal. Named on the H3 ACK; not in the implement diff unless the ACK adds it.
- `build_seat_registration_stamp` (`body.py:135`) stays a registry projection.
- Friction category enum does not gain `register_not_carried` here.
- K29 (e): should the async tracker persist results durably? Open. Kaywan called limited retention fragile. This todo does not change tracker storage. The journal file is the durable store. Capacity and TTL behavior stay as `lifecycle.py:66-87` and `constants.py:14-19`.
- `journal-digest` skill body is cited by the projector prompt (`Use the journal-digest skill`) and is not edited. If its emit shape cannot produce a line-start `RULE:`, that is a later skill-authoring change (install script if the skill SoT changes), not this diff.
- No second todo for the cursor_sdk branch (Q1).

## 7. Constraints (verbatim)

- **forward_only** (every instance and every seat it dispatches; Kaywan 2026-10-08/09: "we always go forward"): never move a shared branch backward. Forbidden: `git reset` (any mode) on master or any shared/lane branch; `git checkout -B` / `git branch -f` / `git switch -C` onto an existing branch; `git push --force` / `--force-with-lease`; `git rebase` of pushed or landed commits. Fresh worktrees branch from the local tip (`git checkout -b <new> master` or `git worktree add -b <new> <path> master`); never re-point master to origin (no fetch-then-`-B`/reset onto `origin/master`). Local and origin diverge ⇒ stop and report DRIFT; recover dropped work only by forward merge or cherry-pick onto the current tip.
- Never force git_integration_worker. Never pass wait_for_boundary. No restart, no push, no land. Never read env, /proc or secrets.
- `anthropic/*` is not a projector model, and never `cursor/claude-*`.
- No cortex writes from this leg. Proposed cortex text lives in the plan file.

## Implement closeout reminder

Before you post. For each change, name the input or state where it breaks (service down, concurrency, partial failure, wrong ordering, install or staging path) and the test that covers it. Change only what the brief names; anything else you think should change goes in the review request as a proposal, not in the diff. Run `git diff --stat <base>..HEAD` and account for every file. Once the request is posted, stop committing.
