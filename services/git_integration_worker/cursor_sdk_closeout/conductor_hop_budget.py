"""Conductor hop budget enforcement (todo:conductor-hop-reactor R6).

Env-tunable caps from bind §2.6 item 6. The verdict is advisory to the
reactor; announcing a park lives in ``conductor_hop_park``.

No-progress is judged against the multi-component progress signature in
``conductor_hop_progress``, not against the scoreboard fold alone: a fold
that has never accepted a witness returns the same first gate and empty set
on every hop, which is an unpaid instrument rather than a stalled mission
(``assertion:32411``).

Budgets apply only to rows that owe a hop — a planned ``ROW_HOP`` or a crash.
A designed stop that owes no successor (``DONE``, ``ROW_PINNED``,
``HOLD_MERGE``, ``OPERATOR_GATE``) is never parked by a budget: a park on it
is a second lock the operator must release by hand (sixteen
``hop_budget_mission_cap`` parks landed on DONE / ROW_PINNED / OPERATOR_GATE
rows in the week to 2026-10-01, each paging). ``CONSULT_PENDING`` and
``PARKED_TRANSPORT`` do admit successors (``consult_pending_continue_owed``,
``park_harvest_continue_owed``), and those chains' only budget check is this
function, so the mission cap still applies. An exempt token on the same
closeout does not cancel that: the early exit requires an exempt token and
none of those continue-owed tokens. The mission cap counts hop
attempts, not substrate churn: a row GIW parked for a service restart
(``park_for_restart`` only) is the same hop as its resume child.
``cancel_discard`` is a finished attempt and still counts.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from claude_bundles.conductor_stop import STOP_TOKENS
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_progress import (
    HOP_ENTRY_GATE_KEY,
    HOP_LANE_TIP_KEY,
    HOP_NEXT_ADMIT_KEY,
    HOP_SCOREBOARD_TIP_KEY,
    HOP_WITNESSED_DONE_KEY,
    progress_signature_for_row,
    record_data,
    signature_advanced,
    signature_can_prove_crash,
    signature_can_prove_loop,
    signatures_share_crash_row,
)
from services.git_integration_worker.cursor_sdk_park_ledger import (
    PARK_KIND_DISCARD,
    PARK_KIND_RESTART,
)

logger = get_logger(__name__)

HOP_PARKED_KEY = "hop_parked"
HOP_PARK_REASON_KEY = "hop_park_reason"
# Mission hop count stamped when ``release_mission_parks`` clears a mission-cap park.
HOP_MISSION_CAP_RELEASE_BASELINE_KEY = "hop_mission_cap_release_baseline"
# Stops that owe no successor by themselves.
_CAP_EXEMPT_STOPS = frozenset({"DONE", "ROW_PINNED", "HOLD_MERGE", "OPERATOR_GATE"})
# Continue-owed stops whose successor is budgeted only by evaluate_hop_budget:
# consult_pending_continue_owed (CONSULT_PENDING) and park_harvest_continue_owed
# (PARKED_TRANSPORT). An exempt token on the same closeout must not skip the cap.
_BUDGET_ONLY_SUCCESSOR_STOPS = frozenset({"CONSULT_PENDING", "PARKED_TRANSPORT"})
HOP_LAST_TERMINAL_AT_KEY = "hop_last_terminal_at"

_DEFAULT_CRASH_CAP = 3
_DEFAULT_NO_PROGRESS_CAP = 2
_DEFAULT_MISSION_CAP = 48
_DEFAULT_BACKOFF_S = (30.0, 120.0, 300.0)
_DEFAULT_REACTOR_GRACE_S = 120.0

PARK_REASON_MISSION_CAP = "hop_budget_mission_cap"
_PARK_REASON_CRASH_CAP = "hop_budget_crash_cap"
_PARK_REASON_NO_PROGRESS_CAP = "hop_budget_no_progress_cap"
PARK_REASON_ADMIT_RETRY_CAP = "hop_budget_admit_retry_cap"


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
        crash_cap_per_row=_env_int(
            "CONDUCTOR_HOP_CRASH_CAP_PER_ROW", _DEFAULT_CRASH_CAP
        ),
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
    from services.git_integration_worker.cursor_sdk_conductor_identity import (
        is_conductor_dispatch_row,
    )

    return is_conductor_dispatch_row(row)


def list_mission_terminal_chain(
    *,
    work_key: str,
    exclude_dispatch_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Terminal conductor rows for one mission, oldest hop_seq first."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    sql = (
        "SELECT * FROM cursor_sdk_dispatches "
        "WHERE work_key=? AND status IN ('completed','failed','cancelled') "
        "ORDER BY CASE WHEN json_extract(record_json, '$.hop_seq') IS NULL "
        "THEN 0 ELSE 1 END, json_extract(record_json, '$.hop_seq'), "
        "COALESCE(terminal_at, queued_at)"
    )

    def _rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
        return connection.execute(sql, (work_key,)).fetchall()

    if conn is not None:
        rows = _rows(conn)
    else:
        ledger = CursorDispatchLedger.instance()
        with ledger._connect() as owned:
            rows = _rows(owned)
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


def _is_crash(*, closeout_tokens: frozenset[str]) -> bool:
    """True when the row carries no designed stop token (bind P1′ crash identity)."""
    return not (closeout_tokens & STOP_TOKENS)


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

    An unprovable pair (empty fold, unstamped tip) breaks the streak rather
    than padding it: hops 1–3 on this mission never recorded a tip, so they
    must not count against hop 4 after hop 4 stamped today's head.
    """
    signature = progress_signature_for_row(row)
    streak = 0
    last = signature
    for prior in reversed(chain):
        if prior.get("dispatch_id") == dispatch_id:
            continue
        if not _planned_closeout(prior, closeout_tokens=prior_record_tokens(prior)):
            break
        prior_signature = progress_signature_for_row(prior, live=False)
        if signature_advanced(last, prior_signature):
            break
        if not signature_can_prove_loop(last, prior_signature):
            break
        streak += 1
        last = prior_signature

    if config.no_progress_cap > 0 and streak >= config.no_progress_cap:
        return HopBudgetVerdict(
            ok=False,
            park=True,
            reason=_PARK_REASON_NO_PROGRESS_CAP,
        )
    return HopBudgetVerdict(ok=True)


def mission_cap_baseline(chain: list[dict[str, Any]]) -> int:
    """Largest hop-attempt count recorded at a mission-cap park release.

    Returns 0 when the mission has never had a mission-cap release baseline stamped.
    """
    baseline = 0
    for prior in chain:
        record = record_data(str(prior.get("record_json") or ""))
        raw = record.get(HOP_MISSION_CAP_RELEASE_BASELINE_KEY)
        if raw is None:
            continue
        try:
            baseline = max(baseline, int(raw))
        except (TypeError, ValueError):
            logger.warning(
                "invalid %s=%r on dispatch %s",
                HOP_MISSION_CAP_RELEASE_BASELINE_KEY,
                raw,
                prior.get("dispatch_id"),
            )
    return baseline


def count_hop_attempts(chain: list[dict[str, Any]]) -> int:
    """Terminal rows that were real hop attempts, for the mission cap.

    A row GIW parked for a service restart (``park_kind=park_for_restart``)
    was ended by the substrate, not by the mission, and its resume child is
    the same hop continued. Counting both charges every restart against the
    cap twice (worker 13618: six of fifteen rows were restart parks), so only
    those parents are skipped. ``cancel_discard`` and any other ``park_kind``
    are finished attempts. A missing ``park_kind`` reads as an attempt.
    """
    return sum(1 for prior in chain if prior.get("park_kind") != PARK_KIND_RESTART)


def evaluate_hop_budget(
    row: dict[str, Any],
    *,
    closeout_tokens: frozenset[str],
    config: HopBudgetConfig | None = None,
) -> HopBudgetVerdict:
    """Return whether the reactor may admit a successor (bind §2.6.6).

    Order matters: an already-parked row stays refused; a closeout that carries
    an exempt stop (``DONE``, ``ROW_PINNED``, ``HOLD_MERGE``, ``OPERATOR_GATE``)
    and no continue-owed stop (``CONSULT_PENDING``, ``PARKED_TRANSPORT``) is
    never parked by a budget. A continue-owed stop falls through to the mission
    cap even when an exempt token shares the closeout. Only then do the
    no-progress verdict (planned ``ROW_HOP``) and the crash cap apply.
    The watchdog calls this before ``hop_owed``, so a budget park on a
    no-successor stop would page and lock a mission that is merely waiting.

    ``park_kind=cancel_discard`` is a mission kill (a:37149): refuse without
    parking. The watchdog evaluates budget before ``hop_owed``, so without this
    gate a discard at the mission/crash cap would still page and ``hop_parked``
    lock the todo the operator just killed.
    """
    cfg = config or load_hop_budget_config()
    work_key = str(row.get("work_key") or "")
    if not work_key:
        return HopBudgetVerdict(ok=True)

    record = record_data(str(row.get("record_json") or ""))
    if record.get(HOP_PARKED_KEY) is True:
        return HopBudgetVerdict(
            ok=False,
            park=False,
            reason=str(record.get(HOP_PARK_REASON_KEY) or "already_parked"),
        )

    if str(row.get("park_kind") or "") == PARK_KIND_DISCARD:
        return HopBudgetVerdict(
            ok=False,
            park=False,
            reason="cancel_discard",
        )

    planned = _planned_closeout(row, closeout_tokens=closeout_tokens)
    # Exempt only when the closeout has no continue-owed token. A shared
    # CONSULT_PENDING or PARKED_TRANSPORT still owes a successor whose only
    # budget check is this function.
    if (
        not planned
        and (closeout_tokens & _CAP_EXEMPT_STOPS)
        and not (closeout_tokens & _BUDGET_ONLY_SUCCESSOR_STOPS)
    ):
        return HopBudgetVerdict(ok=True)

    dispatch_id = str(row.get("dispatch_id") or "")
    chain = list_mission_terminal_chain(work_key=work_key, exclude_dispatch_id=None)
    mission_hops = count_hop_attempts(chain)
    hops_since_release = mission_hops - mission_cap_baseline(chain)
    if cfg.mission_cap > 0 and hops_since_release >= cfg.mission_cap:
        return HopBudgetVerdict(
            ok=False,
            park=True,
            reason=PARK_REASON_MISSION_CAP,
        )

    if planned:
        # A mission-cap release resets the hop window only; no-progress is unchanged.
        return _no_progress_verdict(
            row, chain=chain, dispatch_id=dispatch_id, config=cfg
        )

    if not _is_crash(closeout_tokens=closeout_tokens):
        return HopBudgetVerdict(ok=True)

    signature = progress_signature_for_row(row)
    if not signature_can_prove_crash(signature):
        return HopBudgetVerdict(ok=True)

    crash_streak = 1
    last = signature
    for prior in reversed(chain):
        if prior.get("dispatch_id") == dispatch_id:
            continue
        prior_tokens = prior_record_tokens(prior)
        if prior_tokens & STOP_TOKENS:
            break
        if not _is_crash(closeout_tokens=prior_tokens):
            break
        prior_signature = progress_signature_for_row(prior, live=False)
        if not signatures_share_crash_row(last, prior_signature):
            break
        crash_streak += 1
        last = prior_signature

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
    """Closeout stop tokens stamped on a prior terminal row, upper-cased.

    Reads ``closeout_stop_tokens`` from the ledger record; an unstamped or
    malformed record reads as the empty set, which the budget treats as a
    crash row rather than a designed stop.
    """
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
    """Snapshot the progress signature onto ``record_json`` at terminal evaluation.

    Every component the no-progress streak later compares is stamped here,
    including the scoreboard tip sha, so a prior hop is reconstructed from
    what it recorded rather than from today's state.
    """
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
    if signature.scoreboard_tip:
        patch[HOP_SCOREBOARD_TIP_KEY] = signature.scoreboard_tip
    return patch


__all__ = [
    "HOP_LAST_TERMINAL_AT_KEY",
    "HOP_PARK_REASON_KEY",
    "HOP_PARKED_KEY",
    "PARK_REASON_ADMIT_RETRY_CAP",
    "HopBudgetConfig",
    "HopBudgetVerdict",
    "budget_ok_for_hop",
    "build_budget_authority_patch",
    "count_hop_attempts",
    "evaluate_hop_budget",
    "HOP_MISSION_CAP_RELEASE_BASELINE_KEY",
    "list_mission_terminal_chain",
    "mission_cap_baseline",
    "PARK_REASON_MISSION_CAP",
    "load_hop_budget_config",
    "prior_record_tokens",
]
