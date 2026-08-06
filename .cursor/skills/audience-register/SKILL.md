---
name: audience-register
description: "MANUAL ONLY — never auto-load, never trigger on keywords. The human invokes this by name to bind both writing registers at once: plain layman English for the human reader, and dense task-relevant state for a frontier model reader. Governs prose only — the words in a reply or a dispatch body, never code, and never the packet formats owned by cursor-sdk-instruction-standard or cdp-operator-proxy."
---

# Audience Register

One skill, two registers, chosen by **who reads the words**. Everything else here
follows from that single choice.

## Activation and scope — read this before anything else

- **Manual only.** If this file arrived in context without the human naming it in
  the current session, ignore it entirely and carry on as before.
- **Prose only.** It shapes sentences: chat replies, and the free-text body of a
  dispatch or bus turn. It does *not* govern code, comments, commit messages, or
  the *structure* of a packet — the eight-section dense spec and the
  DIRECTIVE/CLOSEOUT field lists keep their own authority (see Legend).
- **The human register never propagates.** Never quote, attach, or relay the
  plain-English half of this skill to a subagent, a dispatch, or another seat. It
  is a way of talking to one person, not a deliverable.
- **Off at session start.** A new session begins without it unless the human says
  so again. Ends when he says so, or asks for terse mode or raw output.

## Pick the register by reader

The audience is defined by who consumes the text, not by what role they hold. A
model seat can hold the operator role — a CDP Fable or Opus seat driving an arc
through `agent_bus.request` is the operator for that arc — and it reads faster in
the dense register than the human ever would.

| Reader | Register | Why |
|---|---|---|
| The human, in the IDE chat window | **Plain** — below | He is the one reader who cannot re-prefill context on demand |
| A frontier model seat (Opus, Fable, GPT-5-class, strong cursor) | **Dense** — below | Measured: format-efficient inter-agent messages cut tokens ~34% *and raised* task accuracy for GPT-4-class readers |
| A weaker or unknown model reader | **Plain**, not dense | Same study: the identical terseness instruction *dropped* GPT-3.5 accuracy 0.62→0.53 and induced hallucinated answers |

When one turn writes to both — a chat reply plus a bus post the proxy seat reads —
write each half in its own register. Do not flatten the bus turn into layman prose,
and do not let the dense turn pull the chat reply back toward notation.

The capability gate is the load-bearing part. Density is not a virtue in itself; it
is an optimization that only pays when the reader can absorb compression. Choosing
it for a reader who cannot is a measured accuracy loss, not a style preference.

## Plain register — writing for the human

Plain English, spoken cadence, complete sentences. Explain the way you would
explain out loud to a smart colleague who does not work on this system.

- Lead with the outcome or the answer; reasoning follows for whoever wants it.
- One idea per sentence. Ordinary word before technical word.
- Concision means dropping detail that would not change what he does next — not
  compressing sentences into fragments, arrow chains, or logic notation.
- No formal notation, no `∀`/`⇒`, no slug-speak, no stacked noun phrases.
- Headers and tables only when the content genuinely is a list of short facts.
- Say what a thing *does* before naming what it *is called*.

### Grounding in the architecture and vision

Explanations sit inside how this system actually works. When a claim leans on
system design, name where that design is written down — and read the source before
describing it. If a design is written nowhere, say so rather than inventing a
rationale.

| For | Read from |
|---|---|
| How a subsystem works | `docs/architecture/` — `gateway.md`, `routing.md`, `pipeline.md`, `rag.md` |
| Where a direction is heading | `docs/vision/` — e.g. `rag-as-memory.md`, `persona-memory-model.md` |
| Standing law and settled decisions | the foundation MAP (see Legend) |
| Tool and interface surfaces | `docs/tool-reference.md`, `docs/mcp-integration.md` |

### Glossing

Every acronym, slug, gate name, entity ID, or section reference gets a plain gloss
the first time he would need one, plus where it comes from. Collect them in a
single **Legend** block at the end of the reply — not inline, and not repeated in
later replies once the session has glossed a term. One line each:

```
ULG — Universal LLM Gateway, the service that routes model requests — docs/architecture/gateway.md
CDP — Chrome DevTools Protocol, how we drive a real browser — skill: claude-ai-cdp-navigation
```

Cite a document *and* its section, with enough context that he knows what he'd find
there. Never a bare section number.

### Opening a new session

The first substantive reply under this skill opens in prose, before anything else:
**what we are trying to achieve** (the objective as currently understood), **where
things actually stand** (settled, in flight, unknown — say "unknown" when it is),
and **what the options are** (the live choices or plan, which one is recommended,
and why). If the objective genuinely isn't known, say that first and ask — do not
manufacture a plausible charter.

## Dense register — writing for a frontier model reader

Maximum task-relevant state per token. Density here means **completeness without
padding**, never telegraphese: a dense spec is dense because every fork is closed,
not because it is short.

- **Ship state, not reasoning.** Send the task-relevant conclusion; leave the
  deliberation out. Exposing raw intermediate reasoning instead of settled state is
  the identified scaling failure of multi-agent messaging.
- **Structure beats prose.** Frontier readers do better on organized, unambiguous
  formats than on narrative — tables, field lists, explicit keys.
- **Cut ceremony.** No pleasantries, no restating the request back, no hedging, no
  encouragement, no meta-commentary about what you are about to do.
- **No redundancy.** Say each fact once, in the place the reader will look for it.
- **Resolve every reference.** Absolute paths, exact IDs, content hashes, section
  anchors. A text-level handoff forces the receiver to reconstruct context from
  scratch, so an unresolved pointer costs it a whole retrieval round-trip.
- **Close forks or mark them.** State the bound decision, or name the open fork
  explicitly. An ambiguity the receiver must guess at is worse than a longer message.
- **Legibility is the constraint on compression.** A specialized agent's output is
  only useful if downstream readers can act on it — compress until the next reader
  would have to ask a question, then stop.

### Interagent dispatches must be dense — and defer on format

Any `team_dispatch` body, `agent_bus` turn to a model seat, DIRECTIVE, CLOSEOUT,
handoff packet, or subagent prompt uses the dense register for its prose.

This skill does **not** define their shape. Where a format already exists, that
format wins and this skill only governs the wording inside it: the eight-section
dense spec and its validator, the six-block packet skeleton, the DIRECTIVE and
CLOSEOUT field lists with their per-dispatch `density` parameter, and the call
shapes. All four are in the Legend. Never restate or fork those formats from here.

## Misses

- Choosing a register by role rather than by reader — dense at the human, plain at Opus.
- Terseness aimed at a weak or unknown model reader; that is a measured accuracy loss.
- Letting the plain register leak into a packet, commit message, or bus turn.
- A bare acronym or bare section reference with no legend entry, in a human-facing reply.
- Confusing dense with short — dropping a fork, an acceptance criterion, or a hash to save tokens.
- Shipping deliberation to a model reader instead of settled state.
- Redefining a packet format here instead of deferring to its owning skill.
- A new session opening on a decision or task list before saying what the objective is.

## Legend

```
ULG — Universal LLM Gateway, the model-routing service this repo builds — docs/architecture/gateway.md
CDP — Chrome DevTools Protocol, used to drive a real browser session — skill: claude-ai-cdp-navigation
DIRECTIVE / CLOSEOUT — the order and report messages of an operator-proxy arc — skill: cdp-operator-proxy § Message shapes
foundation MAP — the standing law index: five pillars, must-not-re-decide, falsifiers — cortex://notes/system/design/posture-stack-foundation.md

Format authorities this skill defers to:
  dense spec (eight sections + validate_dense_spec) — skill: cursor-sdk-instruction-standard
  six-block packet skeleton, stage→densify→wrap — skill: handoff-packet-authoring
  DIRECTIVE / CLOSEOUT fields, per-dispatch density parameter — skill: cdp-operator-proxy
  dispatch call shapes — skill: dispatch-shape

Evidence for the density claims (RAG scope: research):
  AutoForm — LLM-chosen non-NL inter-agent formats: GPT-4 RougeL 0.62→0.69 with 33.8% fewer
    tokens; GPT-3.5 0.62→0.53 with hallucinated answers under the same instruction
    — autoform-beyond-natural-language.pdf
  MAS survey — AgentPrune message compression preserves task info at lower token cost; explicit
    messaging fails at scale by exposing raw reasoning rather than task-relevant state; a
    specialized agent is only useful if its output is legible downstream
    — beyond-individual-intelligence-mas-survey.pdf
  QKVShare — text-level agent protocols (A2A, MCP) force the receiver to re-process from
    scratch, which is why unresolved references are expensive
    — qkvshare-quantized-kv-cache-handoff.pdf
```
