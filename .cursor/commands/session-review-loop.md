# /session-review-loop

Multi-pass automated dialectic loop for **session review** using the primary `frontier_dispatch` tool on `openai/gpt-5.4-pro` (or adversarial rotation). Combines the dual-dimension review (code + session critique) of `/session-review` with the automated multi-pass loop, thread continuity, convergence detection, and MCP investigation of `/diff-review-loop`.

This is an **automated multi-pass review command** that builds a packet per the architecture-handoff-protocol and invokes the primary `frontier_dispatch` MCP tool, specialized for sessions that warrant both code review *and* retrospective critique of problem framing, investigation quality, decisions, user corrections, and alternatives.

- `architecture-handoff-protocol.mdc` — six-block packet, validation contract, documentation audit, closure
- `handoff-dispatchers.mdc` — `frontier_dispatch` (primary tool) per-pass invocation, polling, failure handling, thread-as-continuity
- `/session-review` — session narrative synthesis, dual review dimensions, packet structure for critique
- `/diff-review-loop` — loop mechanics, convergence signals, adversarial guardrails, per-pass triage/pushback

Prefer `/session-review` for single-pass Claude Web review (richer persona, full agent_bus dialectic). Use `/session-review-loop` when you want automated multi-pass pressure-testing with the primary `frontier_dispatch` tool, programmatic convergence, or adversarial cross-model review on a session that had meaningful pivots or user corrections.

## When to Use

- After a session with non-trivial decisions, investigation dead-ends, user redirections, or architectural choices
- When you want the session narrative pressure-tested by multiple passes / models
- When code changes were made and you want both invariant/code review *and* "was this the right problem/solution?" critique
- When you want automated loop + artifact without manual Claude Web thread management

## Invocation

```
/session-review-loop [model | adversarial] [passes:<N>]
```

| Argument | Meaning |
|---|---|
| omitted | `openai/gpt-5.4-pro` for all passes |
| `adversarial` | Alternate `openai/gpt-5.4-pro` ↔ `google/gemini-3-pro-preview` (pass 1 = openai) |
| `passes:N` | Hard cap (default 4, max 8) |

The command always reviews the **full current session** (narrative synthesis from conversation history). Code review scope defaults to current `git status` (modified files). You can combine with path/since if desired (parsed after model arg, same rules as `/diff-review-loop`).

Examples:

```
/session-review-loop
/session-review-loop adversarial
/session-review-loop adversarial passes:5
/session-review-loop openai/gpt-5.4-pro libs/ since HEAD~2
```

## Instructions

### 0. Resolve Args + Session Context

- Parse model/adversarial/passes exactly as in `/diff-review-loop` step 0. Default model: `openai/gpt-5.4-pro`.
- Synthesize **Session Narrative** from the current conversation history (use the exact structure from `/session-review` step 4). Be accurate, unsanitized, and include missteps, dead ends, user corrections, and open questions. This is the core substrate for Dimension 2 critique.
- Run `/diff-review` steps 0–3 (or equivalent) for the **code dimension** to produce:
  - Branch, HEAD, selection mode, file list, SLOC gate, `REVIEW_MANIFEST`, `INVARIANTS`, `EXCLUDED`.
- Stop conditions from both parent commands apply (empty scope, >150 files, SLOC violations, etc.).

Report resolved settings + one-paragraph session topic before building the packet.

### 1. Build Packet (Six Blocks + Session Narrative)

Create `tmp/reviews/${BRANCH//\//-}-session-review-loop-packet.md` with **all six required blocks** from `architecture-handoff-protocol.mdc`, adapted for dual-dimension session review:

**`<scope>`**: Branch, HEAD, selection mode, file count, session topic (one line), pass info.

**`<invariants>`**: Compact block from code review (≤50 lines, tagged).

**`<task_guidance>`**: Dual dimensions (copy/adapt from `/session-review` with frontier-mcp notes):

```
Review this session along two dimensions. The reviewer has full MCP tools and must investigate live workspace files and Cortex before forming findings.

## Dimension 1 — Code Review
[Exact text from /session-review Dimension 1, updated for frontier-mcp: "Read each manifest-listed file via fs(sandbox=workspaces...) before forming findings. Use observability for recent failures, cortex for prior decisions."]

## Dimension 2 — Session Critique
Critically evaluate the Session Narrative:
1. Problem diagnosis — root cause correct? Simpler framing available?
2. Investigation quality — dead ends handled well? Information gaps?
3. Decision quality — for each Key Decision: rationale sound? Alternatives truly considered?
4. User corrections/redirections — why did the agent reach the wrong path? Reasoning failure, scope misread, or missing context?
5. Solution appropriateness — right layer? Right scope? Durable or symptom-only?
6. Alternatives — better approaches available? Concrete proposals with rationale.

Start with the dimension containing higher-severity findings. Return findings from **both** dimensions.
```

**`<session_narrative>`**: The full synthesized narrative from step 0 (exact structure from `/session-review`).

**`<review_manifest>`**: The code review manifest. Include strong instruction:

```
The manifest lists files changed in this session. Read each live via:

  fs(sandbox="workspaces", op="read", path="universal-llm-gateway/<path>")

Do NOT rely on any inlined corpus (none is provided). Live workspace + Cortex + observability are authoritative. The manifest's HEAD is the review boundary for code changes.
```

**`<mcp_capabilities>`**: Use the `frontier_dispatch` (primary MCP tool) template from `handoff-dispatchers.mdc`, augmented with this session-specific guidance:

```
You have full MCP tools. Investigation is mandatory — do not limit yourself to the inlined packet.

Before findings:
1. fs(sandbox="workspaces", op="read", path="universal-llm-gateway/...") for every manifest file.
2. cortex(tool="search", arguments=...) for prior decisions/todos in the session domain.
3. observability(operation="recent-failures", params=...) and pipeline-trace if relevant.
4. For session critique, fetch the full thread via agent_bus to understand timeline of corrections.

**Important tool failure guidance**: If a tool (especially cortex, fs, or observability) returns an error envelope, do **not** retry the exact same arguments. Surface the failure, the exact arguments used, and the error in your findings, then continue with available context. This prevents long retry loops.

Cite every tool call inline as Evidence. For contract-affecting findings (events, APIs, runtime behavior), state required manual docs updates (docs/event-contracts.md is manual).
```

**`<output_format>`**: v1 structured shape per `architecture-handoff-protocol.mdc` § "Block 6: `<output_format>`". Both Code Finding and Session Finding shapes are required for this dual-dimension review. The frontier reviewer reads the protocol via `fs(sandbox="cortex", ...)` for the full schema; embed the compact form below in the packet so the reviewer has the field list at hand without a fetch:

```
v1 structured shape per `architecture-handoff-protocol.mdc` § "Block 6:
<output_format>". Required fields:

## Code Finding
  FindingID:    F<n>
  Severity:     Critical | Warning | Suggestion
  File:         path/to/file:Line–Line
  FileReadVia:  fs | absolute | not_read
  Concern:      <paragraph; cite invariant tag>
  Evidence:     <MCP calls and results — required>
  Operation:    replace | create_file | delete_file | delete_substring |
                replace_whole_file | replace_all_occurrences |
                plan_required | needs_info | deferred | blocked
  DependsOn:    F<m>[resolved | applied | approach=A]   # semantic only

For mechanical Operations: include an Edits block (column-0 SEARCH/REPLACE
fences, Occurrence=exactly_once, ApplyAfter) and a Verify list (commands
from the closed Verify Allowlist per protocol § "v1 Dispatcher Apply
Contract"). For paused Operations: required subfields per protocol §
"Paused operations".

## Session Finding
  FindingID:   S<n>
  Severity:    Critical | Warning | Suggestion
  Phase:       Investigation | Decision | Solution | User Correction
  Operation:   note | needs_info | deferred   (default: note)
  Issue:       <what went wrong>
  Alternative: <better approach>
  Evidence:    <MCP calls, cortex results, thread turns>

Reviewer MUST NOT emit: NewlineMode, FileSha256Before, ExpectedCount.

Severity preserved across paused states. Return ONLY findings. If nothing
in either dimension: "No findings."
```

**Optional blocks**: `<excluded>`, `<prior_pass>` (for iterations).

### 2–5. Initialize Thread, Loop, Triage, Convergence, Closure, Artifact

Follow the loop mechanics from `/diff-review-loop` steps 3–8 **exactly** (using direct `frontier_dispatch` calls), with these adaptations:

- Thread subject/summary/tags should reference "session-review-loop" and "session-critique".
- In per-pass user message: include the full `<session_narrative>` + refreshed manifest + current `<prior_pass>` summary of applied/rejected/surfaced findings.
- Convergence signals: inherit from loop + "Session review complete." or "No findings in either dimension."
- Adversarial guardrail applies (do not auto-converge on first-model "No findings").
- **Documentation Contract Audit** is mandatory before closure (events, APIs, runtime contracts from *either* code or session findings).
- Artifact: `tmp/reviews/${BRANCH}-session-review-loop.md` with sections for **Code Findings** and **Session Critique Findings** separately, plus full Iteration History, Documentation Contract Audit, and Session Narrative summary.

### 6. Final Output

After convergence or cap:

- Post final triage summary to the thread.
- Close thread with summary including applied/rejected across both dimensions.
- Write artifact.
- Report to user: thread ID, packet path, artifact path, convergence signal, high-level outcome (e.g. "3 code + 2 session findings applied; 1 session critique surfaced for triage").

## Rules (Inherited + Session-Specific)

- **Session Narrative must be accurate and unsanitized** — include dead ends, user corrections, and open questions. This is the primary input for Dimension 2.
- All code-review rules from `/diff-review` and `/diff-review-loop` apply.
- Session critique findings use "reject by context" (factual correction about what actually happened in the session) rather than "reject by rule".
- **Documentation audit is mandatory** for any applied finding that touches events, APIs, coordination, or runtime behavior — update `docs/event-contracts.md` or record why not needed.
- Max one `<need>` round per pass (if model requests additional context).
- Thread body is a table of contents — packet lives in workspace file; model reads via `fs`.
- Convergence requires a clear signal; stable disagreement on session critique is acceptable and should be recorded.
- Do not exceed pass cap. Do not sanitize the narrative for the reviewer.
- Source of truth: `agent_bus` thread.

This command gives frontier-powered, automated, multi-pass pressure on both the **code produced** and the **reasoning process** of the session.

(Adapted from `/session-review` + `/diff-review-loop` per user request. Uses primary `frontier_dispatch` tool. Model fixed preference: `openai/gpt-5.4-pro`.)
