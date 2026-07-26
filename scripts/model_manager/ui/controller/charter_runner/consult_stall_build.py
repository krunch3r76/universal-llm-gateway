"""Build and classify consult-stall recovery CHECKPOINTs.

Recovery orchestration stays separate so the ordering fence is easy to audit.
These builders preserve prior Steps: an R-ADMIT on the root unblocks the
consult seat, but a later charter window must disposition its merits.
"""

from __future__ import annotations

import re
from typing import Any

from .checkpoint_parse import ParsedCheckpoint, Step
from .self_heal import turn_number

R_ADMIT_SUBJECT_PREFIX = "R-ADMIT"
_STATUS_GLYPH = {
    "done": "x",
    "in_progress": "~",
    "blocked": "!",
    "pending": " ",
}
_RESUME = (
    "— RESUME (any seat, no command): load agent-bus-discipline "
    "(§ Standing root threads + § R12 completeness gate) → read scoreboard "
    "→ this is the latest CHECKPOINT. empty Next-pickup ≠ arc complete."
)


def r_admit_verdict(turn: dict[str, Any]) -> str | None:
    """Return the bound R-ADMIT verdict, with the compound token matched first."""
    text = f"{turn.get('subject') or ''}\n{turn.get('body') or ''}".upper()
    verdict_text = text.replace("R-ADMIT", "")
    if re.search(r"\bADMIT_WITH_AMENDMENTS\b", verdict_text):
        return "ADMIT_WITH_AMENDMENTS"
    if re.search(r"\bREJECT\b", verdict_text):
        return "REJECT"
    if re.search(r"\bADMIT\b", verdict_text):
        return "ADMIT"
    return None


def find_r_admit_after(
    turns: list[dict[str, Any]], after_n: int
) -> dict[str, Any] | None:
    """Return the latest post-admission R-ADMIT only when its verdict admits."""
    latest: dict[str, Any] | None = None
    latest_n = after_n
    prefix = R_ADMIT_SUBJECT_PREFIX.upper()
    for turn in turns:
        n = turn_number(turn)
        subject = str(turn.get("subject") or "").upper().strip()
        if n > latest_n and subject.startswith(prefix):
            latest, latest_n = turn, n
    if latest is None:
        return None
    verdict = r_admit_verdict(latest)
    return latest if verdict in {"ADMIT", "ADMIT_WITH_AMENDMENTS"} else None


def discover_child_refs(turns: list[dict[str, Any]], after_n: int) -> list[str]:
    """Preserve post-admission CDP/execution subjects as abandonment evidence."""
    refs: list[str] = []
    for turn in turns:
        if turn_number(turn) <= after_n:
            continue
        subject = str(turn.get("subject") or "").strip()
        lowered = subject.lower()
        if "cdp" not in lowered and "execution" not in lowered:
            continue
        ref = f"turn {turn_number(turn)} — {subject}"
        if ref not in refs:
            refs.append(ref)
    return refs


def _steps_block(steps: list[Step]) -> str:
    lines = []
    for step in steps:
        glyph = _STATUS_GLYPH.get(step.status, " ")
        lines.append(f"{step.ordinal}. [{glyph}] {step.title}")
    return "\n".join(lines) or "1. [ ] (see scoreboard gated lane)"


def _generation_marker(generation: int) -> str:
    return f"heal:consult_stall gen={generation}"


def build_r_admit_advance_checkpoint(
    *,
    prior: ParsedCheckpoint,
    window_index: int,
    worker_thread: str,
    r_admit_turn: dict[str, Any],
    generation: int,
    friction_id: int | None = None,
) -> tuple[str, str]:
    """Build an unblock CHECKPOINT that leaves merits disposition outstanding."""
    marker = _generation_marker(generation)
    scoreboard = prior.scoreboard_uri
    r_n = turn_number(r_admit_turn)
    r_subject = str(r_admit_turn.get("subject") or "")
    verdict = r_admit_verdict(r_admit_turn)
    pickup = (
        f"G3 — read R-ADMIT at turn {r_n} and disposition it "
        "(verdict/amendments) · consult_role cleared"
    )
    stall_note = "the consult worker was fenced after a quiet stale window"
    if friction_id is not None:
        from cortex_store.dispatch_ops._friction_enqueue import (
            frictions_checkpoint_line,
        )

        frictions_block = frictions_checkpoint_line(
            friction_id, category="protocol", note=stall_note
        )
    else:
        frictions_block = f"- Machine consult-stall: {stall_note}."
    subject = (
        f"CHECKPOINT — consult-stall r_admit_on_root (window {window_index}) · {marker}"
    )
    body = f"""# {subject}

## Anchor
- Author: charter-runner (machine consult-stall recovery — not an R12 worker CHECKPOINT)
- Scoreboard: {scoreboard or "(see prior CHECKPOINT / scoreboard)"}
- Generation: {marker}

## State
- Consult-stall: r_admit_on_root — R-ADMIT turn {r_n} landed while WIP window
  {window_index} (worker `{worker_thread}`) had no root terminal
- Prior consult directive cleared; R-ADMIT merits remain undispositioned

## Consult provenance (machine fold)
- consult_thread: root R-ADMIT turn {r_n}
- verdict: {verdict}
- consultant_family: anthropic
- consultant_substrate: web-anthropic
- R-ADMIT subject: {r_subject}

## WIP / In-flight
_None this window._

## Next-pickup
- {pickup}

## Steps
{_steps_block(prior.steps)}

## Frictions
{frictions_block}

## What happened (plain)
Machine consult-stall recovery fenced a quiet worker and re-queued the prior pickup.

## Sidecars
- {scoreboard or "_None this window._"}

## BLOCKED
_None this window._

## Scoreboard URI
{scoreboard or ""}

{_RESUME}
"""
    return subject, body


def build_consult_stall_requeue_checkpoint(
    *,
    prior: ParsedCheckpoint,
    window_index: int,
    worker_thread: str,
    child_refs: list[str],
    generation: int,
    friction_id: int | None = None,
) -> tuple[str, str]:
    """Build a generation-stamped requeue with explicit abandonment lineage."""
    marker = _generation_marker(generation)
    scoreboard = prior.scoreboard_uri
    pickup_lines = list(prior.next_pickup) or [
        "(re-queue prior gated step — see scoreboard)"
    ]
    pickup = "\n".join(f"- {item}" for item in pickup_lines)
    child_lines = (
        "\n".join(f"- {ref}" for ref in child_refs)
        if child_refs
        else "_None discovered._"
    )
    stall_note = "fenced the abandoned worker before re-queue"
    if friction_id is not None:
        from cortex_store.dispatch_ops._friction_enqueue import (
            frictions_checkpoint_line,
        )

        frictions_block = frictions_checkpoint_line(
            friction_id, category="protocol", note=stall_note
        )
    else:
        frictions_block = f"- Machine consult-stall: {stall_note}."
    subject = f"CHECKPOINT — consult-stall requeue (window {window_index}) · {marker}"
    body = f"""# {subject}

## Anchor
- Author: charter-runner (machine consult-stall recovery — not an R12 worker CHECKPOINT)
- Scoreboard: {scoreboard or "(see prior CHECKPOINT / scoreboard)"}
- Generation: {marker}

## State
- Consult-stall: no usable R-ADMIT after quiet stale window; prior pickup re-queued
- abandoned_worker: {worker_thread}
- supersedes_window: {window_index}
- supersedes:{window_index}

## Abandoned child refs
{child_lines}

## WIP / In-flight
_None this window._

## Next-pickup
{pickup}

## Steps
{_steps_block(prior.steps)}

## Frictions
{frictions_block}

## What happened (plain)
Machine consult-stall recovery fenced a quiet worker and re-queued the prior pickup.

## Sidecars
- {scoreboard or "_None this window._"}

## BLOCKED
{"BLOCKED — carried from prior CHECKPOINT." if prior.blocked else "None."}

## Scoreboard URI
{scoreboard or ""}

{_RESUME}
"""
    return subject, body


__all__ = [
    "R_ADMIT_SUBJECT_PREFIX",
    "build_consult_stall_requeue_checkpoint",
    "build_r_admit_advance_checkpoint",
    "discover_child_refs",
    "find_r_admit_after",
    "r_admit_verdict",
]
