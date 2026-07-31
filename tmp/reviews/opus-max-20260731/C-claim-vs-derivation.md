# Question

Which envelope fields are direct observations, which are derivations or
assertions, and how can consumers see the distinction structurally?

The question is unchanged. I narrowed the recommendation to boundary envelopes:
the closeout relay, propagate contract, propagation ledger, and git-worker drain
supervisor. This is not a claim that every internal variable needs provenance.

# What you found

- The propagate envelope is the strongest current model. `_execute_row` records
  a `manage` response, reports `queued` on an observed deferred result, and
  reports `executed` only after `proof_observed(row, proof)` closes the ledger
  (`services/git_integration_worker/cursor_auto/handler_propagation.py:97-145`).
  `_disposition_for` then derives the outer disposition from the weakest
  `executions[]` row rather than asserting success independently
  (`:148-204`). Regression tests pin that a `manage.status=error` cannot become
  `propagated` and that mixed rows floor to the weakest outcome
  (`services/git_integration_worker/tests/test_handler_propagation_disposition.py:26-83`).
- The propagation ledger correctly separates a requested/open state from proof.
  It persists `proof`, `proof_class`, and a later `proof_payload`
  (`libs/charter_runner_store/migrations/migration_004_propagation_ledger.py:17-35`);
  `close_row` and `fail_row` transition only from open rows and store that
  payload (`libs/charter_runner_store/propagation_ledger.py:185-242`).
  The stored `status` is therefore a ledger-state assertion, not a claim that
  the target service is live.
- `propagation_terminal` is the desired terminal shape: an unreachable probe
  defers rather than concludes; a probe that may be from the outgoing process
  remains deferred; only a matching observed `code_version` closes, and a
  post-restart mismatch fails with the observed value retained
  (`libs/charter_runner_store/propagation_terminal.py:96-191`). Its tests
  distinguish outgoing-generation deferral from a genuine mismatch and from a
  matching post-restart close
  (`libs/charter_runner_store/test_propagation_terminal.py:150-225`).
- The closeout relay is still provenance-poor at its API boundary. Its payload
  has only `body`, bare `status`, string `source`, optional full body, and a
  clamp flag (`services/git_integration_worker/cursor_auto/closeout_relay_common.py:39-49`).
  `status` is selected from authored prose, a wrapper, a ledger fallback, and
  later clamps (`closeout_relay.py:269-350`;
  `closeout_relay_briefing.py:228-305`), but the emitted field does not say
  which input won or whether it is observed, derived, or asserted.
- Capture data already demonstrates a useful split but only as sibling scalars:
  a run can be `work_outcome=shipped`, `status=complete`, and
  `capture_status=unavailable` (`services/git_integration_worker/tests/test_cursor_sdk_capture_status.py:706-750`).
  In the relay fixture, `files_modified=[]` coexists with
  `capture_status=partial` and a sidecar that names an edit
  (`services/git_integration_worker/tests/test_cursor_auto_closeout_relay.py:1105-1160`).
  The empty list is an observation of incomplete capture, not proof that no
  file changed.
- The drain supervisor retains a remaining semantic ambiguity. It marks a
  restart intent `STATUS_COMPLETED` immediately after the injected SIGTERM
  callable returns, then separately settles propagation rows through observed
  liveness (`scripts/model_manager/ui/controller/git_worker_drain_supervisor.py:248-270`,
  `:379-410`). Its alternate non-kill path also marks completed if the old
  generation is gone (`:293-307`). Those are defensible control-plane
  completion facts, but the token `completed` is unsafe if any consumer reads
  it as “new code is live.”
- Existing incapacity-like tokens mix policy facts and runtime claims without
  type distinction. `nested_in_seat_unsupported` and
  `gate_at_capacity_park_unavailable` are returned as plain reasons
  (`services/git_integration_worker/cursor_auto/gate_serialize.py:18-62`).
  `execute_multi_op_unsupported` is a static contract/policy refusal
  (`services/git_integration_worker/cursor_auto/execute_admission.py:127-167`).
  These should not be represented like an observed runtime inability; nor
  should a couple of failed calls be able to produce a confirmed global
  incapacity claim.

# What you changed

- No production behavior was changed by this workstream. The reviewed model
  changes are already landed: `4f7367ff0501fe7346cef5b06b2493d0e681902c`
  (honest closeout parser misses) and the current weakest-row propagate
  implementation covered by
  `services/git_integration_worker/tests/test_handler_propagation_disposition.py`.
- Proposal for Workstream A's OpenAPI response schema: replace bare boundary
  state fields with a reusable `ProvenancedValue`:

  ```text
  value
  basis: observed | derived | asserted | policy_denial | inconclusive
  source_ref: URI | null
  observed_at: timestamp | null
  derivation: rule-id + input field paths | null
  attempts: [{source_ref, observed_at, result}] | null
  ```

  `observed` requires a source artifact or probe; `derived` requires named
  inputs and a rule; `asserted` is explicitly a seat statement and cannot be
  silently promoted; `inconclusive` replaces negative inference from missing
  data. `policy_denial` names a checked static rule instead of pretending that
  the system lacks a capability.
- Add a separate `CapabilityAssessment` for negative capability claims. It
  must carry `scope`, `basis`, `attempts`, `expires_at`, and
  `alternative_route_checked`. Only an explicit static policy rule may emit
  `policy_denial`; runtime “unavailable/cannot” remains `inconclusive` until
  multiple independent observations support a bounded assessment. Consumers
  must treat expired or inconclusive assessments as retryable, never as a
  suppression signal.
- Rename or split drain-intent completion into `restart_signal_sent` (control
  plane) and `live_proof` (data plane). Only the latter may support a
  `code_live` conclusion. This follows the propagation-terminal shape rather
  than adding another timeout or parser gate.

# What you did NOT change and why

- I did not add provenance fields ad hoc in Python. The payloads cross the
  relay and API boundaries; a local dataclass change without Workstream A's
  response contract would create another unversioned parallel schema.
- I did not gate every string containing “unsupported” or “unavailable.”
  That would be brittle and would incorrectly block clear static policy
  denials. The gate belongs at typed boundary assessments, where the producer
  must classify the statement and provide evidence.
- I did not reinterpret `capture_status=unavailable` as a global incapacity
  claim. In the capture path it is a scoped observation about a missing
  baseline/probe; the schema must preserve that scope.

# PROPAGATION REQUIRED

- `git_integration_worker` at this workstream's report commit: no runtime code
  changed, so no restart is needed for this investigation artifact.
- ~~`git_integration_worker` at `4f7367ff0501fe7346cef5b06b2493d0e681902c`: restart
  required for the already-landed relay honesty fix; it remains landed-not-live until
  then.~~
  **WITHDRAWN on lead verification, 2026-07-31 13:59 — no restart required; the fix
  is already LIVE.** `4f7367ff` (10:32:58) is an ancestor of the running code
  (`git merge-base --is-ancestor 4f7367ff 82f07260` → true). The serving process
  (pid 1173131, `uvicorn services.git_integration_worker.app:app --port 8091`) started
  12:51:44, and `GET http://127.0.0.1:8091/api/v1/git/cursor-auto/liveness` self-reports
  `{"live":true,"code_version":"82f07260…","uptime_s":4101}`.

  *Retained visibly rather than silently edited: this entry is itself a specimen for the
  inventory above — a **landed-not-live** claim asserted where an **observation** was
  available, produced by the very workstream auditing that failure mode. It argues for
  the proposal concretely: `PROPAGATION REQUIRED` entries want a `ProvenancedValue` with
  basis `observed(code_version)` rather than `derived(commit_is_recent)`, and the
  liveness endpoint already serves exactly that field.*
- `charter-runner` and the model-manager/control-plane process that loads
  `scripts/model_manager/ui/controller/git_worker_drain_supervisor.py` would
  require propagation when the proposed typed terminal/liveness split is
  implemented. The exact service set and SHA depend on Workstream A's schema
  migration; no such code was changed here.

# Open questions and residuals

- Which response boundaries can adopt `ProvenancedValue` without breaking
  consumers? Settle with Workstream A's generated OpenAPI inventory and a
  consumer search before changing any payload.
- Is `STATUS_COMPLETED` documented and consumed as “SIGTERM accepted” or “new
  worker live”? Settle by tracing every `RestartIntentStore` reader and testing
  a kill-returning-but-not-yet-live worker.
- The narrow relay suite currently has one failing stale expectation:
  `test_web_anthropic_missing_access_coverage_clamps_turn38_class` expects
  `caller_auditable("web-anthropic") is False`, while the current allowlist
  deliberately restores it as auditable
  (`caller_auditable.py:18-39`; commit `4b056a34`). Settle by deciding whether
  the test should target the denied `mcp-claude-life` address, then update the
  test in a separate owned fix.
- What evidence threshold should expire a runtime incapacity assessment? Settle
  with a small set of known false-incapacity incidents: compare independent
  endpoint/probe observations against the later successful route, then set
  domain-specific attempt and expiry rules rather than a universal count.
