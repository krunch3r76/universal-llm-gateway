"""Auto-runnable residual gates for operator-proxy mission closeouts.

Sibling of :mod:`claude_bundles.mission_close_wake`, which establishes that a
mission may not close over outstanding work without a named wake token. This
module tightens the same section for the **auto-runnable** class — plugin
install, Customize sync, service restart/propagate, ``wait_healthy``, and the
continuity hop — where a wake token alone proved insufficient.

Two failure shapes, both observed on agent-bus:6655 ep27:

``collector:`` is a label, not a dispatch
    A closeout carried ``plugin install — collector: cursor-auto`` and
    ``mcp restart — collector: cursor-auto · followup: contract:propagate …``
    with no ``agent_bus.request`` ever fired. Both satisfied the wake-token
    regex and both waited on the human, who is the one seat the residual gate
    exists to keep out of the loop. There is no collector runner to pick them
    up — ``scripts/opus-summons-watchdog.py`` owns the *summons*, not
    residual collection — and `cdp-operator-proxy` § Wake path forbids
    inventing a babysitter. So an auto-runnable residual must cite the
    commission it already fired.

``operator_gate:`` on work Auto can reach
    `cdp-operator-proxy` invariant 24 puts plugin install, Customize sync, and
    service restarts inside the operator seat's own authority. Parking one on
    the human contradicts the invariant. Reload Window stays legal — it is the
    single standing exception, and it refreshes the attended IDE picker rather
    than gating propagation to dispatch seats (which copy
    ``~/.cursor/plugins/`` per dispatch in ``cursor_home.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_AUTO_RUNNABLE_RE = re.compile(
    r"""(?ix)
    (
        install[-_\s]ecosystem[-_\s]plugin
      | plugin \s+ (install|sync|refresh)
      | install \s+ (the \s+)? (ecosystem \s+)? plugin
      | customize \s+ (skill \s+)? (sync|upload)
      | claude-ai-sync
      | sync_restart
      | wait_healthy
      | contract \s* :? \s* propagate
      | \b propagate \b
      | restart \s+ (the \s+)? (mcp|giw|git_integration_worker|stargate|gateway|rag)
      | (mcp|giw|git_integration_worker|stargate|gateway|rag) \s+ restart
      | continuity \s+ (hop|window|request)
      | (next|fresh|new) \s+ (cdp \s+)? (operator \s+)? window
    )
    """
)

# Evidence that a commission was actually fired: a bus turn, a dispatch/request
# id, an act-receipt trigger, or a persisted intent. A bare lane number is
# deliberately NOT a match — naming the thread you would post to is not proof
# that you posted (ep27 wrote "on THIS lane (6655)" having fired nothing).
_COMMISSION_REF_RE = re.compile(
    r"""(?ix)
    (
        agent-bus \s* [:#] \s* \d+
      | \b thread \s* \#? \s* \d{3,}
      | \# \d{2,}
      | \b t \d{3,} \b
      | \b (auto|cdp|dispatch|exec|intent) - [0-9a-f]{6,}
      | \b (request_id|dispatch_id|execution_id|trigger_id
           |restart_intent_id|intent_id|turn) \s* [:=]
      | \b [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12} \b
    )
    """
)

# An honest deferral excuses the missing commission reference; it does not
# excuse the wake token, which ``mission_close_wake`` still requires.
_DEFERRED_TOKEN_RE = re.compile(r"(?i)\bdeferred(_reason)?\s*:")
_CHARTER_TOKEN_RE = re.compile(r"(?i)\bcharter_enrolled\s*:")
_OPERATOR_GATE_RE = re.compile(r"(?i)\boperator_gate\s*:")
# The one standing human exception (invariant 24) — a bare Reload Window
# bullet keeps its operator_gate.
_RELOAD_WINDOW_RE = re.compile(r"(?ix) reload \s* window | relaunch \s+ (the \s+)? ide")

UNCOMMISSIONED_FIX_HINT = (
    "Auto-runnable residuals (plugin install, Customize sync, "
    "propagate/sync_restart, wait_healthy, continuity hop) must be "
    "**fired before the closeout posts**, then cited: `collector:` names who, "
    "not that you asked. Add the commission reference to the bullet — the "
    "`agent_bus.request` turn (`6655#1521`, `thread 6655`, `t1521`), a "
    "`dispatch_id:`/`request_id:`/`restart_intent_id:`, or an `auto-…` id. "
    "If it genuinely was not fired, keep the wake token and add "
    "`deferred: <reason>` beside it — an honest deferral still needs a wake "
    "path, because nothing sweeps `collector:` labels (the summons watchdog "
    "owns successor launches only)."
)

OPERATOR_GATE_FIX_HINT = (
    "`operator_gate:` is for work no commissioned seat can perform — "
    "credentials, irreversible human acts, product clicks, Reload Window. "
    "Plugin install, Customize sync, and service restart/propagate are inside "
    "this seat's own authority (`cdp-operator-proxy` invariant 24): commission "
    "cursor-auto and cite the turn. Note that Reload Window refreshes the "
    "attended IDE picker only — cursor-sdk dispatch homes copy "
    "`~/.cursor/plugins/` per dispatch, so it never gates propagation to "
    "dispatch seats."
)


@dataclass(frozen=True, slots=True)
class AutoRunnableRefusal:
    """A refused residual bullet: why, which item, and how to fix it."""

    reason: str
    item: str
    fix_hint: str


def is_auto_runnable(item: str) -> bool:
    """True when a residual bullet names work cursor-auto can reach itself."""
    return bool(_AUTO_RUNNABLE_RE.search(item or ""))


def check_auto_runnable_items(items: list[str]) -> AutoRunnableRefusal | None:
    """Refuse auto-runnable residuals that are uncommissioned or human-parked.

    Args:
        items: Folded ``## Work beyond this close`` bullet contents.

    Returns:
        The first refusal found, or ``None`` when every auto-runnable bullet
        either cites a fired commission, names an honest ``deferred:`` reason,
        or rides a charter enrollment.
    """
    for item in items:
        if not is_auto_runnable(item):
            continue
        if _OPERATOR_GATE_RE.search(item) and not _RELOAD_WINDOW_RE.search(item):
            return AutoRunnableRefusal(
                reason="mission_close_operator_gate_for_auto_runnable",
                item=item,
                fix_hint=OPERATOR_GATE_FIX_HINT,
            )
        if _CHARTER_TOKEN_RE.search(item) or _DEFERRED_TOKEN_RE.search(item):
            continue
        if not _COMMISSION_REF_RE.search(item):
            return AutoRunnableRefusal(
                reason="mission_close_uncommissioned_auto_runnable",
                item=item,
                fix_hint=UNCOMMISSIONED_FIX_HINT,
            )
    return None


__all__ = [
    "OPERATOR_GATE_FIX_HINT",
    "UNCOMMISSIONED_FIX_HINT",
    "AutoRunnableRefusal",
    "check_auto_runnable_items",
    "is_auto_runnable",
]
