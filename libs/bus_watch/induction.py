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

import shlex
from typing import Any

from bus_watch.doorbell_skills import primary_liaison_slug
from bus_watch.friction_rows import event_line as _friction_event
from bus_watch.friction_rows import now_row as _friction_now

INDUCTION_CAP = 700
_EVENT_ITEMS = 3
_SUBJECT_CHARS = 56
_ONE_STEP = (
    "One step: harvest → fold → decide; quote evidence; end turn. "
    "Judgment bind ⇒ cdp/opus-5 first when below Opus."
)
# 10479 tab 12e32c8b (2026-09-13 06:56Z) wrote "Next: R12 recon" and STAYed at
# 0.7 % of its window: a NOW row is the leg to dispatch this tick, not a note.
_NOW_STEP = (
    "One step: harvest → fold → dispatch NOW's first leg (Explore recon · "
    "cdp/opus-5 first for any bind · cursor-sdk implement); STAY only when NOW is "
    "empty; end turn."
)
_HOP_STEP = (
    "One step: CHECKPOINT (residue ≤ 800), run the ide-hop command above, "
    "end turn. ¬ go-under mid-arc — go-under is overnight/departure only."
)
_QUIET_STEP = "Quiet tick: one line, end turn. Do not fetch the bus to double-check."


def go_under_command(root_id: Any, transcript_id: Any) -> str:
    """Hand the house to the gear-3 ticker (overnight/departure only — not CONTEXT_BUDGET)."""
    holder = f" --holder ide:{transcript_id}" if transcript_id else ""
    return f"liaison-tick.py --root {root_id} --go-under{holder}"


def ide_hop_command(root_id: Any, now_row: str) -> str:
    """Fresh-tab successor for attended CONTEXT_BUDGET with remaining work."""
    row = str(now_row or "").strip() or "harvest fold decide"
    return f"liaison-ide-hop.py --root {root_id} --row {shlex.quote(row)}"


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
        # wrote prose into its CP and parked; mid-arc attended budget ⇒ ide-hop.
        step = (
            "→ CHECKPOINT, then "
            + ide_hop_command(
                (digest.get("root") or {}).get("id"), _resolve_now_row(digest)[0]
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
    lanes: list[str] = []
    for lane in digest.get("attention") or []:
        if lane.get("kind") == "budget_estimate" or "id" not in lane:
            continue
        if lane.get("kind") == "friction":
            # The promoted friction is the score row this wake was spawned for;
            # it must survive the _EVENT_ITEMS cut ahead of unread lane noise.
            items.append(_friction_event(lane))
            continue
        subject = str(lane.get("last_subject") or "")[:_SUBJECT_CHARS]
        lanes.append(f"{lane['id']} unread={lane.get('unread')} «{subject}»")
    return items + lanes


def _listed(policy: dict[str, Any], key: str) -> list[str]:
    raw = policy.get(key)
    if isinstance(raw, str):
        raw = [raw]
    return [str(x).strip() for x in (raw or []) if str(x).strip()]


def _skill_slug(label: str) -> str:
    """Reduce an ``induction_loaded`` prose label to the bare activation slug.

    The field carries reader-facing labels ("liaison skill", "git-posture
    § Land"), but claude.ai mounts a body only on an exact ``Use the <slug>
    skill`` match — "Use the liaison skill skill" mounts nothing.
    """
    slug = str(label or "").split(" § ", 1)[0].strip().strip("`")
    if slug.lower().endswith(" skill"):
        slug = slug[: -len(" skill")].rstrip()
    return slug


def _use_skill_lines(loaded: list[str]) -> list[str]:
    slugs = [s for s in (_skill_slug(label) for label in loaded) if s]
    return [f"Use the {slug} skill" for slug in dict.fromkeys(slugs)]


def _resolve_now_row(digest: dict[str, Any]) -> tuple[str, str]:
    """Seat bind ≻ forcing friction ≻ legacy summary_row (11367#7 / a:34028).

    Friction outranks ``summary_row`` so a stale tick bind cannot mask an
    undispositioned row; ``summary_row`` prose is never echoed verbatim on
    the NOW line (see ``_format_now_line``).
    """
    policy = digest.get("policy") or {}
    policy_bind = str(policy.get("now_row") or "").strip()
    if policy_bind:
        return policy_bind, "policy"
    friction_now = _friction_now(digest)
    if friction_now:
        return friction_now, "friction"
    summary = str(digest.get("summary_row") or "").strip()
    if summary:
        return summary, "summary_row"
    return "", "empty"


def _format_now_line(digest: dict[str, Any], now_row: str, *, source: str) -> str:
    """Pointers not prose — tip turn + handoff, not stale summary_row (§7)."""
    if source == "friction":
        return str(now_row or "").strip()
    root = digest.get("root") or {}
    root_id = root.get("id")
    tip = root.get("turns")
    if source == "summary_row":
        if tip is not None and root_id:
            handoff = str(root.get("last_subject") or "").strip()
            if handoff:
                return f"tip #{tip} on agent-bus:{root_id} · «{handoff[:_SUBJECT_CHARS]}»"
            return f"tip #{tip} on agent-bus:{root_id}"
        return ""
    body = str(now_row or "").strip()
    if len(body) > 120:
        body = body[:117].rstrip() + "…"
    if tip is not None and root_id:
        return f"tip #{tip} on agent-bus:{root_id} · {body}"
    return body


def build_wake_induction(
    digest: dict[str, Any], *, cap: int = INDUCTION_CAP, surface: str = "ide"
) -> str:
    """Render the wake as a short planted-address block (≤ ``cap`` bytes).

    M1 plant addresses, not assertions · M2 episodic frame ("Event:") · M4 no
    costume · M5 front-load, never accumulate. ``policy.induction_loaded`` lists
    what the seat already holds and must not re-read; ``policy.induction_binds``
    carries standing operator binds (e.g. "hopper paused (10479#210)").

    ``surface='cse'`` emits one ``Use the <slug> skill`` line per loaded skill
    (claude.ai activation); ``surface='ide'`` keeps the legacy single
    ``Loaded already (do not re-read)`` line.
    """
    root = digest.get("root") or {}
    policy = digest.get("policy") or {}
    root_id = root.get("id")
    # ``policy.now_row`` is the live seat bind (``liaison-tick.py --set now_row=…``);
    # ``summary_row`` is legacy tick state and must not outrank a fresh bind (11367#7).
    friction_now = _friction_now(digest)
    now_row, now_source = _resolve_now_row(digest)
    events = _events(digest)
    forcing = bool(events or friction_now)
    head = f"WAKE {root_id} · turns={root.get('turns')} · {digest.get('ts')}"
    if not forcing and not digest.get("changed_since_last_tick"):
        head = head.replace("WAKE", "QUIET", 1)
    lines = [head]
    for item in events[:_EVENT_ITEMS]:
        lines.append(f"Event: {item}")
    if len(events) > _EVENT_ITEMS:
        lines.append(f"Event: +{len(events) - _EVENT_ITEMS} more in the digest")
    lines.append(
        f"NOW: {_format_now_line(digest, now_row, source=now_source)}"
        if now_row
        else "NOW: (empty — pull the next objective per liaison skill § Objectives; "
        "empty NOW is not a stop)"
    )
    liaison_label = f"{primary_liaison_slug(surface)} skill"
    loaded = list(dict.fromkeys([liaison_label, *_listed(policy, "induction_loaded")]))
    if surface == "cse":
        lines.extend(_use_skill_lines(loaded))
    else:
        lines.append("Loaded already (do not re-read): " + " · ".join(loaded))
    standing = [
        f"register={digest.get('register')}",
        *_listed(policy, "induction_binds"),
    ]
    lines.append("Standing: " + " · ".join(standing))
    if _tab_at_budget(digest):
        lines.append(_HOP_STEP)
    elif forcing and now_row:
        lines.append(_NOW_STEP)
    else:
        lines.append(_ONE_STEP if forcing else _QUIET_STEP)
    fit = _fit_cse if surface == "cse" else _fit
    return fit(lines, cap)


def _fit(lines: list[str], cap: int) -> str:
    """Trim to ``cap`` bytes: the "+N more" line, then lane events, then the
    standing binds. The designed-stop line (``Event: CONTEXT_BUDGET …``) carries
    the ide-hop command and is never dropped — a one-step that says "the
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


def _fit_cse(lines: list[str], cap: int) -> str:
    """Trim to ``cap`` bytes; drop ``Use the … skill`` lines only after events and standing."""
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
            if standing_at is not None and " · " in lines[standing_at]:
                lines[standing_at] = lines[standing_at].split(" · ", 1)[0]
            else:
                use_at = next(
                    (
                        i
                        for i in range(len(lines) - 1, -1, -1)
                        if lines[i].startswith("Use the ")
                        and lines[i].endswith(" skill")
                    ),
                    None,
                )
                if use_at is None:
                    break
                lines.pop(use_at)
        text = "\n".join(lines)
    return text


__all__ = [
    "INDUCTION_CAP",
    "build_wake_induction",
    "go_under_command",
    "ide_hop_command",
]
