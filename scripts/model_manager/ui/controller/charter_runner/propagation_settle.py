"""Order edges, settle verdict, and one-shot revert for charter propagation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from implement_admission.propagation_row import ORDER_AFTER
from implement_admission.settle_gate import SettleVerdict

RevertFn = Callable[..., Awaitable[dict[str, Any]]]


def restart_blocked_by_order(
    service: str,
    provider_verdicts: Mapping[str, str],
    *,
    land_code_ref: str | None = None,
) -> bool:
    """True when an ``after`` provider has not reached verdict ``pass``.

    Missing, ``indeterminate``, and ``fail_attributable`` all block. Harvest
    uses the same predicate so a re-fire cannot bypass the edge.

    When *land_code_ref* is set, only the provider row for that land counts —
    not an older provider verdict from a different merge on the same service.
    """
    for provider in ORDER_AFTER.get(service, ()):
        key = provider
        if land_code_ref:
            key = f"{provider}:{land_code_ref}"
        if provider_verdicts.get(key, provider_verdicts.get(provider)) != "pass":
            return True
    return False


def should_call_revert(verdicts: Mapping[str, str]) -> bool:
    """One land reverts when any row is ``fail_attributable``.

    A mix with ``indeterminate`` still reverts. All-indeterminate does not.
    """
    return "fail_attributable" in verdicts.values()


async def apply_verdict(
    verdict: SettleVerdict,
    *,
    revert: RevertFn,
    already_reverted: bool,
    reprobe: SettleVerdict | None = None,
    revert_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply O1. A non-pass re-probe does not call ``revert`` again."""
    if verdict != "fail_attributable" or already_reverted:
        return {
            "verdict": verdict,
            "revert_calls": 0,
            "escalated": False,
        }
    outcome = await revert(**(revert_kwargs or {}))
    calls = 1
    escalated = reprobe is not None and reprobe != "pass"
    return {
        "verdict": verdict,
        "revert_calls": calls,
        "revert": outcome,
        "escalated": escalated,
        "reprobe": reprobe,
    }


def giw_ready_join_verdict(outcome: str) -> SettleVerdict:
    """Ready-join failure after the new pid is ``fail_attributable``.

    This path does not subscribe to ``federation.catalog.changed``.
    """
    if outcome == "ready":
        return "pass"
    if outcome == "timeout":
        return "fail_attributable"
    return "indeterminate"
