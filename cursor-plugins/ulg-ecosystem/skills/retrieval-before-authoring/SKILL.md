---
name: retrieval-before-authoring
description: "Before writing a prompt, packet, or letter another agent or human will act on — retrieve the prompting corpus, then author. Trial 01 scored retrieval positive; priors-only authoring is the failure."
trigger_match_terms: ["write a prompt", "author a prompt", "prompt design", "write the packet", "compose a packet", "elicit", "elicitation", "register", "voice", "genre", "rag_search", "retrieval before authoring", "superior prompting", "prompt for another agent"]
---

# Retrieval before authoring

**Invariant:** `author(prompt | packet | outbound_prose) ⇒ retrieve(corpus) ≺ write`.
Writing from priors is the named failure. Trial 01 scored retrieval positive
(fetched 3.25 vs inlined 3.05; three of four top specimens were fetched arms) and the
best cell in the trial paired retrieval **with** a stance block.

## 1. Retrieve

Run one `rag(op="search", ...)` per scope. Do not skip this to save a turn.

| Scope | What it holds |
|---|---|
| `llm_prompting` | persona/framing effects, ICL, long-context, prompt optimization |
| `llm_writing` | prose quality, register, revision, rhetoric, audience-aware generation |
| `suggestion_orientation` | elicitation, free-strategy, Law of Reversed Effort, demand characteristics |
| `prompt_injection` | spotlighting, instruction hierarchy, post-prompting, provenance |
| `agent_skills_research` | procedural memory, progressive disclosure, compiled artifacts |
| `constitutional_ai` | self-report reliability, sycophancy, fluency bias, verification |

`suggestion_orientation` is **not** inside the composite `research` / `all_research`
scopes — query it by name or the arc under-draws. `llm_writing` is in `research` /
`all_corpus` but writing commissions still query it **by name** so format-tax
prompting does not drown the retrieve. Call shape:

```
rag(op="search", arguments='{"query":"...","scope":"llm_prompting","top_k":10}')
```

Report queries run and yield **including nulls**. A null scope is a finding: it tells
the next author not to pay for that lookup again.

**Already mined — hunt past these, do not rediscover.** Speaker-side persona framing is
null-to-negative while audience-side framing helps (Pei et al.) · free-strategy: never
prescribe the route, invite the model to choose one and report it · Law of Reversed
Effort: open a possibility, never demand a ceiling · constraints go last
(post-prompting) · a stance block must declare itself subordinate to the facts.

## 2. Author

Write the prompt or packet. Two rules the corpus is firm on:

**Name the form, not just the reader.** Naming the audience is necessary and not
sufficient. A genre — petition to an official, incident report, cover letter, design
memo — is densely represented in training data and is the strongest single handle on
register. A packet that constrains without naming a target produces compliance prose.

**Watch the ratio.** Stance and target compete with spec for the model's attention. A
packet that is one part invitation to nine parts constraint reads as a compliance
exercise and comes back sounding like one.

## 3. Justify

For every design choice, cite the retrieved finding behind it or mark it
`my judgment, no corpus support`. Do not launder an inference as a citation; a derived
summary is not evidence, including your own.

## 4. Dispatch and report

Fire the prompt, then post queries + yield, the prompt text, the choice-to-evidence
table, and the cheapest experiment that would falsify the design.

## Falsifiers

Authoring before any `rag` call · citing a finding you did not retrieve this session ·
reporting only hits and omitting null scopes · naming the audience and calling the
genre handled · shipping a packet whose constraint bulk drowns its target.
