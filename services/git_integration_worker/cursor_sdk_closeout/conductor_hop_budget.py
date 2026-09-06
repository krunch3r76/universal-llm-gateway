"""Conductor hop budget enforcement (todo:conductor-hop-reactor R6).

Env-tunable caps from bind §2.6 item 6. The verdict is advisory to the
reactor; announcing a park lives in ``conductor_hop_park``.

No-progress is judged against the multi-component progress signature in
``conductor_hop_progress``, not against the scoreboard fold alone: a fold
that has never accepted a witness returns the same first gate and empty set
on every hop, which is an unpaid instrument rather than a stalled mission
(``assertion:32411``).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_progress import (
    HOP_ENTRY_GATE_KEY,
    HOP_LANE_TIP_KEY,
    HOP_NEXT_ADMIT_KEY,
    HOP_WITNESSED_DONE_KEY,
    entry_gate_for_row,
    progress_signature_for_row,
    record_data,
    signature_advanced,
    signature_can_prove_loop,
)

logger = get_logger(__name__)

HOP_PARKED_KEY = "hop_parked"
HOP_PARK_REASON_KEY = "hop_park_reason"
HOP_LAST_TERMINAL_AT_KEY = "hop_last_terminal_at"

_DEFAULT_CRASH_CAP = 3
_DEFAULT_NO_PROGRESS_CAP = 2
_DEFAULT_MISSION_CAP = 24
_DEFAULT_BACKOFF_S = (30.0, 120.0, 300.0)
_DEFAULT_REACTOR_GRACE_S = 120.0

_PARK_REASON_MISSION_CAP = "hop_budget_mission_cap"
_PARK_REASON_CRASH_CAP = "hop_budget_crash_cap"
_PARK_REASON_NO_PROGRESS_CAP = "hop_budget_no_progress_cap"


@dataclass(frozen=True, slots=True)
class HopBudgetConfig:
    crash_cap_per_row: int
    no_progress_cap: int
    mission_cap: int
    crash_backoff_s: tuple[float, ...]
    reactor_grace_s: float


@dataclass(frozen=True, slots=True)
class HopBudgetVerdict:
    ok: bool
    park: bool = False
    reason: str | None = None
    backoff_s: float = 0.0


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("invalid %s=%r; using default %s", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("invalid %s=%r; using default %s", name, raw, default)
        return default


def load_hop_budget_config() -> HopBudgetConfig:
    """Read env caps (bind §2.6.6 defaults)."""
    return HopBudgetConfig(
        crash_cap_per_row=_env_int("CONDUCTOR_HOP_CRASH_CAP_PER_ROW", _DEFAULT_CRASH_CAP),
        no_progress_cap=_env_int(
            "CONDUCTOR_HOP_NO_PROGRESS_CAP", _DEFAULT_NO_PROGRESS_CAP
        ),
        mission_cap=_env_int("CONDUCTOR_HOP_MISSION_CAP", _DEFAULT_MISSION_CAP),
        crash_backoff_s=_DEFAULT_BACKOFF_S,
        reactor_grace_s=_env_float(
            "CONDUCTOR_HOP_REACTOR_GRACE_S", _DEFAULT_REACTOR_GRACE_S
        ),
    )


def _is_conductor_row(row: dict[str, Any]) -> bool:
    from services.git_integration_worker.cursor_sdk_conductor_conflict import (
        _record_packet_kind,
    )

    record_json = str(row.get("record_json") or "")
    return _record_packet_kind(record_json) == "conductor"


def list_mission_terminal_chain(
    *,
    work_key: str,
    exclude_dispatch_id: str | None = None,
) -> list[dict[str, Any]]:
    """Terminal conductor rows for one mission, oldest hop_seq first."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches "
            "WHERE work_key=? AND status IN ('completed','failed','cancelled') "
            "ORDER BY COALESCE(json_extract(record_json, '$.hop_seq'), 0), "
            "COALESCE(terminal_at, queued_at)",
            (work_key,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        mapped = {k: row[k] for k in row.keys()}
        if exclude_dispatch_id and mapped.get("dispatch_id") == exclude_dispatch_id:
            continue
        if not _is_conductor_row(mapped):
            continue
        out.append(mapped)
    return out


def _planned_closeout(row: dict[str, Any], *, closeout_tokens: frozenset[str]) -> bool:
    if "ROW_HOP" in closeout_tokens:
        return True
    record = record_data(str(row.get("record_json") or ""))
    tokens = record.get("closeout_stop_tokens")
    if isinstance(tokens, list) and "ROW_HOP" in tokens:
        return True
    return False


def _crash_backoff_s(*, crash_streak: int, config: HopBudgetConfig) -> float:
    if crash_streak <= 0:
        return 0.0
    idx = min(crash_streak - 1, len(config.crash_backoff_s) - 1)
    return config.crash_backoff_s[idx]


def _no_progress_verdict(
    row: dict[str, Any],
    *,
    chain: list[dict[str, Any]],
    dispatch_id: str,
    config: HopBudgetConfig,
) -> HopBudgetVerdict:
    """Park a planned chain only when a live progress signal stayed still.

    ``provable`` is the guard against the false park: a streak assembled
    entirely from hops whose only signal is an empty fold shows that nothing
    was ever measurable, not that nothing happened.
    """
    signature = progress_signature_for_row(row)
    streak = 0
    provable = False
    last = signature
    for prior in reversed(chain):
        if prior.get("dispatch_id") == dispatch_id:
            continue
        if not _planned_closeout(prior, closeout_tokens=prior_record_tokens(prior)):
            break
        prior_signature = progress_signature_for_row(prior)
        if signature_advanced(last, prior_signature):
            break
        provable = provable or signature_can_prove_loop(last, prior_signature)
        streak += 1
        last = prior_signature

    if config.no_progress_cap > 0 and streak >= config.no_progress_cap and provable:
        return HopBudgetVerdict(
            ok=False,
            park=True,
            reason=_PARK_REASON_NO_PROGRESS_CAP,
        )
    return HopBudgetVerdict(ok=True)


def evaluate_hop_budget(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str],
    config: HopBudgetConfig | None = None,
) -> HopBudgetVerdict:
    """Return whether the reactor may admit a successor (bind §2.6.6)."""
    cfg = config or load_hop_budget_config()
    work_key = str(row.get("work_key") or "")
    if not work_key:
        return HopBudgetVerdict(ok=True)

    record = record_data(str(row.get("record_json") or ""))
    if record.get(HOP_PARKED_KEY) is True:
        return HopBudgetVerdict(ok=False, park=False, reason=str(
            record.get(HOP_PARK_REASON_KEY) or "already_parked"
        ))

    dispatch_id = str(row.get("dispatch_id") or "")
    chain = list_mission_terminal_chain(
        work_key=work_key, exclude_dispatch_id=None
    )
    mission_hops = len(chain)
    if cfg.mission_cap > 0 and mission_hops >= cfg.mission_cap:
        return HopBudgetVerdict(
            ok=False,
            park=True,
            reason=_PARK_REASON_MISSION_CAP,
        )

    if _planned_closeout(row, closeout_tokens=closeout_tokens):
        return _no_progress_verdict(
            row, chain=chain, dispatch_id=dispatch_id, config=cfg
        )

    entry_gate = entry_gate_for_row(row)
    crash_streak = 0
    for prior in reversed(chain):
        if prior.get("dispatch_id") == dispatch_id:
            continue
        if entry_gate_for_row(prior) != entry_gate:
            break
        if _planned_closeout(prior, closeout_tokens=prior_record_tokens(prior)):
            break
        crash_streak += 1
    crash_streak += 1

    if cfg.crash_cap_per_row > 0 and crash_streak >= cfg.crash_cap_per_row:
        return HopBudgetVerdict(
            ok=False,
            park=True,
            reason=_PARK_REASON_CRASH_CAP,
        )
    return HopBudgetVerdict(
        ok=True,
        backoff_s=_crash_backoff_s(crash_streak=crash_streak, config=cfg),
    )


def prior_record_tokens(row: dict[str, Any]) -> frozenset[str]:
    record = record_data(str(row.get("record_json") or ""))
    raw = record.get("closeout_stop_tokens")
    if isinstance(raw, list):
        return frozenset(str(t).upper() for t in raw)
    return frozenset()


def budget_ok_for_hop(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str] | None = None,
) -> bool:
    """``hop_owed`` budget gate — park and backoff handled in the reactor."""
    tokens = closeout_tokens or prior_record_tokens(row)
    verdict = evaluate_hop_budget(row, closeout_tokens=tokens)
    return verdict.ok and not verdict.park


def build_budget_authority_patch(row: dict[str, Any]) -> dict[str, Any]:
    """Snapshot the progress signature onto ``record_json`` at terminal evaluation."""
    signature = progress_signature_for_row(row)
    patch: dict[str, Any] = {
        HOP_ENTRY_GATE_KEY: signature.entry_gate,
        HOP_WITNESSED_DONE_KEY: sorted(signature.witnessed_done),
        HOP_LAST_TERMINAL_AT_KEY: time.time(),
    }
    if signature.lane_tip:
        patch[HOP_LANE_TIP_KEY] = signature.lane_tip
    if signature.next_admit:
        patch[HOP_NEXT_ADMIT_KEY] = signature.next_admit
    return patch


__all__ = [
    "HOP_LAST_TERMINAL_AT_KEY",
    "HOP_PARK_REASON_KEY",
    "HOP_PARKED_KEY",
    "HopBudgetConfig",
    "HopBudgetVerdict",
    "budget_ok_for_hop",
    "build_budget_authority_patch",
    "evaluate_hop_budget",
    "list_mission_terminal_chain",
    "load_hop_budget_config",
    "prior_record_tokens",
]
