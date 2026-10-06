"""Parse card, checkpoint residue, continuity, journal, runbook, scoreboard, consults."""

from __future__ import annotations

import re
from typing import Any

CP_TIP_LAST = 50
CP_PAGE_SIZE = 1000
CP_MAX_PAGES = 8

_RUNBOOK_URI_RE = re.compile(r"cortex://notes/runbooks/[^\s`]+\.md")
_HEADING_RE = re.compile(r"(?m)^## (.+?)\s*$")
_CP_STEP_RE = re.compile(r"(?m)^- \[( |x|~)\] (.+?)\s*$")
_NEXT_HEADING_RE = re.compile(r"(?m)^## Next\s*$")
_HANDOFF_NEXT_RE = re.compile(r"(?m)^- Next:\s*(.+?)\s*$")
_ENTRY_GATE_RE = re.compile(r"(?m)^(?:-\s*)?\*\*Entry gate:\*\*\s*(.+?)\s*$")
_NEXT_ADMIT_RE = re.compile(r"(?m)^(?:-\s*)?\*\*NEXT_ADMIT:\*\*\s*(.+?)\s*$")
_SCORE_ROW_RE = re.compile(r"(?m)^\| G\d+ \|")
_TODO_RE = re.compile(r"todo:[\w-]+")
_G_NUM_RE = re.compile(r"G(\d+)")


def is_checkpoint_subject(subject: str | None) -> bool:
    return str(subject or "").strip().upper().startswith("CHECKPOINT")


def pick_checkpoint_turn(turns: list[dict[str, Any]]) -> int | None:
    best: int | None = None
    for t in turns:
        if not is_checkpoint_subject(str(t.get("subject") or "")):
            continue
        n = int(t.get("turn_number") or 0)
        if best is None or n > best:
            best = n
    return best


def next_checkpoint_page(*, lowest_seen: int, page_size: int) -> tuple[int, int] | None:
    if lowest_seen <= 1:
        return None
    a = max(0, lowest_seen - 1 - page_size)
    length = lowest_seen - 1 - a
    return (a, length)


def _first_non_empty_line(text: str) -> str | None:
    for line in text.splitlines():
        s = line.strip()
        if s:
            return s
    return None


def is_consult_turn(turn: dict[str, Any]) -> bool:
    subject = str(turn.get("subject") or "")
    if subject.startswith("CONSULT_PENDING"):
        return True
    body = turn.get("body")
    if body is None:
        return False
    first = _first_non_empty_line(str(body))
    return first == "TYPE: CONSULT_PENDING"


def parse_consult_turn(turn: dict[str, Any]) -> dict[str, Any]:
    thread = str(turn.get("thread") or "")
    turn_n = int(turn.get("turn_number") or 0)
    subject = str(turn.get("subject") or "")
    body = str(turn.get("body") or "")
    m = _G_NUM_RE.search(subject)
    if not m:
        m = _G_NUM_RE.search(body)
    n = m.group(1) if m else "?"
    subject_prefix = f"HARVEST — G{n}"
    gate_unparsed = n == "?"
    out: dict[str, Any] = {
        "thread": thread,
        "turn": turn_n,
        "after_turn": turn_n,
        "created_at": turn.get("created_at"),
        "subject": subject,
        "subject_prefix": subject_prefix,
        "asks": [],
        "body_fields": ["RULING:", "NEXT_ADMIT:"],
        "send": {
            "thread": thread,
            "from_agent": "web-anthropic",
            "to": str(turn.get("from") or turn.get("from_agent") or ""),
            "after_turn": turn_n,
        },
    }
    if gate_unparsed:
        out["error"] = {"kind": "gate_unparsed", "message": "no G<n> in subject or body"}
    return out


def parse_card(body: str) -> dict[str, Any]:
    headings: list[dict[str, str]] = []
    matches = list(re.finditer(r"(?m)^## (.+?)\s*$", body))
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sect_body = body[start:end].strip()
        headings.append({"title": title, "body": sect_body})
    runbook_uris = _RUNBOOK_URI_RE.findall(body)
    return {"headings": headings, "runbook_uris": runbook_uris}


def parse_checkpoint_residue(body: str) -> dict[str, Any]:
    lines = body.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if line.startswith("## Residue (authored"):
            start = i
            break
    residue_lines = lines[start:]
    filtered: list[str] = []
    for line in residue_lines:
        if line.startswith("## Residue (authored"):
            continue
        if line.startswith("— RESUME"):
            continue
        if line.startswith("# ") and not line.startswith("## "):
            continue
        if line.strip().startswith("<!--"):
            continue
        filtered.append(line)
    residue = "\n".join(filtered)
    sections: dict[str, str] = {}
    current_title: str | None = None
    current_lines: list[str] = []
    for line in residue.splitlines():
        m = re.match(r"^## (.+?)\s*$", line)
        if m:
            if current_title is not None:
                sections[current_title] = "\n".join(current_lines).strip()
            current_title = m.group(1).strip()
            current_lines = []
        elif current_title is not None:
            current_lines.append(line)
    if current_title is not None:
        sections[current_title] = "\n".join(current_lines).strip()

    anchor = sections.get("Anchor") or None
    state = sections.get("State") or None
    steps: list[dict[str, str]] = []
    steps_body = sections.get("Steps", "")
    for m in _CP_STEP_RE.finditer(steps_body):
        steps.append({"mark": m.group(1), "text": m.group(2).strip()})

    next_val: str | None = None
    next_source: str | None = None
    if "Next" in sections:
        next_val = sections["Next"].strip() or None
        next_source = "heading"
    else:
        handoff = sections.get("Handoff pointer", "")
        hm = _HANDOFF_NEXT_RE.search(handoff)
        if hm:
            next_val = hm.group(1).strip()
            next_source = "handoff_bullet"

    skip = {"Anchor", "State", "Steps", "Next"}
    other_headings = [k for k in sections if k not in skip]

    return {
        "anchor": anchor,
        "state": state,
        "steps": steps,
        "next": next_val,
        "next_source": next_source,
        "other_headings": other_headings,
    }


def parse_continuity_current(text: str) -> dict[str, Any]:
    if "# Current" not in text:
        return {"error": {"kind": "continuity_current_missing", "message": "no # Current"}}
    start = text.index("# Current")
    rest = text[start + len("# Current") :]
    end_m = re.search(r"(?m)^# [^#]", rest)
    scope = rest[: end_m.start()] if end_m else rest
    sections: dict[str, str] = {}
    current_title: str | None = None
    buf: list[str] = []
    for line in scope.splitlines():
        m = re.match(r"^## (.+?)\s*$", line)
        if m:
            if current_title is not None:
                sections[current_title] = "\n".join(buf).strip()
            current_title = m.group(1).strip()
            buf = []
        elif current_title is not None:
            buf.append(line)
    if current_title is not None:
        sections[current_title] = "\n".join(buf).strip()

    settled = sections.get("Settled") or None
    live = sections.get("Live") or None

    latest_leg: dict[str, str] | None = None
    best_n = -1
    for title, body in sections.items():
        lm = re.match(r"^Leg (\d+)\b", title)
        if not lm:
            continue
        n = int(lm.group(1))
        if n >= best_n:
            best_n = n
            latest_leg = {"title": title, "body": body}

    next_pickup: list[str] = []
    np_body = sections.get("Next-pickup", "")
    for line in np_body.splitlines():
        if line.startswith("- "):
            next_pickup.append(line[2:].strip())
        elif line.strip() and next_pickup:
            next_pickup[-1] = next_pickup[-1] + "\n" + line.strip()

    return {
        "settled": settled,
        "live": live,
        "latest_leg": latest_leg,
        "next_pickup": next_pickup,
    }


def parse_journal_entries(text: str, *, limit: int) -> list[dict[str, str]]:
    entries: list[tuple[int, str, str]] = []
    for m in re.finditer(r"(?m)^## (.+?)\s*\n", text):
        title = m.group(1).strip()
        start = m.end()
        nxt = re.search(r"(?m)^## ", text[start:])
        body = text[start : start + nxt.start()] if nxt else text[start:]
        entries.append((m.start(), title, body.strip()))
    entries.sort(key=lambda x: x[0], reverse=True)
    out: list[dict[str, str]] = []
    for _, title, body in entries[:limit]:
        out.append({"title": title, "body": body})
    return out


def parse_runbook_steps(text: str) -> list[str]:
    if "**Steps**" not in text:
        return []
    chunk = text.split("**Steps**", 1)[1]
    if "**Falsifier:**" in chunk:
        chunk = chunk.split("**Falsifier:**", 1)[0]
    steps: list[str] = []
    for line in chunk.splitlines():
        s = line.strip()
        if re.match(r"^\d+\.", s):
            steps.append(s)
    return steps


def parse_scoreboard_tip(body: str, *, slug: str, discovered_by: str) -> dict[str, Any]:
    entry_gates = _ENTRY_GATE_RE.findall(body)
    entry_gate = entry_gates[0].strip() if entry_gates else None
    next_admits = _NEXT_ADMIT_RE.findall(body)
    next_admit = next_admits[-1].strip() if next_admits else None
    row_lines = [ln for ln in body.splitlines() if ln.strip().startswith("| G")]
    return {
        "slug": slug,
        "discovered_by": discovered_by,
        "entry_gate": entry_gate,
        "next_admit": next_admit,
        "rows": row_lines,
    }


def parse_relationship_todos(resp: dict[str, Any], *, root: str) -> list[str]:
    entity = f"document:{root}-continuity"
    slugs: list[str] = []
    for item in resp.get("items") or []:
        if not isinstance(item, dict):
            continue
        if item.get("source_id") != entity or item.get("type_id") != "related_to":
            continue
        tid = str(item.get("target_id") or "")
        if tid.startswith("todo:"):
            slugs.append(tid.removeprefix("todo:"))
    return slugs


def discover_scores_regex(*, objective: str, checkpoint_anchor: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _TODO_RE.finditer(objective + "\n" + checkpoint_anchor):
        slug = m.group(0).removeprefix("todo:")
        if slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out
