---
name: cheap-recon-before-escalation
trigger_match_terms: ["cheap-recon", "cheap_recon", "recon", "tier escalation", "escalate", "credit", "opus", "composer recon", "cost-aware", "investigation ladder", "skeptic", "panel", "ratification"]
related_skills: ["orchestrator-workflow", "consult-routing", "dispatch-workflow", "consensus-steelman-posture"]
---

# Cheap-recon before tier-escalation

Operator-directed practice. Home: `todo:cheap-recon-before-tier-escalation`.

## Core rule

`scarce_top_tier_credits ⇒ organize_cheap_first ∧ escalate(only_tightened_residual)`.

`∀ tier t : output(t) ∈ {resolved, tightened_residual} ∧ ¬re_ask_broad_question(t+1)`.

Lead adjudicates between tiers and densifies final spec. Tiers investigate; they do not author the final spec.

Two axes are orthogonal:
- **Axis 1:** cost-tiered recon ladder — depth + credit conservation; runs when root cause/direction is open.
- **Axis 2:** adversarial ratification panel — decision soundness for **material** decisions; fires at ratification inflection. Do not treat skeptic as ladder rung.

## Axis 1 — cost ladder

Escalate only on unresolved tightened residual.

1. **Tier-1 cheap recon** — default = source reads, greps, caller/line inventories, scope mapping, bug-class sweeps. On **service / runtime / MCP** investigations: Event Service silence or claim/evidence mismatch is a **first-class gap class**, equal to code-touch gaps (grammar SOT: `path-sim` § Events/gap probe — `EVENTS-PROBE`; ops via `observability` / `scripts/query-events` / `verify-tool-execution`; ¬ raw SQL in operator chat). Optional unattended cursor-sdk lane (`contract=light-bounded`) — **model split**: pure mechanical inventory (grep/list/caller inventory only, no judgment) → `model=cursor/composer-2.5` (Composer OK); **investigate-emphasis** (root-cause / judgment / suggest / densify inputs) → `model=cursor/grok-4.5` (do not default Composer). Output: facts-only anchors sidecar with open forks.
2. **Tier-2 cross-family filter** — `team_dispatch(op=generate, role=reviewer, model=openai/gpt-5.5, mcp=false)` with corpus pre-staged on dispatch thread. Scope to 3–4 focus areas. Verdict ∈ `ADMIT | ADMIT_WITH_AMENDMENTS | RETURN_TO_DESIGN`. Cross-family disagreement is signal.
3. **Tier-3 credit-gated final** — Opus/Fable only for residual that Tier-2 cannot settle. Ask operator first. **Substrate:** Fable → web-anthropic-cdp only; Opus → CDP preferred (cortex-packaged) **or** `seat=cursor-sdk model=cursor/claude-opus-5` when live-source browse is required. **¬** `model=anthropic/*` API (`decision:anthropic-family-dispatch-substrate`).

`tier2_makes_tier3_rare`.

## Optional RAG recon (`rag(op="recon")`)

RAG recon is optional/speculative, not default Tier-1 authority. Hits are candidate leads; absence is never proof of absence.

Use primary `rag(op="recon")`, not dispatch overflow, when a scope-guarded sweep is useful. One concern = one theme; each theme lists explicit sibling scopes. Never query bare `workflows`.

```json
{
  "label": "todo:<slug>",
  "themes": [{"name": "<concern>", "scopes": ["<scope-a>", "<scope-b>"], "queries": ["<q1>", "<q2>"]}],
  "durable_sink": "auto"
}
```

Semantics:
- Sidecar = facts-only anchors handoff at `cortex://notes/system/recon/<label>/<theme>.md`.
- **`durable_sink=cortex`** when recon feeds web-anthropic / Fable / path-sim consults — cross-seat readers use `fs(cortex)`; default `auto` also works when cortex is reachable.
- Tags `RELEVANT/MARGINAL/SKIP` are candidate evidence, not resolved claims.
- Mandatory `## Discards` section: one `- <id-or-path> — <reason>` line per SKIP anchor (≤1 sentence); `_None._` when no SKIPs. Auto `rag(op="recon")` derives one line per `[SKIP]` query.
- Escalation/densify readers: scan `## Discards`; you may veto an individual SKIP by pulling the item — no recon re-run required.
- **Consume `source_manifest`** — each theme result includes compact `source_manifest` (query → `md_path` + sources `{label, line, lead}`) and the sidecar embeds `## Source manifest`. Prefer this index for targeted `md_read` / line jumps; ¬ linearly re-read the whole Results body when the manifest already names the anchors (`_rag_recon_manifest.py`).
- `durable_sink=auto` probes cortex→filesystem→null; `cortex` errors rather than faking URIs; `null` persists nothing. Check `fallback_used` / `warning` before citing.
- **Overflow:** oversized recon sidecars / markdown tool results may return an **md_list-equivalent section tree** (`kind=markdown_structure`, `sections[]`) with selective `fs(md_*)` hints **before** falling through to opaque `rs_xxxx` + `retrieve(id=…)`. Prefer section navigation; full retrieve is last resort (`response_overflow_manifest.py` / `agent_skill:fs`).

Scope set for this practice: `{workflows, code_transformation, constitutional_ai, small_llm_prompting, software_agents, research_small_llm, knowledge_systems}`. Content-hash dedup may place canonical primaries under sibling scopes; this is working as designed, not a re-embed need.

If delegating RAG recon unattended: mechanical multi-scope inventory → Composer (`model=cursor/composer-2.5`); investigate-emphasis RAG recon → `model=cursor/grok-4.5`. Enumerate each scope as a discrete numbered call (`S1..Sn`) and include `execute each once ∧ never-repeat ∧ never-default-scope`. Set `max_tool_turns`; cursor-sdk workers are not safely cancellable mid-flight. For ≤~7 scopes, lead inline may be cheaper than babysitting.

## Axis 2 — material-decision skeptic/panel

`material_hard_trigger ⇒ run_skeptic_by_default` at ratification inflection, after design is shaped and before commit. Hard triggers are from `consensus-steelman-posture`: policy/invariant change, many/hard-to-reverse rows, legal/financial/deadline exposure. Routine/mechanical decisions skip.

Reviewer→skeptic is load-bearing: reviewer tightens and often assumes preconditions; skeptic asks for the one test that kills the design. Default minimum panel = `{reviewer: openai/gpt-5.5, skeptic: xai/grok}`. Independent family = distinct provider. Third family (e.g. gemini synthesizer/tiebreaker) is optional/economics-gated; promote to routine only if it overturns or materially sharpens ≥1 in N no-split panels.

Skeptic must produce a **decisive falsifier**: concrete measurable test, explicit pass/fail, and implication. Reasoned objection alone is insufficient.

### Skeptic dispatch mechanics — dual path (pick by evidence need)

The default `role=skeptic` model (xai/grok-4.5) is **MCP-capable**, and `team_dispatch` omit-`mcp` defaults tools-**on**. Do not cargo-cult `mcp=false` — pick the path by whether the falsifier cites live files.

| Situation | Path | `mcp` | Notes |
|---|---|---|---|
| Axis-2 densify/ratification citing live files | **MCP-ON** (default) | `mcp=true`, `max_tool_turns≥15` | Explicit `mcp=true` (not silent omit) so intent is visible in the transcript; keep the fat design as the **latest** admit-time turn on the dispatch thread; pre-stage `FILE_EVIDENCE_PATHS` candidates; the skeptic may `fs`-verify/extend them. |
| Self-contained inline packet (4728 shape) | MCP-OFF | `mcp=false` | Entire decision in the admit-time latest turn; forbid "read thread"/`agent_bus(get)` instructions there; pre-stage evidence paths for echo (no live discovery). |
| Non-code personal/legal/financial lane | MCP-OFF | `mcp=false` **always** | `consensus-steelman-posture` §8; the personal corpus must not gain tools. |

Default (MCP-ON): `team_dispatch(op=generate, role=skeptic, dispatch_thread_id=<thread>, mcp=true, max_tool_turns=15, contract=light-bounded)`.

**Pointer-overwrite hygiene (F3, threads 4732/4733):** the admit-time prompt is the dispatch thread's latest turn (`read_latest_dispatch_thread_body` → `turns[-1].body`), and a generate pointer posts onto that same thread in single-thread Q/R mode. Do **not** re-generate against a thread whose latest turn is a pointer — under `mcp=false` the skeptic reads "read thread", cannot fetch, and defers (deferral theater). Keep the fat design as the latest turn, or use `split_thread=true` / a fresh single-turn thread for re-dispatch. Code-level fix (whether skeptic re-dispatch should mint a split result thread by default) is a tracked optional follow-up, not resolved here.

Verify resolved model is xAI/grok. Poll per dispatch response (`agent_bus(wait)` for on-behalf thread delivery). To add skeptic after reviewer, dispatch only skeptic and cite reviewer execution as second family; avoid redundant paid reviewer re-fan. For a full roster, use `panel_dispatch(...)`.

Every skeptic prompt must append this footer verbatim:

```text
End your reply with a machine-readable evidence stamp. On its own line write exactly:
FILE_EVIDENCE_PATHS:
then on each following line one BARE resolvable path your falsifier's pass/fail test actually
depends on — scheme-prefixed (e.g. cortex://notes/system/specs/foo.md) or scheme-prefixed workspaces: / cortex: /
ws: — one path per line, NO markdown bullets, NO leading "- ", terminated by a blank line. If
your falsifier genuinely rests on no file evidence, instead write the single line
grounding_mode: reasoning_only (the admission gate treats this as ungrounded and will block —
use only when no path applies).
```

Parser constraints: bare paths only; bullets fail as `skeptic_evidence_unresolved`; allowed schemes `workspaces:` / `cortex:` / `ws:`; non-file tokens (`agent-bus:`, `spec_sha256:`, `execution:`, `assertion:`, `todo:`, `decision:`) are skipped; block ends at first blank line. `grounding_mode: reasoning_only` honestly opts out and still blocks admission. Post-cutoff missing-footer ratifications use remediation ladder: ministerial lead reground → independent/skeptic attestation → full re-run; blanket grandfather rejected.

`FILE_EVIDENCE footer alone ⇏ implement admitted` — lead must stamp `skeptic_ratified` citing that turn (or a designated `gate6_ratification_uri` turn, or hash-matched `recon_waived`); recipe in `implement-todo` § 3b.

Lead non-offloadable duties from `consensus-steelman-posture` Guard 2: live-options steelman, decisive-falsifier adjudication, and panelist Cortex-write review. `consensus_disposition=panel` requires lead-authored adjudication artifact; absent ⇒ `steelman-only`. Never fabricate disposition.

## Invariants

- `candidate_re_derive`: `∀ recon_output : labeled(candidate) ∧ ¬claimed(resolved)`. Higher tier expands/re-derives; it never rubber-stamps.
- **Scope guard:** `rag_recon_for_this_practice ⇒ scope ≠ bare("workflows") ∧ scope ⊆ canonical_composite_set`; run one search per named scope.
- **Anchors handoff:** preserve `{important_information, intentions, decisions, unresolved_questions, discards}`; mandatory `## Discards` lists every SKIP anchor (id/path + ≤1-sentence reason); never drop unresolved questions.
- **Recording posture:** objective recon facts (paper/scope/tag) may be FOL-over-NL; narrative stays pointer+sidecar. Do not replace NL retrieval anchors with symbols only.

## Corpus anchors

Navigation: `cortex://notes/system/threads/cheap-recon-rag-navigation.md`. Synthesis: `cortex://notes/system/threads/cheap-recon-rag-synthesis.md`.

| Concern | Anchor | Mechanism | Read at |
|---|---|---|---|
| Tiered interface | `arcs-agentic-retrieval-code-synthesis` | Small/Medium/Large accuracy-latency trade-offs | Tier-3 / lead |
| Deferral threshold | `frugalgpt-cost-routing` | scoring + τ_i stop/escalate | Tier-2 + lead |
| Selective refinement | `magicore-multi-agent-coarse-to-fine-refinement` | RM-entropy easy→aggregate / hard→refine | lead |
| Learned summarization | `agemem-unified-ltm-stm-management` | SUMMARY preserves info/intent/decisions/open questions | Tier-1 packet |
| Role specialization | `metagpt-sop-multiagent` | SOP roles over pub-sub pool | lead/panel design |

Missed v1 primaries now folded in: `reflexion-verbal-reinforcement-learning`, `self-refine-iterative-refinement-self-feedback`, `mixture-of-agents`.

## When not to use

- `mechanical_only_change ⇒ skip_ladder` and implement directly.
- `dense_implement_spec_exists ⇒ skip_ladder` and execute spec.
- Recon-first only when root cause/direction is open and wrong direction is expensive to unwind.
- Axis-2 skeptic only for material hard-trigger decisions.

## Recon exit → Gate 2

For `density_triage=judgment_required`, Gate-1 close (ladder + skeptic + dense draft) is **stage-for-densify**, not implement authority. `implement_ready` may be authored only by a reasoning-tier seat at Gate-2 densify close. Mechanical/recon Composer never self-stamps. `implement_ready_preflight.admitted` confirms internal consistency only; it is input to Gate 2, not authority.

Default path: `team_dispatch(op=handoff, role=web-consult)` for Gate-2 densify. A reasoning-tier seat that ran recon may author `implement_ready` in-session only after explicitly satisfying Gate-2 criteria. Mechanical-only todos skip straight to implement.

## Investigate-by-probing branch

`recon_returns_UNRESOLVED ∧ ground_truth(external_behavioral) ⇒ switch(investigate_by_probing) ∧ ¬escalate(cost_ladder)`.

Examples: client read-capability audits, UX tolerance, live-traffic questions. Higher tiers share the blind spot; they can design/interpret probes, not observe external behavior. Insert operator-attended or telemetry-instrumented observation.

Rules:
- External factual gap ⇒ route to environment-access seat, not higher reasoning tier.
- Enumerate client-side egress/injection points before declaring unprobeable.
- Completion = adjudicating lead's own reading of observation; executing seat self-report is structurally untrusted.
- Verification rigor depends on outcome sign: ENABLE needs heavy false-pass battery; unambiguous DENY via placeholder observation needs less because confounds create false passes, not false fails.
- Empirical falsifier on default-deny ⇒ no skeptic panel; panel required for state-changing ENABLE direction.

## Worked examples / pointers

- `cortex://notes/system/threads/investigation-escalation-pattern.md`
- `cortex://notes/system/threads/toolresult-mirror-arc-case-study.md`
- `cortex://notes/system/threads/recon-workflow-example-native-client-audit.md`
- Axis-2 full arc: `cortex://notes/system/threads/multi-agent-recon-skeptic-addendum.md`
