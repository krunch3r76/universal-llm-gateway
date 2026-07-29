"""Hardcoded lifecycle signal allowlist and sentence-class mapping (spec Bind 3).

Bind 3's ~12 is a **cap**, not a census: nine existing ``frontier.sdk.*`` members
plus four ``giw.trigger.*`` (``fired``, ``reconciled``, ``fire_failed``,
``reclaimed``) — thirteen tolerated. Six ``giw.trigger.*`` families stay **out**:
``.scheduled``, ``.claimed``, ``.cancelled``, ``.predicate_*``, ``.expired``,
``.act_*``. ``.reclaimed`` is the pre-agreed shed candidate before anything
else is added (S-B14).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StoryClass = Literal["routine", "milestone", "attention"]

# Dropped from operator hypothesis: ``empty_directive_scope_waived`` — routine
# noise when the gate fires often during dogfood; Kaywan mutes chatty feeds.
SIGNAL_ALLOWLIST: tuple[str, ...] = (
    "frontier.sdk.closeout.relayed",
    "frontier.sdk.worker.dispatched",
    "frontier.sdk.worker.completed",
    "frontier.sdk.worker.failed",
    "frontier.sdk.worker.timeout",
    "frontier.sdk.worker.orphaned",
    "frontier.sdk.auto.auth_gate_blocked",
    "frontier.sdk.auto.empty_directive_scope_blocked",
    "frontier.sdk.auto.thread_status_refused",
    "giw.trigger.fired",
    "giw.trigger.reconciled",
    "giw.trigger.fire_failed",
    "giw.trigger.reclaimed",
)


@dataclass(frozen=True, slots=True)
class SignalMapping:
    signal: str
    story_class: StoryClass
    verb: str


SIGNAL_MAPPINGS: dict[str, SignalMapping] = {
    "frontier.sdk.closeout.relayed": SignalMapping(
        signal="frontier.sdk.closeout.relayed",
        story_class="milestone",
        verb="relayed closeout for",
    ),
    "frontier.sdk.worker.dispatched": SignalMapping(
        signal="frontier.sdk.worker.dispatched",
        story_class="routine",
        verb="started work on",
    ),
    "frontier.sdk.worker.completed": SignalMapping(
        signal="frontier.sdk.worker.completed",
        story_class="milestone",
        verb="finished",
    ),
    "frontier.sdk.worker.failed": SignalMapping(
        signal="frontier.sdk.worker.failed",
        story_class="attention",
        verb="failed on",
    ),
    "frontier.sdk.worker.timeout": SignalMapping(
        signal="frontier.sdk.worker.timeout",
        story_class="attention",
        verb="timed out on",
    ),
    "frontier.sdk.worker.orphaned": SignalMapping(
        signal="frontier.sdk.worker.orphaned",
        story_class="attention",
        verb="orphaned while running",
    ),
    "frontier.sdk.auto.auth_gate_blocked": SignalMapping(
        signal="frontier.sdk.auto.auth_gate_blocked",
        story_class="attention",
        verb="refused dispatch (auth gate exhausted) on thread",
    ),
    "frontier.sdk.auto.empty_directive_scope_blocked": SignalMapping(
        signal="frontier.sdk.auto.empty_directive_scope_blocked",
        story_class="attention",
        verb="refused dispatch (empty directive scope) on thread",
    ),
    "frontier.sdk.auto.thread_status_refused": SignalMapping(
        signal="frontier.sdk.auto.thread_status_refused",
        story_class="attention",
        verb="refused dispatch (thread status) on thread",
    ),
    "giw.trigger.fired": SignalMapping(
        signal="giw.trigger.fired",
        story_class="milestone",
        verb="fired scheduled trigger for",
    ),
    "giw.trigger.reconciled": SignalMapping(
        signal="giw.trigger.reconciled",
        story_class="milestone",
        verb="reconciled trigger episode for",
    ),
    "giw.trigger.fire_failed": SignalMapping(
        signal="giw.trigger.fire_failed",
        story_class="attention",
        verb="failed to fire trigger for",
    ),
    "giw.trigger.reclaimed": SignalMapping(
        signal="giw.trigger.reclaimed",
        story_class="attention",
        verb="reclaimed stale firing for",
    ),
}


def mapping_for(signal: str) -> SignalMapping | None:
    """Return allowlist mapping for ``signal``, or None when not projectable."""
    return SIGNAL_MAPPINGS.get(signal)
