# /diff-review-loop

Multi-pass automated dialectic loop for diff review using a frontier model
with MCP tools and `agent_bus` thread continuity. Variant of `/diff-review`
that automates the iteration loop instead of running single-shot dispatch.

This command is a **dispatcher loop adapter** on top of the
architecture-handoff-protocol:

- `architecture-handoff-protocol.mdc` — packet contract, validation, closure
- `handoff-dispatchers.mdc` — `frontier-mcp` per-pass dispatch shape, polling,
  failure handling
- `/diff-review` — task adapter (scope discovery, file list, SLOC gate,
  review manifest, 5 review categories, output format refinement)

`/diff-review-loop` adds: agent_bus thread continuity across passes,
adversarial cross-model alternation, programmatic convergence detection,
hard pass cap.

This command exists separately from `/diff-review frontier-mcp` because the
loop substrate (thread fetch, post-back, automated convergence) is new and
untested at scale. Once stable it may fold back into `/diff-review` as a flag.

## Differences from `/diff-review frontier-mcp`

| Aspect | `/diff-review frontier-mcp` | `/diff-review-loop` |
|---|---|---|
| Iteration | Manual: rebuild packet with `<prior_pass>` preamble | Automated: model fetches thread, posts reply, cursor pushes back |
| Continuity | Per-dispatch system+user partition | `agent_bus` thread carries continuity |
| Convergence | User-driven (rerun until satisfied) | Detected from reply content; capped by `passes:N` |
| Cross-model | One model per dispatch | Optional `adversarial` flag alternates openai ↔ gemini |
| State source of truth | Pipeline result + cursor's running notes | `agent_bus` thread (cursor + reviewer turns) |
| Cost | 1 dispatch per invocation | 1–8 dispatches per invocation (default cap 4) |
| Risk | Tested | Experimental (loop, posting discipline, convergence detection) |

For simpler reviews, prefer `/diff-review frontier-mcp`. Use this command when
the change is complex enough to warrant multi-pass pressure or when you want
adversarial cross-model review.

## When to Use

- Architectural change you want pressured through several passes before merge
- You want adversarial review (one model flags, the other rebuts)
- You want continuity across passes without rebuilding packets manually
- The base `/diff-review frontier-mcp` (single-shot) feels insufficient

## Invocation

```
/diff-review-loop [model | adversarial] [path] [since <git-ref>] [passes:<N>]
```

| Argument | Meaning |
|---|---|
| omitted or `openai` | All passes use `openai/gpt-5.4` |
| `gemini` | All passes use `google/gemini-3-pro-preview` |
| any token containing `/` and not a repo path | Full model id used for all passes |
| `adversarial` | Alternate `openai/gpt-5.4` ↔ `google/gemini-3-pro-preview` per pass (pass 1 = openai) |
| `path` (existing repo path) | Restrict to file/dir, same as `/diff-review` |
| `since <ref>` | Compare from `<ref>`; same alias rules as `/diff-review` |
| `passes:N` | Hard cap on passes; default 4; max 8 |

Examples:

```
/diff-review-loop
/diff-review-loop gemini
/diff-review-loop adversarial
/diff-review-loop adversarial passes:6
/diff-review-loop libs/transport_utils/client_factory.py since HEAD~1
/diff-review-loop adversarial libs/transport_utils/ passes:4
```

## Instructions

### 0. Resolve Args

Apply the same parsing rules as `/diff-review` step 0 (path-before-model
disambiguation, `since` alias resolution), with these additions:

- `adversarial` → `MODE_ADVERSARIAL = True`. Rotation:
  `["openai/gpt-5.4", "google/gemini-3-pro-preview"]`; pass `K` uses index
  `(K - 1) % 2`. Cannot combine with explicit model token.
- `passes:N` → `PASS_CAP = N`. Validate `1 <= N <= 8`. Default 4.

Defaults: `MODE_ADVERSARIAL = False`, `REVIEW_MODEL = "openai/gpt-5.4"`,
`PASS_CAP = 4`.

Report the resolved settings before proceeding (mode, model(s), path/since,
pass cap, cost note).

### 1–2. Scope, File List, Build Packet Blocks

Run `/diff-review` steps 1, 1a, 2 exactly as documented. Outputs needed:

- `BRANCH`, `HEAD_SHA`, selection mode
- `$SOURCE_FILES`, `$NONSOURCE_FILES`, `$AUTO_EXCLUDED`, `$DELETED`
- SLOC gate result (stop and prompt if violations)
- Six packet blocks (`<scope>`, `<excluded>`, `<invariants>`,
  `<task_guidance>`, `<review_manifest>`, `<output_format>`) per the protocol
  with the `/diff-review` MCP manifest override: no inlined source corpus.

Build `REVIEW_MANIFEST` using `/diff-review` step 3. The manifest contains
file paths, line counts, and changed-symbol headers only. It is a starting map;
the manifest's `HEAD` is the canonical review boundary, and live workspace
files read via `fs` are authoritative for current state.

Stop conditions inherited from `/diff-review`: empty scope, > 150 files,
SLOC violations.

### 3. Initialize Review Thread

Create the agent_bus thread that carries continuity across passes. The thread
is the source of truth for review state.

```
THREAD_RESP = agent_bus(tool="post", arguments=json.dumps({
    "slug": f"diff-review-loop-{BRANCH}-{HEAD_SHA}",
    "to": "frontier-reviewer",
    "from_agent": "cursor",
    "subject": f"Diff review (mcp dialectic): {BRANCH} @ {HEAD_SHA}",
    "tags": [f"project:{REPO_NAME}", "type:review", "agent:cursor", "mode:diff-review-loop"],
    "summary": f"Auto multi-pass MCP review of {N} files on {BRANCH}",
    "body": <thread-init body — see template below>,
}))
THREAD_ID = THREAD_RESP["thread"]
```

Thread-init body template:

```
## Diff Review (MCP Dialectic)

**Branch**:        {BRANCH}
**Head**:          {HEAD_SHA}
**Selection**:     {selection mode + path/since if set}
**Files reviewed**: {N} (excluded: {count})
**Mode**:          {single openai/gemini/full-id} OR {adversarial: openai ↔ gemini}
**Pass cap**:      {PASS_CAP}

This thread is the continuity substrate for an automated multi-pass review.

- Reviewer (frontier model) posts findings as a reply each pass.
- Cursor (dispatcher) posts triage decisions as a reply each pass.
- The thread carries continuity. The manifest's `HEAD` is the canonical review
  boundary; live workspace files read via `fs` are authoritative for current
  state.
- Loop ends on convergence signals from `architecture-handoff-protocol.mdc`
  (No findings / Review complete / adversarial concur), or at pass cap.

**Reviewer protocol** (every pass — restated here for thread legibility):

1. agent_bus(tool="fetch", arguments='{"thread": "<this>", "compact": true}')
   — read prior turns; treat as authoritative continuity.
2. Investigate via fs / cortex / observability / rag (per
   `handoff-dispatchers.mdc` § "frontier-mcp" `<mcp_capabilities>`).
   Read every manifest-listed source file via
   `fs(sandbox="workspaces", op="read", path="<repo>/<path>")` before forming
   findings. If live files appear to drift from the manifest's branch/head or
   diffstat, raise the discrepancy as Surfaced for Triage instead of redefining
   scope.
3. For any finding that changes event signals, event payloads, API surfaces, or
   runtime coordination contracts, state whether manual docs such as
   `docs/event-contracts.md` must be updated. `docs/event-contracts.md` is not
   generated.
4. Post findings as a reply (subject: "Pass <K> findings (<model-id>)").
5. ALSO include the same findings in your final response (belt-and-suspenders
   — if the post fails the dispatcher recovers from result.content).
```

Save `THREAD_ID` and report it to the user before entering the loop.

### 4. Pass Loop

Initialize accumulators:

```
PASS = 1
APPLIED = []; REJECTED_BY_RULE = []; SURFACED_FOR_TRIAGE = []; PENDING_USER = []
PASS_RESULTS = []   # (pass, model, exec_id, status, finding_count, convergence_flag)
```

Loop while `PASS <= PASS_CAP`:

#### 4.1 Pick model

```
PASS_MODEL = (
    ["openai/gpt-5.4", "google/gemini-3-pro-preview"][(PASS - 1) % 2]
    if MODE_ADVERSARIAL else REVIEW_MODEL
)
```

#### 4.2 Build per-pass packet

System prompt (durable across passes within this command run):
- Role contract sentence
- `<invariants>` block (from step 2)
- `<mcp_capabilities>` block — use the `frontier-mcp` template from
  `handoff-dispatchers.mdc` PLUS the agent_bus fetch+reply directive shown in
  step 3's thread-init body
- `<output_format>` block — `/diff-review`'s diff-flavored template (per its
  step 2), augmented to require BOTH a thread reply AND inline return

User message (per-pass):
- `<scope>`, branch, head, pass number ({PASS} of {PASS_CAP})
- `THREAD_ID` (so the model knows what to fetch)
- Every pass: `<review_manifest>` from `/diff-review` step 3. Refresh it only
  if the reviewed file set changed.
- Every pass: instruct the model to re-read live files via `fs`; packet
  metadata and prior thread turns can go stale. Live workspace files are
  authoritative for current state; the manifest's branch/head remains
  authoritative for the dispatched review boundary.

#### 4.3 Dispatch

Per `handoff-dispatchers.mdc` § "frontier-mcp" / "Dispatch":

```
EXEC = frontier_dispatch(
    messages=[{"role": "user", "content": <user message>}],
    boot="mcp", agent=None, model=PASS_MODEL,
    system=<system prompt>,
    reasoning_effort="high", caller_agent="cursor",
    generation_options={"max_tool_turns": 25},
)
EXEC_ID = EXEC["execution_id"]
result = pipeline(op="result", execution_id=EXEC_ID, wait_seconds=60)
```

Failure handling per `handoff-dispatchers.mdc` § "frontier-mcp" / "Failure
handling" (Stargate down, tool resolution, exhausted, failed).

#### 4.4 Recover the model's reply

The frontier reviewer returns findings inline in `result.content` (it does not
have `agent_bus` access to self-post). Parse findings from the pipeline result.

Post them to the thread on the reviewer's behalf:

```
agent_bus(tool="reply", arguments=json.dumps({
    "thread": THREAD_ID, "to": "all",
    "from_agent": PASS_MODEL,
    "subject": f"Pass {PASS} findings ({PASS_MODEL})",
    "body": <findings from result.content>,
}))
```

#### 4.5 Triage

Apply the protocol's validation contract per
`architecture-handoff-protocol.mdc` § "Validation Contract":

Five-bucket triage (Apply / Reject by rule / Escalate / Surface for triage /
Defer). Append to accumulators. Report per-pass:

```
Pass {PASS} ({PASS_MODEL}): {N} received → {M} validated
  ({K} Critical, {L} Warning, {J} Suggestion)
  Applied: {X}, Rejected by rule: {Y}, Surfaced: {Z}, Pending: {W}
```

For each validated finding, track whether applying it requires manual docs or
contract updates. Event vocabulary, payload, semantic, or failure-mode changes
require auditing `docs/event-contracts.md`; API surface and user-visible
runtime behavior changes require auditing the relevant docs. If no docs update
is needed, record the reason.

#### 4.6 Post pushback on the thread

```
agent_bus(tool="reply", arguments=json.dumps({
    "thread": THREAD_ID, "to": "frontier-reviewer", "from_agent": "cursor",
    "subject": f"Pass {PASS} triage",
    "body": <pushback body — keep it skim-able>,
    "after_turn": <model's turn number>,
}))
```

Pushback body should be skim-able — verbose pushback is the leading cause of
thread bloat. Cover: findings received count, applied/rejected/surfaced/pending
counts, list of applied with one-line descriptions, list of rejections with
rule citations, list of surfaced items with scope notes, list of open items
or "No open items — convergence candidate". Include a short `Docs contract
audit` line for applied changes: updated docs, pending docs, or not needed with
reason.

#### 4.7 Convergence check

End the loop if ANY:

1. Reply body contains `"No findings."` (case-insensitive line-start match)
2. Reply body ends with `"Review complete."` or contains line equal to `"Convergence."`
3. Reply body contains `"Concur with prior pass. No additional findings."`
   (adversarial-mode early convergence — only valid if `MODE_ADVERSARIAL`
   and `PASS >= 2`)
4. **Soft convergence**: every finding this pass is `Suggestion` AND APPLIED
   is non-empty AND no Critical/Warning remains unresolved across the run.
   Ask the user "Loop converged on suggestions only — continue?" Default to
   stop unless the user requests another pass.
5. `PASS == PASS_CAP` (hard stop)

**Adversarial guardrail**: in adversarial mode, never auto-converge on the
first model's "No findings." — require either two consecutive passes from
different models both returning "No findings." OR the second model returning
"Concur with prior pass. No additional findings." This protects against one
model missing what the other catches.

If converged: break and proceed to step 5.
If pass cap reached: break with `STABLE_DISAGREEMENT = True`.
Otherwise: `PASS += 1` and continue.

### 5. Close and Artifact

Per `architecture-handoff-protocol.mdc` § "Closure" and § "Artifact".

Before closing, complete a documentation contract audit for all applied changes:

- Update `docs/event-contracts.md` for event signal, payload, semantic, or
  failure-mode changes. This file is manually maintained, not generated.
- Update other relevant docs for API surface, coordination behavior, or
  user-visible/runtime contract changes.
- If no docs update is required, record the reason in the final thread reply
  and artifact.
- Do not directly edit generated RAG metadata artifacts; follow the generated
  artifact rules if a finding points at managed metadata.

Post final cursor reply with convergence reason; close the thread:

```
agent_bus(tool="close", arguments=json.dumps({
    "thread": THREAD_ID,
    "summary": f"Diff review (mcp dialectic) {BRANCH}: {APPLIED} applied, "
               f"{REJECTED_BY_RULE} rejected, {SURFACED_FOR_TRIAGE} triaged "
               f"({STABLE_DISAGREEMENT and 'stable disagreement' or 'converged'})"
}))
```

Write artifact at `tmp/reviews/${BRANCH//\//-}-diff-review-loop.md` with the
standard sections (Summary, Critical/Warnings/Suggestions, Applied,
Rejected by Rules, Surfaced for Triage, Documentation Contract Audit,
Iteration History — one row per pass with `execution_id`, model, finding
counts, convergence flag).

Source-of-truth per `handoff-dispatchers.mdc`: agent_bus thread.

## Diff-Review-Loop Specifics

Loop-adapter additions on top of `/diff-review`:

- **Adversarial mode** — alternate models per pass; per-pass rotation with
  guardrails against one-model "No findings" auto-convergence
- **Pass cap** — hard cap on dispatches (default 4, max 8)
- **Thread-as-continuity** — `agent_bus` thread is the source of truth, not
  the per-dispatch packet
- **Programmatic convergence detection** — phrase-matching plus soft-converge
  on suggestions-only and adversarial guardrail
- **Recovery from posting discipline failure** — if the model returns inline
  but doesn't post, dispatcher posts on its behalf with a recovered note
- **Documentation contract audit** — applied event/API/runtime contract changes
  must update manual docs such as `docs/event-contracts.md` or record why no
  docs update was needed

## Rules

- ¬ proceed if scope is empty (inherited from /diff-review)
- ¬ proceed past 150 changed files without user confirmation
- ¬ proceed past SLOC violations without user confirmation
- ¬ exceed `PASS_CAP` dispatches; ¬ exceed 8 passes ever
- ¬ auto-converge on first-model "No findings" in adversarial mode without
  the second-model guardrail
- Always audit docs/contracts before closure; `docs/event-contracts.md` is
  manual, not generated
- All other rules inherited from `architecture-handoff-protocol.mdc`,
  `handoff-dispatchers.mdc`, and `/diff-review`
