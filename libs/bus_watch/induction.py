"""Wake induction — the planted address a woken liaison reads first.

Operator bind (10479 #82 · #118 · #120): seats gloss tool output and drop
standing instructions across turns; what sticks is a short user-turn that
plants an *address* (turn number, watcher label, skill name), frames the wake
as an episode, and front-loads the one step — no costume, no second copy of
the skill, no accumulating preamble. The IDE wake today is Cursor's
content-free nudge ("Briefly inform the user about the task result…"), after
which a below-Opus seat re-reads the whole skill stack to re-orient (Grok 4.6,
10479 hops 1–3 on 2026-09-13: liaison SKILL.md read 4–5× per tab).

This module only *builds* the text. The digest JSON and the DIGEST bus turn
carry it now; a keystroke follow-up paste into the live IDE tab and
``cse_session(op=followup)`` for a claude.ai liaison are the same string.
"""

from __future__ import annotations

from typing import Any

INDUCTION_CAP = 700
_EVENT_ITEMS = 3
_SUBJECT_CHARS = 56
_ONE_STEP = (
    "One step: harvest → fold → decide; quote evidence; end turn. "
    "Judgment bind ⇒ cdp/opus-5 first when below Opus."
)
_QUIET_STEP = "Quiet tick: one line, end turn. Do not fetch the bus to double-check."


def _events(digest: dict[str, Any]) -> list[str]:
    """Designed stops first — they must survive the item cap; lanes after."""
    items: list[str] = []
    budget = digest.get("budget") or {}
    if budget.get("stop_class"):
        used = int(budget.get("used_tokens") or 0)
        limit = max(int(budget.get("window_limit_tokens") or 1), 1)
        items.append(
            f"{budget['stop_class']} {round(100 * used / limit)}% "
            f"({budget.get('source')}) → CHECKPOINT, then hop or PARK"
        )
    if digest.get("checkpoint_due"):
        items.append("CHECKPOINT due (segment CP, supersedes tip)")
    for watcher in digest.get("watchers_complete_unrelayed") or []:
        label = watcher.get("label") or watcher.get("file")
        items.append(f"watcher {label} complete → harvest, --mark-relayed")
    for lane in digest.get("attention") or []:
        if lane.get("kind") == "budget_estimate" or "id" not in lane:
            continue
        subject = str(lane.get("last_subject") or "")[:_SUBJECT_CHARS]
        items.append(f"{lane['id']} unread={lane.get('unread')} «{subject}»")
    return items


def _listed(policy: dict[str, Any], key: str) -> list[str]:
    raw = policy.get(key)
    if isinstance(raw, str):
        raw = [raw]
    return [str(x).strip() for x in (raw or []) if str(x).strip()]


def build_wake_induction(digest: dict[str, Any], *, cap: int = INDUCTION_CAP) -> str:
    """Render the wake as a short planted-address block (≤ ``cap`` bytes).

    M1 plant addresses, not assertions · M2 episodic frame ("Event:") · M4 no
    costume · M5 front-load, never accumulate. ``policy.induction_loaded`` lists
    what the seat already holds and must not re-read; ``policy.induction_binds``
    carries standing operator binds (e.g. "hopper paused (10479#210)").
    """
    root = digest.get("root") or {}
    policy = digest.get("policy") or {}
    root_id = root.get("id")
    # ``policy.now_row`` is the seat's own bind (``liaison-tick.py --set now_row=…``
    # after each Decide step); ``summary_row`` is the older state field.
    now_row = str(digest.get("summary_row") or policy.get("now_row") or "").strip()
    events = _events(digest)
    head = f"WAKE {root_id} · turns={root.get('turns')} · {digest.get('ts')}"
    if not events and not digest.get("changed_since_last_tick"):
        head = head.replace("WAKE", "QUIET", 1)
    lines = [head]
    for item in events[:_EVENT_ITEMS]:
        lines.append(f"Event: {item}")
    if len(events) > _EVENT_ITEMS:
        lines.append(f"Event: +{len(events) - _EVENT_ITEMS} more in the digest")
    lines.append(
        f"NOW: {now_row}"
        if now_row
        else "NOW: (empty — pull the next objective per liaison skill § Objectives; "
        "empty NOW is not a stop)"
    )
    loaded = ["liaison skill", *_listed(policy, "induction_loaded")]
    lines.append("Loaded already (do not re-read): " + " · ".join(loaded))
    standing = [
        f"register={digest.get('register')}",
        *_listed(policy, "induction_binds"),
    ]
    lines.append("Standing: " + " · ".join(standing))
    lines.append(_ONE_STEP if events else _QUIET_STEP)
    text = "\n".join(lines)
    while len(text.encode("utf-8")) > cap and len(lines) > 3:
        # Drop the lowest-value line first: extra events, then standing binds.
        drop_at = next(
            (i for i, ln in enumerate(lines) if ln.startswith("Event: +")), None
        )
        if drop_at is None:
            drop_at = next(
                (
                    i
                    for i in range(len(lines) - 2, 0, -1)
                    if lines[i].startswith("Event:")
                ),
                len(lines) - 2,
            )
        lines.pop(drop_at)
        text = "\n".join(lines)
    return text


__all__ = ["INDUCTION_CAP", "build_wake_induction"]
