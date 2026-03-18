# RAG Operational Visibility: Admin API + CLI

**Document type:** Architecture proposal (pre-implementation)  
**Date:** 2026-03-17  
**Status:** Draft — architect validation applied (`/consult-architect`, 2026-03-17)  
**Scope:** RAG service (`services/rag/`), admin HTTP surface, operator CLI  
**Aligned with:** `services/rag/README.md` (indexing lifecycle, pending journal)

---

## Table of contents

| § | Topic |
|---|--------|
| [1](#1-executive-summary) | Executive summary |
| [2](#2-problem-and-goals) | Problem and goals |
| [3](#3-current-state) | Current state |
| [4](#4-semantics-pending-journal) | Semantics: pending journal |
| [5](#5-proposed-solution) | Proposed solution |
| [6](#6-api-contract) | API contract |
| [7](#7-cli-contract) | CLI contract |
| [8](#8-non-goals-and-alternatives) | Non-goals and alternatives |
| [9](#9-security-and-operations) | Security and operations |
| [10](#10-event-vocabulary) | Event vocabulary |
| [11](#11-implementation-phases) | Implementation phases |
| [12](#12-success-criteria) | Success criteria |
| [13](#13-open-questions) | Open questions |
| [14](#14-consultation) | Consultation |

---

## 1. Executive summary

Operators lack a **single, authoritative** view of RAG indexing backlog and health. The system already maintains a **SQLite pending journal** (`pending` table in `rag_metadata.db`) that tracks every source path from “index started” until “index finished” (success, skip, or terminal failure). That set is the correct proxy for “files not yet done,” including bulk directory reindexes where all paths are pre-marked before concurrent work.

This proposal adds:

1. **`GET /indexing/status`** on the RAG admin surface — returns pending count, optional capped path sample, Chroma chunk count, watcher summary, and optional failed-extraction counts.
2. **`scripts/rag-status`** — thin CLI over the same URL resolution as `libs/transport_utils/rag_client.py` (UDS default, TCP from `stargate.yaml`).

No new persistent state. No change to indexing semantics.

---

## 2. Problem and goals

### 2.1 Problem

- **P1:** Operators cannot answer “how much indexing work is left?” without opening SQLite or inferring from logs.
- **P2:** Existing **`GET /stats`** exposes only Chroma chunk count; it does not surface pending or watcher context in one response.
- **P3:** Direct SQLite reads against `rag_metadata.db` while RAG is running are **not** the service’s contract — locking and path divergence risk misleading scripts.

### 2.2 Goals

| ID | Goal |
|----|------|
| G1 | Expose **pending count** (and optionally a **bounded sample** of paths) from the **live RAG process** that owns the DB. |
| G2 | Provide a **stable JSON** shape for automation and a **human summary** for terminals. |
| G3 | Reuse existing transport resolution; **no new services**. |
| G4 | Keep the admin surface **cheap** — O(1) or O(limit) for pending; avoid full collection scans beyond what `/stats` already does. |

### 2.3 Non-problems (explicit)

- Real-time per-file progress bars (extraction sub-stages).
- Exposing Stargate or Gateway queue depth (out of scope — different hop).

---

## 3. Current state

| Mechanism | Location | Today |
|-----------|----------|--------|
| Pending journal | `property_index.py` — table `pending`, `mark_pending` / `clear_pending` / `get_pending_files()` | Used at index start; cleared in `finally` in `indexing.py`; bulk pre-mark in `admin_routes._bulk_premark` |
| Chunk count | `GET /stats` | `StatsResponse`: `count`, `collection` |
| Watchers | `GET /watch/status` | Per-path `reload_count`, `error_count`, `enabled` |
| Failed extractions | `PropertyIndex.get_failed_chunks()` | Used by admin routes; not aggregated in one “status” payload |

**Source trace:** `services/rag/property_index.py` (pending), `services/rag/admin_routes.py` (`/stats`, `/watch/status`), `services/rag/rag_service/indexing.py` (pending lifecycle).

### 3.1 Integration points (discovered during validation)

| Integration | Detail |
|-------------|--------|
| **Failed extraction counts** | `PropertyIndex.get_failed_count()` → `COUNT(*)` on `failed_extractions` (chunk-granularity). `get_permanent_count()` exists for abandoned chunks — optional second field `failed_extractions_permanent_count`. |
| **Pending reads** | `get_pending_files()` returns full list — **do not** call for status; implement `COUNT(*)` + `SELECT file FROM pending LIMIT ?` (sync, same connection pattern as `get_pending_files`) to avoid O(N) allocation on large backlogs. |
| **Stargate :9999** | RAG admin is **not** generically proxied. Existing passthroughs are explicit routes in `rag_articles.py` (`/api/v1/rag/article`, `/rag/source`, …). **`/indexing/status` is RAG-only** until a new Stargate route is added (mirror `_proxy_rag_request` pattern). |
| **Edge case** | If `prop_index is None` after `mark_pending` (pathological), `indexing.py` `finally` does not clear pending (`logger.error` only) — stuck rows possible; reconciliation on restart still processes `get_pending_files()`. Document in operator help, not blocking for this API. |
| **Naming** | `StatsResponse` uses field `count` for chunks; proposed JSON uses `chunks` for clarity. Implementing model may alias or map — document in API for CLI consumers. |

---

## 4. Semantics: pending journal

**Invariant (document for operators):**

- ∀ file undergoing index: row ∈ `pending` from first `mark_pending` until `clear_pending` in `finally`.
- Bulk directory index: **all** walked files are pre-marked pending before workers run → **`COUNT(pending)` ≈ remaining work** (queued + in-flight) until the batch completes.
- Steady state (RAG idle, no crashed mid-flight): **`pending` should be empty** (count 0). Non-zero after idle implies stuck reconciliation, crash, or very slow in-flight work.

**Not** modeled: depth of in-memory `asyncio.Queue` inside `WatcherManager` — ephemeral; pending table is the durable operational signal.

---

## 5. Proposed solution

### 5.1 New admin endpoint

**`GET /indexing/status`**

- Implemented in `admin_routes.py` (same router as `/stats`).
- Uses `get_property_index_fn()` → `get_pending_files()` or dedicated `COUNT(*)` + `LIMIT` query for sample (prefer single query for count + sample to avoid loading huge lists).

### 5.2 CLI

- **`scripts/rag-status`**: calls `/indexing/status` (+ optionally `/stats` if folded into one response).
- Flags: `--json`, `pending --limit N` (if list not in default payload, either second request `GET /indexing/pending?limit=` or include in status — see §6).

---

## 6. API contract

### 6.1 `GET /indexing/status`

**Response (JSON), proposed:**

```json
{
  "pending_count": 0,
  "pending_sample": [],
  "pending_sample_truncated": false,
  "chunks": 58204,
  "collection": "universal_rag",
  "watchers": [
    {
      "path": "/abs/path",
      "enabled": true,
      "reload_count": 3,
      "error_count": 0
    }
  ],
  "failed_extractions_count": 0,
  "property_index_available": true
}
```

| Field | Rule |
|-------|------|
| `pending_count` | `SELECT COUNT(*) FROM pending` when property index up; else `0` with `property_index_available: false` |
| `pending_sample` | First ≤20 paths (configurable query param `sample_limit`, max 100) — optional; omit empty array if `sample_limit=0` |
| `chunks` / `collection` | Same as current `/stats` (single Chroma `count()`) |
| `watchers` | Same shape as `/watch/status` |
| `failed_extractions_count` | **`PropertyIndex.get_failed_count()`** — row count = failed **chunks** (one source may contribute many rows). Optional: `failed_extractions_permanent_count` via `get_permanent_count()`. |

**Query parameters (optional):**

- `sample_limit` — default `20`, max `100`, `0` to omit sample list.

**Errors:**

- RAG up but Chroma unavailable: return 503 with error envelope consistent with other admin routes (or partial JSON with nulls — **decision:** prefer partial with `property_index_available` / explicit chroma_error string for CLI).

### 6.2 Optional second endpoint

**`GET /indexing/pending?limit=500&offset=0`** — paginated full list for deep debugging. **Phase 2** unless architect mandates single endpoint only.

---

## 7. CLI contract

| Invocation | Behavior |
|------------|----------|
| `rag-status` | Human table: pending count, chunks, watchers (one line each), failed extractions |
| `rag-status --json` | Raw `GET /indexing/status` JSON |
| `rag-status pending` | Print pending paths (uses paginated endpoint or large `sample_limit` — cap documented, e.g. 500) |
| Env / flags | `--url` override; else `resolve_rag_base_url()` |

**Exit codes:** 0 success; non-zero on HTTP error or connection failure (RAG down).

**Help text (mandatory one-liner):**  
*Pending count includes in-flight indexing; after idle it should be zero. Large counts during `reindex_directory` are normal.*

---

## 8. Non-goals and alternatives

| Alternative | Why not |
|-------------|---------|
| CLI-only SQLite read | Bypasses RAG; WAL/lock and path mismatch |
| MCP-only tool | Operators and CI want shell + JSON without MCP token |
| Event Service as sole source | Pending is state in SQLite, not event-sourced count |
| New signals per pending change | High cardinality; noisy |

---

## 9. Security and operations

- Admin routes today are **UDS/TCP boundary** — same trust model as `/stats`, `/clear`, `/reindex_directory`. **No new auth layer** in this proposal (consistent with existing RAG admin).
- Endpoint must stay **bounded**: no unbounded `pending` dump on default path.
- Load: `COUNT(*)` + small sample is negligible vs embedding work.

---

## 10. Event vocabulary

| Change | Signal | Action |
|--------|--------|--------|
| New read-only endpoint | — | **None** (observation via HTTP only) |

If future work emits e.g. `rag.indexing.backlog.snapshot` for dashboards, that would be a separate proposal.

---

## 11. Implementation phases

| Phase | Deliverable | Risk |
|-------|-------------|------|
| **P0** | `GET /indexing/status` + pydantic model in `services/rag/models.py` | Low |
| **P1** | `scripts/rag-status` + one-line README pointer in `services/rag/README.md` | Low |
| **P2** (optional) | `GET /indexing/pending` paginated; MCP `dispatch` wrapper optional | Medium (surface area) |

**Verification:** `curl` via UDS; `rag-status --json` with RAG up/down; quality gate on touched files.

---

## 12. Success criteria

1. With RAG running, operator can run **`rag-status`** and see **pending_count** matching `SELECT COUNT(*) FROM pending` inside the same process.
2. During bulk reindex, **pending_count** decreases to 0 when complete (modulo reconciliation).
3. **JSON output** is stable enough for scripts (field names versioned in doc).
4. No regression on `/stats` latency budget for default deployments.

---

## 13. Open questions

1. ~~**Partial failure:**~~ **Resolved:** If property index is unavailable → `property_index_available: false`, `pending_count: 0`, `pending_sample: []`. If Chroma client fails → expose `chroma_available: false` (or omit `chunks` / null) + HTTP 200 with degraded flags so CLI still prints pending (preferred over hard 503 for half-degraded ops).
2. ~~**failed_extractions_count:**~~ **Resolved:** Use **`get_failed_count()`** (chunk rows). Document in CLI help that the number is chunk-level, not file-level.
3. **Stargate passthrough:** **Deferred product decision.** Default: UDS/TCP to RAG sufficient for local operators. Remote-only access → add `GET /api/v1/rag/indexing_status` on Stargate proxying to RAG `/indexing/status` (explicit route, not generic forward).

---

## 14. Consultation

**Source:** `/consult-architect` run on 2026-03-17  
**Models:** *Cloud consult pipeline (`consult-architect`) returned HTTP 500 — no model output recorded.*  
**Scopes:** `project rag_systems` (RAG returned no chunks for this query; validation relied on attached sources + code review)

### Agent validation (pre-/post-consult)

- **Schema:** `pending(file TEXT PRIMARY KEY)` matches spec. `failed_extractions` is keyed by `chunk_id`; counts are chunk-based — spec table updated to use existing `get_failed_count()`.
- **Performance:** Avoid `get_pending_files()` on the hot path; use bounded SQL for count + sample.
- **Stargate gap:** Spec previously under-specified :9999 exposure; §3.1 documents explicit passthrough requirement.
- **Invariant nuance:** `clear_pending` in code runs in `finally` (not only success); operator semantics in §4 remain valid. `PropertyIndex.clear_pending` docstring (“after successful indexing”) is misleading vs behavior — optional docstring fix in implementation PR.

### Key findings (unified)

| Finding | Fix |
|---------|-----|
| Full pending list API would duplicate `get_pending_files()` memory risk | COUNT + LIMIT only on `/indexing/status`; paginated endpoint for deep dives |
| Stargate does not forward arbitrary RAG paths | Document UDS-only default; optional Stargate route in follow-up |
| Failed extraction metric ambiguous | Standardize on `get_failed_count()` + document chunk vs file semantics |
| Cloud consult unavailable | Re-run `scripts/consult -r architect ...` when pipeline healthy for second opinion |

---

## Appendix: code references

| Concern | File |
|---------|------|
| Pending table | `services/rag/property_index.py` |
| Index pending lifecycle | `services/rag/rag_service/indexing.py` |
| Admin router | `services/rag/admin_routes.py` |
| RAG client URL | `libs/transport_utils/rag_client.py` |
