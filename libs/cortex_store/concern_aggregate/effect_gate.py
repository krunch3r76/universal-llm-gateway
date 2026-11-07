"""Test-only admit-execute-record adapter.

No production module may import this module. Tests import ``run_effect`` from
here; ``concern_aggregate.__init__`` does not re-export it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .clock import TrustedClock
from .effects import (
    STATE_FAILED,
    STATE_OUTCOME_UNKNOWN,
    STATE_SUCCEEDED,
    AdmitResult,
    EffectAdmissionError,
    admit_effect,
    record_outcome,
)
from .evidence import EffectEvidence, EvidenceProvider


class EffectNotApplied(Exception):  # noqa: N818 — spec name, not an Error suffix
    """Executor signals confirmed non-application, with optional evidence."""

    def __init__(self, evidence: EffectEvidence | None = None) -> None:
        super().__init__("effect was not applied")
        self.evidence = evidence


class EffectOutcomeUnknownError(EffectAdmissionError):
    """Execution finished without a verified applied outcome."""

    def __init__(
        self, message: str, *, effect_key: str, state: str | None = None
    ) -> None:
        super().__init__(message)
        self.effect_key = effect_key
        self.state = state


@dataclass(frozen=True)
class EffectPermit:
    """Permit handed to the executor. ``effect_key`` is the resource idempotency key."""

    effect_key: str
    permit_request_id: str
    request: dict[str, Any]


@dataclass(frozen=True)
class EffectRunResult:
    """Ledger-backed result. ``state`` is read after the final write."""

    admit: AdmitResult
    executed: bool
    state: str
    receipt: dict[str, Any] | None


def _ledger_state(conn: sqlite3.Connection, effect_key: str) -> str:
    row = conn.execute(
        "SELECT state FROM concern_effects WHERE effect_key = ?", (effect_key,)
    ).fetchone()
    if row is None:
        raise EffectOutcomeUnknownError(
            "effect row missing after run", effect_key=effect_key
        )
    return str(row[0])


def run_effect(
    conn: sqlite3.Connection,
    *,
    intent_id: str,
    effect_kind: str,
    request: dict[str, Any],
    request_id: str,
    fencing_token: int,
    executor: Callable[[EffectPermit], dict[str, Any]],
    clock: TrustedClock,
    evidence_provider: EvidenceProvider,
) -> EffectRunResult:
    """Admit once, run the executor at most once, then record the ledger outcome.

    The executor must send ``permit.effect_key`` to the accepting resource as its
    idempotency key. This adapter never resends. If ``record_outcome`` raises
    after the executor returns, the exception propagates and the row stays
    ``admitted``, so the next run gets no permit.
    """
    admit = admit_effect(
        conn,
        intent_id=intent_id,
        effect_kind=effect_kind,
        request=request,
        request_id=request_id,
        fencing_token=fencing_token,
        now=clock.now(),
    )
    if not admit.execution_allowed:
        return EffectRunResult(
            admit=admit,
            executed=False,
            state=admit.state,
            receipt=admit.receipt,
        )
    permit = EffectPermit(
        effect_key=admit.effect_key,
        permit_request_id=str(admit.permit_request_id),
        request=request,
    )
    try:
        result = executor(permit)
    except EffectNotApplied as exc:
        if exc.evidence is None:
            record_outcome(
                conn,
                effect_key=admit.effect_key,
                outcome=STATE_OUTCOME_UNKNOWN,
                receipt=None,
                fencing_token=fencing_token,
                now=clock.now(),
            )
            raise EffectOutcomeUnknownError(
                "non-application had no evidence",
                effect_key=admit.effect_key,
                state=_ledger_state(conn, admit.effect_key),
            ) from exc
        try:
            outcome = record_outcome(
                conn,
                effect_key=admit.effect_key,
                outcome=STATE_FAILED,
                receipt=None,
                fencing_token=fencing_token,
                now=clock.now(),
                evidence=exc.evidence,
                evidence_provider=evidence_provider,
            )
        except (ValueError, EffectAdmissionError) as rejected:
            record_outcome(
                conn,
                effect_key=admit.effect_key,
                outcome=STATE_OUTCOME_UNKNOWN,
                receipt=None,
                fencing_token=fencing_token,
                now=clock.now(),
            )
            raise EffectOutcomeUnknownError(
                "non-application evidence was rejected",
                effect_key=admit.effect_key,
                state=_ledger_state(conn, admit.effect_key),
            ) from rejected
        if not outcome.applied:
            raise EffectOutcomeUnknownError(
                "non-application was a late report",
                effect_key=admit.effect_key,
                state=_ledger_state(conn, admit.effect_key),
            ) from exc
        return EffectRunResult(
            admit=admit,
            executed=True,
            state=_ledger_state(conn, admit.effect_key),
            receipt=None,
        )
    except Exception as exc:
        record_outcome(
            conn,
            effect_key=admit.effect_key,
            outcome=STATE_OUTCOME_UNKNOWN,
            receipt=None,
            fencing_token=fencing_token,
            now=clock.now(),
        )
        raise EffectOutcomeUnknownError(
            "executor failed with an unknown outcome",
            effect_key=admit.effect_key,
            state=_ledger_state(conn, admit.effect_key),
        ) from exc

    recorded = record_outcome(
        conn,
        effect_key=admit.effect_key,
        outcome=STATE_SUCCEEDED,
        receipt=result,
        fencing_token=fencing_token,
        now=clock.now(),
    )
    state = _ledger_state(conn, admit.effect_key)
    if not recorded.applied:
        raise EffectOutcomeUnknownError(
            "outcome was a late report",
            effect_key=admit.effect_key,
            state=state,
        )
    return EffectRunResult(admit=admit, executed=True, state=state, receipt=result)
