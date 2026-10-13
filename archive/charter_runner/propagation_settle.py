"""Order edges, settle verdict, and one-shot revert for charter propagation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from implement_admission.propagation_row import ORDER_AFTER
from implement_admission.settle_gate import SettleVerdict
from implement_admission.settle_pipeline_maps import (
    newest_in_scope_verdict,
    provider_land_in_scope,
)

RevertFn = Callable[..., Awaitable[dict[str, Any]]]


def restart_blocked_by_order(
    service: str,
    provider_verdicts: Mapping[str, str],
    *,
    land_code_ref: str | None = None,
    source_repo: Path | str | None = None,
) -> bool:
    """True when an ``after`` provider has not reached verdict ``pass``.

    Missing, ``indeterminate``, ``pending``, and ``fail_attributable`` all block
    when a verdict is in scope. Harvest uses the same predicate so a re-fire
    cannot bypass the edge.

    When *land_code_ref* is set, a provider row is in scope when its land
    equals *land_code_ref* or is a git ancestor of it. Only the newest
    in-scope land governs: a newer ``pass`` supersedes an older non-pass.
    A missing in-scope key does not block. The governing verdict blocks
    when it is not ``pass``, including ``pending``.

    When *land_code_ref* is ``None``, unscoped service keys apply: a missing or
    non-``pass`` provider verdict blocks.
    """
    repo: Path | None = None
    if source_repo is not None:
        repo = Path(source_repo).expanduser()
    for provider in ORDER_AFTER.get(service, ()):
        if land_code_ref:
            scoped_prefix = f"{provider}:"
            in_scope: list[tuple[str, str]] = []
            for key, verdict in provider_verdicts.items():
                if not key.startswith(scoped_prefix):
                    continue
                provider_land = key[len(scoped_prefix) :]
                if not provider_land_in_scope(
                    provider_land, land_code_ref, source_repo=repo
                ):
                    continue
                in_scope.append((provider_land, verdict))
            governing = newest_in_scope_verdict(in_scope, source_repo=repo)
            if governing is not None and governing != "pass":
                return True
            continue
        if provider_verdicts.get(provider) != "pass":
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
    landed = outcome.get("status") == "landed"
    calls = 1 if landed else 0
    recorded = reprobe if reprobe is not None else outcome.get("reprobe")
    escalated = bool(calls) and recorded is not None and recorded != "pass"
    return {
        "verdict": verdict,
        "revert_calls": calls,
        "revert": outcome,
        "escalated": escalated,
        "reprobe": recorded,
    }


def giw_ready_join_verdict(outcome: str) -> SettleVerdict:
    """Ready-join failure after the new pid is ``fail_attributable``.

    This path does not subscribe to ``federation.catalog.changed``.
    """
    if outcome == "ready":
        return "pass"
    if outcome == "skipped":
        return "indeterminate"
    return "fail_attributable"


def emit_settle_verdict(
    *,
    service: str,
    land_sha: str,
    verdict: str,
    detail: str = "",
) -> None:
    """Emit ``propagation.settle.verdict``. The verdict is a field, not the signal."""
    payload = {
        "service": service,
        "land_sha": land_sha,
        "verdict": verdict,
        "detail": detail,
    }
    try:
        from mcp_events import record
    except ImportError:
        return
    record("propagation.settle.verdict", **payload)
