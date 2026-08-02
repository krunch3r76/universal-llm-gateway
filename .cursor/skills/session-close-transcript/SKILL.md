---
trigger_match_terms: ["session-close-transcript", "session_close_transcript", "compose", "transcript_md", "close", "session-boot-close", "composing", "verbatim", "light", "dual-layer", "shape", "canaries"]
description: Before composing transcript_md at verbatim or light close — dual-layer shape, C1–C6 canaries, light/none deltas.
---

# Session Close — Transcript

Gate: `transcript_depth(S) ∈ {verbatim, light}` — read before composing transcript. Structural file checks live in `session-close-audit.md`, not here.

## verbatim — dual layer

`transcript_md = verbatim_layer ⊥ structural_layer`. Neither layer is derivable from the other.

```markdown
## Turn N — [topic]
### User
<verbatim user>
### {Agent}
<verbatim response>

## Session Summary
**Summary:** …
**Domains:** …
**Decisions:** …
**Open items:** …
**Continues:** {prior_session_id}?
```

## Fidelity canaries

`∀ c ∈ C1..C6: pass(c)` before audit Step 2b.

| # | Predicate |
|---|---|
| C1 | `### User` contains quoted user words, not paraphrase |
| C2 | `### {Agent}` contains quoted agent prose |
| C3 | Turn body is not an action-log narrative |
| C4 | Alternation `(User → Agent)*` holds |
| C5 | `|transcript_md| > |summary|` |
| C6 | No hollow turns: `¬(∃ ## Turn ∧ ¬∃ ### User)` |

Hollow bad: `## Turn 1` with empty `### User` and response as peer H2. Good: user quote under `### User`, response under `### {Agent}`.

Fail any canary ⇒ rewrite from first user message. If truncating, use only `[TRUNCATED: turns 1–N omitted]`.

Web: read `web-transcript-preprocessing.md` before assembly; strip payloads, keep metadata.

## Named sections — structural layer

Structural layer (`session_summary_md` for cursor/light; trailing `## Session Summary` block of web `transcript_md`) may include sibling H2s. Server appends it with only trailing-newline trim; heading normalizer rewrites only the `## Session Summary` line. Bare `## Summary` and other siblings remain untouched.

```text
anchor(σ)    ⇔ ∃ `## Session Summary` ∧ first_H2(σ, `## Session Summary`)
preserved(h) ⇔ h ∈ H2(σ) ∧ h≠`## Session Summary`
```

Sanctioned optional siblings (only with real content):

| Section | Purpose |
|---|---|
| `## Summary` | Expanded prose beyond terse `**Decisions:**`; not the required anchor. |
| `## Appendix A: <title>`, `## Appendix B: <title>` | Self-contained reference from this session (RAG list, command transcript, derivation). |
| `## Appendices` | Container for several short H3 appendices. |
| `## References` / `## Sidecars` | Durable artifact pointers: sidecars, entity IDs, spec paths, URIs. |

Contract: `## Session Summary` first; siblings after (Summary → Appendices → References/Sidecars). Keep kilobyte-scale. Appendices are synthesis for next session, not a second copy of verbatim turns; C1 leakage applies everywhere. Full spec: `session-transcript-fidelity.mdc` § Named Sections.

## light — DEFAULT depth (kernel rev 4.1)

Structural layer only. Cortex API writes `session_summary_md` as file. No `## Turn` blocks required. `## Session Summary` heading required. Optional named sections permitted.

The light record must be SELF-SUFFICIENT — it stands alone if the operator later deletes the web chat:

```text
light_record(S) ⊇ { decisions, open_items(as todo: entities, not prose),
                    entity_ids, reasoning_deltas(S) }
reasoning_deltas(S) = conversation_reasoning \ {bus_turns, assertions, journal_rows, execution_ids}
```

Capture the reasoning prose unique to the conversation; do NOT re-encode what vortex already holds (bus turns, assertions, journal rows, execution IDs). Verbatim reproduction later belongs to the fidelity tier (non-deleted web chat / conversation-API pull), never to snippet recall — see session-close-kernel Step 5 § Recall vs fidelity.

## none

No transcript artifact. `session_summary_md` is still required on close call.
