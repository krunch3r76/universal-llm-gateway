"""Implement-readiness gate bypass echo for cursor-sdk closeouts.

``require_implement_ready`` resolves its ``source_ref`` from packet front matter
and no-ops silently when there is none, so a ``contract=implement`` dispatch can
run with zero readiness enforcement. Admission emits
``frontier.sdk.implement.source_ref_unresolved`` for that case, but the event
service is not on any charter-runner read path.

This module re-derives the same condition at closeout time and returns a
``deviations[]`` token, so the finding rides the closeout turn the charter-runner
harvest already reads. That makes the flag observable from a source independent
of the dispatch response (which ``_warn_on_ungated_implement`` inspects) without
introducing a new query path into the tick loop.
"""

from __future__ import annotations

# Wire token — also restated in
# ``scripts/model_manager/ui/controller/charter_runner/gate_bypass_detect.py``,
# which is the consumer in a different service domain.
IMPLEMENT_GATE_BYPASS_DEVIATION = "gate:implement_source_ref_unresolved"


def implement_gate_bypass_deviations(
    *,
    contract: str,
    work_item_ref: str | None,
) -> tuple[str, ...]:
    """Deviation tokens for an implement closeout whose readiness gate no-opped.

    Mirrors the admission-time predicate at
    ``routes/cursor_sdk.py`` (``contract == "implement"`` with no resolvable
    ``source_ref``). Non-implement contracts never carry the token: they have no
    readiness gate to bypass.
    """
    if contract.strip().lower() != "implement":
        return ()
    if work_item_ref:
        return ()
    return (IMPLEMENT_GATE_BYPASS_DEVIATION,)
