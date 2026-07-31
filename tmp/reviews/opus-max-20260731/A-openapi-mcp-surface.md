# Workstream A — OpenAPI over the MCP surface

**Date:** 2026-07-31 · **Checkout:** `/mnt/torus/projects/universal-llm-gateway` · **Seats:** parallel worker A (pass 1), then pass 2 (this document)

Early route map (unblocks other workers): `tmp/reviews/opus-max-20260731/A-rest-route-map.md` — **pass 1, still current; reused, not re-derived.**

**Pass 2 changed two things and preserved the rest:**

1. The hand-maintained `(method, path)` seed is **deleted**. Routes carry native `x-mcp` stamps; the served document is the source of truth. Supersedes pass 1 §"What still must be hand-maintained" row 1 and the first two residual rows.
2. The tier-M question is **answered**, not left at "candidates only". New section **tier-M: gate or property?** supersedes pass 1 §"Tier-M execute allowlist — can it become derivable?".

---

## Question

**Pinned (brief §3-A, unchanged across both passes):**

> What is the canonical description of every ULG endpoint, and can the MCP surface be generated from it rather than hand-maintained alongside it?

Pass 1 narrowed the working increment to deriving the cortex op→route binding from OpenAPI `x-mcp`. Pass 2 keeps that increment and closes it: **derivation with no seed at all**, plus the detectability property that makes the derivation worth having.

Session thesis (brief §2) applies twice over. Pass 1 moved a claim about the served surface into the document but kept a claim about *which routes are reachable* in a Python dict. Pass 2 removes the second claim. And the propagation section below records a live specimen of the same defect class found while verifying: cortex-api's `/health.code_version` **asserts** a SHA it read from the checkout, while the served `/openapi.json` **observes** that the loaded process is 80 minutes stale.

---

## What you found

### Pass 1 findings — verified still true, not re-derived

- Doctrine `decision:http-first-agent-substrate`; pointer `docs/architecture/design/http-first-agent-substrate.md`.
- Three conflicting "how many cortex ops" answers: `canonical.yaml` domain `cortex` = 48 · live wire enum ≈ 61 · `cortex_store.dispatch_ops._OP_SPECS` = **84**.
- REST/UDS map — see `A-rest-route-map.md`. Unchanged.
- Census at HEAD: **84 ops = served 20 / R-b-only 36 / neither 24 / untypeable 4.**

### Pass 2 — the seed was removable, and the reason it looked hard was wrong

The prior pass reported it was structurally prevented from stamping routes because `libs/cortex_store/routes/**` was outside its territory. That was accurate. But there was a second, unreported obstacle it had already hit without recognising it:

- `libs/openapi_mcp/binding.py` `stamp_fastapi_routes()` (pass 1, since deleted) walked `app.routes` looking for `(method, path)` matches. On this FastAPI version `create_app().routes` contains **4 real routes and 32 `_IncludedRouter` objects** — included routers are kept lazy, with `path=None` and no `methods`. The helper would have stamped **0 of 20** routes and returned `0`. It was never exercised, so it never said so.

Route locations were resolved by recursive traversal of `_IncludedRouter.original_router.routes`, giving all 20 endpoints with file:line:

| op | route | endpoint | source |
|---|---|---|---|
| `activate` | `GET /assertions/activate` | `activate` | `libs/cortex_store/routes/graph.py:124` |
| `analyze_impact` | `POST /assertions/analyze-impact` | `analyze_impact_semantic` | `libs/cortex_store/routes/graph.py:80` |
| `impact` | `GET /edges/impact` | `impact_analysis` | `libs/cortex_store/routes/graph.py:31` |
| `assert` | `POST /assertions` | `create_assertion` | `libs/cortex_store/routes/assertions/_create.py:59` |
| `assertions` | `GET /assertions` | `list_assertions` | `libs/cortex_store/routes/assertions/_list.py:207` |
| `search` | `GET /assertions/search` | `search_assertions` | `libs/cortex_store/routes/assertions/_search.py:263` |
| `supersede` | `POST /assertions/supersede` | `supersede_assertion` | `libs/cortex_store/routes/assertions/_supersede.py:82` |
| `audit` | `GET /boot-audit-counters` | `boot_audit_counters` | `libs/cortex_store/routes/boot/audit_counters.py:15` |
| `deadlines` | `GET /deadlines` | `list_deadlines` | `libs/cortex_store/routes/deadlines.py:108` |
| `edges` | `POST /edges` | `create_edge` | `libs/cortex_store/routes/edges.py:60` |
| `edge_types` | `GET /edges/types` | `list_edge_types` | `libs/cortex_store/routes/edges.py:297` |
| `entities` | `GET /entities` | `list_entities` | `libs/cortex_store/routes/entities.py:93` |
| `relationships` | `GET /relationships` | `list_relationships` | `libs/cortex_store/routes/relationships.py:50` |
| `render_subgraph` | `GET /subgraph/render` | `render_subgraph_route` | `libs/cortex_store/routes/subgraph.py:32` |
| `walk_subgraph` | `GET /subgraph/walk` | `walk_subgraph_route` | `libs/cortex_store/routes/subgraph.py:83` |
| `resolve` | `GET /resolve` | `resolve_cortex_uri` | `libs/cortex_store/routes/resolve.py:89` |
| `stats` | `GET /stats` | `get_stats` | `libs/cortex_store/routes/stats.py:23` |
| `surface_forms` | `GET /surface-forms` | `list_surface_forms` | `libs/cortex_store/routes/surface_forms.py:24` |
| `todo_audit` | `GET /todo-audit` | `get_todo_audit` | `libs/cortex_store/routes/todo_audit.py:16` |
| `todo_candidates` | `GET /todo-candidates` | `get_todo_candidates` | `libs/cortex_store/routes/todo_retrieval.py:272` |

**Native stamping is the right mechanism — no FastAPI counter-evidence.** `openapi_extra` is deep-merged into the generated operation (`fastapi/openapi/utils.py`), so `x-mcp` composes with `response_model`, `status_code`, `summary` and auto-generated `operationId` without conflict. All four decorator shapes present in these files took the stamp (bare path, `response_model=`, `response_model=None`, `status_code=`, multi-line with `summary=`). Byte-level confirmation below.

### What still must be hand-maintained (revised)

| Artifact | Why hand / seed | Change vs pass 1 |
|---|---|---|
| ~~`mcp_route_seed()` — 20× `(METHOD, path)`~~ | — | **SUPERSEDED — deleted.** Routes are natively stamped. |
| **`UNTYPEABLE_OPS`** (4) | Adapter-orchestration; no HTTP SOT target by design. This is a *declared exemption*, and an exemption you can read is not the same defect as a table you can't. | unchanged |
| **`canonical.yaml` json_schema rows** | Still the pushed MCP descriptor SOT (132 KB, `generated_by: hand-edit`). W5. | unchanged |
| **Class C surfaces** (`manage`, host-bound `fs`/`quality`) | No OpenAPI document; `manage` is JSON-RPC. | unchanged |
| **Class B untyped bodies** | Routes exist, OpenAPI carries nothing usable. | unchanged |
| **Tier-M allowlist** | **Human ratification is load-bearing — see the argument below.** | strengthened from "not reducible to `readonly` alone" |
| **Exemption / death-path policy** | Telemetry gate + binary partition remain policy. | unchanged |

---

## tier-M: gate or property?

**Verdict: the gate stays. `read_only` is neither necessary nor sufficient for unattended execution, and no op-level schema property is sufficient — because the safety of the two decisive cases depends on *arguments*, not on the operation.** The derivable win is real but is a different thing than the operator hypothesised: not *the allowlist*, but *coverage of the allowlist*.

The operator's framing was explicitly a hypothesis. This is the answer against it, with the evidence.

### The 54-vs-5 discrepancy, explained rather than reported

`config/mcp/canonical.yaml` (120 tool rows: 60 `write`, **54 `read_only`**, 4 `mutating`, 2 `destructive`) against `services/git_integration_worker/cursor_auto/tier_m_manifest.py` `DEFAULT_MANIFEST` (operator-ratified 2026-07-29, `PENDING_OPERATOR_BIND = False`), which allows 5 ops. The 49-op gap is not one population:

| Partition of the 54 `read_only` ops | n | What it means |
|---|---:|---|
| Already allowed | 3 | `cortex.search`, `cortex.entity_get`, `observability.query` |
| **Explicitly denied by a ratified wildcard row** | **11** | `fs.read`, `fs.list`, `fs.search`, `fs.read_multi`, `fs.md_read`, `fs.md_list`, `manage.status`, `manage.health`, `manage.wait_healthy`, `pipeline.iterate`, `pipeline.consult` |
| No manifest row — deny-by-default | 40 | the genuinely-unconsidered tail |

**So the answer to "are the 49 genuinely safe ops nobody bothered to allowlist" is: at least 11 are not.** They are ops an operator looked at, knew were reads, and denied anyway — with the reasons written into the manifest notes: `fs.*` "repo and share writes belong to implement, not to a tool relay"; `manage.*` "substrate lifecycle — operator surface only"; `pipeline.*` "spends dispatch capacity". Deriving `allow = read_only` would flip eleven ratified denials to allow. That is not filling in an incomplete list; it is overturning decisions.

The reasons name three things `read_only` does not model:

- **Authority / blast radius.** `fs.read` mutates nothing and can read anything on the host. Read-only is a statement about writes; the concern here is reach.
- **Cost.** `pipeline.iterate` is annotated in canonical as "Read-only per call" and spends real dispatch capacity per call. Read-only is not free.
- **Surface ownership.** `manage.wait_healthy` is annotated `¬side-effects` and *blocks* — and `manage` is the operator's own console.

And of the remaining 40, `read_only` is demonstrably wrong about at least two:

- **`agent_bus_read.fetch_unread`** — classed `read_only`, and its own `fol_descriptor` in `canonical.yaml` says: *"mark_read=true marks matching unread turns read (read-cursor side effect)."* The row's safety class is a property of the **default arguments**, not of the operation.
- **`cortex.case_audit` / `cortex.todo_audit`** — classed `read_only`. `libs/cortex_store/dispatch_ops/ops_audit.py:29` defaults `emit=True`, and `libs/cortex_store/routes/boot/audit_counters.py:21-29` documents why that matters: a full audit emits one `cortex.audit.gap.detected` per gap, **~17k events per boot**, "write-amplifying the Event Service and breaching the INSPECT no-side-effects contract". A read-only op that writes seventeen thousand rows.

### `email.pull` — the decisive case, resolved

The prior pass reported `email.pull` as allowlisted but not canonically `read_only`. The stronger fact: **`email` has no row in `canonical.yaml` at all.** The registry's domains are `agent_bus, agent_bus_read, close, cortex, cortex_brief, cursor_request, delegate, dispatch, fs, git_*, imprint, manage, notify, observability, panel_dispatch, pipeline, project_ask, rag, retrieve, team_dispatch, tool_search` — no `email`, no `sms`, and `email` is not in `overflow_register_surfaces` either. It is reached through the generic `dispatch(tool="email", …)` route, whose own canonical row is `mandate_safety: write`.

So **2 of the 5 allowlisted ops are invisible to the schema the derivation would read.** A derived allowlist would not "remove `email.pull`" as a considered judgement — it would fail to see it, and email would silently leave tier-M. That is precisely the failure mode of the route seed this same session just deleted: *the artifact cannot represent the thing it is missing.* Rebuilding it one layer up would be the same mistake in a new costume.

On the substance, neither artifact is wrong; **they answer different questions.** `read_only` asks *does this call change stored state?* — and `email.pull` does: it writes new mail rows locally. The manifest asks *may an unattended agent fire this, and is re-firing after an ambiguous failure safe?* — and `email.pull` passes: it is `idempotence="idempotent"`, scoped by mode + mailbox/folder, and its mutation is only the local materialisation of state that already exists on the server, which any later read would have observed anyway. **A mutating-but-idempotent, externally-invisible op is exactly the shape that fails `read_only` and passes tier-M.** So `read_only` is not necessary either. It is wrong in both directions: false-negative on `email.pull`, false-positive on eleven ratified denials plus at least two mislabelled rows.

### What property *would* be sufficient — and why none is

The manifest already carries the right axes; it just does not get them from a schema. A row states `idempotence ∈ {idempotent, at-most-once}` and `authority ∈ {code, life}`, and the free-text notes carry the rest. A candidate sufficient predicate for unattended execution is the conjunction:

1. **No external-world effect** — nothing leaves the boundary in the operator's voice or name (`email.send`: "outbound speech as the operator").
2. **Idempotent** — re-issue after an ambiguous failure is safe by declaration. This is the operationally load-bearing one, because the tier-M failure mode is *"did my call land?"* — the session thesis again.
3. **Reversible, or observably undoable** — `email.move` is denied for exactly this: "mutates mailbox state with no observation path to undo it."
4. **Bounded authority** — a named scope, not "any path on the host".
5. **Bounded cost** — no unbounded dispatch/compute spend.

Note that `read_only` is at best a weak proxy for (3), and says nothing about (1), (2), (4) or (5). Idempotence in particular is *orthogonal* to read-only, which is why the manifest tracks it separately.

Could a schema carry all five? Individually, yes — they are declarable. But two things break the derivation:

- **The unit is wrong.** `fetch_unread(mark_read=True)` and `audit(emit=True)` are unsafe *arguments* of safe operations. A sufficient gate must be over (op, argument-region), not op. A schema *can* express that — `x-mcp: {unattended: {allowed: true, requires: {mark_read: false}}}` — but at that point you are authoring the admission policy in the schema. You have relocated the hand-maintained table, not eliminated it. The route-binding case succeeded because a route's identity genuinely lives at the route; unattended-execution risk does not. Nothing about the definition of `fs.read` determines whether this operator wants an unattended agent reading arbitrary host files tonight.
- **Half of the answer is not a fact about the code.** "`manage` is the operator's surface", "spends dispatch capacity that belongs to a nested contract", "repo writes belong to implement" are *policy about who does what*, contingent on the current fleet arrangement. A schema can record such a decision; it cannot derive it. Deny-by-default plus per-row ratification is the correct level, and `PENDING_OPERATOR_BIND` / DISPOSITION is the correct widening path.

**So the gate stays a gate.** The operator gate does not convert into a property.

### What *is* derivable, and worth building

The honest win is the same shape as Job 1 — not derivation, **detectability**:

1. **Candidate generation + CI drift.** Project `mandate_safety` (and, better, explicit `x-mcp.idempotence` / `x-mcp.authority`) into a candidate set and diff it against `DEFAULT_MANIFEST` in CI. A read-only op newly added with no manifest row surfaces as an unratified candidate; a manifest `allow` row whose schema says `destructive` surfaces as a contradiction. Ratification stays human; **noticing** becomes mechanical.
2. **Coverage, which is the actual current hole.** Two of five allowlisted ops are outside the registry entirely. There is no artifact today that can tell you tier-M references an op the schema has never heard of. That is `unbound_dispatch_ops()` for the allowlist, and it is cheap.
3. **Fix the two mislabels regardless.** `agent_bus_read.fetch_unread` and the `audit` family are classed `read_only` while documenting side effects, in a file whose classifications other systems read. Out of scope here (brief §3-A: describe, do not fix) — filed as residuals below.

---

## What you changed

| SHA | Pass | Change |
|---|---|---|
| `806722dd` | 1 | New `libs/openapi_mcp/` — `extract_typed_routes` / `inject_x_mcp` / `stamp_fastapi_routes`, `registry.default_registry`, hermetic tests |
| `76148b0f` | 1 | Cortex openapi_mcp consumes derivation; seed reduced to `(METHOD, path)`; `operationId` from live OpenAPI; `--write-openapi` + `--services` |
| `5094288b` | 1 | Generated `config/mcp/generated/cortex.openapi.json` — 82 paths, 20 `x-mcp` ops |
| **`dba38ed7`** | **2** | **Native `x-mcp` route stamps on all 20 cortex routes; seed deleted; fail-closed detectability + tests; `stamp_fastapi_routes` removed** |

`dba38ed7` in detail — 27 files, +273 / −165:

- **20 route decorators stamped** with `openapi_extra=x_mcp("<op>")` across 16 files in `libs/cortex_store/routes/**`.
- **`libs/openapi_mcp/binding.py`** — added `x_mcp()` helper (the stamp constructor); **deleted `stamp_fastapi_routes()`** as dead and non-functional (see `_IncludedRouter` finding). `inject_x_mcp()` retained for services not yet natively stamped — agent-bus dry-run still uses it.
- **`libs/cortex_store/openapi_mcp/_route_map.py`** — `_MCP_ROUTE_SEED`, `mcp_route_seed()`, `_legacy_typed_route_by_op()` and `TYPED_ROUTE_BY_OP` **all deleted**. `typed_routes_from_openapi()` is now a straight `extract_typed_routes()`. Added `served_ops()` and **`unbound_dispatch_ops()`**.
- **`census.py` / `bijection.py`** — served bucket derived from document stamps; census carries `served_routes` read out of the document instead of the seed.
- **`scripts/openapi_mcp_codegen.py`** — no seed injection for cortex; new **`--unbound`** listing every dispatch op with no stamp and no exemption (60 today).
- **`services/mcp-server/tools/cortex_named_tools/_surface_render.py`** — one docstring line, pointing at `served_ops` instead of the deleted `TYPED_ROUTE_BY_OP`.

### The seed is gone — and a missing stamp is detectable

Three independent proofs, all in `libs/cortex_store/openapi_mcp/test_openapi_mcp.py`:

- `test_no_hand_maintained_route_seed_remains` — asserts `mcp_route_seed`, `_MCP_ROUTE_SEED` and `TYPED_ROUTE_BY_OP` are absent from the module. Deleted, not relocated.
- **`test_missing_stamp_is_detectable_not_silent`** — the point of the exercise. Takes the live schema, asserts `assert` is bound and `check_generated_module` is `True`; then deletes `paths["/assertions"]["post"]["x-mcp"]` and asserts all three of: `assert` appears in `unbound_dispatch_ops()`, `assert` is gone from the derived manifest, and **`check_generated_module()` is `False`** — i.e. `openapi_mcp_codegen.py --check` exits non-zero. The old seed's failure mode was silence; the new one is a red CI.
- `test_new_op_without_a_stamp_shows_up_unbound` — a dispatch op added with no stamped route is returned by `unbound_dispatch_ops()`, so the "op with no entry" class is enumerable rather than invisible. `test_unbound_ops_enumerate_the_strangler_gap` pins the partition: unbound ≡ `rb_only ∪ neither`, disjoint from served and from the exemptions.

One further piece of evidence that the stamps are faithful: **`config/mcp/generated/cortex.openapi.json` is byte-identical after regeneration** (`git status` reports it unmodified). The natively-stamped document reproduces the seed-injected one exactly — the seed was removed without changing what is served.

**Verification (quoted):**

```
21 passed, 25 warnings in 3.95s
```
(`pytest libs/openapi_mcp/test_binding.py libs/cortex_store/openapi_mcp/test_openapi_mcp.py -q`)

```
check=0
```
(`scripts/openapi_mcp_codegen.py --check`)

```
| served | 20 | Typed OpenAPI route exists (operationId) |
| R-b-only | 36 | canonical.yaml json_schema, no typed route yet |
| neither | 24 | dispatch-handler only; route to mint or vanish |
| untypeable | 4 | adapter-orchestration; no HTTP SOT target |
```
(`scripts/openapi_mcp_codegen.py --census` — **unchanged from pass 1**, total 84)

```
cortex: paths=82 x-mcp-ops=20 facade=cortex
agent-bus: paths=24 x-mcp-ops=0 facade=agent_bus
```
(`--services`; cortex now reports 20 with `seed_bindings=None`)

```
unbound ops: 60
```
(`--unbound`; = 36 R-b-only + 24 neither, exemptions excluded)

`ruff check` clean and `compileall` OK on all touched Python.

---

## What you did NOT change and why

- **Tool semantics / MCP handlers** — brief §3-A out of scope: describe, do not fix.
- **The tier-M manifest** — `services/git_integration_worker/**` is a sibling's territory this session, and the argument above concludes it should not change anyway. Read only.
- **`canonical.yaml`** — including the two mislabelled `read_only` rows (`agent_bus_read.fetch_unread`, the `audit` family). Correcting a safety classification other systems read is a ratification-shaped act, not a drive-by. Residual below.
- **`libs/cortex_store/routes/session_journals.py`** — carries two pre-existing `ruff` failures at HEAD (`F401` unused `_FILES_ROOT` re-export at line 7, `I001` at line 147), committed in `41afde60`. Not mine, unrelated to the task, and the `_FILES_ROOT` import is a deliberate re-export that a naive fix would break. Flagged, untouched.
- **agent-bus / rag / GIW `x-mcp` harvest** — W1. The registry dry-runs agent-bus at 0 stamped ops; stamping it is the same mechanical pass now proven on cortex.
- **`POST /dispatch` retirement** — telemetry death-path gate unmet; `todo:openapi-mcp-dispatch-retire`.
- **Sibling territory** — `services/git_integration_worker/**`, `libs/charter_runner_store/**`, `services/cdp-ask/**`, `libs/cdp_ask/**`, `libs/claude_bundles/**`, `project_ask.py`, `frontier.py`, `.gitignore`. Two GIW files were dirty in the shared checkout throughout; left alone and excluded from the commit by explicit path staging.
- **Gateway `:9998` / cdp-ask `:8770` liveness** — observed unreachable while `manage` reports running; not this workstream.

---

## PROPAGATION REQUIRED (observed)

**cortex-api must be restarted. This is the first wave in this workstream that changes what a running service serves** — pass 1 was build-time only.

Observed, not inferred:

| Probe | Observation |
|---|---|
| `ss -ltnp` | `:8202` held by pid **1173844** |
| `ps -o lstart` pid 1173844 | started **Fri 2026-07-31 12:51:46 −0700** (19:51:46Z) |
| `ps -o lstart` pid 1173134 (UDS `cortex-api.sock`) | started **12:51:44 −0700** (19:51:44Z) |
| `curl :8202/openapi.json` | 82 paths, **`x-mcp` ops = 0** |
| `curl --unix-socket …/cortex-api.sock /openapi.json` | 82 paths, **`x-mcp` ops = 0** |
| `create_app().openapi()` at HEAD | 82 paths, **`x-mcp` ops = 20** |

So both cortex-api processes predate `dba38ed7` (committed 21:10:28Z) by ~80 minutes and serve the un-stamped document. **Landed, not live.**

| Service | Why | At SHA | Action |
|---|---|---|---|
| **cortex-api** (TCP `:8202` **and** UDS `cortex-api.sock` — both, they are separate processes) | Serves `/openapi.json`; native stamps only appear after reload | `dba38ed7` | `manage sync_restart` per `restart-drain-discipline`; then verify `curl :8202/openapi.json` reports **20** `x-mcp` ops, and the same over UDS |
| CI / developer checkouts | Pick up libs + codegen by checkout; no process | `dba38ed7` | none |
| mcp-server | Docstring-only touch; no import path changed | `dba38ed7` | none required for this land |
| agent-bus, rag, GIW | W1 harvest, not started | pending | later |

**Verification for whoever propagates:** `x-mcp` count over the served document is the observation. `--check` exiting 0 on disk is not evidence the service is live, and neither is `/health`.

### A live specimen of the session thesis, found while verifying this

`/health` on **both** cortex-api processes reports `deploy_mode: source_synced` and a `code_version` — and that field is read from the checkout at request time, not from the loaded process:

- 21:10:45Z, TCP `:8202` → `code_version: dba38ed7…` — **my commit, made 17 seconds earlier**, by a process running since 19:51:46Z.
- 21:11:59Z, UDS → `code_version: 82f07260…` — **a different SHA again**, a sibling worker's commit landed in the intervening 74 seconds, from a process running since 19:51:44Z.

Two probes, 74 seconds apart, two different reported versions, zero restarts. `code_version` here is an assertion about `git HEAD`, presented in a health envelope where a reader will take it as an observation of the running binary. It is the propagation-ledger defect (brief §2) with a different field name, and it would have let this very session claim its work was live. The served OpenAPI document — 0 stamps — is the observation that falsifies it. **Recommend: `code_version` should report the loaded module's provenance, or be renamed to something that does not read as a statement about the process.** Filed as a residual; it is Workstream C's shape, not A's, and I did not change it.

---

## Open questions and residuals

| Residual | What would settle it |
|---|---|
| **cortex-api restart** (above) | `manage sync_restart`, then observe 20 `x-mcp` ops on both the TCP and UDS documents |
| `/health.code_version` reports checkout HEAD, not loaded code | Bind it to the module actually imported (or to the process's start-time SHA) and re-probe; two probes minutes apart with an intervening commit must return the same value |
| W1: stamp agent-bus / rag / GIW routes | Same mechanical pass as `dba38ed7`; `--services` currently shows agent-bus at 0. `inject_x_mcp` exists for the interim |
| 60 unbound dispatch ops (36 R-b-only + 24 neither) | W2 binary partition per OMDR A1 — mint a typed route or remove from reachable. `--unbound` now enumerates them |
| `canonical.yaml` vs `_OP_SPECS` vs wire enum (48 / 84 / ~61) | W5: generate the canonical projection from OpenAPI; one census in CI |
| **`read_only` mislabels** — `agent_bus_read.fetch_unread` (documents `mark_read` side effect), `cortex.case_audit` / `cortex.todo_audit` (`emit=True`, ~17k events) | Operator ratification of the corrected class; these rows are read by other systems, so a drive-by edit is the wrong shape |
| **tier-M coverage hole** — `email.*` is in the allowlist and absent from `canonical.yaml` and from `overflow_register_surfaces` | Either register the `email` domain, or add an explicit "off-registry, allowlisted" declaration. Until then no CI diff over tier-M can be complete, and it will not know that it is incomplete |
| tier-M candidate diff in CI | Project `mandate_safety` + new `x-mcp.idempotence` / `x-mcp.authority` into candidates; diff against `DEFAULT_MANIFEST`; report contradictions. Ratification stays human (argued above) |
| `libs/cortex_store/routes/session_journals.py` ruff debt at HEAD | Owner decides whether `_FILES_ROOT` re-export should be `__all__`-declared or `# noqa: F401` |
| Class C host-bound fork (`manage` JSON-RPC, host-bound `fs`) | Wide-detent architecture consult before W4 |
| gateway `:9998` / cdp-ask `:8770` "running" but refusing TCP | A seat with `manage`; same claim-vs-observation class as `code_version` above |

---

## Verdict

**Yes — and for cortex it is now done rather than demonstrated.** The MCP op→route surface is generated from the served OpenAPI document with **no hand-maintained table of any kind**: 20 routes declare their own bindings, the codegen reads them out, and the previously-invisible failure mode — an op with no entry — is now enumerable (`--unbound`) and CI-fatal (`--check` on the committed manifest). The generated document is byte-identical to the seeded one, so nothing about the served surface changed while the seed was removed.

What remains hand-maintained is smaller and honest: declared exemptions (4 untypeable ops), the canonical descriptor push, Class B/C surfaces, and tier-M policy.

On tier-M the operator's hypothesis does not survive contact. `read_only` is wrong in both directions — false-negative on `email.pull`, false-positive on eleven ops a ratified operator decision explicitly denies — and the two decisive counter-examples turn on arguments rather than operations, which no op-level schema property can capture. Encoding the policy into the schema would relocate the hand-maintained table rather than eliminate it: the same mistake this session just finished undoing one layer down. **The gate should stay a gate.** What should be built instead is the thing the seed taught us: make the *absence* of a ratification visible, so an unratified op is enumerable rather than silent.
