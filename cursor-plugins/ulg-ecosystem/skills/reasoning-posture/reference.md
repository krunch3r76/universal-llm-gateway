# Reasoning posture: reference

Material moved out of SKILL.md on 2026-10-02. The rules live in SKILL.md; this file carries where the skill is loaded, which other skills own adjacent concerns, the incident specimens behind each rule, and the vocabulary older documents use. Nothing here adds a rule.

## Where the body is loaded

| Surface | How |
|---|---|
| Attended Cursor IDE, parent seat | SKILL.md has `alwaysApply: true`; Cursor promotes it to a global rule. The thin `.mdc` stub was retired 2026-09-21 (`dc3156ef`). An open tab keeps its inject set until Reload Window or a new chat. |
| Cursor IDE native `Task` subagent | Does not inherit the promotion; starts cold. The parent prompt opens with `/reasoning-posture` then `Use the reasoning-posture skill` unless the dispatch is purely mechanical (`native-subagent-reasoning-posture_ulg`). Launching a mutating Task without the parent kernel produced the wrong Salt & Straw review (agent-bus:9758 turn 71). |
| `team_dispatch` generate, `seat=cursor-sdk` | GIW `resolve_prompt_preamble` prepends the slash line and the Use-line on judgment contracts, including `none`. Skip set: `REASONING_POSTURE_SKIP_CONTRACTS` in `libs/reasoning_posture_contracts.py` (implement, mechanical, propagate, execute, answer, ask). The skill directory stays in the dispatch HOME for on-demand reads. `skills=` mount is a no-op on this path. |
| `team_dispatch` `op=handoff`, consult or none | Stargate enrich inserts the Use-line into `<invariants>`; skipped for implement. |
| `cursor-auto` admit, first episode | Admit report prepends the slash line and Use-line when the handoff contract warrants; nested cursor-sdk also gets the GIW preamble. |
| CDP generate (`model=cdp/…`, including `panel_dispatch` legs) | Staging merges `reasoning-posture` into `skills=` (`ensure_cdp_judgment_skills`). |
| claude.ai (Customize, life, Cowork) | No always-apply section. Catalog chip plus Use-line; `render_bundle` emits `name` and `description` only, so `alwaysApply` has no effect there. `cortex_brief` carries a short operational summary. |

`skill_suggest` Stage-A is not the attach path for cursor-sdk; it scored this skill 0.0 on three sample prompts (probe 14604, 2026-10-02). The GIW preamble is the attach path.

## Who owns what

| Concern | Owner |
|---|---|
| Question, out-of-scope, the six rules, one bounded step | `reasoning-posture` |
| Rival approaches and simulation before binding | `hypothesize-simulate` |
| When to fire a consult, exemptions, posture before transport | `consult-posture` |
| Seat and transport choice for a consult | `consult-routing` |
| When to pause at all | `advisor-timing` |
| Recon before implement | `recon-default`, `cheap-recon-before-escalation` |
| L0/L1/L2 ranking, header, per-family windows | `path-sim` |
| Scope-lock field shape | `cortex://notes/system/specs/consult-scope-lock-template.md` |

Composes with `engagement-stance`, `auditor-validatable-confidence`, provenance discipline, and `srm`. Older documents say "detent" or "aperture" for how wide you are looking (closed, standard, wide, frontier), and "cascade" for a greater model exploring while a lesser model binds. SKILL.md says "widening to X because Y"; the meaning is the same. Thinking mode off does not waive any rule; the rules matter more without thinking.

## Specimens behind the rules

| Rule | Specimen |
|---|---|
| Closure language is a claim | 23257: a source that clarified mechanism was reported as resolving a still-open source-of-truth question, then walked back in the same message. |
| Pushback is a verify trigger | 22167: the operator's correction was partly right (a term scoped to one context, not the one in dispute); reading the artifact showed the partial truth. |
| One bounded step | Thread 4401 sampling, 4 of 10 P2 incidents (2026-07-05); boundary tightened by panel in thread 4410 (executions c9b5ff08, 7a10f140). |
| Do the current step of an authorized mission | Incident 7419: a conductor verified the mission was genuine, then refused the whole mission over a later step's scale. |
| Parent kernel into Task subagents | agent-bus:9758 turn 71. |

Bad and good shapes for one bounded step:

| Bad | Good |
|---|---|
| Audit, code fix, rebuild, smoke test and bus reply in one unbound turn | Fix, then a verification gate, then the deploy leg |
| Parallel cortex asserts, sidecar and bus post before the write is verified | Verify the write, then assert citing its evidence |
| Read sweep, exploratory subagent and consult dispatch in one unverified turn | Reads first; a subagent is its own bounded leg ending at the poll |
| Follow-on implementation in the same turn as the fan-out dispatch | Dispatch N bounded workers, then wait, poll, adjudicate |
| "Let me also…" chains appended mid-turn | New intent, new bounded step next turn |
| Verify a mission is genuine, then refuse it over a later step's scale | Do the current step; flag the concern in the closeout; reassess at that step |

Other anti-patterns from the previous body: merits before the question is pinned; silent widening; weak paraphrase then dismissal; "it might perhaps"; a sensitivity disclaimer with no specific caveat; agreeing after pushback because pushed; treating an entrenched multi-session finding as true; defending a prior turn against new evidence; batching speculative mutations in one turn; copying path-sim machinery into this skill; gating the skill on thinking knobs.

## History

3,410 bytes on 2026-08-01. Merged with frontier-reasoning-discipline 2026-08-11 (10,146). One-determinate-step clause 2026-08-16/17. Always-apply in the attended IDE 2026-09-21 (14,216). Split into SKILL.md and this file 2026-10-02 after probe 14604 found the full body loading in every attended chat, about half of it plumbing.
