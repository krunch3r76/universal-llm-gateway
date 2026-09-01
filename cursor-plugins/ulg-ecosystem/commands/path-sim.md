Path-sim — frontier question/solution-space search with **off-seat Q→A→R→implement**.

**Sole command-layer entry.** Machinery SOT: Use the `path-sim` skill (cascade · § Dispatch bindings · § Bundled dispatch · § Stage-A Gate-2 densify closeout · § R positions · § Todo lifecycle bind). This file is a thin wrapper — ¬ re-derive L0/L1/L2, R semantics, Gate-2 densify, or transport here.

`/path-sim-address` is a **retired redirect** to this command (bundled). Prefer `/path-sim`.

## When

| Condition | Route |
|---|---|
| **Request to change the codebase** (cursor-auto DIRECTIVE, friction fix, codework arc) | S4a → conductor (`work-item-seed-path`); `/layer` is gate-shape. Abstraction layering supersedes path-sim ratification windows for codework (`decision:abstraction-layering`). Non-codework search + fat-packet deepen lane stay here |
| Operator `/path-sim …` | This command → skill § Dispatch bindings |
| Friction / bug needing **codework** fix cycle | S4a → conductor (or re-admit if todo exists) — not this command |
| Non-codework friction / fat-packet deepen | Skill § Friction entry → **bundled** (or split Q-then-A when mid-cascade) |
| `judgment_required` todo ∧ (`arc_lane=path_sim` ∨ `dispatch_lane=path-sim-admit-gate` ∨ named `/path-sim`) | **Bundled** → skill § Bundled dispatch |
| `judgment_required` **codework** todo pickup | Re-admit conductor — not this command |
| `density_triage=mechanical` + dense spec + `implement_ready` | `/todo pickup` → `source_ref` implement only |
| `bind_status=settled` or `shipping` (≠ `recon_pending`) | `/address` peer — settled bind ship/advance (SOT: consult-routing § Address) |
| `bind_status=unsettled` ∧ `density_triage∈{judgment_required,recon_pending}` ∧ codework | Re-admit conductor (SOT: consult-routing § Address) |
| same bind ∧ (non-codework ∨ `arc_lane=path_sim` ∨ named `/path-sim`) | **Bundled** `/path-sim` — `workflow=path_sim` recon front-half |
| Operator wants to **skip** web R-admit **and** R-after | `check_requested=false` on todo (or say "no check") |
| Post-ship critique (standalone / non-path-sim work item) | `/work-item-review` — also **auto-fired** after path-sim Stage-B (skill § R positions) |
| Attended web densify | **`web-consult`** — not this lane |
| In-seat L1/L2 tables only (no fix arc) | Operator must say so explicitly — not the default for frictions |
| **Unattended full arc on the charter tick** | Same `/path-sim` entry + **`autonomous`** token (below) → lead sets todo attr **`attendance=autonomous`**, enrolls root with tag `charter-runner` **and** `enroll_charter_runner=true` (dual-key — tag alone is 422) → skill § Autonomous charter procession. No env-var arming: `CHARTER_ADMISSION_MODE` is retired, and the kernel resolves attendance per root from the todo attr (or an `attendance:autonomous` root tag). Attended is the default (omit the token). **Consult stops** (`CONSULT_PENDING`, depth-1) ride this attendance axis — skill cites design-doc stop vocabulary; tick enforces. ¬ prose-paste a charter brief; the token is the short form. |
| **Hang todo on tick / enroll `charter-runner` root** | Skill § **Tick enrollment — initial CHECKPOINT** — copy-paste template (byte-identical RESUME); mint scoreboard first. ¬ defer to agent-bus-discipline at enroll time. |

## Invocation

```
/path-sim friction {assertion_id}
/path-sim todo:{slug}
/path-sim a:{assertion_id}

# Attendance (orthogonal to detent) — omit = attended (default)
/path-sim friction {assertion_id} autonomous
/path-sim todo:{slug} autonomous
/path-sim a:{assertion_id} autonomous
```

Aliases for the autonomous token (same effect): `attendance=autonomous`, `--attendance=autonomous`.
Lead on autonomous: set durable todo attr `attendance=autonomous` (transient flag alone dies at the first window boundary) + enroll the root with `tags` including `charter-runner` **and** `enroll_charter_runner=true` (reserved enrollment dual-key — free-form tag alone returns 422 `reserved_enrollment_tag`; workers/reviews must use `charter-window` / `root:` / `window:` instead) — then stop; tick drives windows. The todo attr **is** the arming step (`resolve_attendance` reads it per root each tick); there is no runner-wide `CHARTER_ADMISSION_MODE` to confirm and no restart needed to change attendance. ¬ invent a long operator brief.

## Lead obligations (binding)

1. Load skill — scope-lock + detent before merits.
2. **Orchestrate recon** when breadth / unknown locus — skill § Recon: fire **Explore subagent** (`Task(subagent_type="explore")`; ¬ tool; UI Exploring ≠ Explore) per `cheap-recon` Tier-1; durable anchors sidecar; `rag(op=recon)` **optional** only when a named corpus scope covers the concern. **¬** block Q for missing RAG. **¬** Composer-as-recon / in-seat Grep spray. Lead Auto = orchestrator of the arc, ¬ default recon worker.
3. **Fire Q (CDP Fable L0)** — skill § Q-only · § L0 / Q pairing + operator-framed Q — **¬ in-seat L0**, **¬ default Q to Grok** on the bundled arc (Grok Q = closed-detent carve-out or explicit operator skip only), **¬ default Q to Opus CDP** (R-admit owns Opus — keep Q≠R). Primary: `team_dispatch(model=cdp/fable)` + `agent_bus.wait` from `poll_hint`; escape: CLI `claude-ai-sync-jupiter project-ask --model fable-5.1`. MCP `project_ask` is removed. Operator-framed only if **joint positive attestation** (`operator_framed=true` + `pinned_question` + resolvable `frame_uri`) ⇒ **bounded adopt-or-contradict Q** (`frame_verdict` + `frame_delta`) then A — **¬** `q_skipped`, **¬** frame-as-Q. Unframed/isolated (no stamp) ⇒ normal Fable Q then A — **¬ escalate to human**. **Order:** recon then Q.
4. **Dispatch A (Grok L1+L2 + Gate-2 densify)** as **Stage-A** worker — `model=cursor/grok-4.6`, `contract=light-bounded`; packet `tmp/prompts/path-sim-{slug}-dispatch-packet.md`; worker completes **Gate-2 closeout** (doc_template → fill → doc_validate → distill → implement_ready → STOP); **¬ implement, ¬ R inside the worker packet**. **¬ in-seat A + hand-implement**. **¬ Composer on A** (Composer = Stage-B implement only).
5. **Run R-admit yourself on CDP (lead-owned, default-on)** — only after Stage-A Gate-2 closeout passes skill § Auto-advance **→ R** row; skill § R positions · § R-admit CDP recipe · § R ≠ skeptic ≠ Gate-6 · § Docstring AC challenge (public-surface ⇒ AC names docstring conformance). **Primary:** `team_dispatch(model=cdp/opus-5)` + `agent_bus.wait` from `poll_hint` (`from_agent=web-anthropic`). **Escape:** CLI `claude-ai-sync-jupiter project-ask` (IF6 / satellite-direct). MCP `project_ask` is removed. Use the `claude-ai-cdp-navigation` skill when on the escape path (`--converse --no-uuid` on `/new`; ¬ endeavor/`cdp_ask_falsifiers` UUID). **¬ delegate R to the cursor-sdk worker**. **¬ skip because "mechanical".** **¬ treat long `running` as stalled/`cdp_unavailable`** — keep polling until proof/`archive_uri` or `failed`+`stall_stage` / DELIVERY FAILED (skill § R-admit poll). Skip only closed set: operator no-check (`check_requested=false`) or proven `cdp_unavailable` → log `reason_code` + friction.
6. **Dispatch implement (Stage-B worker)** only after skill § **Auto-advance checklist** passes — including `implement_ready_preflight(source_ref=todo:{slug}).admitted`; R-admit sidecar with CDP harvest URI (or allowed-skip evidence); if R-admit amended bind: re-validate dense spec + refresh assertion hash; `contract=implement, source_ref=todo:{slug}` (`skills=` includes `docstring-quality` + `event-instrumentation-discipline`).
7. **Run R-after yourself (lead-owned, default-on)** — after Stage-B ship: `/work-item-review todo:{slug}` on **`seat=cursor-sdk, model=cursor/grok-4.6, contract=light-bounded`** (skill § R positions substrate split — ¬ web-anthropic CDP, ¬ `xai/grok-*` artisan). Acceptance ledger · drift · docstring scan · event-instrumentation challenge. Same closed skip set as R-admit. **¬ skip because "implement looked clean".** Apply REVISE (direct patch or follow-up todo) before closeout.
8. **Auto-advance without operator gate** — Q OK ⇒ A+Gate-2; Gate-2 OK ⇒ R-admit; R-admit ADMIT ⇒ implement; implement OK ⇒ R-after; R-after done ⇒ closeout. **¬ pause for "go" between legs**. **¬ invent a fourth skip reason.**
9. Post-arc densify; `path_sim_self_certify` **only** for closed-set R-admit/R-after skip + friction already filed; friction/todo closeout per skill § Todo lifecycle bind.

Packet shapes, two-stage arc, Gate-2 closeout, monitor events: skill § Dispatch bindings / § Bundled dispatch — not duplicated here.

SOT: consult-routing § Address

## Anti-patterns

| Bad | Good |
|---|---|
| Lead in-seat L1/L2 + hand-implement on friction | Dispatch per skill § Dispatch bindings |
| **Put R as a phase inside the cursor-sdk worker packet** | **Lead fires R-admit on CDP** between Stage-A and Stage-B |
| Worker / lead self-certifies R as normal flow | Self-certify only `cdp_unavailable` \| `operator_no_check` → friction |
| **"Mechanical / simple bind ⇒ skip R"** | **Run CDP R — that claim is what R challenges** |
| **"`running` for N minutes ⇒ abort / self-certify / implement"** | **Keep polling** — wall-clock ≠ unavailable; only `failed`+`stall_stage` or proven lane-down is stall |
| Stamp `recon_waived` as if path-sim R ran | R ≠ skeptic ≠ Gate-6 — skill § R ≠ skeptic ≠ Gate-6 |
| Stage-B without R sidecar / CDP harvest / preflight admitted | Skill § Auto-advance checklist |
| Stage-A halt after A sidecar only (skip Gate-2 densify) | Worker completes Gate-2 closeout before STOP |
| Public-surface bind with silent docstring AC / R-after without scan | A densifies AC; R-admit challenges; R-after + lead closeout scan criticals=0 — skill § Docstring AC |
| Skip R-after after path-sim implement (no closed-set evidence) | Fire `/work-item-review` · `cursor/grok-4.6` — delivery half of external R |
| R-after defaulted to web-anthropic / CDP | R-after = cursor-sdk Grok; R-admit = web Opus (§ R positions) |
| Skip R because credits feel thin (no operator no-check) | Run R-admit + R-after; operator says "no check" to opt out |
| Path-sim without `todo:` | Skill § Friction entry first |
| Path-sim without RAG ⇒ incomplete / block Q | Tier-1 anchors when needed; RAG optional — skill § Recon |
| Fork arc tables into a second command file | Edit **this file** or the skill; `/path-sim-address` stays a redirect |
| `workspaces://` corpus only for web R | `cortex://` recon URIs |

## Skills

Use the `path-sim` skill · `cheap-recon-before-escalation` skill · `handoff-packet-authoring` skill (packets + Gate-2) · `implement-todo` skill (closeout) · `claude-ai-cdp-navigation` skill when R-admit runs · `event-instrumentation-discipline` skill (Stage-B + R-after) · `/work-item-review` for R-after (`cursor/grok-4.6`)
