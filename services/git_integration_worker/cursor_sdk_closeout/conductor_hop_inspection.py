"""Loop inspection for conductor hop budgets (friction:39253).

Pure read of terminal chain signatures — no ledger writes, no paging.
``evaluate_hop_budget`` maps ``LoopInspection`` outcomes to ``HopBudgetVerdict``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
    _PARK_REASON_NO_PROGRESS_CAP,
    HopBudgetConfig,
    HopBudgetVerdict,
    prior_record_tokens,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_progress import (
    HopProgressSignature,
    progress_signature_for_row,
    signature_advanced,
    signature_can_prove_loop,
)
from services.git_integration_worker.cursor_sdk_park_ledger import PARK_KIND_RESTART

PARK_REASON_LOOP = "hop_budget_loop"
REASON_INSPECTION_REQUESTED = "inspection_requested"
REASON_INSPECTION_DEFERRED = "inspection_deferred"
REASON_INSPECTION_UNPROVABLE = "inspection_unprovable"

LoopOutcome = Literal["clear", "loop", "suspicion", "unprovable"]


@dataclass(frozen=True, slots=True)
class LoopInspection:
    """Result of comparing the current hop to prior terminal rows on one mission."""

    outcome: LoopOutcome
    loop_reason: str | None = None
    signature_key: str | None = None


def signature_key_for(signature: HopProgressSignature) -> str:
    """Stable dedup key for inspection paging and hop inspection events."""
    payload = {
        "entry_gate": signature.entry_gate,
        "witnessed_done": sorted(signature.witnessed_done),
        "lane_tip": signature.lane_tip,
        "next_admit": signature.next_admit,
        "scoreboard_tip": signature.scoreboard_tip,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _planned_row_hop(row: dict[str, Any], *, closeout_tokens: frozenset[str]) -> bool:
    if "ROW_HOP" in closeout_tokens:
        return True
    from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_progress import (
        record_data,
    )

    record = record_data(str(row.get("record_json") or ""))
    tokens = record.get("closeout_stop_tokens")
    if isinstance(tokens, list) and "ROW_HOP" in tokens:
        return True
    return False


def _loop_eligible(row: dict[str, Any], *, closeout_tokens: frozenset[str]) -> bool:
    if _planned_row_hop(row, closeout_tokens=closeout_tokens):
        return True
    if closeout_tokens & frozenset({"CONSULT_PENDING", "PARKED_TRANSPORT"}):
        return True
    return False


def _eligible_priors(
    chain: list[dict[str, Any]],
    *,
    dispatch_id: str,
) -> list[dict[str, Any]]:
    """Newest-first terminal rows strictly prior to ``dispatch_id`` that count toward loop evidence."""
    prior_slice = chain
    for idx, item in enumerate(chain):
        if item.get("dispatch_id") == dispatch_id:
            prior_slice = chain[:idx]
            break
    out: list[dict[str, Any]] = []
    for prior in reversed(prior_slice):
        if prior.get("dispatch_id") == dispatch_id:
            continue
        if str(prior.get("park_kind") or "") == PARK_KIND_RESTART:
            continue
        tokens = prior_record_tokens(prior)
        if not _loop_eligible(prior, closeout_tokens=tokens):
            continue
        out.append(prior)
    return out


def _adjacent_no_progress_loop(
    current: HopProgressSignature,
    priors_newest_first: list[dict[str, Any]],
    *,
    no_progress_cap: int,
) -> bool:
    if no_progress_cap <= 0:
        return False
    streak = 0
    last = current
    for prior in priors_newest_first:
        prior_sig = progress_signature_for_row(prior, live=False)
        if signature_advanced(last, prior_sig):
            break
        if not signature_can_prove_loop(last, prior_sig):
            break
        streak += 1
        last = prior_sig
    return streak >= no_progress_cap


def _recurrence_loop(
    current: HopProgressSignature,
    priors_newest_first: list[dict[str, Any]],
) -> bool:
    if not current.lane_tip or not current.scoreboard_tip:
        return False
    for idx, prior in enumerate(priors_newest_first):
        if idx == 0:
            continue
        prior_sig = progress_signature_for_row(prior, live=False)
        if not prior_sig.lane_tip or not prior_sig.scoreboard_tip:
            continue
        if current == prior_sig:
            return True
    return False


def _newest_unpaid(current: HopProgressSignature) -> bool:
    if current.witnessed_done:
        return False
    if current.lane_tip or current.next_admit or current.scoreboard_tip:
        return False
    return True


def _no_fold_advance(
    current: HopProgressSignature,
    prior: HopProgressSignature | None,
) -> bool:
    if prior is None:
        return False
    return (
        current.entry_gate == prior.entry_gate
        and current.witnessed_done == prior.witnessed_done
    )


def inspect_hop_loop(
    row: dict[str, Any],
    chain: list[dict[str, Any]],
    *,
    config: HopBudgetConfig,
    closeout_tokens: frozenset[str] | None = None,
) -> LoopInspection:
    """Classify loop evidence for the hop closing on *row*.

    *chain* is the full terminal mission chain (oldest first), including *row*.
    Priors are read with ``live=False`` so today's fold cannot overwrite stamps.
    """
    tokens = closeout_tokens or prior_record_tokens(row)
    if not _loop_eligible(row, closeout_tokens=tokens):
        return LoopInspection(outcome="clear")

    dispatch_id = str(row.get("dispatch_id") or "")
    current = progress_signature_for_row(row)
    key = signature_key_for(current)
    priors = _eligible_priors(chain, dispatch_id=dispatch_id)

    if _adjacent_no_progress_loop(
        current, priors, no_progress_cap=config.no_progress_cap
    ):
        return LoopInspection(
            outcome="loop",
            loop_reason=_PARK_REASON_NO_PROGRESS_CAP,
            signature_key=key,
        )

    if _recurrence_loop(current, priors):
        return LoopInspection(
            outcome="loop",
            loop_reason=PARK_REASON_LOOP,
            signature_key=key,
        )

    if _newest_unpaid(current):
        return LoopInspection(outcome="unprovable", signature_key=key)

    prior_sig = (
        progress_signature_for_row(priors[0], live=False) if priors else None
    )
    if prior_sig is not None and signature_advanced(current, prior_sig):
        return LoopInspection(outcome="clear", signature_key=key)

    if _no_fold_advance(current, prior_sig):
        if prior_sig is not None and not signature_can_prove_loop(
            current, prior_sig
        ):
            return LoopInspection(outcome="unprovable", signature_key=key)
        return LoopInspection(outcome="suspicion", signature_key=key)

    return LoopInspection(outcome="clear", signature_key=key)


def inspection_to_verdict(
    inspection: LoopInspection,
    *,
    config: HopBudgetConfig,  # noqa: ARG001 — reserved for future cap wiring
) -> HopBudgetVerdict:
    """Map a pure ``LoopInspection`` outcome to the reactor-facing budget verdict."""
    if inspection.outcome == "loop":
        return HopBudgetVerdict(
            ok=False,
            park=True,
            reason=inspection.loop_reason or PARK_REASON_LOOP,
        )
    if inspection.outcome == "suspicion":
        return HopBudgetVerdict(
            ok=True,
            park=False,
            reason=REASON_INSPECTION_REQUESTED,
        )
    if inspection.outcome == "unprovable":
        return HopBudgetVerdict(
            ok=True,
            park=False,
            reason=REASON_INSPECTION_UNPROVABLE,
        )
    return HopBudgetVerdict(ok=True, park=False)


__all__ = [
    "LoopInspection",
    "LoopOutcome",
    "PARK_REASON_LOOP",
    "REASON_INSPECTION_DEFERRED",
    "REASON_INSPECTION_REQUESTED",
    "REASON_INSPECTION_UNPROVABLE",
    "inspect_hop_loop",
    "inspection_to_verdict",
    "signature_key_for",
]
