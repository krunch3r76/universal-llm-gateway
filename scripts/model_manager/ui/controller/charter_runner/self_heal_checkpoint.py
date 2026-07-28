"""Machine self-heal CHECKPOINT body builder + pickup round-trip check."""

from __future__ import annotations

from cortex_store.dispatch_ops._friction_enqueue import frictions_checkpoint_line

from .checkpoint_parse import ParsedCheckpoint, parse_checkpoint

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


def _steps_block(prior: ParsedCheckpoint) -> str:
    lines = []
    for s in prior.steps:
        glyph = _STATUS_GLYPH.get(s.status, " ")
        lines.append(f"{s.ordinal}. [{glyph}] {s.title}")
    return "\n".join(lines) or "1. [ ] (see scoreboard gated lane)"


def build_self_heal_checkpoint(
    *,
    prior: ParsedCheckpoint,
    window_index: int,
    worker_thread: str,
    reason: str,
    root_id: str,
    friction_id: int | None = None,
) -> tuple[str, str]:
    """Build (subject, body) for a machine recovery CHECKPOINT."""
    _ = root_id  # call-signature stable; reserved for future provenance lines
    pickup_lines = list(prior.next_pickup) or [
        "(re-queue prior gated step — see scoreboard)"
    ]
    pickup_block = "\n".join(f"- {item}" for item in pickup_lines)
    scoreboard = prior.scoreboard_uri
    sidecar_lines: list[str] = []
    if scoreboard:
        sidecar_lines.append(f"- {scoreboard}")
    if worker_thread:
        sidecar_lines.append(f"- agent-bus:{worker_thread} — prior window transcript")
    sidecars = "\n".join(sidecar_lines) if sidecar_lines else "_None this window._"
    scoreboard_section = (
        f"\n## Scoreboard URI\n{scoreboard}\n" if scoreboard else ""
    )
    blocked_line = (
        "BLOCKED — carried from prior CHECKPOINT (self-heal)."
        if prior.blocked
        else "None."
    )
    heal_note = {
        "checkpoint_missing": (
            "worker reported success-shaped closeout without posting a bound "
            "window terminal on this root"
        ),
        "dispatch_orphan": (
            "fleet dispatch never ran or left GIW without worker closeout"
        ),
    }.get(
        reason,
        "worker reported success-shaped closeout without posting a bound "
        "window terminal on this root",
    )
    plain_note = {
        "dispatch_orphan": (
            "The fleet slot was busy or the dispatch left GIW without a worker "
            "closeout, so the runner re-queued the prior gated pickup."
        ),
    }.get(
        reason,
        "The worker closed without posting a CHECKPOINT on the charter root, "
        "so the runner re-queued the prior gated pickup.",
    )
    if friction_id is not None:
        frictions_block = frictions_checkpoint_line(
            friction_id, category="protocol", note=heal_note
        )
    else:
        frictions_block = f"- Machine self-heal: {heal_note}."
    subject = f"CHECKPOINT — self-heal {reason} (window {window_index})"
    body = f"""# {subject}

## Anchor
- Author: charter-runner (machine self-heal — not an R12 worker CHECKPOINT)
- Scoreboard: {scoreboard or "(see prior CHECKPOINT / scoreboard)"}

## State
- Self-heal: {reason} — worker `{worker_thread or "(unknown)"}` closed without root terminal
- Window {window_index} incomplete; gated Next-pickup re-queued (not advanced)

## WIP / In-flight
_None this window._

## Next-pickup
{pickup_block}

## Steps
{_steps_block(prior)}

## Frictions
{frictions_block}

## What happened (plain)
{plain_note}

## Sidecars
{sidecars}

## BLOCKED
{blocked_line}
{scoreboard_section}
{_RESUME}
"""
    return subject, body


def pickup_survives_round_trip(
    prior: ParsedCheckpoint, body: str
) -> tuple[bool, list[str], list[str]]:
    """True when re-parse of ``body`` preserves prior gated pickup semantics."""
    echo = parse_checkpoint(body)
    want = list(prior.next_pickup)
    got = list(echo.next_pickup)
    if prior.next_pickup_gated and not echo.next_pickup_gated:
        return False, want, got
    if prior.blocked and not echo.blocked:
        return False, want, got
    if want and got != want:
        return False, want, got
    if not got and not echo.next_pickup_gated:
        return False, want, got
    return True, want, got


__all__ = [
    "build_self_heal_checkpoint",
    "pickup_survives_round_trip",
]
