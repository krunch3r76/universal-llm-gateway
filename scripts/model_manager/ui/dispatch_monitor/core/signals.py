"""Signal-name registry -- the single place G5 reconciles names against live code.

Every signal string the core reacts to is declared here and nowhere else. Fold
logic keys off these constants, so a name that turns out to be wrong at graft
time is a one-line edit in this module rather than a hunt through handlers.

Provenance is tracked per family because it differs sharply:

``manage.charter.tick.*`` -- **VERIFIED**. Named in
``cortex://notes/system/specs/charter-runner-state-close-on-no-gated-pickup.md``
(``root_skipped`` / ``root_closed`` / ``scanned.skipped_by_reason``),
``charter-runner-tick.md`` (``waiting_open``), ``charter-runner-admit-intent-orphan-self-heal.md``
(``intent_healed``), and ``recon/charter-admission-environment-contract/tier1-anchors.md``
(``error``, ``audit``, ``root_skipped`` live samples).

``frontier.sdk.worker.*`` / ``pipeline.frontier.dispatch.*`` -- **PARTLY VERIFIED**.
``frontier.sdk.worker.completed`` and ``pipeline.frontier.dispatch.completed`` /
``.started`` are named as existing signals in ``specs/dispatch-token-cost-rollup.md``,
``specs/model-capability-pricing.md``, ``specs/cached-token-telemetry-prefix-audit.md``
and ``specs/dispatch-surface-unify.md``. The ``.started`` / ``.progress`` / ``.failed``
members of the *worker* family are INFERRED and flagged in ``README.md``.

``cdp.generate.*`` -- **PROSPECTIVE**. Assigned by the G4 scope packet. The CDP
lane emits no events today (``cortex://notes/system/threads/5718-session-review-substrate-apis.md``:
"the entire CDP lane emits zero events"), so this family is a contract the
emitter side must still honour. Folding it is harmless until then: absent
signals simply leave ``cdp`` empty.

Naming tension worth a G5 decision, not a core change: ``[universal:events]``
fixes the signal grammar at ``^[a-z]+(\\.[a-z]+){1,4}$`` -- no underscores. Four
live charter signals (``root_skipped``, ``root_closed``, ``waiting_open``,
``intent_healed``) violate it. The core is a consumer and accepts what the bus
actually emits; the divergence belongs to the charter-runner owner.
"""

from __future__ import annotations

# --- charter family (VERIFIED) ---------------------------------------------
CHARTER_SCANNED = "manage.charter.tick.scanned"
CHARTER_ADMITTED = "manage.charter.tick.admitted"
CHARTER_CLOSED = "manage.charter.tick.closed"
CHARTER_ROOT_SKIPPED = "manage.charter.tick.root_skipped"
CHARTER_ROOT_CLOSED = "manage.charter.tick.root_closed"
CHARTER_WAITING_OPEN = "manage.charter.tick.waiting_open"
CHARTER_ERROR = "manage.charter.tick.error"
CHARTER_INTENT_HEALED = "manage.charter.tick.intent_healed"
CHARTER_AUDIT = "manage.charter.tick.audit"

#: Signals accepted only as cold-start seed material, never as live transitions.
CHARTER_COLD_START = (CHARTER_AUDIT,)

# --- sdk family: worker lane (GS2 emitter A) -------------------------------
SDK_WORKER_PROGRESS = "frontier.sdk.worker.progress"
SDK_WORKER_COMPLETED = "frontier.sdk.worker.completed"
SDK_WORKER_FAILED = "frontier.sdk.worker.failed"

# --- sdk family: pipeline lane (GS2 emitter B) -----------------------------
SDK_PIPELINE_STARTED = "pipeline.frontier.dispatch.started"
SDK_PIPELINE_COMPLETED = "pipeline.frontier.dispatch.completed"
SDK_PIPELINE_FAILED = "pipeline.frontier.dispatch.failed"

#: Emitter identity per signal. Both lanes describe the *same* dispatch; the
#: fold reconciles them rather than letting the later one overwrite the earlier.
EMITTER_WORKER = "worker"
EMITTER_PIPELINE = "pipeline"

SDK_EMITTER_BY_SIGNAL = {
    SDK_WORKER_PROGRESS: EMITTER_WORKER,
    SDK_WORKER_COMPLETED: EMITTER_WORKER,
    SDK_WORKER_FAILED: EMITTER_WORKER,
    SDK_PIPELINE_STARTED: EMITTER_PIPELINE,
    SDK_PIPELINE_COMPLETED: EMITTER_PIPELINE,
    SDK_PIPELINE_FAILED: EMITTER_PIPELINE,
}

SDK_TERMINAL_SIGNALS = frozenset(
    {
        SDK_WORKER_COMPLETED,
        SDK_WORKER_FAILED,
        SDK_PIPELINE_COMPLETED,
        SDK_PIPELINE_FAILED,
    }
)

SDK_FAILURE_SIGNALS = frozenset({SDK_WORKER_FAILED, SDK_PIPELINE_FAILED})

# --- cdp family (PROSPECTIVE) ---------------------------------------------
CDP_SUBMITTED = "cdp.generate.submitted"
CDP_RUNNING = "cdp.generate.running"
CDP_PROGRESS = "cdp.generate.progress"
CDP_COMPLETED = "cdp.generate.completed"
CDP_FAILED = "cdp.generate.failed"
CDP_ABORTED = "cdp.generate.aborted"
CDP_STALLED = "cdp.generate.stalled"

CDP_TERMINAL_SIGNALS = frozenset({CDP_COMPLETED, CDP_FAILED, CDP_ABORTED})

# --- realtime-plane drop counters (VERIFIED, doctrine §2) ------------------
EVENTS_DROPPED_INGEST = "events.dropped.ingest"
EVENTS_DROPPED_SUBSCRIBE = "events.dropped.subscribe"

# --- cold-start seed meta (G5 graft — not emitted on the live bus) ---------
MONITOR_SEED_FOLD_STATUS = "monitor.seed.fold_status"
#: Synthetic SDK row injection from reconcile / lease-snapshot (D2: no live emitter).
MONITOR_META_SDK_STARTED = "monitor.meta.sdk_started"
#: Reconcile source failure — graft-only; drives attention, never steady-state poll.
MONITOR_RECONCILE_SOURCE_FAILED = "monitor.reconcile.source_failed"

#: Payload key graft uses to mark lease-snapshot reconcile rows. SdkFold reads
#: this; live bus signals omit it and therefore count as ``signal`` provenance.
PROVENANCE_RECONCILED_KEY = "_provenance"
PROVENANCE_RECONCILED = "reconciled"

CHARTER_FAMILY = (
    CHARTER_SCANNED,
    CHARTER_ADMITTED,
    CHARTER_CLOSED,
    CHARTER_ROOT_SKIPPED,
    CHARTER_ROOT_CLOSED,
    CHARTER_WAITING_OPEN,
    CHARTER_ERROR,
    CHARTER_INTENT_HEALED,
    CHARTER_AUDIT,
)

SDK_FAMILY = tuple(SDK_EMITTER_BY_SIGNAL)

CDP_FAMILY = (
    CDP_SUBMITTED,
    CDP_RUNNING,
    CDP_PROGRESS,
    CDP_COMPLETED,
    CDP_FAILED,
    CDP_ABORTED,
    CDP_STALLED,
)

META_FAMILY = (
    EVENTS_DROPPED_INGEST,
    EVENTS_DROPPED_SUBSCRIBE,
    MONITOR_SEED_FOLD_STATUS,
    MONITOR_META_SDK_STARTED,
    MONITOR_RECONCILE_SOURCE_FAILED,
)

#: Every signal the handler table claims. ``README.md`` restates this list; the
#: coverage test asserts the two agree, so the README cannot drift silently.
ALL_HANDLED = CHARTER_FAMILY + SDK_FAMILY + CDP_FAMILY + META_FAMILY
