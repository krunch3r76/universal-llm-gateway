# Claude.ai CDP navigation — operations annex (L3)

Load on demand for recovery, abort, cleanup, and completion detail. L2 carries binding rules; this file holds mechanics and recipes.

## Cowork Outputs-first (mode=large — BINDING)

When a sealed prompt expects a **large structured deliverable** (`expected_size=large` or prose `mode=large`):

1. Write the **full deliverable** to a Cowork task **Output** file first (terminal write — no post-Output chat revision).
2. Keep the chat reply to a **one-line pointer** (filename + self-computed sha256).
3. Name the **`harvest_uri`** / `archive_path` target in the packet.

| Knob | Default | Behavior |
|---|---|---|
| `expected_size` | `auto` | `large` ⇒ attempt Output download; `small` ⇒ chat scrape only |
| `harvest_source` | `auto` | try Output → `cortex://` pointer in chat → **hard-fail** under `large` |
| `download_output` | `false` | when true (or `expected_size=large`), attempt Output before archive |
| `harvest_provenance` (poll) | — | `output-file` \| `cortex-uri` \| `chat`; null on non-success |

Dual-completion unchanged: `archive_uri` and `content_proof_sha256` attest the **same** archive file. `turn_idle` alone never advances.

**Window liveness on poll (a:25681):** while `status=running` and `completion_phase=running`, poll exposes `streaming` / `stop` / `tool_pause` / `liveness_observed_at` from held-page harvest. Prefer those over wall-clock. Nulls while `running` mean harvest not yet succeeded — keep polling.

MCP: `project_ask(op=submit, expected_size=…, harvest_source=…, download_output=…)`.

## Completion predicate — detail

**Cowork `/cowork/cse_` fallback (24864):** Chat paths keep global `cur_n > base_n`. On Cowork CSE URLs only, `wait_assistant_reply` may also complete when:
- extended harvest selectors increment `n`, OR
- `body_len > base_len` with stable polls, OR
- `task_map_working → task_map_idle` with `body_len > base_len` (requires task map present).

`¬ idle-on-stale-content`: fallback never fires on pre-existing idle prose alone.

**Stop detection (24873 — MUST):** `HARVEST_JS` `stop` only inside generation/composer roots (`main`, `[role="main"]`, chat-input ancestor). Sidebar thread rows must harvest `stop=false`. **`streaming` is defense-in-depth** when `stop` momentarily false mid-generation.

**error_banner harvest (25486 + 25654 + 25684 — BINDING):**

| Bind | Mechanism |
|---|---|
| **25486** | Match banner/toast/alert only; **exclude** composer / `[data-testid="chat-input"]` |
| **25654** | Fail-closed **idle-only** (`banner ∧ ¬in_flight`) |
| **25684** | **`Overloaded` may linger after completion** — structural completion wins over lingering banner |

## Lead recovery (a:24864 — MUST)

When durable cortex sidecar exists AND CDP page shows `hasStop=false` (idle) BUT `project-ask` CLI harvest loop still running:

```
lead_recovery ⇒
  (1) Stop if streaming (hasStop)
  ∧ (2) kill holder / release lane
  ∧ (3) delete_chat_if_active via hygiene
  ∧ ¬ wait on CLI stdout indefinitely
```

Sidecar + idle page = turn succeeded; hung CLI is harness defect. Check 24873 stop false positive before assuming in-flight. Apply hygiene delete before releasing lane.

## Progress introspection — Cowork task map (SHOULD)

```
liveness = hasStop(button)            # streaming ⟺ Stop control present
progress = ordered task-map steps
tail     = body.innerText.slice(-N)
```

Recipe: CDP `Runtime.evaluate` on Jupiter per lane port (`:922N/json`). `¬read-only introspection ⇒ ¬mutate page`. Never inject clicks on a lane owned by another session.

## Abort in-flight Cowork (MUST — friction 24838)

```
abort(project-ask) ⇒ Stop(Cowork stream)  THEN  release(lane flock / holder)
¬ kill(holder) alone while Stop button is present
```

Kill-only leaves live generation that can still post to agent-bus. Use `hasStop` from progress introspection to drive step (1).

## Archive-before-delete (MUST)

```
delete(chat) ⇐ validated(harvest) ∧ persisted(raw_harvest → cortex_sidecar)
```

Success output **prints the archive URI**. Delete without archive URI is a bug.

## Cleanup success signal (MUST — a:25084)

```
delete_success ⇐ delete_after.ok == true
cleanup_ok (CLI) ⇐ delete requested ⇒ delete_after.ok; else true
returned_to = navigation telemetry only — ¬ delete success
```

| Signal | Role |
|---|---|
| `delete_after.ok` | **Sole authoritative delete success** |
| `returned_to` | Post-cleanup URL — **never** implies deletion |

**Anti-pattern:** `returned_to=https://claude.ai/new` when `delete_after.ok` is false.

Hygiene: bounded header poll (4s), session→chat trigger order, sidebar Recents fallback by title in aria-label.

## Model attestation (MUST)

```
∀ harvested_turn: attest(model_indicator)
mismatch ⇒ discard ∧ re-ask ∧ friction
¬ accept unattested body as "Fable said" / "Opus said"
```

## Command identity — delete semantics (MUST)

| Command | Delete |
|---|---|
| `project-ask` (single) | Always after validated archive |
| `project-ask --converse` | **Never** auto-delete; only `--close` on final turn |

## Timeout + cookie staleness (MUST)

```
timeout ∨ cookie_stale(401|login_redirect|compose_reject)
  ⇒ capture_state ∧ ¬delete ∧ friction
```

**Idle vs in-flight (24666):** timeout is **idle** budget only. While `Stop` ∨ `streaming` ∨ `tool_pause`, idle clock **pauses** — no wall ceiling. Long Cowork tool-runs may run arbitrarily long.

## Anti-redispatch after abort (MUST — friction 24911)

```
shell_abort ∨ exit_unknown ∨ empty_harvest
  ⇒ list_lanes()
  ∧ CDP_attest(prior_registration.port → live claude.ai/cowork tab)
  ⇒ if prior active: reattach(--registration-id) ∨ wait; ¬ new --register
```

**24838 ordering:** Stop if `hasStop`/streaming → then deregister+kill.

**Q8 escalation:** second `--register` while prior still `active` → bind falsifier → open friction.

## False abort / detached remote running (MUST — friction 24976)

```
harness_abort ∨ exit_unknown
  ⇒ list_lanes() — check attached ∧ driver_pid
  ⇒ if remote driver authoritative (attached:true): reattach ∨ wait; ¬ new --register
```

| Field | Semantics |
|---|---|
| `attached` | **Liveness** — sole truth for driver alive |
| `driver_pid` | **Identity** — never read alone as alive |

Stdout `status=detached_remote_running` is **best-effort** — absent under SIGKILL; closure must not depend on it alone.

**F5 carve-out:** `--deregister-on-exit` opts out of 24976 detach-continue.

## Post-converse recovery (MUST — friction 24834)

`project-ask --converse` may exit **0** with successful harvest while `--out-dir` empty or `turn-N.md` never written. **Do not** claim turn loss until recovery lookup completes.

```
recovery_order =
  (1) stdout JSON `{ok, results[].url, results[].body_len, …}`
  (2) `--ledger` path when passed
  (3) `--out-dir/turn-N.md` when passed
  (4) `results[].url` — active tab on lane port; CDP harvest
  (5) claude.ai sidebar Recents on primary `:9222` when pooled lane used
```

```
exit 0 ∧ summary.ok ∧ results[].body_len > 0 ⇒ turn succeeded
¬ empty(out-dir) ⇒ lost_turn   # without recovery_order first
```

**Durable capture:** pass `--out-dir` **or** `--ledger` on every converse you must cite later.

**Abort recovery (24911):** after shell abort, run recovery_order first; then `list-lanes` + CDP attestation. Reattach via `--registration-id` — **¬** fresh `--register`.
