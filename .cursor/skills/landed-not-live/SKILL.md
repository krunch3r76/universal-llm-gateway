---
name: landed-not-live
description: "On any lane closeout that touched a deployed service path — landed is not done until live; probe liveness, own propagation obligations, mint rows not footer prose."
trigger_match_terms: ["landed-not-live", "landed_not_live", "landed not live", "propagation obligation", "sync_restart", "liveness probe", "code_version", "process_live", "RESIDUE", "deploy identity", "not_running_committed_code"]
related_skills: ["service-lifecycle", "restart-drain-discipline", "dispatch-report-discipline", "pre-deploy-gate-discipline"]
---

# Landed Is Not Live

**Invariant:** `commit(deployed_path) ∧ ¬probe_live(code_ref) ⇒ ¬status_complete`.

A lane that commits to a path served by a fleet service does not close as `complete` while its propagation obligation is undischarged. It closes as `partial` with the obligation **owned**, or it fires the restart and proves liveness before close.

## Five operative rules

### 1. Complete requires live, not landed

`status_claim: complete` is forbidden when `fleet_liveness(code_ref=land_sha)` reports `liveness.answer=no` for any consumer touched by the lane's `files_*` set.

**On close:** either discharge (rule 4) or emit `status_claim: partial` naming each undischarged service, holder, and `code_ref`.

### 2. Every propagation obligation names a holder

`Owner: path-derived obligation candidates only` is **forbidden** on close surfaces.

Every obligation carries `{holder, service, code_ref, proof_class}` where `holder` is a seat (`cursor-sdk`, `cursor-auto`, `navigator`) or a minted row id — never "candidates only".

### 3. Unfired propagation mints a row

Prose in a `TYPE: RESIDUE` footer is invisible to the unattended work-selection ticker. Unfired propagation **must** mint a selectable row (`contract:propagate` ledger row, or `friction()` with `actionable=true` when auto-enqueue is warranted) before the lane terminalizes.

`¬` rely on RESIDUE prose alone; `¬` assume a human or navigator will read the footer.

### 4. Liveness is proven by probe, not path prefix

Path-derived inference (`derived:path_prefix`) may **nominate** a restart candidate; it never **proves** liveness.

**Proof recipe (`proof_class=process_live`):**

1. Capture **pre-restart** probe: `pid`, `process_start_time` (or `source_synced_at` for containers), `observed_code_version`.
2. Fire `manage(action="sync_restart", service=…)`.
3. Capture **post-restart** probe via `fleet_liveness(code_ref=land_sha)` or service health endpoint.
4. Verify: `code_ref_relation` ∈ `{equal, ancestor-of-observed}` **and** process identity changed (pid ≠ pre, or start time advanced).

Restart fired without post-probe is **not** discharge.

### 5. Pre-restart values are mandatory

Without pre-restart `pid` and `process_start_time`, the proof is unfalsifiable — any "success" claim is `derived`, not `observed`.

Quote pre and post verbatim in closeout `evidence`.

## Worked example — lane 11456 / cloud_proxy (specimen)

| Field | Value |
|---|---|
| Land sha | `5b7a272d38ec3a61542b598283c57004b47ae451` |
| Land time | 2026-09-15T19:46Z |
| Service | `cloud_proxy` |
| Pre-restart pid | `576542` |
| Pre-restart start | `2026-09-15T14:52:01.079088Z` |
| Pre-restart `code_version` | `bd4bfc97146fc5596e5de0fee74a4bee3a3847fa` |
| Pre `code_ref_relation` | `descendant-of-observed` → `liveness.answer=no` |
| Closeout defect | RESIDUE block with `liveness: unknown`, Owner: candidates only — lane reported `complete` |
| Staleness | ~10h landed-not-live before discharge |
| Discharge (lane 11468) | `manage(sync_restart, cloud_proxy)` → post pid `2332869`, start `2026-09-16T05:48:07.915714Z`, `code_version=5b7a272…`, `relation=equal`, `liveness.answer=yes` |

**Failure class:** emitter inferred propagation from path prefixes, named no holder, rendered as footer prose — obligation invisible to unattended ticker; lane terminalized complete with last mile unowned.

## Closeout checklist

Before `status_claim: complete` on any lane touching `services/` or consumer `libs/`:

- [ ] `fleet_liveness(code_ref=<land_sha>)` run for each touched consumer
- [ ] All consumers show `liveness.answer=yes` OR obligations minted as owned rows
- [ ] Each restart: pre/post pid + start time quoted
- [ ] `¬` RESIDUE-only propagation without a row

## Composes with

- `service-lifecycle` — `manage` sync_restart mechanics
- `restart-drain-discipline` — drain-gated restart on cursor-sdk seat
- `dispatch-report-discipline` — closeout field contract
