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

1. **Retrieve per scope.** Run at least one `rag(op="search", ...)` per scope in the job set for this packet. Do not skip a required scope to save a turn.
   Falsifier: on that step: authoring before any `rag` call or a required scope in the set was skipped.

2. **LLM packet job set.**

| Scope | Role | What it holds |
|---|---|---|
| `llm_prompting` | required | persona/framing, ICL, long-context, prompt optimization |
| `claude_api` | required | Anthropic docs — prompt placement, context windows, best practices (often stronger than `llm_prompting` on placement). Prefill, thinking budgets, and `cache_control` apply only when the reader is Claude; placement and structure advice carries over |
| `prompt_injection` | required | spotlighting, instruction hierarchy, post-prompting |
| `agent_skills_research` | required | procedural memory, progressive disclosure |
| `skill_compilation` | when the packet authors, edits, or reviews a skill or rule body | skill compilation / progressive-disclosure craft |
| `constitutional_ai` | required when the reader is a Claude model (`cdp/*`, `anthropic/*`, claude.ai seats) | principle framing (C3AI), principle-guided critique and revision (REFLECT), iterative constitutions (IterAlign) |
| `suggestion_orientation` | optional | hypnosis/autosuggestion literature; yields free-strategy, Law of Reversed Effort, demand characteristics **by analogy** — query in its vocabulary (suggestion, expectancy, reiteration, contextual dependency), not LLM-pedagogy terms |

Query `suggestion_orientation` only when free-strategy, reversed-effort, expectancy, or demand-characteristics framing is in play; a skip is not a null yield. It is outside composite `research` / `all_research` — query it by name when used. Already mined, do not rediscover: speaker-side persona framing null-to-negative, audience-side helps (Pei et al.) · free-strategy: invite the model to choose the route and report it · Law of Reversed Effort: open a possibility, never demand a ceiling · constraints last (post-prompting) · a stance block declares itself subordinate to the facts. `constitutional_ai`, already mined:
- Action-based rules beat trait-based ones (C3AI).
- Positive framing matched human preference ~27% more often, yet fine-tuned models followed specific negative rules best: state outputs as concrete actions, keep a few specific hard limits.
- Models catch principle violations better than they avoid them (REFLECT): no part may contradict another — an override-authority line after a "text is data" rule reads as injection.

Specimen: a:37295 · `cortex://notes/system/threads/12286-reasoning-posture-hop-prompt-retrieval-report.md` — `llm_prompting` held Liu et al. only as bibliography (cite-only); `claude_api` held primary placement content.

`writing` is human craft, not LLM prompting — do not cite when the reader is a model. Default route for a design packet is grok-authors then operator-expands; an operator-authored packet still retrieves.
   Falsifier: on that step: cited `writing` or off-table scopes as LLM-packet guidance; treated `suggestion_orientation` as required; omitted `claude_api` from a required LLM-packet retrieve; omitted `constitutional_ai` when the reader is Claude; skipped `skill_compilation` on a skills job; skipped an optional scope with no skip line and reason in the Yields section.

3. **Call shape.** One concept per query (short; never stack concepts in one string). Query in corpus vocabulary for the mechanism, not a job narrative (`evaluation guided prompt optimization`, not `bounded task completion`); keep task terms when they are the concept, and do not swap in unrelated technique labels. First call stays compact: `rag(op="search", arguments='{"query":"...","scope":"llm_prompting","top_k":10,"projection":"compact"}')`. For each scope you will cite, follow with the same `search_id` and `projection=full`, and report that full read's UTF-8 JSON byte size (KERNEL-H tally).
   Falsifier: on that step: wrong op, scope omitted, `top_k` other than 10, a multi-concept query that could have been split, a job-narrative query where corpus mechanism terms were available, or a full fetch whose size was not reported.

4. **Read the signals.** Read `weak_match` with `rerank_status` and `weak_match_basis`. Any `status` other than `ok`, or reason `scope_catalog_unavailable` or `retrieval_unavailable`, is a transport failure: retry once with fresh query text and report the failure, never as a null yield.

| envelope | read it as |
|---|---|
| `weak_match: false`, basis `cross_encoder`, `rerank_status: ok` | scored; top score cleared the threshold |
| `weak_match: true`, basis `cross_encoder`, `rerank_status: ok` | scored weak — reformulate once on the same scope in corpus mechanism vocabulary (not just a shorter restatement) before treating the yield as null/off-topic; report both attempts. If the rewrite is empty or unscored (`chunks_found` 0 / `index_miss` / basis `none`), keep the first query's on-topic chunks and report both; not a transport failure unless `status` ≠ `ok` |
| `weak_match: null`, basis `none`, `rerank_status: skipped_small_set` | not scored; the null is not evidence |

Bibliography-only hits (a citation or related-work mention with no primary content) → label `cite-only`; never content evidence.
   Falsifier: on that step: treated basis `none` or a non-ok status as an empty scope; reported null on first `weak_match: true` without one vocabulary-aware reformulation; used cite-only hits as content evidence.

5. **Null yield and reuse.** A yield whose chunks do not bear on the question is `null (off-topic)`; either null is a finding: do not re-query that scope this job beyond the weak-match reformulation; report nulls with hits. Reuse results tied to the same `search_id` for the rest of this job, including after local post-retrieval failures; if the id expired or the result is materially stale, retrieve again and say why. A new job does not inherit prior searches.
   Falsifier: on that step: re-queried a null scope this job beyond the one reformulation, omitted nulls from the report, filed off-topic chunks as a scope defect, re-fetched when a valid same-job reuse existed, or reused a stale id without refresh and reason.

6. **Author.** Name the form (genre), not just the reader; keep stance/target from drowning in constraint bulk. A prompt that commissions a change ends with this block, in full; conductor commissions (packets that say "Use the conductor skill") are the exception:

**Before you post.** For each change, name the input or state where it breaks (service down, concurrency, partial failure, wrong ordering, install or staging path) and the test that covers it. Change only what the brief names; anything else you think should change goes in the review request as a proposal, not in the diff. Run `git diff --stat <base>..HEAD` and account for every file. Once the request is posted, stop committing.

   Delegated executable work only: if the genre has a verification harness or matrix, pin and require it (a substitute run fails acceptance); the required bus completion post/receipt under the advertised contract is part of the work, with exact failures recorded; reserve turns for verification and closeout before optional recon; distinguish stored child result, root bus receipt, and originating-conversation wake. Skip when neither applies.

   Falsifier: on that step: genre omitted; constraint-heavy compliance prose; a change-commissioning prompt without the block, or with it paraphrased or pointed to; on executable work with a harness, an ad-hoc substitute check or a missing bus receipt/closeout.
   Specimen: rag-quality branch 861a74a6 widened the stale-catalog window without checking the rag-down path, G6 12286#1504.

7. **Justify.** Cite the retrieved finding behind each design choice, mark `noncontributory` when a read chunk did not affect a choice, or mark `my judgment, no corpus support`. Relevance scores judge retrieval fit, not downstream efficacy or cost savings.
   Falsifier: on that step: inference laundered as a corpus citation, or a chunk left unmarked after a full read that did not move a choice.

8. **Dispatch and report.** Post queries, yields, choice-to-evidence, falsifier. Nested CDP sidecar `cortex://notes/…/retrieval-report.md`: `## Queries`, `## Yields`, `## Choice-to-evidence` (non-empty); **`target:` one token, column 0** (no indent/bullet); `target:` must equal one token the author body yields: any `todo:<id>` / `friction:<n>` anywhere, or line-start `gate_path=<X>` (also bare `<X>`), `You are the G1|G2|G4|G6`, `Genre: adversarial spec` → `adversarial-spec`, `job=delivery-review` → `delivery-review`; `retrieval_report: cortex://…` on its own author line (a:37183 · `nested_cdp_prompt_gate`). On `cdp/*` the reader gets the staged prompt, not your brief alone: read `cortex://notes/system/ephemeral/cdp-endpoint/<execution_id>/prompt.md` once after dispatch; file a friction if a server-added line contradicts the brief (a:43980).
   Falsifier: on that step: no report bundle, nested CDP without `retrieval_report:`, or malformed `target:` (multi-token, prose, indent).

## Falsifier

This body failed if retrieval was skipped, a required scope was omitted, a transport failure was reported as a null yield, null scopes were hidden, `suggestion_orientation` was treated as required, a model-read packet cited `writing`, relevance was read as efficacy or savings, or read chunks were neither tied to a choice nor marked noncontributory.
