---
name: landed-not-live
description: "On any lane closeout that touched a deployed service path — landed is not done until live; probe liveness, own propagation obligations, mint rows not footer prose."
trigger_match_terms: ["landed-not-live", "landed_not_live", "landed not live", "propagation obligation", "sync_restart", "liveness probe", "code_version", "process_live", "RESIDUE", "deploy identity", "not_running_committed_code"]
related_skills: ["service-lifecycle", "dispatch-report-discipline", "pre-deploy-gate-discipline"]
---

# Landed is not live

`commit(deployed_path) ∧ ¬probe_live(code_ref) ⇒ ¬status_complete`.

## Trigger

Lane terminal closeout after commits under fleet-served paths (`services/` or consumer `libs/`).

## Refuse

- `status_claim: complete` while any touched consumer has `fleet_liveness(code_ref=land_sha)` → `liveness.answer=no` and propagation is undischarged.
- `Owner: path-derived obligation candidates only` on close surfaces.
- Propagation only in `TYPE: RESIDUE` footer (unattended ticker cannot select it).
- Path-prefix inference (`derived:path_prefix`) as liveness proof.
- Discharge claims after restart without post-probe, or without pre `pid` and `process_start_time`.

When the commission assigns restarts to the operator seat or forbids restarts, that outranks propagation duty here because the operator seat sequences restarts after review. Then: no `sync_restart` / restart / stop, no propagate, no restart intent arm/cancel, no propagate row or auto-enqueue friction. Land as instructed, close `partial`, list each obligation `{service, code_ref, holder: operator-seat}`. Without that line, steps below apply.

## Steps

1. **Operator-seat commission.** If Refuse line applies, skip executor discharge (steps 4–5); partial closeout with `{service, code_ref, holder: operator-seat}` per gap. Falsifier: executor restarted, armed intent, or minted propagate/friction under operator-seat commission.

2. **Live before complete.** Run `fleet_liveness(code_ref=<land_sha>)` per touched consumer. `status_claim: complete` only when every touched consumer shows `liveness.answer=yes` (or was discharged by step 5). Any other gap → `partial`, listing each gap as `{holder, service, code_ref, proof_class}`. Falsifier: complete while any consumer has `liveness.answer=no` without step-5 discharge. Specimen cloud_proxy: path-prefix inference, no holder, RESIDUE footer, closed complete — obligation invisible to ticker.

3. **Named holder.** `holder` ∈ {`cursor-sdk`, `cursor-auto`, `navigator`, `operator-seat`, minted row id} — never candidates-only. Falsifier: closeout without seat or row id.

4. **Mint unfired propagation.** Before terminalize, mint selectable row (`contract:propagate` or `friction(actionable=true)` when auto-enqueue warranted). Skip mint when step 1 applies. Falsifier: RESIDUE-only propagation.

5. **Probe discharge (`proof_class=process_live`).** Prefix may nominate restart; probes prove liveness. (a) Pre: quote `pid`, `process_start_time` (or `source_synced_at`), `observed_code_version`. (b) `manage(action="sync_restart", service=…)`. (c) Post: `fleet_liveness(code_ref=land_sha)` or health endpoint. (d) `code_ref_relation` ∈ `{equal, ancestor-of-observed}` and identity changed (pid ≠ pre or start advanced). Restart without post-probe is not discharge; without pre values proof is unfalsifiable — quote pre/post in `evidence`. Falsifier: complete on path inference or restart without quoted post probe.

## Falsifier

Deployed-path lane closed complete while any touched consumer has `liveness.answer=no` without step-5 discharge.

## Composes with

- `service-lifecycle` — `manage` sync_restart
- `restart-drain-discipline` (rule) — drain-gated restart on cursor-sdk seat
- `dispatch-report-discipline` — closeout fields
- `pre-deploy-gate-discipline`
