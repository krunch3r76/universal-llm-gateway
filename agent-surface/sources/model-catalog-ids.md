<!-- target:* -->
# Model Catalog & Synthetic IDs

## Dual-Write Architecture

| Catalog | Location | Schema | Contents |
|---|---|---|---|
| **Static** | `config/models/<domain>/<engine>/<id>.yaml` | 4 | Metadata-only, no `devices` section. Version-controlled. |
| **Local** | `~/.gateway/catalog/<domain>/<engine>/<id>.yaml` | 3 | Full entry with `devices` section containing measured profiles. Written by measurement jobs. |

The Gateway loads **both**. A model is loadable only when the local entry exists with device profiles.

## Synthetic ID Generation (CRITICAL)

Context variants are derived from the `devices` section profiles in the **local** catalog entry.

| Local catalog profile | Available model ID |
|---|---|
| `devices.gpu.profiles."4096"` | `{base-model-id}-4096` |
| `devices.gpu.profiles."8192"` with `n_gpu_layers > 0` | `{base-model-id}-8192-hybrid` |
| `devices.cpu.profiles."4096"` | `{base-model-id}-4096-cpu` |

Example: base model `qwen3-embedding-8b-q8-0` with GPU profile `4096` → model ID `qwen3-embedding-8b-q8-0-4096`.

## `activated` vs `available`

| Field | Meaning |
|---|---|
| `activated: true` | Published to the default `/v1/models` list. Controlled by `activated_gpu_contexts` / `activated_cpu_contexts` in the local catalog metadata. |
| `activated: false` | NOT in the default list, but may still be routable if local catalog has the profile. |
| `available: true` | Exists in the full catalog (static or local). Does NOT mean it's loadable — only loadable if the local entry has device profiles. |

**Invariant**: `activated` is a **publish filter**, not an exhaustive availability list.
`activated_gpu_contexts: [8192]` means only the 8192 context is published — a 4096 profile may still
exist in the local catalog and be routable. The default `/v1/models` response is non-exhaustive.

## REST API for Model Discovery (PREFERRED)

∀ model status checks: use the API, not local catalog YAML files.

```bash
# All published (activated) models — default, non-exhaustive
GET /v1/models

# ALL models including non-activated — full catalog view
GET /v1/models?activation=unfiltered

# Check one specific model — always checks full catalog
# Returns: activated (bool), available (bool), optionally status (loaded/loading/busy/available)
GET /v1/models/{model_id}
GET /v1/models/{model_id}?include_status=true
```

Example: `GET /v1/models/qwen3-embedding-8b-q8-0-4096` → `{"activated": false, "available": true}`
means the model exists in the catalog but isn't published. It may still be routable on demand.

### Cold-load latency (local GGUF)

`status` from `include_status=true`:

| status | Meaning | Caller implication |
|---|---|---|
| `loaded` / `busy` | Resident on a gateway | Safe for latency-sensitive one-shots |
| `loading` | Cold-load in progress | `POST /v1/chat/completions` will block until load finishes |
| `available` | Catalog-ready, not resident | First CC triggers T2 load; large GGUFs (≈26B+) often take **minutes** with no mid-request progress signal |

Latency-sensitive automation (fluidity passes, short consults) must not fire blind CC against a cold large local seat. Preflight:

```bash
GET /v1/models/{model_id}?include_status=true
```

`consult_lib` exposes this as `require_warm=True` / CLI `--require-warm` with optional `--fallback` models (warm 8B/12B local or cloud Gemma). Prefer a warm smaller local or cloud seat for those passes; reserve intentional cold-load for long-budget consults that omit `--require-warm`.

**Diagnostic rule**: When a service reports "exists but no gateway can serve it", check
`GET /v1/models/{model_id}` first — if `activated: false`, the configured ID is no longer
published (activation changed). Options: update the service config to use the activated ID,
or re-add the context to `activated_gpu_contexts` in the local catalog.

## Debugging Model Load Failures

∀ "Failed to initiate load" or "no gateway can serve it" errors:

1. `GET /v1/models/{model_id}` — check `activated` and `available` fields
2. If `available: false` — local catalog missing the profile; model needs measurement or manual profile
3. If `available: true, activated: false` — profile exists but isn't published; service config uses stale ID
4. If `available: true, activated: true` — issue is downstream (engine factory, health check, VRAM)

The Gateway's HTTP layer returns 404 (swallowed to `{}` at the HTTP-methods layer)
when synthetic-ID resolution fails — this propagates as
"Failed to initiate load" through the federation layer.

## MCP Injection (Post-Catalog)

Catalog presence (`available: true`) determines if the gateway can serve a model. MCP client tools are governed by the single `mcp` boolean (default on for tool-capable families; `false` forces inline-only). Remote-connector vs client-side-loop selection is internal and card-derived — not a caller parameter. Server-side provider built-ins are governed independently by the optional `server_tools` knob (omit = ALL; `false` suppresses card-derived built-ins). Agents should not reason about injection details.
<!-- /target:* -->
