---
name: retrieval-before-authoring
description: "Before writing a prompt, packet, or letter another agent or human will act on — retrieve the prompting corpus, then author. Trial 01 scored retrieval positive; priors-only authoring is the failure."
trigger_match_terms: ["write a prompt", "author a prompt", "prompt design", "write the packet", "compose a packet", "elicit", "elicitation", "register", "voice", "genre", "rag_search", "retrieval before authoring", "superior prompting", "prompt for another agent"]
---

# Retrieval before authoring

## Trigger

About to author a prompt, packet, or outbound prose another agent or human will act on.

## Refuse

Author from priors without retrieve. Filing an off-topic yield as a scope mis-map — dense retrieval never returns zero chunks; judge fit, report null. Reading a non-ok status, `scope_catalog_unavailable`, `retrieval_unavailable`, or `weak_match: null` with basis `none` as an empty corpus.

## Steps

1. **Retrieve per scope.** Run at least one `rag(op="search", ...)` per scope in the job set for this packet; split multi-concept designs into several queries on the scope that holds them. Do not skip a required scope to save a turn.
   Falsifier: on that step: authoring before any `rag` call or a required scope in the set was skipped.

2. **LLM packet — job set (required + conditional + optional):**

| Scope | Role | What it holds |
|---|---|---|
| `llm_prompting` | required | persona/framing, ICL, long-context, prompt optimization |
| `claude_api` | required | Anthropic docs — prompt placement, context windows, best practices (often stronger than `llm_prompting` on placement). API mechanics such as prefill, thinking budgets, and `cache_control` apply only when the reader is Claude; placement and structure advice carries over |
| `prompt_injection` | required | spotlighting, instruction hierarchy, post-prompting |
| `agent_skills_research` | required | procedural memory, progressive disclosure |
| `skill_compilation` | when the packet authors, edits, or reviews a skill or rule body | skill compilation / progressive-disclosure craft |
| `suggestion_orientation` | optional | hypnosis/autosuggestion literature + LLM↔suggestion parallels; yields free-strategy, Law of Reversed Effort, demand characteristics **by analogy** — query in corpus vocabulary (suggestion, expectancy, reiteration, contextual dependency), not LLM-pedagogy terms |

Query `suggestion_orientation` only when free-strategy, reversed-effort, expectancy, or demand-characteristics framing is in play for this packet; skip it otherwise (do not treat skip as a null yield). It is outside composite `research` / `all_research` — query it by name when used. Already mined, do not rediscover: speaker-side persona framing null-to-negative, audience-side helps (Pei et al.) · free-strategy: invite the model to choose the route and report it · Law of Reversed Effort: open a possibility, never demand a ceiling · constraints last (post-prompting) · a stance block declares itself subordinate to the facts.

Specimen: a:37295 · `cortex://notes/system/threads/12286-reasoning-posture-hop-prompt-retrieval-report.md` — `llm_prompting` held Liu et al. only as bibliography (cite-only); `claude_api` held primary placement content.

`writing` is human craft, not LLM prompting — do not cite when the reader is a model. Default route for a design packet is grok-authors then operator-expands; the operator's own retrieval still runs when the operator authors.
   Falsifier: on that step: cited `writing` or off-table scopes as LLM-packet guidance; treated `suggestion_orientation` as required; omitted `claude_api` from a required LLM-packet retrieve; skipped `skill_compilation` on a skills job; skipped an optional scope with no skip line and reason in the Yields section.

3. **Call shape.** One concept per query (short; do not stack placement + hierarchy + progressive disclosure in one string). Use `rag(op="search", arguments='{"query":"...","scope":"llm_prompting","top_k":10}')`.
   Falsifier: on that step: wrong op, scope omitted, `top_k` other than 10, or a multi-concept query that could have been split.

4. **Read the signals.** Read `weak_match` with `rerank_status` and `weak_match_basis`. A null `weak_match` whose basis is `none` is not a weak corpus. Any `status` other than `ok`, or reason `scope_catalog_unavailable` or `retrieval_unavailable`, is a transport failure: retry once with fresh query text and report the failure, never as a null yield.

| envelope | read it as |
|---|---|
| `weak_match: false`, basis `cross_encoder`, `rerank_status: ok` | scored; top score cleared the threshold |
| `weak_match: true`, basis `cross_encoder`, `rerank_status: ok` | scored weak — reformulate once (shorter, single-concept) on the same scope before treating the yield as null/off-topic; report both attempts. If the rewrite returns empty or unscored (`chunks_found` 0 / `index_miss` / basis `none`), keep the first query's on-topic chunks and report both results; that is not a transport failure unless `status` ≠ `ok` |
| `weak_match: null`, basis `none`, `rerank_status: skipped_small_set` | not scored; the null is not evidence |

Bibliography-only hits (citation line / related-work mention of a paper with no primary content) → label `cite-only` in the yield; do not treat them as content evidence for the design choice.
   Falsifier: on that step: treated basis `none` or a non-ok status as an empty scope; reported null on first `weak_match: true` without one reformulation; used cite-only bibliography hits as content evidence.

5. **Null yield.** A yield whose chunks do not bear on the question is `null (off-topic)`; either null is a finding: do not look that scope up again for the same job after the weak-match reformulation (if any); report nulls with hits; do not file an off-topic yield as a mis-map.
   Falsifier: on that step: re-queried a null scope same job beyond the one weak-match reformulation, omitted nulls from the report, or filed off-topic chunks as a scope defect.

6. **Author.** Name the form (genre), not just the reader; keep stance/target from drowning in constraint bulk. A prompt that commissions a change ends with this block, in full; conductor commissions (packets that say "Use the conductor skill") are the exception:

**Before you post.** For each change, name the input or state where it breaks (service down, concurrency, partial failure, wrong ordering, install or staging path) and the test that covers it. Change only what the brief names; anything else you think should change goes in the review request as a proposal, not in the diff. Run `git diff --stat <base>..HEAD` and account for every file. Once the request is posted, stop committing.

   Falsifier: on that step: audience named but genre omitted, or constraint-heavy compliance prose, or a change-commissioning prompt without the block, or with it paraphrased or pointed to.
   Specimen: rag-quality branch 861a74a6 widened the stale-catalog window without checking the rag-down path, G6 12286#1504.

7. **Justify.** Cite the retrieved finding behind each design choice or mark `my judgment, no corpus support`.
   Falsifier: on that step: inference laundered as a corpus citation.

8. **Dispatch and report.** Fire the prompt; post queries + yield, prompt text, choice-to-evidence table, cheapest falsifying experiment. For a nested CDP prompt (`team_dispatch(model=cdp/…)` / `cse_session(op=followup)` / conductor G1·G2·G4·G6), the report is a **sidecar artifact**, not chat prose: write `cortex://notes/…/retrieval-report.md` with a `target:` line binding the prompt (`todo:…` / `G4` / `gate_path=SKEPTIC` / `adversarial-spec`), headings `## Queries`, `## Yields`, `## Choice-to-evidence` (each section non-empty; include `weak_match` / null off-topic yields), and put `retrieval_report: cortex://…` on its own line in the **author** prompt body so admit can refuse a missing, unbound, or incomplete bundle (a:37183 · A1–A4 · `nested_cdp_prompt_gate`).
   Falsifier: on that step: dispatch without the report bundle, or a nested CDP prompt without a resolvable `retrieval_report:` citation bound by `target:`.

## Falsifier

This body failed if retrieval was skipped, a required scope was omitted, a transport failure was reported as a null yield, null scopes were hidden, `suggestion_orientation` was treated as required, or an LLM packet cited human-writing scopes as model-execution guidance.
