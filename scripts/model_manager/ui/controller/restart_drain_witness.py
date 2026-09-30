"""In-flight witnesses for a restart probe that did not answer.

``RestartDrainGate`` decides a non-force restart from one busy probe — an HTTP
call into the service being restarted. When that process is CPU-pinned the
probe times out and the gate can only say ``probe_error`` (a:36939 cdp_ask;
a:36595 agent_bus via the GIW census). Fail-closed is right — a probe that
did not answer must not kill a maybe-busy service — but "did not answer" is
not the end of the evidence. This module lets the gate consult witnesses that
do not need the pinned process to serve HTTP, and merges their verdicts
fail-closed::

    busy > idle > unknown

- ``busy``: a witness names in-flight work → the gate defers ``state=busy`` and
  names that work, exactly as a live busy probe would.
- ``idle``: an independent witness whose source would show the work if it
  existed says none is recorded → the gate may proceed. Only such a witness may
  return ``idle``.
- ``unknown``: nothing can say → the gate stays ``state=probe_error`` and
  reports which witnesses it consulted, so the caller's next step is still
  non-force (retry / ``busy_status``), never a blind force.

``LastProbeWitness`` is the universal witness every live-probed service gets
for free: the gate's memory of the target's own last successful answer. It
turns a probe timeout into a *named* busy deferral when that answer is recent
and busy. It never returns ``idle`` — work may have been admitted since the
snapshot, and only the target or a durable ledger can rule that out.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

WitnessVerdict = Literal["busy", "idle", "unknown"]

# A recent busy answer is a strong prior that the same work is still running:
# cdp_ask executions live minutes to hours (TTL 7200s), GIW dispatches longer.
# Past this age the snapshot says nothing about the present.
LAST_PROBE_WITNESS_TTL_S = 120.0

_IN_FLIGHT_REASON = "service has in-flight work; retry later or pass force=true"
_PROBE_ERROR_REASON = "could not determine in-flight work"
UNKNOWN_NEXT_STEP = (
    "retry after retry_after_s; manage(action='busy_status', service=...) shows "
    "which sources answered; force=true stays an explicit caller choice"
)


@dataclass(slots=True, kw_only=True)
class WitnessReport:
    """One witness's verdict on a service's in-flight work."""

    verdict: WitnessVerdict
    source: str
    holders: list[dict[str, Any]] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def provenance(self) -> dict[str, Any]:
        row: dict[str, Any] = {"source": self.source, "verdict": self.verdict}
        if self.note:
            row["note"] = self.note
        return row


@runtime_checkable
class InFlightWitness(Protocol):
    """Strategy: report in-flight work without calling into the probed process."""

    async def observe(self, service: str) -> WitnessReport: ...


def holders_from_active_work(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive census-shaped holder records from any busy-probe payload.

    Accepts the shapes the fleet's probes emit: a pre-built ``holders`` list,
    cdp_ask ``rows`` / ``execution_ids``, GIW ``dispatch_ids`` / ``active_ops``
    / ``write_lease``. Returns ``[]`` when the payload names nothing.
    """
    holders = detail.get("holders")
    if isinstance(holders, list) and holders:
        return [h for h in holders if isinstance(h, dict)]
    out: list[dict[str, Any]] = []
    rows = detail.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            eid = row.get("execution_id")
            if not isinstance(eid, str) or not eid.strip():
                continue
            rec: dict[str, Any] = {"kind": "execution", "op_id": eid.strip()}
            subject = " ".join(
                str(row[k]).strip() for k in ("holder", "purpose") if row.get(k)
            )
            if subject:
                rec["subject_preview"] = subject
            out.append(rec)
        if out:
            return out
    for key, kind in (("execution_ids", "execution"), ("dispatch_ids", "dispatch")):
        ids = detail.get(key)
        if isinstance(ids, list):
            out = [
                {"kind": kind, "op_id": i.strip()}
                for i in ids
                if isinstance(i, str) and i.strip()
            ]
            if out:
                return out
    ops = detail.get("active_ops")
    if isinstance(ops, list):
        for op in ops:
            if not isinstance(op, dict):
                continue
            rec = {"kind": str(op.get("kind") or "op")}
            ident = op.get("op_id") or op.get("dispatch_id") or op.get("job_id")
            if isinstance(ident, str) and ident.strip():
                rec["op_id"] = ident.strip()
            subject = op.get("subject_preview")
            if isinstance(subject, str) and subject.strip():
                rec["subject_preview"] = subject.strip()
            out.append(rec)
    lease = detail.get("write_lease")
    if isinstance(lease, dict):
        hid = lease.get("holder_dispatch_id")
        if isinstance(hid, str) and hid.strip():
            out.append(
                {
                    "kind": "write_lease",
                    "op_id": hid.strip(),
                    "dispatch_id": hid.strip(),
                }
            )
    return out


def _deferral_holder_label(detail: dict[str, Any]) -> str | None:
    """Name the first census holder, when the payload carries one."""
    holders = detail.get("holders")
    if not isinstance(holders, list) or not holders:
        return None
    first = holders[0]
    if not isinstance(first, dict):
        return None
    ident = first.get("op_id") or first.get("dispatch_id")
    if not isinstance(ident, str) or not ident.strip():
        return None
    label = f"{first.get('kind') or 'holder'}:{ident.strip()}"
    subject = first.get("subject_preview")
    if isinstance(subject, str) and subject.strip():
        label += f" subject={subject.strip()}"
    return label


def in_flight_defer_reason(detail: dict[str, Any]) -> str:
    """Busy-deferral reason. Names a holder only when the census attached one."""
    named = _deferral_holder_label(detail)
    if not named:
        return _IN_FLIGHT_REASON
    return f"{_IN_FLIGHT_REASON}; holder={named}"


@dataclass(slots=True)
class _ProbeSnapshot:
    busy: bool
    detail: dict[str, Any]
    recorded_at: float


class LastProbeWitness:
    """The gate's memory of each service's last successful busy probe.

    ``record`` is called by ``RestartDrainGate.probe`` on every answered probe
    (evaluate, busy_report, drain supervisors), so the snapshot is warm whenever
    any seat has read busy state recently. Verdicts: recent busy snapshot →
    ``busy`` naming the recorded holders; anything else → ``unknown``. A recent
    *idle* snapshot is still ``unknown``: work admitted after it would be cut.
    """

    def __init__(
        self,
        *,
        ttl_s: float = LAST_PROBE_WITNESS_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_s = ttl_s
        self._clock = clock
        self._snapshots: dict[str, _ProbeSnapshot] = {}

    def record(self, service: str, *, busy: bool, detail: dict[str, Any]) -> None:
        self._snapshots[service] = _ProbeSnapshot(
            busy=busy, detail=dict(detail), recorded_at=self._clock()
        )

    async def observe(self, service: str) -> WitnessReport:
        snap = self._snapshots.get(service)
        source = "last_probe"
        if snap is None:
            return WitnessReport(
                verdict="unknown",
                source=source,
                note="no successful probe recorded in this manage process",
            )
        age_s = max(0.0, self._clock() - snap.recorded_at)
        if age_s > self._ttl_s:
            return WitnessReport(
                verdict="unknown",
                source=source,
                note=f"last successful probe {age_s:.0f}s ago exceeds {self._ttl_s:.0f}s",
            )
        if not snap.busy:
            return WitnessReport(
                verdict="unknown",
                source=source,
                note=(
                    f"last successful probe {age_s:.0f}s ago reported idle; "
                    "work may have started since"
                ),
            )
        return WitnessReport(
            verdict="busy",
            source=source,
            holders=holders_from_active_work(snap.detail),
            detail=snap.detail,
            note=f"last successful probe {age_s:.0f}s ago reported busy",
        )


@dataclass(slots=True, kw_only=True)
class ProbeFailureResolution:
    """What the gate says about a service whose live probe raised."""

    verdict: WitnessVerdict
    probe_error: str
    active_work: dict[str, Any]
    witness: WitnessReport | None

    @property
    def state(self) -> str:
        """Deferral ``state`` for the non-proceed verdicts."""
        return "busy" if self.verdict == "busy" else "probe_error"

    @property
    def reason(self) -> str:
        if self.verdict == "busy":
            note = self.witness.note if self.witness is not None else ""
            tail = f"live probe failed: {self.probe_error}"
            if note:
                tail += f"; {note}"
            return f"{in_flight_defer_reason(self.active_work)} ({tail})"
        return f"{_PROBE_ERROR_REASON}: {self.probe_error}"

    def busy_report_row(self, *, restart_in_progress: bool) -> dict[str, Any]:
        """Row for ``RestartDrainGate.busy_report`` — mirrors what evaluate would do."""
        if self.verdict == "busy":
            determination = "busy"
            would_defer = True
        elif self.verdict == "idle":
            determination = "in_progress" if restart_in_progress else "idle"
            would_defer = restart_in_progress
        else:
            determination = "undetermined"
            would_defer = True
        return {
            "busy": self.verdict == "busy",
            "restart_would_defer": would_defer,
            "determination": determination,
            "active_work": self.active_work,
        }


async def resolve_probe_failure(
    service: str,
    *,
    probe_error: str,
    witnesses: Iterable[InFlightWitness],
) -> ProbeFailureResolution:
    """Consult every witness and merge fail-closed: busy > idle > unknown.

    A witness that raises counts as one more ``unknown`` — it can never turn
    into a proceed. ``active_work`` carries ``probe_error`` and the consulted
    ``witnesses`` on every verdict; the deciding ``witness`` on busy/idle; and
    ``error`` + ``next_step`` on unknown so summaries keep rendering the probe
    failure and the caller sees the non-force route.
    """
    reports: list[WitnessReport] = []
    for witness in witnesses:
        try:
            reports.append(await witness.observe(service))
        except Exception as exc:  # noqa: BLE001 — a failing witness is one more unknown
            reports.append(
                WitnessReport(
                    verdict="unknown",
                    source=type(witness).__name__,
                    note=f"witness raised {type(exc).__name__}: {exc}",
                )
            )
    consulted = [report.provenance() for report in reports]

    busy = next((r for r in reports if r.verdict == "busy"), None)
    if busy is not None:
        work = dict(busy.detail)
        work.update(
            {
                "busy": True,
                "holders": busy.holders,
                "probe_error": probe_error,
                "witness": busy.provenance(),
                "witnesses": consulted,
            }
        )
        # busy_work_summary renders active_ops, not holders — give it a line.
        work.setdefault("active_ops", busy.holders)
        return ProbeFailureResolution(
            verdict="busy", probe_error=probe_error, active_work=work, witness=busy
        )

    idle = next((r for r in reports if r.verdict == "idle"), None)
    if idle is not None:
        work = dict(idle.detail)
        work.update(
            {
                "busy": False,
                "probe_error": probe_error,
                "witness": idle.provenance(),
                "witnesses": consulted,
            }
        )
        return ProbeFailureResolution(
            verdict="idle", probe_error=probe_error, active_work=work, witness=idle
        )

    return ProbeFailureResolution(
        verdict="unknown",
        probe_error=probe_error,
        active_work={
            "error": probe_error,
            "probe_error": probe_error,
            "witnesses": consulted,
            "next_step": UNKNOWN_NEXT_STEP,
        },
        witness=None,
    )


__all__ = [
    "LAST_PROBE_WITNESS_TTL_S",
    "UNKNOWN_NEXT_STEP",
    "InFlightWitness",
    "LastProbeWitness",
    "ProbeFailureResolution",
    "WitnessReport",
    "WitnessVerdict",
    "holders_from_active_work",
    "in_flight_defer_reason",
    "resolve_probe_failure",
]
