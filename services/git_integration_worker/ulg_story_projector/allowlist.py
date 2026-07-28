"""Hardcoded lifecycle signal allowlist and sentence-class mapping (spec Bind 3)."""

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
}


def mapping_for(signal: str) -> SignalMapping | None:
    return SIGNAL_MAPPINGS.get(signal)
