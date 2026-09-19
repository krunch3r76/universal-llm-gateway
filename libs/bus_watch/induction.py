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

import re
import shlex
from pathlib import Path
from typing import Any

from claude_bundles.catalog import load_skill_catalog
from claude_bundles.cdp_inline_excerpt import _yaml_frontmatter_end, excerpt_skill_body

from bus_watch.doorbell_skills import navigator_doorbell_skills_from_policy, primary_liaison_slug
from bus_watch.friction_rows import event_line as _friction_event
from bus_watch.friction_rows import now_row as _friction_now
from bus_watch.goal_escalation import (
    classify_goal,
    format_dispatch_instruction,
)
from bus_watch.spawn_pending import attention_now_row
from bus_watch.spawn_wake.predicate import compute_spawn_signal_sources

INDUCTION_CAP = 700
# Per-skill excerpt budget for CSE ``<skills_inline>`` (full SOT stays on disk).
CSE_INLINE_SKILL_MAX_CHARS = 800
CSE_INLINE_TRUNCATION_MARKER = "full SOT not sealed"
# Distinctive SOT lines the successor must receive inside ``<skills_inline>`` (not via fetch).
_CSE_INLINE_ANCHORS: dict[str, str] = {
    "liaison": "harvests → folds → decides → dispatches → checkpoints → hops",
}
_INLINE_SLUG_RE = re.compile(r'<skill slug="([^"]+)"')
_EVENT_ITEMS = 3
_SUBJECT_CHARS = 56
_ONE_STEP = (
    "One step: harvest → fold → decide; quote evidence; end turn. "
    "Repo→cursor-auto · design→cdp/fable."
)
# 10479 tab 12e32c8b (2026-09-13 06:56Z) wrote "Next: R12 recon" and STAYed at
# 0.7 % of its window: a NOW row is the leg to dispatch this tick, not a note.
_NOW_STEP = "Dispatch NOW (repo→auto · design→fable); STAY iff empty/OPERATOR_GATE; end."
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


def _is_droppable_cse_skill_line(line: str) -> bool:
    """``Use the … skill`` lines trimmed before ``<skills_inline>`` bodies under cap."""
    return line.startswith("Use the ") and line.endswith(" skill")


def _cse_skill_excerpt(raw: str, slug: str, max_chars: int) -> str:
    """Size-gated excerpt for CSE induction — anchor line first, then operational prose."""
    text = raw if raw.endswith("\n") else f"{raw}\n"
    anchor = _CSE_INLINE_ANCHORS.get(slug)
    if anchor and anchor in text:
        idx = text.find(anchor)
        start = text.rfind("\n", 0, max(0, idx - 1)) + 1
        anchor_end = idx + len(anchor)
        if anchor_end < len(text) and text[anchor_end : anchor_end + 2] == "**":
            anchor_end += 2
        chunk = text[start:anchor_end].strip()
        if len(chunk) + 1 <= max_chars:
            return f"{chunk}\n"
        return excerpt_skill_body(f"{chunk}\n", slug=slug, max_chars=max_chars)
    fm_end = _yaml_frontmatter_end(text)
    body = text[fm_end:] if fm_end else text
    return excerpt_skill_body(body, slug=slug, max_chars=max_chars)


def _anchor_line_budget(raw: str, slug: str) -> int:
    """Minimum ``max_chars`` so an anchored slug's witness line survives excerpting."""
    anchor = _CSE_INLINE_ANCHORS.get(slug)
    if not anchor or anchor not in raw:
        return 64
    idx = raw.find(anchor)
    start = raw.rfind("\n", 0, max(0, idx - 1)) + 1
    anchor_end = idx + len(anchor)
    if anchor_end < len(raw) and raw[anchor_end : anchor_end + 2] == "**":
        anchor_end += 2
    return len(raw[start:anchor_end].strip()) + 8


def _render_cse_skills_inline(
    inline_slugs: list[str],
    *,
    repo_root: Path,
    max_chars_per_skill: int = CSE_INLINE_SKILL_MAX_CHARS,
    per_slug_budget: dict[str, int] | None = None,
) -> str:
    """Compact ``<skills_inline>`` block — SOT bodies, not ``fs`` pointers."""
    if not inline_slugs:
        return ""
    catalog = load_skill_catalog(validate_sot=False)
    parts = ["<skills_inline>"]
    for slug in inline_slugs:
        entry = catalog.get(slug)
        sot, _ = catalog.resolve_sot(slug, repo_root)
        raw = sot.read_text(encoding="utf-8")
        budget = (per_slug_budget or {}).get(slug, max_chars_per_skill)
        body = _cse_skill_excerpt(raw, slug=slug, max_chars=budget)
        parts.append(f'<skill slug="{slug}" surface_class="{entry.surface_class}">')
        parts.append(body.rstrip())
        parts.append("</skill>")
    parts.append("</skills_inline>")
    return "\n".join(parts)


def _inline_slugs_from_block(block: str) -> list[str]:
    return _INLINE_SLUG_RE.findall(block)


def _cse_skill_activation_lines(loaded: list[str]) -> list[str]:
    """Route ``loaded`` through ``partition_cdp_skills`` before emitting CSE delivery.

    ``shared_sync`` slugs keep the Customize self-fetch ``Use the … skill`` line.
    Every other catalog class (``cursor_only`` included) seals SOT **bodies** inside
    ``<skills_inline>`` — the successor must not spend a turn fetching what induction
    claimed it already held.
    """
    slugs = [s for s in (_skill_slug(label) for label in loaded) if s]
    unique = list(dict.fromkeys(slugs))
    if not unique:
        return []
    catalog = load_skill_catalog(validate_sot=False)
    repo_root = Path(__file__).resolve().parents[2]
    slash: list[str] = []
    inline: list[str] = []
    seen: set[str] = set()
    for slug in unique:
        entry = catalog.get(slug)
        if entry.slug in seen:
            continue
        seen.add(entry.slug)
        if entry.surface_class == "shared_sync":
            slash.append(entry.slug)
        else:
            inline.append(entry.slug)
    slash_set = set(slash)
    inline_set = set(inline)
    lines: list[str] = []
    for slug in unique:
        if slug in slash_set:
            lines.append(f"Use the {slug} skill")
    if inline_set:
        ordered_inline = [slug for slug in unique if slug in inline_set]
        lines.append(_render_cse_skills_inline(ordered_inline, repo_root=repo_root))
    return lines


def _resolve_now_row(digest: dict[str, Any]) -> tuple[str, str]:
    """Seat bind ≻ friction ≻ summary_row ≻ attention child ≻ empty (11693#4).

    Friction outranks ``summary_row`` so a stale tick bind cannot mask an
    undispositioned row; ``summary_row`` prose is never echoed verbatim on
    the NOW line (see ``_format_now_line``). When those are empty, the newest
    non-terminal commissioned ``sub_mission`` in attention becomes NOW.
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
    attention_now = attention_now_row(digest)
    if attention_now:
        return attention_now, "attention"
    return "", "empty"


def _format_now_line(digest: dict[str, Any], now_row: str, *, source: str) -> str:
    """Pointers not prose — tip turn + handoff, not stale summary_row (§7)."""
    if source in ("friction", "attention"):
        return str(now_row or "").strip()
    root = digest.get("root") or {}
    root_id = root.get("id")
    tip = root.get("turns")
    if source == "summary_row":
        if tip is not None and root_id:
            handoff = str(root.get("last_subject") or "").strip()
            if handoff:
                return f"tip turn #{tip} on agent-bus:{root_id} · «{handoff[:_SUBJECT_CHARS]}»"
            return f"tip turn #{tip} on agent-bus:{root_id}"
        return ""
    body = str(now_row or "").strip()
    if len(body) > 120:
        body = body[:117].rstrip() + "…"
    if tip is not None and root_id:
        return f"tip turn #{tip} on agent-bus:{root_id} · {body}"
    return body


def build_wake_induction(
    digest: dict[str, Any],
    *,
    cap: int = INDUCTION_CAP,
    surface: str = "ide",
    state: dict[str, Any] | None = None,
    lock: dict[str, Any] | None = None,
) -> str:
    """Render the wake as a short planted-address block (≤ ``cap`` bytes).

    M1 plant addresses, not assertions · M2 episodic frame ("Event:") · M4 no
    costume · M5 front-load, never accumulate. ``policy.induction_loaded`` lists
    what the seat already holds and must not re-read; ``policy.induction_binds``
    carries standing operator binds (e.g. "hopper paused (10479#210)").

    ``surface='cse'`` routes each loaded slug through ``partition_cdp_skills``:
    ``shared_sync`` → ``Use the <slug> skill``; ``cursor_only`` (etc.) → SOT body
    inside ``<skills_inline>``. ``surface='ide'`` keeps the legacy single
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
    goal_kind = (
        classify_goal(now_row)
        if now_row and now_source in ("policy", "friction", "attention")
        else None
    )
    st = state if state is not None else {}
    lock_now = lock if lock is not None else digest.get("fable_lock") or {}
    spawn_sources = compute_spawn_signal_sources(digest, st, lock=lock_now)
    digest = {**digest, "spawn_signal_sources": spawn_sources}
    if spawn_sources:
        events = [f"wake {','.join(spawn_sources)}", *events]
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
    nav_labels = [
        f"{slug} skill" for slug in navigator_doorbell_skills_from_policy(policy)
    ]
    loaded = list(dict.fromkeys([*nav_labels, *_listed(policy, "induction_loaded")]))
    if surface == "cse":
        liaison_label = f"{primary_liaison_slug('ide')} skill"
        loaded = list(dict.fromkeys([liaison_label, *loaded]))
    if surface == "cse":
        lines.extend(_cse_skill_activation_lines(loaded))
    else:
        lines.append("Loaded already (do not re-read): " + " · ".join(loaded))
    standing = [
        f"register={digest.get('register')}",
        *_listed(policy, "induction_binds"),
    ]
    lines.append("Standing: " + " · ".join(standing))
    if _tab_at_budget(digest):
        lines.append(_HOP_STEP)
    elif goal_kind and now_row and root_id:
        lines.append(
            "Dispatch: " + format_dispatch_instruction(goal_kind, now_row, root_id=str(root_id))
        )
        lines.append(_NOW_STEP)
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
                    and not lines[i].startswith("Event: wake")
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


def _resize_cse_skills_inline(lines: list[str], cap: int) -> bool:
    """Re-excerpt ``<skills_inline>`` to the largest bodies that still fit ``cap``."""
    inline_at = next(
        (i for i, ln in enumerate(lines) if ln.startswith("<skills_inline>")), None
    )
    if inline_at is None:
        return False
    repo_root = Path(__file__).resolve().parents[2]
    slugs = _inline_slugs_from_block(lines[inline_at])
    if not slugs:
        return False
    prefix = "\n".join(lines[:inline_at])
    suffix = "\n".join(lines[inline_at + 1 :])
    overhead = len(prefix.encode("utf-8")) + len(suffix.encode("utf-8"))
    if prefix and suffix:
        overhead += 2  # newlines joining prefix, block, suffix
    elif prefix or suffix:
        overhead += 1
    catalog = load_skill_catalog(validate_sot=False)
    fixed: dict[str, int] = {}
    for slug in slugs:
        if slug in _CSE_INLINE_ANCHORS:
            sot, _ = catalog.resolve_sot(slug, repo_root)
            fixed[slug] = _anchor_line_budget(sot.read_text(encoding="utf-8"), slug)
    variable = [slug for slug in slugs if slug not in fixed]
    per_slug_min = {**fixed, **{slug: 64 for slug in variable}}
    block_min = _render_cse_skills_inline(
        slugs, repo_root=repo_root, per_slug_budget=per_slug_min
    )
    min_block_len = len(block_min.encode("utf-8"))
    if overhead + min_block_len <= cap:
        slack = cap - overhead - min_block_len
        if slack < 80:
            lines[inline_at] = block_min
            return True
    lo, hi = 64, CSE_INLINE_SKILL_MAX_CHARS
    best: str | None = None
    while lo <= hi:
        mid = (lo + hi) // 2
        per_slug = {**fixed, **{slug: mid for slug in variable}}
        block = _render_cse_skills_inline(
            slugs,
            repo_root=repo_root,
            per_slug_budget=per_slug,
        )
        if overhead + len(block.encode("utf-8")) <= cap:
            best = block
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        if overhead + len(block_min.encode("utf-8")) > cap:
            return False
        best = block_min
    lines[inline_at] = best
    return True


def _fit_cse(lines: list[str], cap: int) -> str:
    """Trim to ``cap`` bytes; drop ``Use the … skill`` before ``<skills_inline>`` bodies."""
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
                    and not lines[i].startswith("Event: wake")
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
                wake_at = next(
                    (i for i, ln in enumerate(lines) if ln.startswith("Event: wake ")),
                    None,
                )
                if wake_at is not None:
                    lines.pop(wake_at)
                elif _resize_cse_skills_inline(lines, cap):
                    pass
                else:
                    use_at = next(
                        (
                            i
                            for i in range(len(lines) - 1, -1, -1)
                            if _is_droppable_cse_skill_line(lines[i])
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
