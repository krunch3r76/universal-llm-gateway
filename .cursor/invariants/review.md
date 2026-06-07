# Review Invariants

Use this block when building review handoff packets for this workspace.
Canonical source remains the loaded parent rules plus workspace `_ws.mdc` rules.

## Universal Layer

- Shared primitives from `universal-llm-gateway/libs` must be importable as
  top-level modules via `./libs`, `PYTHONPATH`, or container copy.
- Use `transport_utils` for internal HTTP clients; do not construct direct
  `httpx` UDS transports or hard-code socket paths.
- Use `ModelId` for model identity and routing; no ad-hoc string parsing.
- Construct events through `@event_factory`; do not call `Event(...)` directly.
- Signal names match `^[a-z]+(\.[a-z]+){1,4}$`.
- MCP tools are thin REST relays via `transport_utils`.
- Services with runtime state expose FastAPI on UDS.
- Existing files stay at or below 400 SLOC; new files stay at or below 300 SLOC.
- Caught exceptions emit WARN/ERROR events or are re-raised.
  - **Carve-out**: `libs/cortex_store` routes and dispatch ops are a library
    service that cannot import the workspace event bus without circularity.
    In these modules, `logger.warning(...)` in `except` blocks with safe
    fallback values is the approved pattern in place of `emit_event`. The
    fallback value must be documented inline and execution must continue
    non-fatally (not silent-swallow into an undocumented state). Mark with
    `# [quality:exceptions] carve-out` comment to aid review.
- Every changed line must trace to the requested task.

## Workspace Layer

- Stargate `:9999` is the sole client-facing endpoint.
- Gateway `:9998` is container-internal only.
- Internal service traffic is UDS-first.
- Standard OpenAI-compatible routes live under `/v1/*`; nonstandard routes live
  under `/api/v1/*` or another explicit namespace.
- Stargate is sole authority for model load/unload decisions.
- RAG generated metadata artifacts are managed by tooling, not hand edits.
- MCP Python source changes require restart, not rebuild.
