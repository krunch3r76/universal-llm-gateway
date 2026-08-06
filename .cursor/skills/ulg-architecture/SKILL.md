---
description: "Before ULG repo work on service ops, routes, rebuilds, model lifecycle, or topology — universal-llm-gateway architecture layer reference."
---

# ULG Architecture — universal-llm-gateway Layer

## When to read

Read before ULG repo work on service operations, route changes, rebuild commands, or model-lifecycle decisions.  
Pair with Use the `architecture-invariants` skill for transport, model IDs, events, MCP, REST, and quality gates.  
Cite applicable `[ulg:*]` tags in handoff Block 2; load deferred refs by tag.

**Posture layer above this floor (vision/MAP).** This skill + `architecture-invariants`
are the *code-invariant* floor. On `judgment_required` / pillar-touching work (seat-lane,
imprint/continuity, HTTP substrate, claim-record/WWP), compose with the foundation MAP +
a `VISION-ALIGN` footer — see `path-sim` § Vision-align flag (G4). MAP is the posture
layer *above* the floor — ¬ absorb pillar law into the `[ulg:*]` tag table.

## Tag index

| Tag | Invariant (one line) | Load when | Enforcement |
|---|---|---|---|
| `[ulg:topology]` | `:9999` Stargate sole client-facing endpoint; `:9998` Gateway container-internal only | Topology docs, port references, federation routing | → `workspaces://universal-llm-gateway/.cursor/rules/topology_ws.mdc` |
| `[ulg:lifecycle]` | Stargate sole authority for model load/unload; catalog presence is the structural gate; lifecycle signals advisory | Model loading, routing, coordinator throttling | → `ulg-architecture/model-lifecycle.md` |
| `[ulg:service-ops]` | Service ops via `manage` MCP or `./manage` TUI; post-code loop quality_gate → sync_restart → wait_healthy | Restarts, deploys, MCP dependency changes | → `ulg-architecture/service-ops.md` |
| `[ulg:host-process]` | Except **satellites**, host fleet services run as **`./manage` subprocesses** (`service_ctl`); ¬ live systemd units (repo `systemd/*.service` ≠ deployment path) | Service home / extract / placement / lifecycle architecture; CDP arch packets when hosting is load-bearing | → `ulg-architecture/service-ops.md` § Host process model |
| `[ulg:cortex-data]` | `~/.cortex/cortex.db` is production; manual sqlite3 forbidden by default; use `cortex_conn()` with FK ON | Cortex DB repair, standalone scripts touching cortex.db | FK check before commit |
| `[ulg:generated-metadata]` | ¬direct edit `scope_vocabulary` / `corpus_hints` in `~/.rag/store/rag_metadata.db` | RAG vocabulary quality issues | Fix classifier/prompt/corpus layer |
| `[ulg:events-first]` | Query Event Service before application logs (debug); ∀ spec/packet touching behavior: event vocabulary plus authority/projection/recovery required (design); runtime consumers reconcile with authority unless event delivery/replay is proven. | Issue investigation, pipeline/request debug; spec / consult packet authoring for behavior changes | → `workspaces://universal-llm-gateway/.cursor/rules/event-debugging_ws.mdc`; design check → `architecture-invariants.md` § Spec / design authoring — mandatory event check |
| `[ulg:logging]` | `from universal_logging import get_logger`; ¬stdlib `logging` | Any ULG ecosystem component | Import grep |
| `[ulg:async]` | Request path ¬await gather on handlers; publish_nowait/publish_from_sync per context; one named authority/update path per state key; projections derive from it, ¬co-mutate | Async paths, event bus usage | Blocking-call grep |
| `[ulg:passthrough]` | Gateway/Workers pass client_params unmodified except allowed removals; only Stargate modifies generation params | Generation parameter handling | Param mutation grep |
| `[ulg:concurrency]` | Prefer atomic ops → events → routing → sequential → queues → locks; one load op per model_id | Concurrent subsystems, load coordination | Sequential base class |

## Port doctrine (inline)

Stargate `:9999` — sole client-facing endpoint. Gateway `:9998` — container-internal; external clients MUST NOT target it.  
Full roles, federation, SSH, ports: `workspaces://universal-llm-gateway/.cursor/rules/topology_ws.mdc`.

## Deferred references

- `ulg-architecture/model-lifecycle.md` — `[ulg:lifecycle]` Stargate authority, catalog gates, advisory signals
- `ulg-architecture/service-ops.md` — `[ulg:service-ops]` deploy loop + `[ulg:host-process]` manage-subprocess hosting model, MCP source-sync verification, manage socket
- Event query detail: `workspaces://universal-llm-gateway/.cursor/rules/event-debugging_ws.mdc` — `[ulg:events-first]`
