"""Classify tab-launch WORK prompts for orchestrator handoff envelopes.

Swarm prompts require Multitask + parallel lead-spawned Task(Grok) workers.
Implement prompts are single-clone hops with optional **sequential** Task legs
(Composer / Grok — one subagent at a time, never parallel). Audit prompts are
bounded evaluate-only slices.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]


class WorkClass(str, Enum):
    SWARM = "swarm"
    IMPLEMENT = "implement"
    MECHANICAL = "mechanical"
    AUDIT = "audit"


class AuthorityHeaderKind(str, Enum):
    """Status vocabulary for session-effectiveness / resume-stack WORK drafts."""

    PROPOSED_ADMITTED = "proposed(admitted_with_amendments)"
    RATIFIED = "ratified"
    BOUND = "bound"
    INTERIM = "interim"


_AUTHORITY_HEADER_DOC = """\
When citing session-effectiveness or resume-stack binds in a WORK draft header, use:

```
{example}
```

Kinds: `proposed(admitted_with_amendments)` · `ratified(by=…)` · `bound(by=…)` · `interim`
"""


def format_authority_header(
    kind: AuthorityHeaderKind | str,
    *,
    by: str | None = None,
    ref: str | None = None,
    amendments: str | None = None,
) -> str:
    """Render a single-line authority header for WORK prompt minting."""

    label = kind.value if isinstance(kind, AuthorityHeaderKind) else str(kind).strip()
    extras: list[str] = []
    if by:
        extras.append(f"by={by}")
    if ref:
        extras.append(f"ref={ref}")
    if amendments:
        extras.append(f"amendments={amendments}")
    if not extras:
        return label
    inner = ", ".join(extras)
    if label.endswith(")"):
        return f"{label[:-1]}, {inner})"
    return f"{label}({inner})"


def authority_header_template_block(
    *,
    kind: AuthorityHeaderKind = AuthorityHeaderKind.PROPOSED_ADMITTED,
    by: str | None = None,
    ref: str | None = None,
    amendments: str | None = None,
) -> str:
    """Documented template block operators paste into tab-launch WORK drafts."""

    example = format_authority_header(kind, by=by, ref=ref, amendments=amendments)
    return _AUTHORITY_HEADER_DOC.format(example=example)


_SWARM_TEXT_MARKERS = (
    "enable multitask mode",
    "work swarm",
    "subagent speed",
    "parallel grok",
    "w1 grok",
    "task(model=",
    "task(subagent_type=",
    "session lead (this tab",
)

_MECHANICAL_TEXT_MARKERS = (
    "multitask off",
    "single lead, mechanical",
    "mechanical implement",
    "mechanical only",
)

_AUDIT_TEXT_MARKERS = (
    "evaluate-only",
    "audit-only",
    "verify-only",
    "contract: verify",
    "contract` | `verify`",
)

_AUDIT_FILENAME_MARKERS = (
    "-quality-audit-",
    "-consolidation-audit-",
)


def _is_audit_prompt(name: str, lower: str) -> bool:
    """Audit wins over swarm headers (e.g. Multitask boilerplate on verify slices)."""

    if any(m in name for m in _AUDIT_FILENAME_MARKERS):
        return True
    if name.startswith("tab-launch-work-") and "audit" in name:
        return True
    if re.search(r"contract[^|\n`]*verify", lower):
        return True
    if any(m in lower for m in _AUDIT_TEXT_MARKERS):
        return True
    return False


def classify_work_prompt(path: Path | str) -> WorkClass:
    """Return work class from prompt body + filename heuristics."""

    p = Path(path)
    if not p.is_file():
        p = _REPO / path
    if not p.is_file():
        return WorkClass.MECHANICAL

    name = p.name.lower()
    text = p.read_text(encoding="utf-8", errors="replace")
    lower = text.lower()

    if _is_audit_prompt(name, lower):
        return WorkClass.AUDIT

    if "work-swarm" in name or "-swarm-" in name:
        return WorkClass.SWARM
    if any(m in lower for m in _SWARM_TEXT_MARKERS):
        return WorkClass.SWARM

    if any(m in lower for m in _MECHANICAL_TEXT_MARKERS):
        return WorkClass.MECHANICAL

    if name.startswith("tab-launch-work-"):
        return WorkClass.IMPLEMENT

    return WorkClass.MECHANICAL


def work_execution_lines(
    *,
    work_class: WorkClass,
    work_prompt: Path | None,
) -> list[str]:
    """Handoff envelope step 1 (and swarm step 0) for orchestrator tabs."""

    work_ref = f"`{work_prompt}`" if work_prompt else "the bound WORK prompt"
    lines: list[str] = []

    if work_class == WorkClass.SWARM:
        lines.append(
            "**Multitask:** enable Multitask Mode in this tab before executing WORK."
        )
        lines.append(
            f"1. Read and execute: {work_ref} — WORK **session lead in-seat**. "
            "Spawn W* / slice legs via "
            "`Task(subagent_type=generalPurpose, model=cursor/grok-4.6-xhigh, run_in_background=true)` "
            "per the prompt dependency table. **Only the lead** merges, commits, recycles, "
            "and posts CLOSEOUT. Inject task-subagent-parity kernel into every Task prompt."
        )
        lines.append(
            "   Forbidden on this tab: `team_dispatch` · expecting Multitask to auto-spawn workers · "
            "posts on agent-bus:10223 except the required CHECKPOINT."
        )
    elif work_class == WorkClass.AUDIT:
        lines.append(
            f"1. Read and execute: {work_ref} — **evaluate/audit in-seat** "
            "(Multitask OFF; no Task subagents). "
            "Quote live probes; implement only when prompt ACs require code changes."
        )
    elif work_class == WorkClass.IMPLEMENT:
        lines.append(
            f"1. Read and execute: {work_ref} — **session lead in-seat** "
            "(Multitask OFF; **one clone, sequential subagents only**)."
        )
        lines.append(
            "   Heavy / W* slices: spawn **one** `Task` at a time — wait for terminal "
            "result before the next. Mechanical legs: "
            "`Task(subagent_type=generalPurpose, model=composer-2.5)` (or omit `model=`). "
            "Reasoning / review legs: "
            "`Task(subagent_type=generalPurpose, model=cursor/grok-4.6-xhigh)` — "
            "**`run_in_background=false`** always on hop tabs."
        )
        lines.append(
            "   Only the lead merges, commits, recycles, and posts CLOSEOUT. "
            "Inject task-subagent-parity kernel into every Task prompt. "
            "Forbidden: parallel Task · Multitask Mode · `team_dispatch` unless WORK names it."
        )
    else:
        lines.append(
            f"1. Read and execute: {work_ref} — WORK **in-seat**, mechanical single-lead "
            "(Multitask OFF; no Task subagent unless prompt explicitly names Task)."
        )

    lines.append(
        '   WORK-prompt "no posts on 10223" binds WORK content only; CHECKPOINT step still required.'
    )
    return lines


def summarize_work_prompt(path: Path | str) -> dict[str, Any]:
    """Metadata for queue rows and heartbeat ticks."""

    p = Path(path)
    if not p.is_file():
        p = _REPO / path
    wc = classify_work_prompt(p)
    rel = str(p)
    try:
        rel = str(p.relative_to(_REPO))
    except ValueError:
        pass
    return {
        "work_class": wc.value,
        "work_prompt": rel,
        "prompt_file": p.name if p.is_file() else Path(path).name,
        "exists": p.is_file(),
    }
