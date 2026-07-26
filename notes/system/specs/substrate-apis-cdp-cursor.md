# Agent-substrate native APIs (CDP + cursor) — peer of cloud provider natives

## Pattern

Cloud models:

```
wrapper:  POST /v1/chat/completions
native:   POST /api/v1/providers/{anthropic|xai|openai|google}/…
```

Agent substrates (async spawn — not sync chat):

```
wrapper:  team_dispatch(model=cdp/… | model=cursor/… | seat=cursor-sdk)
native:   POST /api/v1/providers/cdp/ask
          GET  /api/v1/providers/cdp/executions/{id}
          POST /api/v1/providers/cdp/executions/{id}/abort
          POST /api/v1/providers/cursor/dispatch
          GET  /api/v1/providers/cursor/catalog
```

`cursor/` and `cdp/` are never `Unknown provider` in
`model_id.resolve_wire_model_id`. Cloud-only surfaces
(`native_loop`, MCP `llm_proxy` chat, `frontier_dispatch`,
`build_dispatch_body` / pipeline admission) raise
`substrate_capability_unimplemented` with `substrate` + `capability`.

Empty pickers (`cdp/`, `cursor/`) reject at wire resolve.

## Capability matrix

| Capability | CDP | Cursor | Cloud |
|---|---|---|---|
| Async ask + poll | yes (native poll/abort) | dispatch only; results via agent-bus | yes (pipeline) |
| Native catalog | N/A | yes | yes |
| Sync `/v1/chat/completions` | unimplemented | unimplemented | yes |
| Generation params (temperature, …) | unimplemented | unimplemented | yes |
| Harvest Outputs vs chat | `harvest_source` / `expected_size` / `download_output` on admit | N/A | N/A |
| `implement` / `wrap` contracts | no | yes | role-dependent |
| Model-only admit (no seat/role) | `cdp/<picker>` | `cursor/<model>` | N/A (role) |
| `role` + substrate model | 422 `substrate_model_role_conflict` | same | N/A |

## Status codes (`substrate_capability_unimplemented`)

| Surface | HTTP | Notes |
|---|---|---|
| MCP `llm_proxy` chat | 501 | `{"error": {code, substrate, capability, …}}` |
| `build_dispatch_body` / frontier HTTP admission | 501 | `FrontierEndpointError` with `details` |
| `frontier_dispatch_v1` step | raises in-handler | capability=`frontier_dispatch` |

## Satellite auth / transport

- CDP satellite (`PROJECT_ASK_URL`): loopback-trusted, no Bearer today;
  HTTP via `transport_utils.make_*_client`.
- Agent-bus delivery: requires `AGENT_BUS_TOKEN`.
- Cursor GIW relay: does **not** forward inbound `Authorization`/`Cookie`
  from Stargate callers (worker authenticates independently).

## Body SoT

- CDP submit: `cdp_ask.models.SubmitProjectAskRequest` (satellite + Stargate)
- Cursor dispatch: `services/git_integration_worker/models/cursor_api.CursorDispatchRequest`

## Delivery

- CDP: agent-bus `from=cdp` + cortex harvest URIs
- Cursor: agent-bus `from=cursor-sdk` + workspaces closeout sidecar
- Peer of chat/completions for these substrates is **async spawn + poll_hint**,
  not OpenAI chat JSON (v1 — no sync facade).

## Event Vocabulary

| Signal | When | Payload keys |
|---|---|---|
| `cdp.generate.admitted` | Worker task spawned after admit | `request_id`, `execution_id`, `model`, `thread_id` |
| `cdp.generate.submitted` | Satellite returned `execution_id` | `request_id`, `execution_id`, `satellite_execution_id`, `model` |
| `cdp.generate.proof` | Harvest proof present | `archive_uri`, `content_proof_uri`, … |
| `cdp.generate.stalled` | Stall / fail without proof | `stall_stage`, `error` |
| `cdp.generate.delivery_failed` | On-behalf bus post exhausted | `thread_id`, `stall_stage` |

Cursor lane continues to emit `frontier.sdk.worker.*` (existing).

## Pipeline model parity (Option 3 — implemented)

**Shipped:** substrate branch in `frontier_dispatch_v1` (``cdp/<picker>`` passes
`normalize_frontier_wire_model`; handler routes to `run_cdp_generate` via
`cdp_dispatch.py`). Virtual `cdp-model-endpoint` pipeline rejected (Option 2).

**Step output contract (bound): dual bind — not Cowork-only**

Harvest proof URIs (`archive_uri`, `content_proof_uri`) are **Cowork-shaped**
and may be skipped when `harvest_source` / `download_output` / `expected_size`
make full output-file harvest uneconomic. Pipelines must support **both**
downstream bind styles without forcing either:

| Bind surface | `StepOutput` fields | Typical downstream |
|---|---|---|
| Inline text | `raw`, `json.content` | `respond.json.content`, text handlers |
| Proof / harvest | `json.archive_uri`, `json.content_proof_uri`, `json.content_proof_sha256`, `json.harvest_provenance` (when present) | URI-following steps, audit, re-fetch |

**Rule:** map `CdpGenerateResult` → `StepOutput` with **both surfaces whenever
the adapter produced them** — do not drop `body` because URIs exist, and do not
require URIs for step success when inline `body` suffices. Empty `body` with
proof URIs only is valid; body-only without Cowork file harvest is valid.

**Harvest economics** (satellite submit — same as `team_dispatch` / `project_ask`):

| `pipeline_options` key | Role |
|---|---|
| `harvest_source` | `auto` \| `output-file` (Cowork paths — **use these**) · `chat` — **stub only** (see below) |
| `expected_size` | `small` \| `large` \| `auto` — `large` may trigger Output download on Cowork paths |
| `download_output` | explicit Cowork Output download attempt |

**Operational posture (operator bind 2026-07-26):** **Cowork is the live harvest
interface** — `/new` compose, Output download, `cortex-uri` pointer ladder under
`auto`+`large`, Outputs-first packet doctrine. Do **not** steer fleet docs/skills
toward `harvest_source=chat` yet.

**Chat harvest — stub (future, ¬ production):** `harvest_source=chat` remains on
the wire for harness parity but is **not** fleet-default today. Chat is a
**different UI interface** (authorization prompts, compose gates, failure modes
not fully cataloged). Future intent: prefer chat harvest for **small** work;
it **can** carry **large** work too (cortex-direct packet doctrine — life `fs` +
`cortex://` pointer) but that path is **untested** and **likely to hit auth gates**
on anything that touches life MCP / pinned cortex paths. **One-off asks with no
cortex / imprint / pinned-deliverable machinery** are the low-risk chat cases —
expected to run without a hitch once promoted. Distinct from Cowork Outputs-first.
Until the chat arc ships (gate catalog + smoke): **stub in spec/code only**; ¬
author skills, packets, or pipeline defaults around chat harvest; ¬ recommend
`harvest_source=chat` in operator guidance.

These knobs affect **what the satellite returns**, not which bind fields exist
on `StepOutput`. Callers choose bind at consume time.

**Rejected:** single-bind-only steps that always require `content_proof_uri` or
always require inline body — blocks pipelines that only need the other shape.

`frontier_dispatch_v1` with `pipeline_options.model=cdp/…` runs the CDP
substrate branch (sync poll-to-proof). `build_dispatch_body(model=cdp/…)` still
raises `substrate_capability_unimplemented` (`capability=pipeline_dispatch_admission`).
Async spawn-only outside the step handler: `team_dispatch(model=cdp/…)`.
