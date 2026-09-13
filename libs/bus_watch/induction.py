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
_UNDER_STEP = (
    "One step: CHECKPOINT (residue ≤ 800), run the go-under command above, "
    "paste its UNDER line, end turn. PARK is not a step while NOW or unread remain."
)
_QUIET_STEP = "Quiet tick: one line, end turn. Do not fetch the bus to double-check."


def go_under_command(root_id: Any, transcript_id: Any) -> str:
    """The one verb an attended tab runs to hand the house to the ticker."""
    holder = f" --holder ide:{transcript_id}" if transcript_id else ""
    return f"liaison-tick.py --root {root_id} --go-under{holder}"


def _tab_at_budget(digest: dict[str, Any]) -> bool:
    budget = digest.get("budget") or {}
    return bool(budget.get("stop_class")) and budget.get("source") == "ide.transcript"


def _events(digest: dict[str, Any]) -> list[str]:
    """Designed stops first — they must survive the item cap; lanes after."""
    items: list[str] = []
    budget = digest.get("budget") or {}
    if budget.get("stop_class"):
        used = int(budget.get("used_tokens") or 0)
        limit = max(int(budget.get("window_limit_tokens") or 1), 1)
        # The address is the command, not the verb's name: on 10534 the tab
        # wrote "restore cursor-sdk successor hop" into its CP and parked.
        step = (
            "→ CHECKPOINT, then "
            + go_under_command(
                (digest.get("root") or {}).get("id"), budget.get("transcript_id")
            )
            if _tab_at_budget(digest)
            else "→ CHECKPOINT, release the seat; the ticker spawns the successor"
        )
        items.append(
            f"{budget['stop_class']} {round(100 * used / limit)}% "
            f"({budget.get('source')}) {step}"
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
    loaded = list(
        dict.fromkeys(["liaison skill", *_listed(policy, "induction_loaded")])
    )
    lines.append("Loaded already (do not re-read): " + " · ".join(loaded))
    standing = [
        f"register={digest.get('register')}",
        *_listed(policy, "induction_binds"),
    ]
    lines.append("Standing: " + " · ".join(standing))
    if _tab_at_budget(digest):
        lines.append(_UNDER_STEP)
    else:
        lines.append(_ONE_STEP if events else _QUIET_STEP)
    return _fit(lines, cap)


def _fit(lines: list[str], cap: int) -> str:
    """Trim to ``cap`` bytes: the "+N more" line, then lane events, then the
    standing binds. The designed-stop line (``Event: CONTEXT_BUDGET …``) carries
    the go-under command and is never dropped — a one-step that says "the
    command above" with the command trimmed away is the 10534 park again."""
    text = "\n".join(lines)
    while len(text.encode("utf-8")) > cap and len(lines) > 3:
        drop_at = next(
            (i for i, ln in enumerate(lines) if ln.startswith("Event: +")), None
        )
        if drop_at is None:
            drop_at = next(
                (
                    i
                    for i in range(len(lines) - 2, 0, -1)
                    if lines[i].startswith("Event:")
                    and not lines[i].startswith("Event: CONTEXT_BUDGET")
                ),
                None,
            )
        if drop_at is not None:
            lines.pop(drop_at)
        else:
            standing_at = next(
                (i for i, ln in enumerate(lines) if ln.startswith("Standing: ")), None
            )
            if standing_at is None or " · " not in lines[standing_at]:
                break
            lines[standing_at] = lines[standing_at].split(" · ", 1)[0]
        text = "\n".join(lines)
    return text


__all__ = ["INDUCTION_CAP", "build_wake_induction", "go_under_command"]
