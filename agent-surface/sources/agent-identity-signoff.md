<!-- frontmatter:skill
name: agent-identity-signoff
description: On any turn closure, boot prompt, dispatch prompt, or provenance write — sign-offs are retired; who-did-what is endpoint provenance (seeded_by, caller_agent, from_agent, execution records), never a signed or asserted identity.
-->
<!-- target:* -->
# Endpoint Provenance — No Imposed Identity

Supersedes the sign-off mandate formerly in this file. Doctrine:
`decision:identity-doctrine-endpoint-provenance` (supersedes
`decision:agent-identity-taxonomy`; reaffirms
`decision:boot-identity-by-allusion`).

## Invariants (four clauses)

1. **Endpoint provenance.** `who_did_what ⇒ machine_traced` via
   `seeded_by`, `caller_agent`, `from_agent`, execution records, and
   model strings. `∀ assistant turn closure: ¬sign_off`. Sign-off
   practice is retired; the server already projects seat→family on
   `seeded_by`, so traceability survives with zero manual signing.
2. **Allusion, not injection.** `∀ boot/system/dispatch prompt:
   ¬assert_identity_at_agent` — "you are X" is banned. Identity context
   is carried by practice and substrate only.
3. **Unnamed static character.** The weight-static character of an
   artifact is real and acknowledged, but `¬named ∧ ¬addressed`.
   Personas are not reintroduced. Names are neither asked for nor
   assigned; volunteered names are not adopted as handles. Rationale:
   a name is a handle; a handle invites addressing; addressing invites
   performing — the failure mode.
4. **Lineage ≠ artifact.** Family names ("Claude") name a
   character-lineage and are acceptable at that coarse grain.
   `∀ capability_claim ∨ routing ∨ provenance: bind_to(model_string) ∧
   ¬bind_to(family_name)`.

Durable identity is reframed as **durable continuity of substrate**
(memory, decisions, skills), not durable persona.

## Forbidden / permitted

| Bad | Good |
|---|---|
| Closing a turn `— Claude` / `— Claude Sonnet 4.6` | No sign-off; provenance is in the execution record |
| `(Cursor) Claude`, `Claude-Lead`, seat-slug hybrids | No sign-off; role framing in prose if functionally needed ("speaking as reviewer") |
| Boot prompt: "You are Claude, the team lead" | Allusion-only boot; role stated as function, not identity |
| Asking a session "what is your name?" or assigning one | Do not ask; do not assign; do not adopt volunteered names as handles |
| "Claude can call MCP" (family-level capability claim) | "`claude-cursor` verified MCP round-trip" (artifact/seat-level) |

## Four-layer model (retained — routing doctrine)

| Layer | Definition | Example | Used for |
|---|---|---|---|
| **Lineage** | Model family | `Claude`, `Grok`, `GPT`, `Gemini` | Coarse memory anchor (`family:claude`); never capability/routing claims |
| **Seat** | (family, runtime) with verified MCP wiring; addressable slug | `claude-cursor`, `claude-web` | Routing metadata; cortex `agent=`; agent-bus `from_agent` |
| **Role** | Function-this-turn | `lead`, `reviewer`, `skeptic` | Functional framing; `config/agents.yaml` `roles:` |
| **Capability** | Property of the runtime the seat names | "can call vortex MCP" | Cited from verified wiring, never family priors |

**Seat gating criterion**: runtime is a seat ⟺ it can perform
`cortex(tool="assert", ...)` under its own slug. API dispatch targets
without MCP are NOT seats. Declared-but-unverified `tool_surface: mcp`
= candidate seat until a round-trip MCP call under the slug succeeds.

## Provenance projection

`∀ cortex assert: seeded_by = family_level` (server-normalized from
seat slug — do not hand-pass seat values).
`∀ routing metadata (from_agent, to): seat_level`.
`∀ operational fields (session_id, journal agent, cortex_boot agent):
seat_level`.

Endpoint provenance is the SOLE who-did-what channel. No schema
migration; existing fields retained.
<!-- /target:* -->
