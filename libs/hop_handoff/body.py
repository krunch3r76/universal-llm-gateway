"""Single author of the structural ``TYPE: CONTINUITY_HANDOFF`` hop body.

Cadence and the request-surface ``hop`` verb both call
:func:`build_continuity_handoff_body` so identity binds, the standing-handoff
``missing`` branch, and the keep-alive/wake doctrine stay one source. Fresh-run
invariant: re-author at each fire with current freshness — do not reuse a
previous hop's body. I6 uniqueness is procedural and depends on that fresh-run
rule together with claim-once (one commission per hop job).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from hop_handoff.standing_handoff import StandingHandoffFreshness

_DEFAULT_YOU_ARE = (
    "this successor CSE — session address (chat_url) is descriptive; "
    "selection key is successor_birth_id on this body"
)
_BIRTH_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_BIRTH_ID_PREFIX = "successor_birth_id:"


def mint_successor_birth_id() -> str:
    """Return a collision-resistant per-birth key (uuid4 hex, not a 1s clock)."""
    return uuid.uuid4().hex


def parse_successor_birth_id(body: str) -> str | None:
    """Return the ``successor_birth_id`` header value from a hop or stamp body."""
    for line in body.splitlines():
        if line.startswith(_BIRTH_ID_PREFIX):
            value = line.split(":", 1)[1].strip()
            return value or None
    return None


def is_successor_birth_id(value: str | None) -> bool:
    """True when *value* is a 32-char lowercase hex birth id."""
    return bool(value) and _BIRTH_ID_RE.fullmatch(value or "") is not None


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
    successor_birth_id: str | None = None,
) -> str:
    """Author a first-line ``TYPE: CONTINUITY_HANDOFF`` body for one hop fire.

    ``source`` distinguishes cadence (``cursor-auto-hop-cadence``) from the
    request-surface verb (``agent-bus-hop-verb``). ``trigger`` is the reason
    line. When ``handoff.status`` is ``missing`` the successor is told to
    author the S7 state file; otherwise it is told to read the URI first.

    ``successor_birth_id`` is the I6 selection key: originated here at build,
    always emitted on this structural body (never the L2 orientation block),
    echoed onto the ``TYPE: SEAT_REGISTRATION`` stamp. Omit to mint; pass a
    32-char hex only in tests that must pin equality across two authors.
    """
    resolved_you = (you_are or "").strip() or _DEFAULT_YOU_ARE
    birth_id = (successor_birth_id or "").strip() or mint_successor_birth_id()
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
    lines.append(f"{_BIRTH_ID_PREFIX} {birth_id}")
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
            "You are the operator CSE on parent_thread above. Identity key is",
            "successor_birth_id (this structural header). Match TYPE:",
            "SEAT_REGISTRATION by equality on that field. chat_url on the stamp",
            "is descriptive. Extras on this lane are predecessors, not peers.",
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
            "Predecessor Cowork send_later is unobservable to cursor-auto",
            "(predecessor_wake_status=unobservable). wake-guide §7: a one-shot",
            "armed before this hop may still fire into the retired seat; that",
            "seat must read the lane and stand down — do not act on remembered rank.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_seat_registration_stamp(
    *,
    successor_birth_id: str,
    registration_id: str,
    execution_id: str,
    parent_thread: str,
    chat_url: str | None = None,
    observed_at: str | None = None,
) -> str:
    """Author the append-only ``TYPE: SEAT_REGISTRATION`` projection turn.

    Echoes the hop-body ``successor_birth_id`` so a successor holding only
    first-turn tokens can equality-match this stamp. ``chat_url`` is
    descriptive (fleet reconstruction), not the I6 key.
    """
    observed = observed_at or datetime.now(UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    url = (chat_url or "").strip()
    lines = [
        "TYPE: SEAT_REGISTRATION",
        f"{_BIRTH_ID_PREFIX} {successor_birth_id}",
        f"registration_id: {registration_id}",
        f"execution_id: {execution_id}",
        f"chat_url: {url}" if url else "chat_url:",
        f"parent_thread: {parent_thread}",
        f"observed_at: {observed}",
        (
            "source: cursor-auto — projection of cdp-registry active store; "
            "recovery: /v1/project-ask/active-work"
        ),
    ]
    return "\n".join(lines) + "\n"
