<!-- frontmatter:skill
name: advisor-timing
description: Pause and consult at decision points before substantive work, consult dispatch, declaring done, recurring failure, or phase boundaries — read BEFORE choosing transport or editing.
-->
<!-- target:* -->
# Advisor Timing Discipline

## Principle

Intelligence allocation matters more at specific moments than uniformly. Most
implementation work is mechanical — the value of higher reasoning concentrates
at decision points. This rule encodes **when** to pause and consult; the
mechanism (Plan mode, MCP `advisor`, `/consult-implement`, subagent) is chosen
per the situation.

## Checkpoints

### 0. Before any dispatch (MANDATORY)

∀ **any** `team_dispatch` — including `op=handoff` with
**`seat=claude-cursor, contract=implement`** (bound implement handoff) — and any substantive `agent_bus` post/reply
that opens a consult: pause **before** the first call. Implement routing is
**NOT exempt**: a bound-implementation handoff is a dispatch and must complete
preflight exactly like a consult handoff.

**Trigger**: you are about to route ANY work off-seat — consult, review,
second opinion, hand off reasoning, **or hand off a bound implementation**.

1. If `cortex_boot` ran this session: the briefing card **Consult routing gate**
   (`_CONSULT_ROUTING_GATE`) is **binding**, not orientation fluff. Skipping
   `consult-routing.md` before a dispatch is a protocol violation — the same
   severity as skipping the packet protocol reads.
2. Complete the **mandatory preflight** (consult AND implement handoffs):
   1. Load skill: `consult-routing` (canonical slug — platform trigger; do not fs-read skill body) — transport + authority map
   2. `fs(workspaces, .cursor/rules/architecture-handoff-protocol.mdc)` — md_read § The Six Required Blocks (rule artifact — read)
   3. `fs(workspaces, .cursor/rules/handoff-dispatchers.mdc)` — § target seat (rule artifact — read)

   The protocol files live at **project** `.cursor/rules/` (no
   `universal-llm-gateway/` prefix), NOT under the repo's `.cursor/rules/`.
   Cursor IDE may also load `.cursor/skills/consult-routing/SKILL.md` (stub).
3. **Standard openers** (see consult-routing decision table):
   - **claude-web** → `team_dispatch(op=handoff, role=web-consult, packet_path=…)` + six-block packet
   - **claude-cursor** (fresh tier / IDE) → `team_dispatch(op=handoff, seat=claude-cursor, …)`
   - **hands-off API** → `team_dispatch(generate, role=reviewer)`
   - **thin implement ping** → `agent_bus(post, …)` — **not** handoff
4. **`agent_bus(reply)`** on an existing thread = **iteration/follow-up only** — not the
   standard opener for a substantive review consult. Thread continuity does not override
   handoff routing.
5. **Bug/friction tickets (investigate→execute + pass zoom-out)**: an actionable defect needing a fix
   cycle routes in **two stages** — **investigate + decide** (`seat=claude-cursor`
   from the IDE, or `role=web-consult` from web) to trace root cause, inventory touch points,
   and resolve design choice into a dense spec; **investigate close** distills `files_expected` /
   `acceptance_criteria` (+ `required_skills`) and records implement-ready + `spec_sha256`;
   **execute** default = `team_dispatch(op=generate, role=cursor-sdk, contract=implement,
   source_ref=todo:{slug})` (or web-native inline `fs` fix) only once attrs are distilled;
   `cursor-implement` / `web-implement` + `packet_path` = named fallback. **Pass zoom-out duty:**
   on every `type:bug` bus pickup or bug handoff to web/cursor, zoom out in the pass — grep the
   bug-class pattern service-wide, audit sibling touch points, and label secondary findings in
   closeout (`verify-now` | `flag-deferred` | `spin-ticket`; `None observed.` if empty).
   **Default:** a filed bug/friction → assume investigate unless the operator says mechanical-only
   or a dense implement spec already exists — do **not** make `cursor-implement` the first hop on
   a bug whose root cause or design is still open. `friction()` is the observation log only — it
   records a defect, it does NOT submit a ticket. See `consult-routing.md` § Codified bug reports
   → Pass zoom-out duty.

### 1. Before Substantive Work

∀ task requiring new code, architectural interpretation, or behavioral change:
pause after initial exploration (file reads, searches, context gathering) and
before the first write.

**Material-decision branch (before approach lock-in):** if the work changes a
policy/invariant, is hard-to-reverse, carries deadline/legal/financial exposure,
or is a close call among defensible options with reversal cost — read
`agent-skills/consensus-steelman-posture.md` §1, steelman every live option,
and use **`panel_dispatch(disposition=panel, …)`** when a hard trigger fires
(≥2 provider families via skeptic + reviewer). Adjudication +
`panel_adjudication_artifact` stay NON-offloadable on the calling seat.

**Trigger**: you have gathered context and are about to commit to an approach.

- ✅ Read files, trace call paths, understand the problem → **checkpoint** → write code
- ❌ Read one file → immediately start editing

**What to do at this checkpoint**:
- State the approach explicitly before acting (even a one-sentence plan)
- Apply the model-tier awareness check (`model-tier-awareness.mdc`) — if
  the task hits an escalate-to-Opus or step-down-to-Sonnet trigger,
  surface a tier note now (over-flag bias on escalation)
- If the approach has non-obvious trade-offs or touches >3 files: consult
  (`advisor`, Plan mode, or `/consult-implement`)
- If the approach is mechanical and obvious: proceed, noting you assessed and
  chose to skip consultation

### 2. Before Declaring Done

∀ task completion: pause before reporting success to the user.

**Trigger**: you believe the implementation is complete.

- Verify: do the changes actually satisfy the original request?
- Check: did you introduce regressions, violate invariants, or leave dead code?
- If substantive work was done: re-read the modified files and run quality gates
- Make deliverables **durable** before this checkpoint (files written, changes
  staged) — if a consultation happens here and the session ends during it,
  durable work persists

### 3. On Recurring Failure

∀ approach that fails ≥2 times on the same problem: stop and reassess.

**Trigger**: same fix attempted twice with the same class of failure, or
bouncing between two incompatible approaches.

- ¬ try a third variant of the same idea
- Instead: consult (`advisor` or `/consult-implement`) with the full failure
  context — what was tried, what failed, what the error says
- Surface a tier-escalation note per `model-tier-awareness.mdc` — prefer
  **`team_dispatch(op="handoff", seat="claude-cursor")`** (fresh IDE thread + Opus)
  over re-reasoning in the same polluted executor thread when MCP + IDE access matters
- The value here is escaping the executor's framing trap: a packet-booted consult
  pass often identifies root causes the executor missed

### 4. At Phase Boundaries

∀ multi-step task (3+ distinct steps): checkpoint at each phase transition.

**Trigger**: completing one logical phase and about to start the next.

- Verify the phase just completed actually achieved its goal
- Confirm the next phase's assumptions still hold given what was learned
- For `/implement-plan` workflows: this is the summary artifact + review step
- For ad hoc multi-step work: state what was done, what's next, and whether
  the plan needs adjustment

## Mechanism Selection

| Situation | Preferred mechanism |
|---|---|
| Simple approach validation (am I on the right track?) | State plan in text, proceed |
| Trade-off between 2-3 approaches | `advisor` MCP tool (concise, inline) |
| Stuck after 2 failures | `advisor` or `/consult-implement` |
| Architectural decision with cross-subsystem impact | Plan mode or `/consult-plan` |
| Pre-commit quality check on large changeset | `/consult-review` |
| Unknown territory requiring exploration | Subagent (per subagent-strategy) |
| Fresh perspective, tier upgrade (Opus), or escape executor framing — **from Cursor** | `team_dispatch(op="handoff", seat="claude-cursor", …)` per `handoff-dispatchers.mdc` § `cursor-claude` — applies to reviews, projects, and exploration alike |
| Consult **claude-web** (review, dialectic, architecture) | `team_dispatch(op=handoff, role=web-consult, packet_path=…)` — poll `agent_bus(wait)` |
| Hands-off synchronous review (no operator push) | `team_dispatch(generate, role=reviewer)` — poll `pipeline(result)` |

## Anti-Patterns

| Bad | Good |
|---|---|
| Read one file, immediately start editing 200 lines | Read → state approach → edit |
| Try 4 variations of the same broken fix | Stop at attempt 2, consult |
| Report "done" without re-reading modified files | Re-read, verify, then report |
| Skip checkpoint because "it's a small change" that touches 5 files | Small intent ≠ small impact; checkpoint anyway |
| Consult on every trivial change | Checkpoints are for non-obvious decisions; skip for rename/typo/format |
| Open substantive consult via `agent_bus(post/reply)` without `team_dispatch(handoff)` | `team_dispatch(op=handoff, …)` + packet file; `agent_bus` pointer-only |
| Use handoff for thin implement ping | `agent_bus(post, to=claude-cursor, …)` + spec/tags |
| Poll `pipeline(op=result)` after handoff | `agent_bus(wait)` from `poll_hint` — handoff has no `execution_id` |
| Override operator `team_dispatch` with `agent_bus`, citing the thin-ping row | Operator-named transport wins; obey it or stop and ask — never silently substitute |
| `cursor-implement` as the first hop on a bug with open root cause / design (friction 13571 → thread 1377) | investigate + decide (`cursor-consult`/`web-consult`) → distill attrs at investigate close → execute default `cursor-sdk` + `source_ref`; `cursor-implement` = fallback |
| Composer / `cursor-implement` authors its own dispatch-ready spec (`cortex://notes/system/specs/{slug}.md`) | Reasoning tier (`web-consult` / `cursor-consult` / Opus) authors spec + todo seed; mechanical tier executes — never the reverse (`handoff-packet-authoring.md` § Dispatch lifecycle) |
| Open a codified bug report with redesign / graph-walk before investigate/fix/report | Run the bug cycle first; secondary findings belong in the closeout |
| Treat a codified bug report as file+friction only (no fix) | Bound implement: investigate, fix, verify, report |
| Fix only the filed symptom on a bug bus pickup (no touch-point sweep / secondary findings) | Pass zoom-out duty: inventory touch points, bug-class grep, labeled `## Secondary findings` in closeout |
| Submit a bug ticket via `friction()` (observation log) | investigate→execute cycle; execute default = `cursor-sdk` generate + `source_ref` once attrs distilled; `friction()` only as grounding |

## Relationship to Other Rules

- **Subagent strategy** (`subagent-strategy.mdc`): governs *delegation* — when
  to hand off work. Advisor timing governs *consultation* — when to seek guidance
  while retaining control.
- **Plan mode**: advisor timing checkpoint 1 is a micro-version of Plan mode.
  Switch to full Plan mode when the decision is large enough to warrant it.
- **Model tier awareness** (`model-tier-awareness.mdc`): governs *resident
  tier choice* — when to recommend switching Cursor between Sonnet 4.6 and
  Opus 4.8. Fires at the same junctures as advisor-timing checkpoints 1 and 3.
- **Consult routing** (`.cursor/skills/consult-routing/SKILL.md` →
  `cortex://agent-skills/consult-routing.md`): governs *transport* — which
  dispatcher to use once checkpoint 0 fires. Deep matrix: `handoff-dispatchers.mdc`.
<!-- /target:* -->
