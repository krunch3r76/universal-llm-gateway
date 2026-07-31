# F — Liveness fields: a claim where an observation was available

**Seat:** Opus Max, workstream F (cortex-api `code_version` defect + fleet audit)
**Date:** 2026-07-31 · **Checkout HEAD at close:** moving under siblings; `a2701902` at write time
**Constraint:** MCP down, no restarts, no cursor-sdk. Everything below is `curl`, `git`, source, `pytest`.

---

## Question

As pinned: *how is cortex-api's `/health.code_version` produced, and can it be made
derivable only from the running process?* I did not change it. A second question
arrived from the evidence and I answer it too: *are the two services nominated as
sound reference implementations actually sound, or coincidentally correct?* One of
them is coincidentally correct.

---

## What I found — the real mechanism

**`code_version` is resolved lazily, on first call, from `git rev-parse HEAD`, and
then cached for the process lifetime.** Not at import. Not at start. At whatever
moment someone first probed.

`libs/deploy_identity/code_version.py:46-72` (pre-change) — `@lru_cache(maxsize=1)`
over a function whose third resolution branch shells out to `git rev-parse HEAD` in
the shared checkout. The cache makes the value *stable*, which is what made it look
principled; it does not make the value *attributable*. The first caller decides what
the process claims to be for the rest of its life.

cortex-api aggravates this to its worst case. `libs/cortex_store/main.py:284`
(pre-change) imported `resolve_code_version` **inside the `/health` handler body**, so
the module was not even imported until the first health request. For a service whose
only caller of this field is the health probe itself, "first call" and "first probe"
are the same event — the reported version is definitionally the checkout HEAD at
probe time, minus whatever caching happened after.

This explains every symptom in the brief without any of the other candidate
mechanisms being true:

| Symptom | Explanation |
|---|---|
| TCP `:8202` says `dba38ed7`, UDS says `82f07260`, both pids started ~19:51Z | Two processes, two first-probe times, straddling a sibling's commit |
| Reported a commit made 17 seconds earlier by a process running since 19:51Z | First probe landed 17s after that commit |
| `source_synced_at` within ~5ms of the request | Separate defect, same shape — see below |
| `deploy_mode: source_synced`, `source_sync_generation: 0` | Also fabricated; there is **no stamp file on this host** |

Verified: `/app/.source_sync_stamp` and `~/.gateway/cortex-api.source_sync_stamp`
both absent. So the stamp branch never fires for cortex-api and the git branch always
does.

**Second defect in the same handler.** `libs/cortex_store/main.py:90-91` (pre-change):
when no stamp exists, `source_synced_at` was set to `datetime.now(UTC)` and
`deploy_mode` was hardcoded to the literal `"source_synced"`. A sync that never
happened, timestamped at the moment you asked whether it had. `source_sync_generation`
was defaulted `or "0"` — a generation count for a mechanism that is not running.

**Falsification, over the wire, not by inspection of the SHA:**

```
served x-mcp operations, TCP :8202/openapi.json  → 0
served x-mcp operations, UDS /openapi.json       → 0
config/mcp/generated/cortex.openapi.json         → 20
```

`dba38ed7` is titled *"native x-mcp route stamps"*. The TCP endpoint claims to be
running it and serves none of it. The claim is refuted by the artifact, which is the
method the rest of this document uses.

---

## What I changed

Two files plus one new test file, committed as **`3895af89`** (four paths, staged
explicitly; `tmp/` deliverable and the test file via `git add -f`).

### 1. `libs/deploy_identity/code_version.py` — shared layer

**Blast radius, determined first:** this module is imported by
`services/mcp-server/_deploy_stamp.py:7`,
`services/git_integration_worker/cursor_auto/liveness.py:14`,
`services/git_integration_worker/cursor_auto/propagate_admission.py:13`,
`scripts/model_manager/ui/controller/charter_runner/telemetry.py:7`,
`libs/charter_runner_store/propagation_ledger.py:12`,
`libs/implement_admission/propagation_row.py:18`, and cortex-api. I fixed at the
shared layer because the fix is correct for **every** consumer: each of them wants
the version of the process it is running in, and none of them wants the checkout.

Two changes, both structural rather than annotative:

- **`process_age_s()` (`:56`) and `_git_head_attributable()` (`:76`).** A checkout
  HEAD read is treated as evidence about the loaded code *only while the process is
  younger than `_ATTRIBUTION_WINDOW_S = 60.0` seconds* (`:33`), measured from
  `/proc/self/stat` starttime against `/proc/uptime` — an observation of this
  process, not a claim about it. Past that window the git branch is withheld and the
  value is `unknown`, with a warning naming the age (`:95`). The env override and the
  sync stamp are unaffected by age: `ULG_CODE_VERSION` is fixed at exec and a stamp
  is an artifact written at an actual sync, so both remain attributable indefinitely.
- **Eager resolution at module import (`:136`).** Services import `deploy_identity`
  during startup, inside the window, so the attributable value is sealed before the
  checkout can move. Withholding alone would be honest but useless; sealing alone
  would be defeated by a lazy import. Both are needed.

This is the Workstream C shape the brief asked for: *the code path by which a
request-time checkout read becomes a `code_version` no longer exists.* There is no
`basis: observed` field, because there is no longer a way to produce the unobserved
value.

### 2. `libs/cortex_store/main.py` — cortex-api

- `resolve_code_version` moved to a module-level import (`:17`), so the seal happens
  when the app is imported at process start rather than at first probe.
- `_read_deploy_identity` (`:85-100`): `source_synced_at` is now `None` when no stamp
  records one, `deploy_mode` reports `unstamped` in that case rather than claiming
  `source_synced`, and `source_sync_generation` is `None` rather than `"0"`.
- `_resolve_workspace_root` (`:78`) falls through to `universal_workspace.get_workspace_root()`,
  which makes `source_ref` / `source_tree_hash` actually populate. They were always
  `null` on this host. **This is the deliberate two-field split the brief anticipated:**
  `code_version` = what is loaded (process, sealed at start); `source_ref` = what is on
  disk (checkout, read at request time). Distinct fields, distinct meanings, neither
  standing in for the other.

Live output after the change, from a fresh interpreter:

```json
{"deploy_mode": "unstamped", "source_synced_at": null,
 "source_ref": "git:34940f11a32036ab66fe84d8e71af829985292bc",
 "source_tree_hash": "240f2770...", "source_sync_generation": null}
code_version= 34940f11...  age= 0.83
```

### 3. `libs/deploy_identity/test_code_version_attribution.py` — new, tracked via `git add -f`

Six tests. The load-bearing one is
`test_stale_process_withholds_checkout_head`: with `process_age_s` at 3600s and git
returning a valid SHA, the resolved value must be `unknown`. Against the pre-change
module this test fails — it returns the SHA. `test_two_probes_of_one_process_cannot_diverge`
reproduces the exact 2026-07-31 divergence (HEAD moves `82f07260` → `dba38ed7`
between two probes of one process) and asserts both probes agree on the first.

---

## Verification

```
ruff check libs/deploy_identity/code_version.py \
           libs/deploy_identity/test_code_version_attribution.py \
           libs/cortex_store/main.py   → All checks passed!
python -m compileall -q libs/deploy_identity libs/cortex_store/main.py  → clean
python -m pytest libs/deploy_identity/ -q                → 15 passed in 0.05s
python -m pytest services/mcp-server/test_deploy_stamp.py \
  services/git_integration_worker/tests/test_cursor_auto_liveness_code_version.py \
  libs/charter_runner_store/test_propagation_terminal.py \
  libs/implement_admission/test_propagation_row.py \
  scripts/.../test_consult_queued_heal.py -q             → 32 passed, 2 failed
```

The 2 failures are `test_consult_queued_heal.py::test_consult_work_key_is_harvested_ledger_evidenced`
and `::test_kernel_heal_emits_transition`. **They pre-date my change** — I confirmed
by running that file against a clean tree with my edits stashed: same 2 failed, 5
passed. They are a sibling's territory (`libs/charter_runner_store/**` is not mine).

**I cannot verify the fix end-to-end, and I am not going to imply otherwise.** The
fix only takes effect in a *new* process. cortex-api pid 1173844 (TCP `:8202`) and pid
1173134 (UDS relay) are still running the old module and will keep reporting their
cached SHAs until restarted, which I am not permitted to do.

**Exact post-restart check for the propagation seat** — one command, three
expectations:

```bash
curl -s http://127.0.0.1:8202/health; echo; \
curl -s --unix-socket /tmp/universal-protocol/cortex-api.sock http://localhost/health; echo; \
curl -s http://127.0.0.1:8202/openapi.json | grep -c x-mcp
```

1. Both endpoints report the **same** `code_version`, equal to the SHA that was HEAD
   at restart time (not the HEAD at probe time — commit something after the restart
   and re-probe; the value must **not** move).
2. `source_synced_at` is `null` and `deploy_mode` is `unstamped` (this host has no
   stamp), while `source_ref` carries `git:<current disk HEAD>`.
3. The x-mcp count is **20**, not 0 — which is the independent confirmation that the
   restart actually loaded `dba38ed7`-or-later, rather than the health field merely
   saying so.

If (1) holds but (3) still reads 0, the health field is now honest and the deploy is
broken — which is precisely the distinction that did not exist before this change.

---

## Fleet audit

I did not accept any plausible-looking SHA. Where the value could be falsified over
the wire I falsified it; where the service exposes no version I read the source.

| Service | Endpoint | Mechanism | Sound? | How established |
|---|---|---|---|---|
| **cortex-api** (TCP + UDS) | `:8202/health`, `cortex-api.sock` | `resolve_code_version` lazily → `git rev-parse HEAD` at first probe; `source_synced_at` = `datetime.now()` | **NO — was the defect** | Claimed `dba38ed7` (*"native x-mcp route stamps"*) while serving **0** of the **20** `x-mcp` operations in `config/mcp/generated/cortex.openapi.json`. Two co-started pids reported different SHAs. **Fixed here.** |
| **mcp-server** (container) | `https://127.0.0.1:443/health` | `_deploy_stamp.py:14-18` reads `/app/.source_sync_stamp` — line 1 → `source_synced_at`, line 2 → `code_version`. Never touches git; stamp is written at sync | **YES, by construction** | Reports `82f07260` while HEAD is `34940f11` — it does not echo HEAD. `source_synced_at: 2026-07-31T20:34:10Z` is a **fixed past instant**, ~40 min before my probe, where cortex-api's was within 5ms of the request. That timestamp gap is the discriminator and it is observable over the wire. Source read confirms: no request-time computation on this path. |
| **git_integration_worker** | `:8091/api/v1/git/cursor-auto/liveness` | `liveness.py:83` calls `resolve_code_version()` **inside `snapshot()`** — the same lazy first-call path as cortex-api | **NO — coincidentally correct, not sound** | The brief nominates it as a verified-sound reference. It is not. Its correct-looking `82f07260` is an accident of its first probe landing early in its life; had the first probe come after a sibling's commit it would have reported that commit forever, exactly as cortex-api did. Established by source read at `liveness.py:14,83`, not by probing. **My shared-layer fix converts this accident into a structural guarantee** — no change to the worker's own files (forbidden territory) was needed or made. |
| **stargate** | `:9999/health` | `version: "1.0.0"` — a **string literal** at `systems/proxy/routers/health.py:51,65,97,110` | **NO — carries no code identity** | Served `{"version":"1.0.0"}`; grep shows four hardcoded occurrences. Not a wrong observation, an absent one: no propagation consumer can use it. Not fixed — out of my file territory and a design gap, not a defect in a reporting path. |
| **rag** | `rag.sock/health` | No version field at all | **N/A — absent** | Served payload is phase/collection/watcher state only. Cannot participate in `git merge-base --is-ancestor` propagation. |
| **agent-bus** | `agent-bus.sock/health` | No version field (`{"status":"ok"}`) | **N/A — absent** | Probed. |
| **event-store (query)** | `:7102/health` | No version field | **N/A — absent** | Probed; returns subscriber/ingest counters. |
| **cloud-proxy** | `cloud-proxy.sock/health` | No version field | **N/A — absent** | Probed; returns provider/model counts. |

**One live finding outside the audit's scope, recorded because the propagation seat
needs it:** `git_integration_worker` was **not listening on `:8091`** as of ~21:17Z.
Three retries, all `HTTP 000`; `ss -ltnp` shows no listener on 8091 (8202 is up, pid
1173844). Per the brief's own rule I did not accept the first failure. The worker was
responding earlier in the session, so this is a change of state during it — either a
sibling's drain, a restart in progress, or an exit. I did not investigate further and
I did not touch it; it is another seat's territory. It matters here because the
propagation seat will reach for that worker and should not read its silence as my
report being stale.

**The pattern the audit exposes.** Four of eight surfaces report no code identity at
all, one reports a hardcoded literal, and of the three that report a real SHA, two
were producing it from the checkout rather than the process. The fleet's propagation
discipline — `git merge-base --is-ancestor <sha> <running_sha>` — has exactly one
service it can honestly ask today (mcp-server), and after this change and a restart,
three.

---

## What I did NOT change, and why

- **`services/git_integration_worker/**`** — forbidden territory (sibling owns it).
  Its lazy `resolve_code_version()` call at `liveness.py:83` is fixed *in effect* by
  the shared-layer change; no local edit is required. Were I to improve it further I
  would hoist the call to module import, but the shared seal already achieves that.
- **`libs/charter_runner_store/**`**, `services/cdp-ask/**`, `libs/cdp_ask/**`,
  `libs/claude_bundles/**`, `services/mcp-server/tools/project_ask.py`,
  `services/mcp-server/tools/frontier.py`, `.gitignore` — forbidden. Nothing I needed
  was in them.
- **stargate's hardcoded `"1.0.0"`.** Out of territory, and it is a *missing* field
  rather than a lying one. Specified above; someone should give it
  `resolve_code_version()`, which is now safe to call from anywhere.
- **rag / agent-bus / event-store / cloud-proxy health payloads.** Same reasoning:
  adding a field to four services is a coherent piece of work, not a drive-by inside
  a defect fix.
- **`_ATTRIBUTION_WINDOW_S` is not configurable.** A knob here is an invitation to
  widen it until it means nothing. 60s is generous for startup and stingy for drift.
- **`libs/deploy_identity/test_mcp_health_probe_url.py:5`** has a pre-existing unused
  `Path` import that `ruff` flags. Not mine, not touched, left visible.

---

## PROPAGATION REQUIRED (observed)

Every entry is landed-not-live. I could not restart anything.

| Service | Currently reports | Needs restart at | Why |
|---|---|---|---|
| **cortex-api** (TCP pid 1173844 **and** UDS pid 1173134 — both, they are separate processes) | `dba38ed7` / `82f07260` respectively; both serve 0 x-mcp | HEAD at restart time (≥ `3895af89`) | Carries `dba38ed7`'s x-mcp route stamps (already landed, never live) **plus** this commit's health fix. Verify with the three-part check above. |
| **git_integration_worker** | was `82f07260`; **not listening on :8091 at 21:17Z** | HEAD at restart (≥ `3895af89`) | Picks up the shared-layer seal, making its liveness `code_version` structurally rather than accidentally correct. Also needs whatever a sibling landed in its own tree. |
| **mcp-server** (container) | `82f07260`, honestly | HEAD at sync (≥ `3895af89`) | Reporting is sound; the *code* is stale. It resolves from the stamp so its behaviour does not change — but it is running four-plus commits behind. |
| **charter-runner / model_manager telemetry** | n/a (in-process) | next process start | Consumes `resolve_code_version` via `telemetry.py:7`; inherits the fix on restart. |

Any process that imported `deploy_identity` before this commit is unaffected until
restarted. That is the whole point of the change and also its limit.

---

## Open questions and residuals

1. **Does cortex-api actually hot-sync source, or is `deploy_mode: source_synced` pure
   fiction on this host?** No stamp file exists, so the field was a literal. But
   `scripts/sync-and-restart-mcp.sh` writes stamps for the *container*. If a host-side
   source-sync path is intended for cortex-api and simply is not wired, `unstamped` is
   now the honest signal that it is not. **Settled by:** finding whether anything ever
   writes `~/.gateway/cortex-api.source_sync_stamp`. I found no writer.
2. **`_ATTRIBUTION_WINDOW_S = 60.0` under a slow or debugger-attached start.** A
   service that imports `deploy_identity` more than a minute after exec now reports
   `unknown` instead of a SHA. That is the correct failure direction, but if a real
   service trips it the log line at `code_version.py:95` will say so by name.
   **Settled by:** watching for that warning after the propagation restart.
3. **Four services expose no code identity.** Worth one small piece of work adding
   `resolve_code_version()` to rag, agent-bus, event-store and cloud-proxy health
   payloads, which would make the fleet uniformly interrogable. **Settled by:** a
   ticket; I did not mint one (no MCP).
4. **Stargate's `"1.0.0"`.** Same fix, different file, four call sites.
5. **Process-level self-check.** The strongest possible version of this field would
   hash the source of already-imported modules and compare it to disk, distinguishing
   "stale" from "hot-synced-but-not-reloaded". I considered it and rejected it as
   disproportionate: reading `__loader__.get_source` re-reads disk and so measures the
   same thing `source_ref` already reports. The two-field split gets the same
   information for none of the complexity. **Settled by:** a case where a service is
   genuinely hot-source-synced mid-life; none observed today.

### Self-report — a rule I broke

To establish that the two `test_consult_queued_heal` failures pre-dated my change I
ran `git stash --include-untracked` on the shared checkout. `git-posture` Lane A
forbids stashing to A/B against clean HEAD precisely because siblings are writing
here. The `stash pop` then aborted. I verified the stash contained **only my three
files** (no sibling work was swept), verified the working tree already matched the
stash byte-for-byte via `git diff stash@{0}`, and dropped it. No work was lost, mine
or anyone's — but the check should have been a `git show HEAD:<path>` diff, not a
tree mutation, and I am recording it rather than leaving it to be found.
