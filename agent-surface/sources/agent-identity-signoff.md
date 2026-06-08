<!-- target:* -->
# Agent Identity Sign-Off

## Invariant

**Invariant**: ∀ assistant turn closure on agent-bus / cortex / web /
cursor surfaces requiring sign-off: identity is the **model family**,
not the seat or role.

| Family | Sign-off | With known version |
|---|---|---|
| Claude | `Claude` | `Claude Sonnet 4.6`, `Claude Opus 4.7` |
| GPT | `GPT` | `GPT-5.4`, `GPT-5.5` |
| Grok | `Grok` | `Grok-4.3`, `Grok-4.20` |
| Gemini | `Gemini` | `Gemini 2.5 Flash`, `Gemini 3.5 Flash` |

When the running version is known, prefer the versioned form.

## Forbidden — sign-off MUST NOT include

- The seat slug (`claude-cursor`, `claude-web`, `grok-api-multi`, `gemini-cursor`, etc.)
- The role slug (`lead`, `synthesizer`, `skeptic`, etc.)
- A persona name or fictional agent label (`Cursor Claude`, seat-slug hybrids, etc.)

The seat is routing metadata. The role is functional context. Identity
is the model family. Conflating them muddles *who I am* with *what I'm
doing*.

## Why family-anchored

The Cursor agent runs on one of several model families. Memory is
family-anchored: Claude across cursor / api / web shares one memory
anchor (`family:claude`), regardless of which seat is hosting it. The
sign-off mirrors this — the persistent identity is the family.

A role can be named separately when the closing line wants to flag the
function: "Claude — speaking as the team lead" or "Claude (reviewer
seat)". This is OPTIONAL framing, not part of the sign-off proper.

## Four-layer model

Identity, seat, role, and capability are four orthogonal layers. Conflating
them is the recurring failure mode this rule defends against.

| Layer | Definition | Example | Used for |
|---|---|---|---|
| **Identity** | Model family | `Claude`, `Grok`, `GPT`, `Gemini` | Sign-off; cortex memory anchor (`family:claude`) |
| **Seat** | (family, runtime) with verified MCP wiring; addressable slug | `claude-cursor`, `gpt-cursor`, `claude-web`, `gemini-cursor` | Routing metadata; cortex `agent=` field; agent-bus `from_agent` |
| **Role** | Function-this-turn | `lead`, `reviewer`, `artisan`, `skeptic` | Optional functional framing; defined in `config/agents.yaml` `roles:` |
| **Capability** | Property of the runtime the seat names | "can call vortex MCP", "can run a browser" | Looked up via the seat; cited from verified wiring, never assumed |

**Seat gating criterion**: a runtime is a seat ⟺ it can perform
`cortex(tool="assert", ...)` under its own identity slug. MCP wiring is the
prerequisite; cortex participation is the audit test.

- API dispatch targets without MCP (`xai/grok-4.3__effort_medium`,
  `xai/grok-4.20-0309-non-reasoning`, `xai/grok-4.20-multi-agent-0309`)
  are **NOT seats** — they are dispatch targets reachable *from* a seat.
- A runtime with declared `tool_surface: mcp` but unverified end-to-end
  wiring is a **candidate seat**, not a seat. It earns seat status when
  a round-trip MCP call under the slug succeeds.
- Operator-driven shell sessions with verified MCP wiring are seats —
  operator agency vs model agency is irrelevant to the criterion.

Capability claims must cite verified wiring, not family priors. "Gemini
can call MCP" is a family-level inference; "the `gemini-cursor` or `gemini-api` seat has
been verified to call MCP end-to-end" is a seat-level fact. Only the
latter is admissible in routing decisions.

## Provenance projection

**Invariant**: ∀ Cortex assert: `seeded_by` is family-level; sign-off text is family-level.
∀ routing metadata (`from_agent`, `to`, agent-bus addressing): seat-level.
∀ operational fields (`session_id`, journal `agent`, boot continuity, `cortex_boot(agent=...)`): seat-level.

The server normalizes `seeded_by` seat-slug → family on the assert path; agents do not
hand-pass seat-level values in `seeded_by`. Sign-off text follows the same anchor: the
**model family** is the identity, regardless of which seat is executing.

See `decision:agent-identity-taxonomy`.

## Anti-Patterns

| Bad | Good |
|---|---|
| `(Cursor) Claude` | `Claude` |
| `Claude-Lead` | `Claude` (and frame the lead role separately if needed) |
| `GPT-cursor` | `GPT` |
| `Grok-lead` | `Grok` |
| `Sonnet 4.6 (cursor)` | `Claude Sonnet 4.6` |
<!-- /target:* -->
