"""What an unattended cursor-auto dispatch is allowed to spend.

Distinct from ``wire_map``'s capability clamp, which asks what a model *accepts*.
This module asks what the **autonomous lane** may commission when no operator is
in the loop to approve the bill.

The distinction matters because Auto POSTs the cursor-sdk worker directly, so
Stargate's ``sdk_cost_risk`` guard never sees these binds (see the comment at the
commissioning site in ``handler``). ``premium_bind`` announces them, but an
announcement is not a gate — until this module the orchestrator could bind a
premium reasoner at unbounded depth against a directive carrying no scope at all.

Three bounds, all encoding standing policy rather than new policy:

* **Executor** — ``bind-then-compose`` already says premium models bind and
  Composer implements. Mechanical work is therefore redirected onto the compose
  tier regardless of who was named, because once judgment has closed the edits
  are the same edits whoever makes them.
* **Scope** — ``NESTED_SCOPE_CONTRACTS`` already refuse a directive with no
  actionable scope, but a ``contract:`` override waives that refusal outright.
  The waiver stays for the roaming tier and is withdrawn for everything else, so
  a premium bind must arrive with scope the orchestrator actually bounded.
* **Effort** — ``lean-context-dispatch-first`` reserves ``xhigh``/``max`` for a
  standing trigger. The autonomous lane has none, so it stops one rung below.

Observed 2026-08-09 (24h): four autonomous ``xhigh`` Opus runs consumed 13.26M of
23.2M total Opus input tokens and two of the four failed to deliver, while
eighteen directives were admitted on a waived empty scope — eight of them
``implement``.
"""

from __future__ import annotations

from typing import Any

from cursor_capabilities import canonical_cursor_bare_id

from services.git_integration_worker.cursor_auto.wire_map import (
    BINDABLE_EFFORT_VALUES,
    resolve_desired_model,
)

# Roaming tier: cheap enough per agent step that an unbounded loop is affordable.
# Every other cursor model costs multiples per step for the same walk.
ROAMING_TIER_BARE_MODELS: frozenset[str] = frozenset(
    {"composer-2.5", "composer-2.5-fast", "grok-4.5"}
)

AUTONOMOUS_EFFORT_CEILING = "high"

# ``resolve_handoff_contract`` maps contract ``implement`` here and nothing else;
# this is the codebase's own name for work that carries no open judgment.
MECHANICAL_HANDOFF_CONTRACT = "pure-mechanical"

# The compose leg of bind-then-compose. Mechanical work lands here no matter who
# the orchestrator named.
MECHANICAL_EXECUTOR_MODEL_ID = "cursor/composer-2.5"


def is_roaming_tier(model_id: str | None) -> bool:
    """True when *model_id* is cheap enough to roam without a bounded scope."""
    try:
        return canonical_cursor_bare_id(str(model_id or "")) in ROAMING_TIER_BARE_MODELS
    except ValueError:
        return False


def scope_waiver_allowed(model_id: str | None) -> bool:
    """True when a ``contract:`` override may waive the empty-scope refusal.

    A waiver is a promise that the executor can find its own scope. That promise
    is affordable on the roaming tier and expensive everywhere else, because an
    unscoped premium bind pays the full context prime on every step of a walk
    nobody bounded.
    """
    return is_roaming_tier(model_id)


def clamp_effort_to_autonomous_ceiling(
    model_id: str | None,
    effort: dict[str, Any],
) -> dict[str, Any]:
    """Hold non-roaming models at :data:`AUTONOMOUS_EFFORT_CEILING`.

    Returns *effort* unchanged when it is already at or below the ceiling, so the
    common path stays identity. Mirrors ``resolve_desired_effort``'s dict shape
    (``requested`` / ``resolved_effort`` / ``clamped`` / ``notes``) so the admit
    turn reports the clamp in the field the operator already reads.
    """
    resolved = str(effort.get("resolved_effort") or "").strip().lower()
    ladder = BINDABLE_EFFORT_VALUES
    if is_roaming_tier(model_id) or resolved not in ladder:
        return effort
    if ladder.index(resolved) <= ladder.index(AUTONOMOUS_EFFORT_CEILING):
        return effort
    prior = str(effort.get("notes") or "").strip()
    note = (
        f"{resolved}→{AUTONOMOUS_EFFORT_CEILING} (autonomous lane ceiling; "
        "xhigh/max needs a standing trigger)"
    )
    return {
        **effort,
        "resolved_effort": AUTONOMOUS_EFFORT_CEILING,
        "clamped": True,
        "notes": f"{prior}; {note}" if prior else note,
    }


def redirect_mechanical_executor(
    model: dict[str, Any],
    *,
    contract: str,
    handoff_contract: str,
) -> tuple[dict[str, Any], str | None]:
    """Move mechanical work off a reasoning model onto the compose tier.

    A reasoning model never runs the mechanical leg: once judgment has closed,
    the remaining edits are the same edits whoever makes them, and the premium
    tier charges multiples per step for the identical walk. This is
    ``bind-then-compose`` stated as substrate instead of as guidance — the bind
    leg may be any model the orchestrator wants, the compose leg is Composer.

    Returns the model to actually dispatch plus the displaced model id, or
    ``None`` when nothing was displaced. Redirect rather than refuse because the
    work itself is legitimate; only the executor was wrong, and refusing would
    strand a bounded implement packet that has nothing left to decide.
    """
    if handoff_contract != MECHANICAL_HANDOFF_CONTRACT:
        return model, None
    requested_id = str(model.get("resolved_model_id") or "")
    if is_roaming_tier(requested_id):
        return model, None
    executor = resolve_desired_model(MECHANICAL_EXECUTOR_MODEL_ID, contract=contract)
    prior = str(model.get("notes") or "").strip()
    note = (
        f"mechanical contract — executor {requested_id or '(unset)'}"
        f"→{executor['resolved_model_id']} (reasoning tier binds, compose tier "
        "implements)"
    )
    executor["requested"] = model.get("requested")
    executor["honored"] = False
    executor["notes"] = f"{prior}; {note}" if prior else note
    return executor, requested_id


__all__ = [
    "AUTONOMOUS_EFFORT_CEILING",
    "MECHANICAL_EXECUTOR_MODEL_ID",
    "MECHANICAL_HANDOFF_CONTRACT",
    "ROAMING_TIER_BARE_MODELS",
    "clamp_effort_to_autonomous_ceiling",
    "is_roaming_tier",
    "redirect_mechanical_executor",
    "scope_waiver_allowed",
]
