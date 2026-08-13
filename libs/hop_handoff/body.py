"""Single author of the structural ``TYPE: CONTINUITY_HANDOFF`` hop body.

Cadence and the request-surface ``hop`` verb both call
:func:`build_continuity_handoff_body` so identity binds, the standing-handoff
``missing`` branch, and the keep-alive/wake doctrine stay one source. Fresh-run
invariant: re-author at each fire with current freshness — do not reuse a
previous hop's body.
"""

from __future__ import annotations

from hop_handoff.standing_handoff import StandingHandoffFreshness

_DEFAULT_YOU_ARE = (
    "this successor CSE — identity is the chat_url of the Cowork session you are in"
)


def build_continuity_handoff_body(
    *,
    thread_id: str,
    trigger: str,
    source: str,
    handoff: StandingHandoffFreshness,
    you_are: str | None = None,
    age_s: float | None = None,
    threshold_s: float | None = None,
    superseded_registration_id: str | None = None,
) -> str:
    """Author a first-line ``TYPE: CONTINUITY_HANDOFF`` body for one hop fire.

    ``source`` distinguishes cadence (``cursor-auto-hop-cadence``) from the
    request-surface verb (``agent-bus-hop-verb``). ``trigger`` is the reason
    line. When ``handoff.status`` is ``missing`` the successor is told to
    author the S7 state file; otherwise it is told to read the URI first.
    """
    resolved_you = (you_are or "").strip() or _DEFAULT_YOU_ARE
    lines = [
        "TYPE: CONTINUITY_HANDOFF",
        "contract: light-bounded",
        f"source: {source}",
        f"trigger: {trigger}",
        f"thread_id: {thread_id}",
        f"you_are: {resolved_you}",
        f"parent_thread: {thread_id}",
        f"cse_age_s: {age_s:.1f}" if age_s is not None else "cse_age_s: unknown",
        (
            f"threshold_s: {threshold_s:.1f}"
            if threshold_s is not None
            else "threshold_s: unknown"
        ),
        f"standing_handoff: {handoff.uri}",
        f"standing_handoff_freshness: {handoff.status}",
    ]
    if handoff.age_s is not None:
        lines.append(f"standing_handoff_age_s: {handoff.age_s:.1f}")
    if superseded_registration_id:
        lines.append(f"superseded_registration_id: {superseded_registration_id}")
    if handoff.status == "missing":
        resume_read: tuple[str, ...] = (
            "The S7 standing-handoff state file is absent.",
            "Lane-tip reconstruction is degraded, not equivalent.",
            "Author the standing handoff before you leave.",
        )
    else:
        resume_read = (
            "Read the standing handoff URI above before trusting any wake prose.",
        )
    lines.extend(
        [
            "",
            "Resume as operator-proxy on this private lane.",
            *resume_read,
            "This is a CONTINUITY HOP (seat refresh) — do NOT emit MISSION_CLOSEOUT.",
            "You are the operator CSE on parent_thread above. Identity is chat_url",
            "(you_are). Extras on this lane are predecessors, not peers.",
            "Never touch operator CSEs on other lanes.",
            "Arc continues; predecessor wakes must be torn down only after this",
            "successor launch is confirmed.",
            "",
            "KEEP-ALIVE / wake cycle (BINDING — 6661 sole-wake · suspended pattern):",
            "Do NOT arm Monitor loops. Do NOT re-arm send_later for durable wake.",
            "Wake authority is the mission PRIMARY orchestrator only (monitor 6661 ↔",
            "mission root) — hop successors are subordinates, not peer wake servers.",
            "If you inherit a predecessor Monitor, TaskStop it after successor admit;",
            "delete only trigger_ids this seat recorded (never class-delete).",
            "CDP one-off work from the mission runner remains fine; keep-alive is not",
            "ready for fleet hops under the current pattern.",
            "(cursor-auto cannot reach Cowork-internal timers — seat duty.)",
        ]
    )
    return "\n".join(lines) + "\n"
