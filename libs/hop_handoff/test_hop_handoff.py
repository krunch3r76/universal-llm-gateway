"""Shared hop-handoff body author.

Covers cadence byte-parity against the frozen pre-extraction body, the
standing-handoff missing vs current branch, and the verb-source first-line
TYPE token the GIW classifier consumes.
"""

from __future__ import annotations

import pytest

from hop_handoff import StandingHandoffFreshness, build_continuity_handoff_body

pytestmark = pytest.mark.offline

_THREAD = "7182"
_URI = f"cortex://notes/system/threads/{_THREAD}-standing-handoff.md"

_CADENCE_CURRENT_EXPECTED = """\
TYPE: CONTINUITY_HANDOFF
contract: light-bounded
source: cursor-auto-hop-cadence
trigger: watch_seated_at
thread_id: 7182
you_are: this successor CSE — identity is the chat_url of the Cowork session you are in
parent_thread: 7182
cse_age_s: 2000.0
threshold_s: 1500.0
standing_handoff: cortex://notes/system/threads/7182-standing-handoff.md
standing_handoff_freshness: current
standing_handoff_age_s: 10.0

Resume as operator-proxy on this private lane.
Read the standing handoff URI above before trusting any wake prose.
This is a CONTINUITY HOP (seat refresh) — do NOT emit MISSION_CLOSEOUT.
You are the operator CSE on parent_thread above. Identity is chat_url
(you_are). Extras on this lane are predecessors, not peers.
Never touch operator CSEs on other lanes.
Arc continues; predecessor wakes must be torn down only after this
successor launch is confirmed.

KEEP-ALIVE / wake cycle (BINDING — 6661 sole-wake · suspended pattern):
Do NOT arm Monitor loops. Do NOT re-arm send_later for durable wake.
Wake authority is the mission PRIMARY orchestrator only (monitor 6661 ↔
mission root) — hop successors are subordinates, not peer wake servers.
If you inherit a predecessor Monitor, TaskStop it after successor admit;
delete only trigger_ids this seat recorded (never class-delete).
CDP one-off work from the mission runner remains fine; keep-alive is not
ready for fleet hops under the current pattern.
(cursor-auto cannot reach Cowork-internal timers — seat duty.)
"""


def _handoff(status: str) -> StandingHandoffFreshness:
    return StandingHandoffFreshness(
        status=status,
        uri=_URI,
        mtime_epoch=None if status == "missing" else 1.0,
        age_s=None if status == "missing" else 10.0,
    )


def test_shared_author_matches_frozen_cadence_body() -> None:
    """Byte-identical to the pre-extraction cadence author for a fixture fire."""
    body = build_continuity_handoff_body(
        thread_id=_THREAD,
        trigger="watch_seated_at",
        source="cursor-auto-hop-cadence",
        handoff=_handoff("current"),
        age_s=2000.0,
        threshold_s=1500.0,
    )
    assert body == _CADENCE_CURRENT_EXPECTED


@pytest.mark.parametrize("status", ["current", "stale", "missing"])
def test_missing_vs_current_handoff_branch(status: str) -> None:
    body = build_continuity_handoff_body(
        thread_id=_THREAD,
        trigger="watch_seated_at",
        source="cursor-auto-hop-cadence",
        handoff=_handoff(status),
        age_s=2000.0,
        threshold_s=1500.0,
    )
    assert f"standing_handoff_freshness: {status}" in body
    if status == "missing":
        assert "The S7 standing-handoff state file is absent." in body
        assert (
            "Read the standing handoff URI above before trusting any wake prose."
            not in body
        )
    else:
        assert (
            "Read the standing handoff URI above before trusting any wake prose."
            in body
        )
        assert "The S7 standing-handoff state file is absent." not in body


def test_verb_source_body_starts_with_type_token() -> None:
    """Verb-authored body still has TYPE: CONTINUITY_HANDOFF as first nonblank."""
    body = build_continuity_handoff_body(
        thread_id=_THREAD,
        trigger="mcp-restart-healthy",
        source="agent-bus-hop-verb",
        handoff=_handoff("missing"),
    )
    first = next(line for line in body.splitlines() if line.strip())
    assert first == "TYPE: CONTINUITY_HANDOFF"
    assert "source: agent-bus-hop-verb" in body
    assert "trigger: mcp-restart-healthy" in body
