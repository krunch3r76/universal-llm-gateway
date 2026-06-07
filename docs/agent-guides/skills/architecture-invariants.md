---
description: On any task involving API changes, deletions, new modules, signal/event changes, transport choices, model-ID handling, or architectural decisions — read this skill before findings, code, or design.
---

# Architecture Invariants — Universal Layer

Version: canonical rewrite 2026-05-13; source authority retained.  
Created: not stated in source.  
Authority: universal architectural invariants for every assistant session resident operating across `universal-*` workspaces and shared `libs/` primitives.

## When to read

This skill is the universal invariant layer. Each invariant keeps its citation tag (`[universal:transport]`, `[scope]`, …) so handoff packets can cite invariants in `<invariants>`.

∀ applicable task: read this skill before forming findings ∨ writing code ∨ proposing design.  
∀ deletion task: `[universal:no-bc] ∈ handoff_packet.<invariants>`; omission ⇒ packet bug.

## Core rule

∀ changed architecture surface: preserve the current canonical surface, remove obsolete surfaces completely, update all consumers and contracts in the same change, and verify with the relevant quality gates.  
∀ operational rule with conditional/prohibition/cardinality/set/bi-implication shape: express as predicate logic where clearer than prose.

## Procedures

### `[universal:no-bc]` sole-maintainer constraint

Scope: sole-maintainer ecosystem (`gateway`, `stargate`, `inference_djinn`, `process_ipc`, `event_bus`, `logging`, `transport`, `protocol`, `cortex`).

∀ removed_or_renamed_surface: ¬compat_shim ∧ ¬migration_stub ∧ ¬deprecation_alias ∧ deleted=deleted ∧ update_all_consumers_same_change.

Forbidden examples: 422-with-redirect stub, `OldName = NewName` alias, `if version < N: legacy_path()`, README migration table listing old → new names.  
Required replacements: delete the old route; rename every consumer in the same change; bump versions together and delete legacy branches; document only the new surface.

Canonical failure case: dispatch-surface-split Phase 4 422-stub rework. A reviewer recommended a polite-migration shim because `[universal:no-bc]` was absent from the handoff packet. Reviewers default to generic best practice; without this invariant, they rationalize compat scaffolding from first principles.

### `[docs:no-vestigial]` vestigial-reference scrub

∀ post_deletion_docs_or_docstrings: ¬name(retired_surfaces).  
Forbidden phrases/forms: migration tables, `previously called`, `retired in Phase X`.  
Required style: write the new surface as if the old never existed.

Bad → good: `# foo() (formerly bar() — retired Phase 3)` → `# foo()`; README history about v2 replacing v1 → README describes only v2; `Replaces deprecated FooHandler` → standalone handler description.

### `[universal:transport]` transport

∀ internal_http_client: use `transport_utils`; ¬direct `httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=...))`; ¬direct `httpx.Client(transport=httpx.HTTPTransport(uds=...))`; ¬hand_rolled_socket_constants.

```python
from transport_utils import make_async_client, make_sync_client
from transport_utils import DEFAULT_CORTEX_URL, DEFAULT_RAG_URL
from transport_utils import CORTEX_API_SOCK, RAG_SOCKET_PATH
```

Use `make_async_client(url)`, `make_sync_client(url)`, `CORTEX_API_SOCK`, `RAG_SOCKET_PATH`, `DEFAULT_CORTEX_URL`, and `DEFAULT_RAG_URL` instead of local UDS wiring or `os.getenv("CORTEX_SOCK", "/tmp/...")` constants.

∀ containerized service ∈ universal-family workspaces: build_context includes `universal-llm-gateway/libs`.

```dockerfile
COPY libs/ /app/libs/
ENV PYTHONPATH=/app/libs
```

Compose files use repo-root context:

```yaml
build:
  context: ../..   # repo root (from docker/compose/)
  dockerfile: services/<name>/Dockerfile
```

### `[universal:modelid]` model identity

∀ API_boundary: parse model identifier once into `ModelId` from `universal-llm-gateway/libs/model_id`.  
∀ routing_logic: use `ModelId`, ¬string_manipulation, ¬custom_prefix_router.  
needed_pattern ∉ ModelId_support ⇒ extend `ModelId`, ¬local parsing workaround.

`str(model_id)` returns `.original` and is display only; it can include routing suffixes such as `-hybrid`. Comparisons must use the parsed object.

```python
# Bad: false for hybrid models when loaded models omit the suffix.
is_loaded = str(model_id) in gateway.get_loaded_models()

# Good: ModelId equality normalizes identity consistently.
is_loaded = any(model_id == m for m in gateway.get_loaded_models())
```

Properties: `.routing_key` = identity for gateway API calls; `.normalized` = dict keys and deduplication; `.catalog_lookup_id` = catalog config lookup; `.synthetic_id` = wire serialization between Stargates; `.original` / `str()` = display only.

Cloud routing: bare `provider/model` ⇒ direct native provider API; `openrouter/provider/model` ⇒ route through OpenRouter.

Bad → good: `str(model_id) in loaded_models` → `any(model_id == m for m in loaded_models)`; `str(model_id)` for gateway calls → `model_id.routing_key`; `model_id: str` inside routing logic → `model_id: ModelId`; parse in loop → parse once at boundary.

### `[universal:events]` events

∀ event_construction: use `@event_factory`; ¬direct `Event(...)`.

```python
# ✅ factory validates signal format
@event_factory
def ModelExecutionCompleted(model_id: str) -> Event: ...

# ❌ direct construction blocked at runtime
event = Event(signal="...", payload={...})
```

Signal regex: `^[a-z]+(\.[a-z]+){1,4}$` ⇒ 2-5 dot-separated lowercase-alpha segments.  
∀ signal: ¬underscore ∧ ¬digit ∧ ¬hyphen; compound words split into dot segments.

Valid: `federation.model.lifecycle`, `federation.peer.auth.failed`, `federation.vram.request.sent`.  
Invalid: `federation.model.lifecycle_event`, `federation.vram_request.sent`, `federated_gateway.removed`.

Event taxonomy: `role ∈ {coordination, observation, debug, realtime}` with default `observation`; `scope ∈ {node, global}` with default `global`.  
Signals consumed by state machines ∨ admission control ∨ queues ⇒ `role="coordination"` explicitly.  
Temporary diagnostic instrumentation ⇒ `role="debug"` ∧ prune at session boundary ∧ exclude from business-metric operations.  
High-frequency ephemeral events stored only in in-memory ring buffer and broadcast to WebSocket subscribers ⇒ `role="realtime"`.  
Originating-node-only signals not re-emitted on master ⇒ `scope="node"`.

∀ architecture_change adding/removing/changing observable behavior: update event vocabulary in same change. New capability ⇒ new signal(s) at meaningful boundaries. Changed flow ⇒ update decision/coordination signals. New failure mode ⇒ new observable failure signal. Removed behavior ⇒ deprecate/remove stale signal docs.

#### Admission-phase payload contract

∀ event factory F : signal(F) ∈ admission_phase ⇒ payload(F) ⊇ {execution_id, model_entity_id}

| Phase | `model_entity_id` |
|---|---|
| Admission-phase | Required on the rejecting/admitting event itself |
| Lifecycle | Inherited via `execution_id` join against `.started` |

On a rejection path `.started` is never emitted, so `model_entity_id` must travel on the rejecting event itself.

#### Sibling-family audit

∀ change C to factory F in event_family E : apply(C, F) ⇒ audit(siblings(F, E), C.field_shape)

Extending one factory's payload contract within a family ⇒ audit every sibling in the same phase before commit. Asymmetric extensions = latent debt.

### `[universal:mcp]` MCP tool architecture

∀ MCP_tool_implementation: thin HTTP relay to REST endpoint via `transport_utils`.  
∀ tool_handler_body: ¬business_logic ∧ ¬direct_DB_access ∧ ¬internal_imports.  
∀ tool_docstring: include workflow guidance for when to use this tool over alternatives.

```python
# ✅ Correct
@mcp.tool()
async def my_tool(arg: str) -> str:
    async with make_async_client(DEFAULT_CORTEX_URL) as client:
        r = await client.post("/endpoint", json={"arg": arg})
        return r.text

# ❌ Forbidden
@mcp.tool()
async def my_tool(arg: str) -> str:
    db = sqlite3.connect("~/.cortex/cortex.db")  # direct DB access
```

### `[universal:rest]` REST surface

∀ service_with_runtime_state: FastAPI + UDS.  
Standard OpenAI-compatible endpoints ∈ `/v1/*` (`chat/completions`, `models`, …).  
Project-specific ∨ nonstandard endpoints ∈ `/api/v1/*` ∨ another explicit non-`/v1/*` namespace.  
Browser UIs ∪ health checks ∪ non-API surfaces use explicit namespaces (`/cloud-ui`, `/local-ui`, `/health`).  
Custom endpoints under `/v1/*` are forbidden.

Bad → good: `GET /v1/local-models` → `GET /api/v1/local-models`; `GET /v1/gateway-states` → `GET /api/v1/gateway-states`.

REST API preference: ∀ data_access across service boundaries ∨ within services with multiple sources of truth: prefer REST API calls over direct code/catalog/function access. HTTP endpoints are canonical because they reconcile local gateway, federation, WebSocket caches, and other sources while internal data structures change.

Bad → good: `from systems.routing.selection.catalog import get_local_model_ids` in a handler → `GET /v1/models?type=model&source=local`; `context._proxy.gateway_manager.get_gateway()` in pipeline code → HTTP call to Stargate API; reinventing model filtering in each caller → query params on the endpoint.

Projection fidelity: ∀ wire_projection compact/full/filtered view: projected_field_set = contract_with_every_consumer, including renderers.  
Renderer derives V from larger textual payload ⇒ smell; renderer needs V ⇒ projection exposes V as first-class wire field.  
Dropping field F from compact projection ⇒ search renderer dependencies on F.content, not only key presence.  
Bad → good: regex-parse `[web-YYYY-MM-DD-HHMM]` out of `evidence` for `session_tag` → server-side `session_tag` field; drop F because nothing calls `row[F]` while renderers parse F.content → grep substring/regex content use first; compute derived V at render time from fat payload → project V as its own wire field.

### `[universal:satellite]` satellite pattern

Personal workflow microservices follow the satellite pattern: own Docker container + compose project, or host process for lightweight satellites; UDS at `/tmp/universal-protocol/{name}.sock` through shared volume or join `mcp-network` for Docker-network access; never publicly exposed; MCP server reaches satellite via `local_api` relay; `openapi.yaml` is the contract; implementation follows the spec; FastAPI auto-serves `/openapi.json`; Bearer-token auth such as `SERVICE_TOKEN`; all endpoints except `/health` require auth.

### `[scope]` change scope

∀ changed_line: traceable_to(user_request) ∨ direct_consequence(user_request).  
¬unrelated_change_same_diff.  
Test: removing line from diff would not change task completion ⇒ line should not be in diff.

Forbidden: improving adjacent code/comments/formatting encountered during the task; refactoring working code outside the task; removing pre-existing dead code unless explicitly asked; rewriting accurate comments only for phrasing preference.  
Required: match existing style even when another style would be preferred; mention pre-existing problems to the user instead of silently fixing.

Orphan cleanup: if own changes create orphans (unused imports, dead functions) ⇒ clean up. Pre-existing orphan ⇒ leave it ∨ mention it.

Bad → good: reformat file while fixing bug → change only bugfix lines; add type hints to untouched functions → mention gap or fix only if asked; rename adjacent variable → leave it; `While I was here, I also...` → stop and open separate item or mention it.

### `[quality]` quality gates

SLOC: existing files ≤ 400 SLOC; new files ≤ 300 SLOC; exceeded ⇒ split before committing.

Compile/lint/modernize gates:

```bash
python -m compileall -q {files}
ruff check && ruff format --check
ruff check --select=UP --fix
scripts/modularize scan {files}   # SLOC scan; yellow→red transition fails the gate
```

∀ f ∈ touched_files : ¬(pre(SLOC(f)) ≤ 400 ∧ post(SLOC(f)) > 400)

Yellow→red ⇒ extract before recommitting. The SLOC scan is a first-class gate step beside ruff and compileall, not a post-hoc reviewer pass.

∀ function: typed params + return.

Cleanup gates: ∀ deprecated: removed, not commented out. ∀ unused imports/functions/files: deleted. Run `ruff check --select F401 {files}`. ¬`deprecat|obsolete|legacy` mentions in code.

Event-factory dormancy:

- ∀ event_factory F : |emission_sites(F)| = 0 ⇒ delete(F)
- ∀ exception_class E : |raise_sites(E)| = 0 ⇒ delete(E)
- ∀ doc_row R in `docs/event-contracts.md` : ¬∃ live_factory(R.signal) ⇒ delete(R)

Audit: grep `signal_name | factory_name` repo-wide; only the definition site matches ⇒ dormant. Delete factory + matching exception (if no other raise site) + doc row in the same change. Canonical pattern: `PipelineFrontierDispatchBootMismatch` / `BootProviderMismatchError` dormant pair.

#### `[quality:bug-class-sweep]` bug-class sweep

∀ bugfix B targeting code_pattern P in file F : declare_complete(B) ⇒ swept(scope(service(F) ∪ lib(F)), P).

When a fix targets a specific code pattern — a wrong dict key, a mis-named response field, an unguarded None — grep the entire affected service/library for the same pattern before declaring the fix complete. A single-file fix that leaves siblings unswept ships the same bug under a different filename. Canonical failure case: the F3 `result.get("entities", …)` → `result.get("items", …)` fix was scoped to `_evidence_entity_ops.py`; the identical wrong-key pattern survived in `finance.py:226` (silent zero-result `op_status`) and `finance_reconcile.py:48` (dead `entities` fallback) because no sweep ran. Audit: grep the pattern service-wide, fix every instance in the same change, then declare complete.

Exception handling invariant: ∀ caught_exception: emit_event(WARN-level) ∨ re_raise.  
Events are primary telemetry: structured, queryable, durable. Logs are secondary. Bare `logger.warning` / `logger.error` outside event-transport carve-out ⇒ code-review finding.

Pattern actions: caught exception ⇒ `emit_*(...)` with error payload or re-raise; fallback used ⇒ `emit_*(...)` with fallback payload + reason; config error ⇒ raise immediately; using defaults ⇒ `emit_*(...)` with default payload; silent failure ⇒ forbidden; `except CancelledError` ⇒ MUST re-raise; inside `_event_transport`, `libs/event_store/*`, or event-service boot ⇒ `logger.warning/error` permitted as last-resort breadcrumb.

```python
# ✅ Correct — event carries the payload
except Exception as exc:
    emit_equity_fetch_failed(exc=str(exc), fallback=fallback_value)
    raise

# ❌ Forbidden — silent
except Exception:
    result = None
```

Defaults policy: ∀ default: user_configured ∨ emit_event(ERROR-level payload). Agents never invent defaults; ask the user. ¬`getattr(..., default)` for resources.

`[quality:error-envelope]`: ∀ error_response shape = `{code, message, source, retryable, data}`. Use `ProtocolError` from `universal_protocol`. Forbidden: nested `{"error": {...}}`, string error details for capacity/retry scenarios, unstructured 503 errors for capacity conditions.

### `[simplicity]` simplicity

∀ implementation: minimum code solving stated problem; ¬speculative_generality.  
Forbidden: abstractions for single-use code paths; wrappers that add no logic; configurability/extensibility not in request; speculative parameters; features beyond request.  
200 lines that could be 50 ⇒ rewrite before committing.

Root cause rule: ∀ problem encountered during implementation: fix cause, ¬defend around symptom.  
Bad → good: `if x is not None` around unexpected None → fix caller; try/except swallowing error → fix error cause; default for field that should always be set → fix unset path; retry bad input → fix input; normalize malformed data → fix producer.

Defensive code is appropriate for external input (network, file I/O, user input), documented API contracts where None/missing is valid, system boundaries not controlled, or root-cause-out-of-scope with explicit user approval and comment explaining why workaround was accepted.

### `[docs]` documentation contract

Event/API/runtime contract changes ⇒ manual `docs/event-contracts.md` audit. The file is manually maintained, not generated.  
Applied handoff findings that change event signals, event payloads, event semantics, failure modes, coordination behavior, API surfaces, or runtime/user-visible contracts MUST trigger a documentation audit before closure. Other manual docs follow the same audit invariant.

Audit record shape:

```markdown
## Documentation Contract Audit
- `docs/event-contracts.md`: updated | not needed: <reason>
- other docs: <paths>          | not needed: <reason>
```

Generated artifacts, including RAG metadata `scope_vocabulary` and `corpus_hints`, are off-limits to direct edit; fix at classifier or prompt layer, not by hand-editing the DB.

#### Event-contracts row lifecycle

∀ row R in docs/event-contracts.md : ¬∃ live_factory(R.signal) ⇒ delete(R)  
∀ factory F newly added ⇒ add_row(F.signal)  
∀ payload_field_change(F) ⇒ amend_row(F.signal)

Rows are tied to live factories by signal. No row may outlive its factory; no factory may ship without its row. Add, amend, and delete in the same change as the factory edit. (Intentional overlap with event-factory dormancy in `[quality]` — two mental models, two access points.)

#### Module-docstring drift

∀ change C to module M : C.adds_or_removes_top_level_function ⇒ audit(docstring(M).responsibilities_list)

Modules that summarize their contents in a header docstring — especially numbered "N responsibilities" preambles — carry a contract with downstream readers. Adding or removing a top-level function in such a module ⇒ audit the list for staleness in the same change.

### `[docker]` Docker build cache

`--no-cache` means disable layer reuse for that build and refresh base images with `--pull`. It does not mean delete Docker cache globally or claim service-specific cache deletion unless using a dedicated `buildx` builder.

Prefer scoped cache cleanup through dedicated `buildx` builders: `docker buildx prune --builder <workspace-service-builder> ...` and wrappers/scripts that create one builder per image family.  
Forbidden as normal rebuild step: `docker builder prune -af`.  
Forbidden claim: “fresh from source” while still reusing a stale base image.  
Runtime orchestration in `docker compose up` is fine; decouple image builds from global cache state.

### Cross-resident references: URI scheme

Web Claude actively reads `workspaces://` and writes via `fs(sandbox="workspaces", ...)`. Cursor edits both sandboxes. Any future executor seat fetches both via curated MCP set. Therefore: ∀ cross_resident_reference: use URI form, ¬absolute path, ¬resident-specific tool sugar.

Schemes: `cortex://path/within/sandbox` maps to `fs(sandbox="cortex", op="read"|"md_read"|"write"|..., path="path/within/sandbox")` for skills, identity, notes, journals, transcripts, and threads. `workspaces://repo-name/path/within/repo` maps to `fs(sandbox="workspaces", op=..., path="repo-name/path/within/repo")` for source code, docs, plans (`tmp/prompts/...`), reviews (`tmp/reviews/...`), and commits.

Section anchors such as `cortex://agent-skills/foo.md#section-name` map to `op="md_read"`, `section=`; translation is mechanical.

Absolute filesystem paths (`/mnt/torus/...`, `/home/io/...`) are reserved for host-tier infrastructure outside MCP sandboxes: descriptor JSON files, agent-transcripts directory, container mounts, terminals folder. Anything reachable through cortex or workspaces sandboxes uses URI form.

Resident-specific sugar including Cursor `Read`, Cursor `StrReplace`, and absolute paths such as `/mnt/torus/mcp-data/files/...` MUST NOT be cited in artifacts that may be consumed by other residents.

Bad → good: `Read /mnt/torus/projects/universal-llm-gateway/foo.py` in a packet → `workspaces://universal-llm-gateway/foo.py`; `/mnt/torus/mcp-data/files/agent-skills/x.md` → `cortex://agent-skills/x.md`; `StrReplace(...)` in Web-consumable artifact → `fs(sandbox="workspaces", op="md_replace", ...)`.

## Anti-patterns

- Background before invariant ⇒ delete or move after the rule.
- Compatibility scaffolding in this sole-maintainer ecosystem ⇒ delete and update consumers.
- Retired-surface mentions after deletion ⇒ scrub.
- Direct UDS wiring, direct DB access in MCP handlers, local model-id parsing, renderer-side projection derivation, custom `/v1/*` endpoints ⇒ architectural findings.
- Unrequested cleanup, style churn, speculative abstraction, silent defaults, silent exceptions, global Docker cache pruning, resident-specific path sugar ⇒ findings.

## Examples

Failure-mode examples are embedded with each invariant. Use them as review checks, not as happy-path templates.

Packet bug example: deletion task without `[universal:no-bc]` ⇒ reviewer may recommend 422 migration shim; fix packet before review.  
Projection bug example: compact response drops `evidence` because no renderer reads `row["evidence"]`, but renderer regex-parsed `session_tag` from the evidence string; search content dependencies and add `session_tag` to the wire model.  
Telemetry bug example: `except Exception: logger.warning(...)` outside event transport ⇒ emit event with structured payload or re-raise.

## Minimal operating summary

Read before architecture-affecting work. Cite applicable tags. Delete old surfaces completely. Use `transport_utils`, `ModelId`, event factories, REST/UDS, thin MCP relays, canonical error envelopes, URI references, manual docs audits, scoped Docker-cache semantics, strict change scope, and quality gates. Prefer root-cause fixes and minimum code. Never invent defaults, hide exceptions, add compatibility scaffolding, or cite resident-specific paths in cross-resident artifacts.
