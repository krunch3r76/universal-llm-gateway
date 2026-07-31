# Opus Max multitask session — 2026-07-31

Brief: `cortex://notes/system/specs/opus-max-multitask-brief-20260731.md`.
Workstreams A–E as commissioned, plus F (liveness fields) which the session
discovered rather than received.

---

## 1. Propagation — everything landed is now live

Every commit authored this session has been deployed and verified by reading the
service back. Nothing is landed-and-inert.

| Service | State | How it was established |
|---|---|---|
| `cortex_api` (both TCP `:8202` and the UDS relay) | **live** at `68f5ee5e` | Both endpoints agree; serves **20** `x-mcp` operations where it served 0 before |
| `mcp-server` | **live** at `68f5ee5e` | Real sync stamp at `21:25:37Z`, replacing a stamp from `20:34:10Z` |
| `git_integration_worker` | **live** at `a0ee596b` | Start event — restarted from an observed HEAD after a silent outage |
| `stargate` | **live**, carries `55379fd6` | Start event only — this service reports no code identity (see below) |
| `cdp-ask` (Jupiter) | **live**, carries `a2701902` | The `code_version` field now *exists* on `/health`; that commit is what added it |
| `manage` controller | **ambiguous — see §5** | Two processes bind one socket; they disagree |

The result worth pausing on is that these surfaces **no longer report the same
SHA as each other**. `git_integration_worker` says `a0ee596b` while `cortex_api`
and `mcp-server` say `68f5ee5e`, and each is accurate to the code that process
actually loaded. Before today they would have converged on whatever HEAD
happened to be at the moment they were asked. That convergence looked like
consistency and was the bug.

**Restarts performed:** `cortex_api` (sync_restart), `stargate` (sync_restart),
`cdp_ask` (remote restart on Jupiter over shared NFS), `mcp` (cached
source-sync), and `git_integration_worker` (cold start after it exited on its
own). All through the `manage` JSON-RPC surface; no process was killed for
lifecycle.

---

## 2. The thesis after contact

The brief's organising claim — *nearly every expensive error here is a claim
asserted where an observation was available* — held, and got sharper. It is not
only a discipline failure. In several places the system had **no code path by
which the honest answer could be produced**, so discipline could not have saved
it.

Six new specimens, none of which were in the brief:

1. **`cortex_api` asserted a commit its own served document refuted.** `/health`
   reported `dba38ed7` — the commit that added twenty `x-mcp` route stamps —
   while `/openapi.json` served zero of them. Its two processes reported
   *different* SHAs for the same service.
2. **The mechanism was a cache, not a lie.** `resolve_code_version()` was
   `lru_cache`'d over a lazy first call that shelled out to `git rev-parse HEAD`.
   The cache made the value *stable*, which is why it looked principled. Stable
   is not attributable: whichever caller probed first decided what the process
   claimed to be for life.
3. **`source_synced_at` was `datetime.now()`** and `deploy_mode` a string
   literal, with no stamp file on the host at all. Pure fiction, freshly minted
   per request.
4. **A helper that could never have worked reported success.** Workstream A's
   first pass added `stamp_fastapi_routes()`; this FastAPI version keeps included
   routers lazy, so it would have stamped 0 of 20 routes and returned `0`. It was
   never exercised, so it never said so.
5. **A publish path swallowed 35 hours of failures in silence.** `publish_cdp_event`
   caught bare `Exception` with no log, counter, or event — a factory `TypeError`,
   an uninitialised proxy, and a clean publish were indistinguishable.
6. **The closeout relay is never uncertain and usually wrong.** Across 67 real
   closeouts it reported *zero* parse-misses on `ac_verdict` while silently
   dropping acceptance criteria in 51 of them.

**The coordinating seat made the same error twice**, which is worth recording
rather than hiding. It claimed six propagation ledger rows were false because
their commits are ancestors of the running version — ancestry answers "does the
running descendant contain this commit," not "did this restart succeed then."
And it declared `git_integration_worker`'s version field sound after watching it
hold a SHA while HEAD moved, which demonstrates the value is *cached*, not that
it is *attributed*. Both are the thesis, committed by the agent auditing for it.

---

## 3. Verdicts

**A — OpenAPI over MCP.** The hand-maintained `(method, path)` seed is deleted.
All twenty MCP-reachable `cortex_api` routes now declare their own binding at the
decorator, so the served OpenAPI document is the source of truth and a removed
stamp is provably detectable — it lands in `unbound_dispatch_ops()` and turns
`--check` red. The regenerated document came out byte-identical, which is strong
evidence the stamps are faithful rather than merely present.

*On the tier-M hypothesis: it does not hold.* `read_only` is neither necessary
nor sufficient for unattended execution. Of 54 `read_only` ops, eleven are
**explicitly denied** by ratified wildcard rows (`fs.*`, `manage.*`,
`pipeline.*`), so deriving the allowlist from the schema would overturn eleven
decisions an operator made *knowing* those ops were reads — for reasons the flag
cannot model: blast radius (`fs.read` mutates nothing and reads anything), cost,
and surface ownership. The decisive counter-examples turn on *arguments*, not
operations: `agent_bus_read.fetch_unread` is `read_only` while documenting
`mark_read=true` as a side effect, and the `audit` family is `read_only` while
defaulting `emit=True`, which produces ~17k events per call. And `email` has no
row in `canonical.yaml` at all, so a derived allowlist would not reject
`email.pull` on judgement — it would fail to see it. **The gate stays a gate.**
What *is* derivable is coverage: candidate generation plus CI drift, so an
unratified op becomes enumerable instead of silent.

**B — the closeout relay projection: delete it.** The failure is *dimensional,
not parsing*. The relay budget is 2,000 characters across eleven judgement
fields, yielding a median `ac_verdict` of 136 bytes against a median authored
sidecar of 4,274. A real parser fix landed and the median did not move — that
non-movement is the proof. Because the cell is *derived* rather than *quoted*,
its loss is silent: a truncated quotation announces itself, a wrong projection
does not.

Two amendments the opposing case won: the replacement must be a **bounded
verbatim excerpt**, not the envelope-only design; and deletion is **ordered
behind giving closeouts a durable home**, because `source_ref` points into
gitignored `tmp/` and **zero of 2,474 sidecars are tracked**. Either branch ends
with derivation removed.

**C — claim versus derivation: structural, but bounded.** Survived an adversarial
attempt to kill it. The settlement: **structure** for same-referent,
machine-probeable runtime facts; **discipline** for judgements, policy, scoped
observations, and untyped prose. Provenance-*as-data* is rejected only for
**self-reported enums** — framework-issued typed evidence, minted solely by the
probe path, remains legitimate.

The empirical claim was withdrawn and replaced with a stronger one. Seven ledger
rows were terminally failed **within 0.083 seconds**, all observing the same
already-running generation (uptime 929.565 → 929.649s), before any incoming
generation was identified. Those verdicts were *unearned*, not falsified.
Ancestry cannot rehabilitate them: the documented obligation is **exact
equality** (`code_version == code_ref`), so ancestry satisfies nothing at all.
The prescribed row repair is withdrawn entirely.

**D — CDP transport.** 69% of sessions fail, and the plurality cause is
`wall_clock_exceeded` — our own budget aborting our own work. The fleet had
conflated "the compose toggle is broken" with "the session is unauthenticated or
parked behind an unclicked approval" for a full day, reading intermittency as
resolution. Aborts now record a progress fingerprint with a verdict
(`advancing` / `slowing` / `frozen` / `oscillating` / `never_advanced`), which
discriminates *budget too small* from *transport decay* — the two answers imply
opposite fixes, and widening the budget first would have destroyed the signal.

On the event blackout, all three suggested causes were **falsified** and no root
cause is claimed: Stargate emitted 134,331 events during the window, and the
byte-identical publish helper succeeded 26 minutes *before* the restart that
supposedly restored emission. What was fixed is why it was undiagnosable. Next
suspect named: `publish_from_sync` returns a Task nobody holds a reference to,
which asyncio may collect before it runs.

**E — `.gitignore:76`.** The repo-wide `**/test_*.py` rule was temporary hygiene
and has already been reversed; `tasks/` at line 224 is deliberate. No bulk
`git add -f` was warranted, which was the outcome that mattered. One residual:
`services/universal-stargate/.gitignore:73` still hides 25 test files.

**F — liveness fields (unscheduled).** Fixed at the shared layer: a checkout HEAD
read counts as evidence only while the process is younger than 60 seconds, and
the module resolves eagerly at import so services seal an attributable value at
startup. The audit is the uncomfortable part — of eight surfaces, four expose no
code identity at all (`rag`, `agent-bus`, `event-store`, `cloud-proxy`),
`stargate` reports a hardcoded `"1.0.0"`, and of the three with real SHAs two
were reading the checkout. **`mcp-server` is the only service sound by
construction**, because it reads a sync stamp and never touches git.

---

## 4. Forks that need the operator

**The propagation ledger row: operation-history record, or outstanding
obligation?** Neither reading is derivable from the data, and the two imply
opposite reconciliations. The recommendation is the **obligation** reading: under
exact equality a row minted at commit X becomes permanently unsatisfiable the
moment its service restarts to X+1, which would make terminal failure the routine
outcome for any row not settled before the next commit lands — incoherent for a
token meaning "this failed." The surrounding machinery is queue-shaped
(`list_open_rows`, `defer_reason`, `bump_age_for_open_rows`, `safe_window`).
Under that reading, satisfaction is ancestry, states are
satisfied / outstanding / superseded, and the exact-equality predicate is itself
the defect. Each reading has a gap: the audit reading needs a settling caller the
schema does not record, and the obligation reading cannot resolve a `code_ref`
that is not a commit — one failed row carries the literal `"working"`.

**Host-bound MCP tools** (`fs`, `project`, `quality`, `sqlite`, `browser`):
per-host services, or declared MCP-native exemptions? Blocks wave 4 of the
OpenAPI program.

**A durable home for closeouts.** B's deletion is ordered behind it, and 2,474
untracked sidecars say the current answer is "there isn't one."

---

## 5. Residuals

- **Two `manage` controllers bind one socket.** Pid 669567 (10:43:41, running out
  of a **cursor-dispatch-home venv**) and pid 1630785 (14:20:33). The canonical
  controller that held the socket from 11:24 is gone. `4aae67b2` landed at
  14:07:03, so one controller has it and one does not — whether that fix is live
  is not a single fact. A fleet lifecycle controller serving from a dispatch home
  is worth a deliberate look.
- **Six orphaned `cursor-sdk` bridge processes** were terminated under explicit
  operator authorization, all idle (2–5s CPU across up to 16h). Recorded in
  `OPS-bridge-termination.md`.
- `git_integration_worker` logs a recurring unhandled `missing_bridge_endpoint`
  on `GET /api/v1/cursor/catalog` (`routes/cursor_catalog.py:51`). Latent.
- `services/universal-stargate/.gitignore:73` hides 25 test files.
- A worker self-reported using `git stash` on the shared checkout, which Lane A
  forbids; it verified the stash held only its own three files and that nothing
  was lost.
- `stargate` still reports a hardcoded version, so its propagation can only be
  established from process start time.

---

## 6. Deliverables

`A-openapi-mcp-surface.md` · `A-rest-route-map.md` · `B-projection-layer.md` ·
`C-claim-vs-derivation.md` · `C-adversarial-review.md` · `D-cdp-transport.md` ·
`E-gitignore-test-visibility.md` · `F-liveness-fields.md` ·
`OPS-bridge-termination.md`
