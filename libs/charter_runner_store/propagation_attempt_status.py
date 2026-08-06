"""Ledger ``status`` is an observed-of-attempt *event*, not current liveness.

Packet D / row 29 member 2 bind (2026-08-06, agent-bus:6655):

- **STATUS STAYS AN EVENT.** Values remain ``open`` | ``failed`` | ``closed`` —
  do not redefine tokens and do not add a ``status_register`` column.
- **READER OWNS CURRENT STATE.** Ask
  :func:`charter_runner_store.propagation_liveness.observe_code_ref_live`
  whether a ``code_ref`` is live now. Reading ``status=failed`` as durable
  not-live is a consumer defect.
- **Consumer census that decided the fork:** zero production sites treat
  ``propagation_ledger.status`` as current liveness. Restart-intent
  ``status`` (drain lifecycle) is a different store/question. Therefore the
  pragmatic ``status_register``-on-column option was overruled in favor of
  typing this column as attempt-event + keeping the existing reader.
- **Row 28 does not close this remainder.** Retirement-on-observed-liveness
  closes ``status='open'`` only. Already-failed GIW rows (specimen
  ``git_integration_worker:40f8eadde10a2fb2afcfde4960c11db11a22c56c:sync_restart``)
  stay failed events while the reader can answer ``yes``.

``PropagationAttemptStatus`` names the contract for importers and tests.
The column itself is the event register — that is the provenance
discriminator for member 2 (structural, not a per-value ``ClaimRegister``).
"""

from __future__ import annotations

from typing import Literal

PropagationAttemptStatus = Literal["open", "failed", "closed"]

ATTEMPT_STATUS_VALUES: frozenset[str] = frozenset({"open", "failed", "closed"})

# Family claim tag: status values are observed outcomes of one attempt.
STATUS_CLAIM_KIND = "observed_of_attempt"

STATUS_OPEN: PropagationAttemptStatus = "open"
STATUS_FAILED: PropagationAttemptStatus = "failed"
STATUS_CLOSED: PropagationAttemptStatus = "closed"


def is_attempt_status(value: object) -> bool:
    """True when *value* is a known ledger attempt-status token."""
    return isinstance(value, str) and value in ATTEMPT_STATUS_VALUES
