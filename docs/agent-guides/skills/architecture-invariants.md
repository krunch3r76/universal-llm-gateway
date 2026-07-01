---
description: On any task involving API changes, deletions, new modules, signal/event changes, transport choices, model-ID handling, or architectural decisions — read this skill before findings, code, or design.
---

# Architecture Invariants — Universal Layer

## Trigger

Universal invariant layer for every resident across `universal-*` workspaces and shared `libs/`.

Read before findings, code, or design on any architecture-affecting task.

`deletion_task ⇒ [universal:no-bc] ∈ handoff.<invariants>`; omission is a packet bug.

Cite applicable tags in handoff Block 2. Load deferred refs by tag when procedure detail is needed.

## Tag index

| Tag | Invariant | Load when | Enforcement |
|---|---|---|---|
| `[universal:no-bc]` | Delete removed surfaces outright; ¬compat shim ∧ ¬migration alias ∧ update all consumers same change | Deletion/rename in sole-maintainer ecosystem: gateway, stargate, inference_djinn, process_ipc, event_bus, logging, transport, protocol, cortex | Packet tag + grep consumers |
| `[docs:no-vestigial]` | Post-deletion docs name only current surfaces; write new surface as if old never existed | Doc/docstring edits after removal | Review for migration/formerly prose |
| `[universal:transport]` | Internal HTTP via `transport_utils`; containerized builds include `libs/` in build_context + PYTHONPATH | HTTP client wiring, Docker/compose builds | grep direct httpx UDS / local socket env constants |
| `[universal:modelid]` | Parse once to `ModelId` at API boundary; routing uses parsed object; `str()` is display-only | Routing, gateway calls, catalog lookup | grep `str(model_id)` in routing logic |
| `[universal:events]` | `@event_factory` only; signal regex + role/scope taxonomy; update vocabulary same change | Adding/changing/removing observable behavior | → `architecture-invariants/events-docs.md` |
| `[universal:obs-over-timeouts]` | Prefer lifecycle/observability events over timeouts for completion; any timeout MUST be idle timeout on absence of progress events, not wall-clock completion deadline. Caveat: gated by project event maturity; known exception to revisit: pipeline IO ops | Dispatch/worker waits, async completion, blocking wait on another process/job | Review wall-clock completion deadlines where lifecycle events exist; grep fixed `*_TIMEOUT_SECONDS` completion budgets |
| `[universal:mcp]` | Thin HTTP relay via `transport_utils`; handler ¬business logic ∧ ¬direct DB | MCP tool implementation | Review handler bodies |
| `[universal:rest]` | `/v1/*` OpenAI-compatible only; project/admin endpoints under `/api/v1/*` or explicit namespace; prefer REST over direct catalog access | New endpoints, wire projections | Namespace grep; sole owner of API namespace rules |
| `[universal:satellite]` | Personal microservices: own container/process, UDS at `/tmp/universal-protocol/{name}.sock`, `openapi.yaml` contract, Bearer auth | New satellite services | Contract + auth review |
| `[scope]` | Every changed line traces to user request ∨ direct consequence; ¬unrelated same-diff edits | All edits | Diff review |
| `[quality]` | SLOC ≤400 existing / ≤300 new; compile + ruff + modularize gates; typed functions | Code changes | → `architecture-invariants/quality-gates.md` |
| `[quality:bug-class-sweep]` | Bugfix on pattern P ⇒ grep entire service/library for P before declaring complete | Bugfixes targeting a code pattern | → `quality-gates.md` |
| `[quality:error-envelope]` | Error shape `{code, message, source, retryable, data}` via `ProtocolError` | Error response changes | → `quality-gates.md` |
| `[simplicity]` | Minimum code solving stated problem; fix root cause not symptom | Implementation | Review speculative abstraction |
| `[docs]` | Event/API/runtime contract changes ⇒ doc audit; generated catalog regions via `gen-event-catalog --check` | Contract/semantics changes | → `events-docs.md` |
| `[docker]` | `--no-cache` disables layer reuse + `--pull`; scoped buildx prune only | Docker rebuild/cache decisions | → `quality-gates.md` |
| `[universal:refs]` | Cross-resident refs use `cortex://` or `workspaces://`; ¬absolute paths in cross-resident artifacts | Packets, specs, handoffs consumed by other residents | grep `/mnt/torus/` in artifacts |
| `[universal:git-posture]` | Canonical state = working tree + cortex/RAG + live process; git ≠ project index; no standing git workflow; commit optional; `git diff` unreliable on master, reliable on arc worktrees; ¬ diffs to LLMs — whole files/sections only | Git state inference, canonicality, cursor-sdk substrate, git CLI | → `git-posture.md` |
| `[universal:executor-rec]` | Executor-recommendation surfaces emit an always-present advisory container naming model, thinking, effort as independent axes; transport preserves no-recommendation/unsupported/partial states; never collapse effort into thinking; additive + versioned beside legacy coarse fields | Executor advisory wire surfaces | Review collapsed axes / omitted container / mutated legacy field |

## Spec/design event check

For any spec, consult packet, or dense spec covering new/changed behavior, include:

- State from events, not manual polling or wall-clock completion deadlines — `[universal:obs-over-timeouts]`.
- New/changed behavior → event vocabulary named — `[universal:events]`.
- Every state transition, decision point, and failure mode observable via events.

Phase/spec docs include:

```markdown
## Event Vocabulary
| Behavioral change | Signal | Action |
|---|---|---|
| {new flow / state transition} | `{signal.name}` | add / modify / none needed |
```

`none needed` is valid; skipping the decision is not.  
Signal format: `^[a-z]+(\.[a-z]+){1,4}$` — no underscores, hyphens, or digits.  
Factory rule: `@event_factory` only; ¬ direct `Event(...)` construction.  
Full procedure: `architecture-invariants/events-docs.md`.

## Deferred references

- `architecture-invariants/events-docs.md` — `[universal:events]`, `[docs]`, event-catalog detail, admission payload, sibling-family audit.
- `architecture-invariants/quality-gates.md` — `[quality]`, `[docker]`, `[quality:bug-class-sweep]`, `[quality:error-envelope]`.
