---
name: retrieval-before-authoring
description: "Before writing a prompt, packet, or letter another agent or human will act on — retrieve the prompting corpus, then author. Trial 01 scored retrieval positive; priors-only authoring is the failure."
trigger_match_terms: ["write a prompt", "author a prompt", "prompt design", "write the packet", "compose a packet", "elicit", "elicitation", "register", "voice", "genre", "rag_search", "retrieval before authoring", "superior prompting", "prompt for another agent"]
---

# Retrieval before authoring

## Trigger

About to author a prompt, packet, or outbound prose another agent or human will act on.

## Refuse

Author from priors without retrieve. Filing an off-topic yield as a scope mis-map — dense retrieval never returns zero chunks; judge fit, report null.

## Steps

1. **Retrieve per scope.** Run one `rag(op="search", ...)` per scope in the job set; do not skip a scope to save a turn.
   Falsifier: on that step: authoring before any `rag` call or a scope in the set was skipped.

2. **LLM packet — four scopes only:**

| Scope | What it holds |
|---|---|
| `llm_prompting` | persona/framing, ICL, long-context, prompt optimization |
| `suggestion_orientation` | hypnosis/autosuggestion literature + LLM↔suggestion parallels; yields free-strategy, Law of Reversed Effort, demand characteristics **by analogy** — query in corpus vocabulary (suggestion, expectancy, reiteration, contextual dependency), not LLM-pedagogy terms |
| `prompt_injection` | spotlighting, instruction hierarchy, post-prompting |
| `agent_skills_research` | procedural memory, progressive disclosure |

`suggestion_orientation` is outside composite `research` / `all_research` — query it by name. Already mined, do not rediscover: speaker-side persona framing null-to-negative, audience-side helps (Pei et al.) · free-strategy: invite the model to choose the route and report it · Law of Reversed Effort: open a possibility, never demand a ceiling · constraints last (post-prompting) · a stance block declares itself subordinate to the facts.

`writing` is human craft, not LLM prompting — do not cite when the reader is a model. Default route for a design packet is grok-authors then operator-expands; the operator's own retrieval still runs when the operator authors.
   Falsifier: on that step: cited `writing` or off-table scopes as LLM-packet guidance.

3. **Call shape.** Use `rag(op="search", arguments='{"query":"...","scope":"llm_prompting","top_k":10}')`.
   Falsifier: on that step: wrong op or scope omitted from the call.

4. **Null yield.** A yield whose chunks do not bear on the question is `null (off-topic)`; either null is a finding: do not look that scope up again for the same job; report nulls with hits; do not file an off-topic yield as a mis-map.
   Falsifier: on that step: re-queried a null scope same job, omitted nulls from the report, or filed off-topic chunks as a scope defect.

5. **Author.** Name the form (genre), not just the reader; keep stance/target from drowning in constraint bulk. A prompt that commissions a change ends with this block, in full; conductor commissions (packets that say "Use the conductor skill") are the exception:

**Before you post.** For each change, name the input or state where it breaks (service down, concurrency, partial failure, wrong ordering, install or staging path) and the test that covers it. Change only what the brief names; anything else you think should change goes in the review request as a proposal, not in the diff. Run `git diff --stat <base>..HEAD` and account for every file. Once the request is posted, stop committing.

   Falsifier: on that step: audience named but genre omitted, or constraint-heavy compliance prose, or a change-commissioning prompt without the block, or with it paraphrased or pointed to.
   Specimen: rag-quality branch 861a74a6 widened the stale-catalog window without checking the rag-down path, G6 12286#1504.

6. **Justify.** Cite the retrieved finding behind each design choice or mark `my judgment, no corpus support`.
   Falsifier: on that step: inference laundered as a corpus citation.

7. **Dispatch and report.** Fire the prompt; post queries + yield, prompt text, choice-to-evidence table, cheapest falsifying experiment.
   Falsifier: on that step: dispatch without the report bundle.

## Falsifier

This body failed if retrieval was skipped, null scopes were hidden, or an LLM packet cited human-writing scopes as model-execution guidance.
