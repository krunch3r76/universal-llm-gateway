---
name: handoff-packet-authoring
description: Before staging/densifying/wrapping a todo, or authoring any team_dispatch handoff/implement/consult packet (the six-block skeleton) — read first. Covers Gate 1→2→3 and the spec-vs-packet distinction.
trigger_match_terms: ["handoff-packet-authoring", "handoff_packet_authoring", "handoff", "packet", "dispatch", "team_dispatch", "six-block", "consult brief", "dense spec", "implement packet", "source_ref", "spec vs packet"]
sot: workspace
---

# Handoff Packet Authoring

Stage → densify → wrap → `source_ref` dispatch. Default:

```text
team_dispatch(op=generate, seat=cursor-sdk, contract=implement, source_ref=todo:{slug})
```

Six-block authority: `architecture-handoff-protocol.mdc` § The Six Required Blocks. Human kickoff: `handoff-prompt-authoring`.
Framing: L3 annex § Framing house style.

## Spec vs packet

| | Spec | Packet |
|---|---|---|
| Path | `cortex://notes/system/specs/{slug}.md` | `tmp/reviews/{slug}-*.md` |
| Author | reasoning tier | dispatching seat |
| Holds | design + `<reasoning_trace>` | six XML blocks |

Kinds: **consult brief** (Gate-2, asks dense spec); **dense spec** (durable, hash only); **implement packet** (Gate-3,
materialized from attrs). Lifecycle: `consult → spec → implement`. Never dispatch spec raw. Pre-dispatch: line-anchored
grep for six tags; `validate_packet` authoritative.

Shared envelope vocabulary + kinds registry (four parts, pair rule, R1–R6 rows):
`cortex://notes/system/specs/contract-envelope-v0.md`.

## Dispatch lifecycle

Reasoning authors; mechanical executes. Read `dispatch_lane`:

| Lane | Who | Seat |
|---|---|---|
| `web-implement-packet` / `web-spec` | web-anthropic | `handoff web-consult` |
| `cursor-sdk-implement` | any | `generate cursor-sdk source_ref implement` |
| `cursor-mechanical` / `cursor-implement` | cursor | sdk default / IDE fallback |
| `path-sim-admit-gate` | lead + sdk | `/path-sim` |

Bugs: investigate first (`consult-routing` § Codified bug reports); execute via `source_ref`.

## Gate 2 — stage todo for densification

Stage ⇒ Gate-2 consult brief — ¬ Gate-3 implement. Stager retrieves only. Load `consult-routing` § Densify lane before
implement-ready.

**Triage:** `judgment_required` ⇒ densify; `mechanical` ⇒ skip w/ dense source; unset ⇒ blocked.

**Sequence:** verify lane → stub spec (`doc_template`) → consult brief → `handoff web-consult` (¬ `contract=consult`
param) → distill attrs → implement-ready + `spec_sha256`. Detail: L3 annex § Gate 2 — expanded sequence. **`required_skills`:**
catalog-registered slugs only (`config/skills.yaml`). Rule `*_ulg.mdc` stems (e.g. `skill-surface`) are not valid — write-time 422 `required_skills_uncatalogued`.

**Self-referential `spec_sha256` (friction, agent-bus:7323):** a document cannot correctly embed the hash of its
own final bytes — a trailer inside the spec body is necessarily stale/self-referential. Quote `spec_sha256` in the
dispatch/CLOSEOUT turn or the todo attribute, never as a trailer inside the document it hashes.

## Gate 3 — direct implement dispatch

```python
team_dispatch(op="generate", seat=cursor-sdk, contract=implement, source_ref="todo:{slug}", dispatch_thread_id="{arc-id}")
```

**Compliance:** todo resolves; implement-ready + `spec_sha256`; attrs populated; zero forks; `validate_dense_spec` passes.
Reject/wrap table: L3 annex. `wrap` non-remedial — fix todo, not hand-wrap.

**Nested-packet `todo:` front-matter collision (friction, agent-bus:7323):** when a
parent conductor dispatch already holds a non-terminal dispatch against
`todo:{slug}`, a nested child packet repeating that same `todo:` front-matter value
409s `CURSOR_SOURCE_REF_IN_FLIGHT` — the work-identity is a single-holder lock. Fix:
omit `todo:` from the nested packet's front-matter (prose mentions of the todo name
in the packet body are unaffected).

## Mandatory preflight before writing packet

```text
consult-routing · architecture-handoff-protocol.mdc § Six Required Blocks · handoff-dispatchers.mdc § target seat
```

Via skill triggers — ¬ `fs(cortex, agent-skills/…)`.

## Skill load resolution (handoff seats)

Web-anthropic: `inline_authoritative` (full body + sha256) **or** verified server resolution per slug — ¬ slug-only
`Use the <slug> skill` for code/workflow skills. Corpus fs = non-skill pointers only. Implementation detail: L3 annex.

## Hedges / reopen conditions (friction 24429)

`bind(execute_map) ⇒ preserve(uncertainty) ∨ lock(binds)`. Keep `[UNGROUNDED]`, thin-corpus warnings, reopen warrants.
`do_not_relitigate ≠ strip_hedges`.

## The Six Required Blocks

| # | Block | Holds |
|---|---|---|
| 1 | `<scope>` | target, path, selection mode |
| 2 | `<invariants>` | rules; skill refs + ≤15 task lines. Consult / light-bounded: include `reasoning-posture` (enrich auto-inserts if omitted). |
| 3 | `<task_guidance>` | work; **acceptance** for implement |
| 4 | `<corpus>` | pointers |
| 5 | `<mcp_capabilities>` | life-on/code-off or code plan |
| 6 | `<output_format>` | closeout shape |

Implement needs `acceptance` in `<task_guidance>`. Frontmatter `contract:` is **required**
on consult/light-bounded packets (checked AC — missing ⇒ dispatch reject at enrich).
Executor override in frontmatter;
silence ⇒ composer (`consult-routing` R1/R2). Primers + skeleton: L3 annex.

**G1 architecture consult (envelope R1) — inverted load.** Blocks 1–5 are priming; `<output_format>`
carries the load (¬ files/acceptance — that is the implement rotation). Packet fields + answer shape
(8 core + 5 conditional-by-kind; omitted conditionals OMITTED, ¬ N/A-filled; §8 Falsifiers MUST open
with the **Adjudication check** line):
`cortex://notes/system/specs/lane-architecture-consult-brief-template-v2.md` — specializes envelope
**R1** (semantic locator: registry URI + row id; ¬ row sha — W7).
Lane + gates: `abstraction-layering` § Gates G1 + § **Fable / CDP G1 lead preflight**.
**Skill floor (BINDING):** sealed `architecture-invariants` ∧ `ulg-architecture` before Fable/Opus
submit — judgment chips alone fail. **Post-harvest seeding:** stamp
`document:{slug}-architecture-consult` (`consult_kind=architecture`) +
`derived_from` from the work item (abstraction-layering § G1 skip / Stage 0 attach).

### Judgment-class closeout — resumable sidecar

`judgment_class(generate) ⇒ Block 6 requires a resumable sidecar` — the **curated** artifact is the continuity unit;
thread + closeout are audit trail. Bar: judgment calls **with rationale** · numbered deviations · open flags (mirroring
any `status=partial` rationale from the closeout) · header pointers to worker `thread_id` + closeout path marked
**escalation-only**. Resume cites the **sidecar alone** in `<corpus>`; open thread/closeout only if it proves
insufficient. ¬ a "continuity pack" triple — a required triple teaches resumers to re-linearize the thread.
Ranked S1–S4 by Fable (`agent-bus:6086`); falsifier: >~1 in 3 resumes escalate past the sidecar.

## Life-surface cortex-mirror gate

Shared SOT: `life-handoff-corpus`. Mirror to `cortex://ephemeral/handoffs/…`; cite in bus pointer. Life `<corpus>`:
cortex + entities + agent-bus only. Skills ⇒ inline bodies pre-push. Full checklist: L3 annex § Life-surface cortex-mirror gate.

## Naming + delivery

Default: `generate cursor-sdk source_ref implement`. Escape: `packet_path`. Web: priming checklist (L3). Cursor: arch
skill refs in Block 2. Bus post ≤25 lines.

## Authority

| Topic | Source |
|---|---|
| Six-block | `architecture-handoff-protocol.mdc` |
| Dispatchers | `handoff-dispatchers.mdc` |
| Routing | `consult-routing` |
| Admission | `frontier_consult/handoff.py` |

## L3 detail

`packet-detail-annex.md` — framing, Gate 2/3 expansions, CONFORM/CONVERSE, friction preflight, web priming, skeleton.
