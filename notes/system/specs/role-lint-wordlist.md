# Role lint observed vocabulary

**Purpose**: ground `role_lint` R2/R3 patterns in the retired persona corpus instead of speculative phrasing.

**Source sample**: Cortex `entities(type=ai_agent, limit=30)` and `entities(type=prompt, limit=30)` on 2026-05-12, after Phase 5/6 soft-retirement. Live non-persona entity `ai_agent:grok-legal-retrieval` is intentionally excluded from the persona-smuggling examples.

## R2 — voice / embodiment indicators

| Observed phrase | Source shape | Lint handling |
|---|---|---|
| `Identity-bound to ... family` | Deprecated API dispatch agents (`agent:bard`, `agent:orion`, etc.) | `identity[- ]bound` |
| `same Forge identity` | Deprecated Forge/web-forge descriptions | Covered by identity-coded prose review; do not add a broad `same ... identity` regex unless it appears in role payloads. |
| `sign-off` / `persona` / `birth prompt` adjacency | Deprecated persona descriptions and prompt entities | Handled by explicit role schema policy; use targeted lint additions only after a failing fixture. |

## R3 — metaphor-as-identity indicators

| Observed phrase | Source shape | Lint handling |
|---|---|---|
| `The aperture — ...` | Deprecated `ai_agent:bard` | Bare archetype pattern for `aperture` |
| `One of the music makers` | Deprecated `ai_agent:bard` | `one of the (...)` pattern |
| `the mind that keeps the whole picture` | Deprecated `ai_agent:web-claude` | `the mind (that|whose)` |
| `the mind whose hands are on the material` | Deprecated `ai_agent:cursor-claude` | `the mind (that|whose)` |
| `the conscience of ...` | Prior lint fixture / persona-style role prose | `the conscience of` |

## Do-not-add list

Avoid broad regexes for normal role labels that still appear in valid execution contracts:

- `advisor`
- `reviewer`
- `leader`
- `builder`
- `critic`
- `synthesis`

These words are only problematic when embedded in identity or metaphor constructions. The lint should reject constructions, not generic functional nouns.
