"""Apply every review suggestion under — never park it for the operator.

Operator 2026-09-21 (agent-bus:11960): always apply all suggestions from
reviews. The ticker fires one apply hop; do not come up or page. A frozen
``ready=false`` or an attended check-in ``ide:`` seat does not hold the apply.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from bus_watch.fable_lock import current_night_id
from bus_watch.loop_tape import loop_tape_thread

REVIEW_APPLY = "review_apply"
_KEEP = 200

_SUGGESTIONS_RE = re.compile(r"\bSUGGESTIONS\b", re.I)
_REVIEW_MARK_RE = re.compile(r"\b(?:G6|PRE-LAND REVIEW|TYPE:\s*REVIEW)\b", re.I)
_SHOULD_FIX_RE = re.compile(r"SHOULD-FIX|##\s+Nits\b", re.I)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def review_apply_key(root_id: str, turn_number: object) -> str:
    """Stable latch / work identity for one review turn."""
    return f"{root_id}#{turn_number}"


def review_apply_fired(state: dict[str, Any], key: str) -> bool:
    """True once the ticker has fired apply for this review turn."""
    return key in (state.get("review_apply_fired") or {})


def mark_review_apply_fired(
    state: dict[str, Any], key: str, *, at: str | None = None
) -> None:
    """Latch apply-fired so the same review cannot remint."""
    if not key:
        return
    store = dict(state.get("review_apply_fired") or {})
    store[key] = at or _utcnow()
    state["review_apply_fired"] = dict(list(store.items())[-_KEEP:])


def is_review_owing_apply(turn: dict[str, Any]) -> bool:
    """Subject/body (digest compact, 400-char body) names unapplied suggestions."""
    subject = str(turn.get("subject") or "")
    body = str(turn.get("body") or "")
    blob = f"{subject}\n{body}"
    if _SUGGESTIONS_RE.search(blob):
        return True
    return bool(_REVIEW_MARK_RE.search(subject) and _SHOULD_FIX_RE.search(blob))


def _root_turns(digest: dict[str, Any]) -> list[dict[str, Any]]:
    root = digest.get("root") or {}
    raw = root.get("unread_turns")
    if isinstance(raw, list) and raw:
        turns = [t for t in raw if isinstance(t, dict)]
        if turns:
            return turns
    recent = root.get("recent_turns") or []
    return [t for t in recent if isinstance(t, dict)]


def owing_review_apply(
    digest: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any] | None:
    """Newest root review that still owes an apply hop."""
    root_id = str((digest.get("root") or {}).get("id") or "").strip()
    newest: dict[str, Any] | None = None
    newest_n = -1
    for turn in _root_turns(digest):
        if not is_review_owing_apply(turn):
            continue
        num = int(turn.get("turn_number") or 0)
        key = review_apply_key(root_id, num)
        if review_apply_fired(state, key):
            continue
        if num >= newest_n:
            newest = {
                "root_id": root_id,
                "turn_number": num,
                "subject": str(turn.get("subject") or ""),
                "body": str(turn.get("body") or ""),
                "key": key,
                "thread": str(turn.get("thread") or root_id),
            }
            newest_n = num
    return newest


def ready_review_apply_attention(
    digest: dict[str, Any], state: dict[str, Any]
) -> list[dict[str, Any]]:
    """Promote an owing review into digest attention (fail-closed wake)."""
    review = owing_review_apply(digest, state)
    if not review:
        return []
    return [
        {
            "kind": REVIEW_APPLY,
            "id": f"review-apply:{review['key']}",
            "thread": review["thread"],
            "turn_number": review["turn_number"],
            "subject": review["subject"][:120],
        }
    ]


def maybe_review_apply_body(
    root_id: str,
    policy: dict[str, Any],
    digest: dict[str, Any] | None,
    state: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Apply body when a review still owes a hop; None otherwise."""
    if not isinstance(digest, dict) or not isinstance(state, dict):
        return None
    review = owing_review_apply(digest, state)
    if not review:
        return None
    return build_review_apply_body(root_id, policy, review)


def build_review_apply_body(
    root_id: str,
    policy: dict[str, Any],
    review: dict[str, Any],
) -> dict[str, Any]:
    """Ticker-fired apply: one lane-B hop for every suggestion on the review.

    ``contract=implement`` plus ``prompt`` is rejected on the wire
    (``prompt is not supported with contract='implement'``). Rematerializing
    the review turn as ``source_ref=agent-bus:{root}#turn-N`` would hand the
    worker a CLEAR pre-land review and let it skip suggestions. House generate
    (``contract=none``) plus an explicit apply-all ``message`` is the admit
    path the ticker already uses for sit hops.
    """
    max_hop = int(policy.get("max_hop_minutes") or 60)
    turn = review["turn_number"]
    key = review["key"]
    night = current_night_id()
    work_key = f"agent-bus:{root_id}:review-apply:{turn}:night-{night}"
    tape = loop_tape_thread(root_id, policy)
    message = (
        "REVIEW APPLY (mandatory, under): Apply ALL suggestions from this review "
        "on the house thread. Do not skip SHOULD-FIX as a follow-up. Do not page. "
        "Do not come up. Land on green when AC met — silence is not HOLD_MERGE.\n\n"
        f"Read agent_bus_read(get, thread={root_id}, turn_number={turn}) and any "
        "sidecar URI in that body. Apply every SHOULD-FIX, nit, unused-code leftover, "
        "and adjacent-unfiled item named there.\n\n"
        f"Subject: {review.get('subject') or ''}\n"
    )
    body: dict[str, Any] = {
        "op": "generate",
        "seat": "cursor-sdk",
        "contract": "none",
        "lane": "B",
        "work_key": work_key,
        "message": message,
        "dispatch_thread_id": tape,
        "timeout_seconds": max_hop * 60 + 1800,
        "caller_agent": "liaison-ticker",
        "_review_apply": True,
        "_review_key": key,
    }
    if model := policy.get("successor_model"):
        body["model"] = model
    return body


__all__ = [
    "REVIEW_APPLY",
    "build_review_apply_body",
    "is_review_owing_apply",
    "mark_review_apply_fired",
    "maybe_review_apply_body",
    "owing_review_apply",
    "ready_review_apply_attention",
    "review_apply_fired",
    "review_apply_key",
]
