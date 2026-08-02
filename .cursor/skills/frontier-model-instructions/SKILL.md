---
name: frontier-model-instructions
description: "Before writing or revising procedural text for frontier LLMs — agent prompts, boot prompts, personas, dispatch packets, or skill/rule bodies."
trigger_match_terms: ["frontier-model-instructions", "frontier_model_instructions", "procedural", "text", "frontier-llm", "reader", "writing-documents", "write", "revise", "whose", "primary", "frontier"]
---

# Writing Instructions for Frontier Models

High-authority discipline for model-targeted procedural text.

## Trigger

Read before composing or revising:

- agent system prompts, boot prompts, persona prompts, or birth-prompt fragments;
- `team_dispatch.system` fields;
- SKILL.md files (also load `skill-document-writing`);
- `.mdc` Cursor rules (also load `cursor-rule-authoring` for file mechanics);
- inline procedural rulesets another agent will execute;
- any text whose primary downstream reader is a frontier model acting on the content.

If the primary reader is human, use `prose-discipline` instead.

## Core principle

`reader = frontier_model ⇒ instruction ≠ documentation`.

A frontier-model instruction is consumed in one forward pass under context pressure. `byte ∉ behavior_change ⇒ context_burn`, except redundancy that suppresses a high-probability wrong completion (see Compression floor).

Consequences:
1. `rule.compressible_to_predicate ⇒ write_as_predicate`.
2. `sentence ¬changes_behavior ⇒ delete`, not soften.

## FOL default

Default to FOL for operational rules. Allowed notation: `∀, ∃, ⇒, ∧, ∨, ¬, ⇔, |x|, ∈, ∉, ∪, ∩, ⊆, ⊂`.

Use FOL for conditionals, prohibitions under condition, cardinality, quantification, set membership, iff gates, and thresholds:

`rule.shape ∈ {if_then, do_not_when, iff, forall, membership, threshold} ⇒ write_as_FOL`.

Use prose only when the rule is irreducibly narrative, FOL would invent obscure predicates, or the rule is meta-procedural about writing itself.

Predicate form may carry an inline prose suffix when it clarifies predicates: `|ids| > 1 ⇒ retry (one call, not a loop)`. Decorative FOL is prohibited: every predicate must compress a real decision.

## Voice and form

- Imperative: “Run X,” not “you should run X.”
- Present tense: “Returns,” not “will return.”
- No hedging adverbs unless load-bearing. If conditional, state the condition.
- Name what is missing: “The record does not show X,” not “It is not entirely clear.”
- Lead with the verb. Use active voice when prose is required.

## Token economy

Revision filter:

- `deletable ∧ ¬behavior_change ⇒ delete`
- `compressible_to_predicate ⇒ compress`
- `extractable_to_reference ⇒ link_out`
- `example_targets_real_failure ⇒ keep`; happy-path examples delete

Background, motivation, and rationale belong in sidecars/commit messages unless they change rule firing. This filter assumes ignorance; for model-prior failures, use the compression floor.

## Compression floor — prior-override rules

`failure_mode(R)=ignorance ⇒ compress_to_invariant`.

`failure_mode(R)=model_prior ⇒ retain(invariant(R) ∧ forbidden/permitted_enumeration(R) ∧ canonical_bad/good_pairs(R))` even when a strong model could infer them.

Prior-override examples: fabricating done-claims, broad-reverting a shared tree, signing a persona, scope-creeping a diff. The explicit enumeration and bad/good pairs are the behavior-change mechanism because they suppress the model's highest-probability wrong completion.

Strippable: rationale narrative, historical-incident prose, relationship-to-other-rules sections. Not strippable: invariant, forbidden/permitted enumeration, canonical-failure anchor, bad/good pairs naming the prior.

Test: `competent_model told only invariant(R) still emits wrong completion under task pressure ⇒ example_is_load_bearing ⇒ keep`.

## Calibrate to weakest reader

`shared_ruleset ∧ multi_family_consumers ⇒ inference_floor = weakest_loader`.

Do not compress to the strongest model's inference capacity. Family-specific load-bearing notes are not rationale; they are rule firing for seats that need them.

## Compression mode

`mode=compress ⇒ preserve(normative_content) ∧ ¬add_content ∧ ¬reframe ∧ flag(every_deviation) ∧ relocate(unique_removed_content)`.

Subtraction only. Cut redundancy and inferable detail; never silently drop a definition, invariant, enumeration, or sole-home concept. Relocate or confirm mirror first.

## Trigger precision

For any procedural ruleset `R`:

- `trigger(R) ∩ trigger(R') = ∅` unless one explicitly supersedes the other.
- `∀ situation s : applies(R,s) ⇔ s ∈ trigger_set(R)`.
- `over_broad(R) ⇒ false_positive_fires ⇒ context_burn ∨ wrong_action`.
- `under_specified(R) ⇒ false_negative_misses ⇒ rule_never_fires`.

Applies to SKILL.md descriptions, system prompt activation, and “when to apply” clauses.

## Forward-pass survival

Survives: named predicates, explicit trigger/non-trigger enumerations, one-sentence core rules in first screen, and failure-targeting before/after examples.

Does not survive: vague verbs, marketing voice, topic-only triggers, front-loaded background, happy-path examples.

## Anti-patterns

- Decorative FOL.
- “You should” voice.
- Topic-only triggers.
- Rationale before the rule.
- Empty section headers: “Notes,” “Additional Information,” “Miscellaneous.”
- Unqualified conditionals.
- Hedge stacking.
- Stripping prior-override enumerations or bad/good pairs as “inferable.”
- Compressing shared rules to the strongest reader.
- Silently dropping unique content during compression.

## Examples

### Prose to predicate

Before: “If the pull operation skips emails, that does not mean they are missing. Those emails are already in the local index. Use search instead of trying to pull again.”

After: `pulled = 0 ∧ skipped > 0 ⇒ ¬missing`. Recovery: `search` by sender or subject. Do not re-pull.

### Vague trigger to precise

Before: “Helps with email-related tasks and folder access through MCP.”

After: “On any email/MCP/mailbox task, especially reading Sent, folder-scoped email search, ingesting emails, or an agent cannot see a mail folder, read this skill before acting.”

### Conditional rule with named predicates

Before: “Generally, you should validate the response before trusting it, especially if the response involves writing files.”

After: `dispatch_result.claims_write ⇒ verify_via_filesystem ∧ ¬trust_metadata_alone`.

### Compression floor — guardrail kept

Over-compressed wrong: `completion_claims ⇒ observed_tool_response`.

Floor-respecting right: keep invariant + bad/good pairs: `Session closed.` (no IDs) → `Session closed — session_close 201, journal_row_id=4138`; `I have done X` (tool never called) → `I did not call <tool>; I cannot confirm X`. The pairs are load-bearing under protocol-shape pressure.

## Cross-references

- `skill-document-writing` — SKILL.md L1/L2/L3, frontmatter, registration.
- `cursor-rule-authoring` — `.mdc` file mechanics.
- `prose-discipline` — human-reader prose; mutually exclusive trigger.
- `dispatch-workflow` — team_dispatch construction; this skill governs prompt prose.

## Minimal operating summary

Model reader, one forward pass. FOL by default. Imperative, present tense, no non-load-bearing hedging. Delete bytes that do not change behavior, except prior-override redundancy that suppresses known model priors. Compress to the weakest loader. In compression mode, preserve normative content, flag deviations, and relocate unique content.
