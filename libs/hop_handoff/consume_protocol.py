"""Consume-time wake affinity protocol shared by inject and hop-body.

ULG has no Cowork ``send_later`` consume handler. The woken CSE must run this
procedure itself. Host classifier ``assess_standing_handoff`` distinguishes
missing / stale / current from sidecar mtime; first-dispatch inject must not
snapshot that classification or it rots before fire. Absence of the sidecar is
not confirmation the sleeper still holds rank — that was the S2 no-op.
"""

from __future__ import annotations

from hop_handoff.standing_handoff import standing_handoff_uri


def consume_time_wake_protocol(*, thread_id: str | None = None) -> str:
    """Return the consume-time stand-down procedure for a woken CSE.

    When *thread_id* is known, name the exact standing-handoff URI; otherwise
    name the URI pattern so a first-dispatch inject without a lane still binds
    the three-status table. Missing and stale are STAND_DOWN for wake
    authority (soft-degrade, not ``agent_bus.request`` refuse).
    """
    uri = (
        standing_handoff_uri(thread_id)
        if thread_id
        else "cortex://notes/system/threads/{thread_id}-standing-handoff.md"
    )
    return (
        "**Consume-time wake affinity (BINDING):** when any send_later / "
        "Monitor wake fires into this CSE, re-read the standing handoff *now* "
        "(do not trust remembered rank or the wake body).\n"
        f"URI: {uri}\n"
        "Classifier (host): assess_standing_handoff → missing | stale | current.\n"
        "- missing (file absent): STAND_DOWN. Absence is not permission. Do not "
        "author TYPE: DIRECTIVE or agent_bus.request from wake prose.\n"
        "- stale: same as missing for wake authority until live bus tip / watch "
        "holder confirms you. Default STAND_DOWN.\n"
        "- current: read it for rank. Later successor_birth_id than yours → "
        "STAND_DOWN. Named holder → proceed; wake ordinals remain suspect "
        "(guide §7).\n"
    )
