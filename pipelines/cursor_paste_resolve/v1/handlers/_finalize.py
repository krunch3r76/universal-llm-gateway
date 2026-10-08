"""Finalize ownership option for cursor-paste-resolve compose (a:38698).

``options.finalize`` selects who restarts and closes after the executor lands.
``""`` and ``"dispatchee"`` keep today's bytes (the executor finalizes).
``"dispatcher"`` appends a plain, untagged constraints block last so the
dispatching seat owns restart + friction_close.
"""

from __future__ import annotations

from typing import Any

FINALIZE_DISPATCHEE = "dispatchee"
FINALIZE_DISPATCHER = "dispatcher"
FINALIZE_VALUES = frozenset({"", FINALIZE_DISPATCHEE, FINALIZE_DISPATCHER})
DISPATCHER_LAUNCH_TARGETS = frozenset({"cursor_sdk", ""})


def parse_finalize(raw: Any, launch_target: str) -> str | tuple[str]:
    """Return the bound finalize value, or ``(error,)`` on refuse.

    Unknown values refuse (no silent fallback). ``dispatcher`` refuses on
    Glass/IDE launches; compose-only (empty launch_target) is allowed.
    """
    value = "" if raw is None else str(raw).strip()
    if value not in FINALIZE_VALUES:
        return (
            f"unknown finalize value {value!r}; allowed: "
            f"{FINALIZE_DISPATCHEE}, {FINALIZE_DISPATCHER}",
        )
    target = (launch_target or "").strip()
    if value == FINALIZE_DISPATCHER and target not in DISPATCHER_LAUNCH_TARGETS:
        return (
            "finalize=dispatcher applies to cursor_sdk launches only "
            f"(got launch_target={target})",
        )
    return value


def dispatcher_finalize_suffix(kind: str, assertion_id: int) -> str:
    """Locked v1 block (plan D4). Plain H2, never wrapped in a data tag."""
    return (
        "\n"
        "## Finalize ownership: dispatcher (overrides earlier steps)\n"
        "\n"
        "The seat that dispatched you finalizes this work item. These "
        'constraints supersede implementer steps 6–7, the "Mid-fix land / '
        'go-live" constraint, the maestro closure-memo paragraph, and '
        "restart-drain's needed(restart) ⇒ fire(restart) for this dispatch:\n"
        "\n"
        "1. Do not restart, stop, start, sync_restart, rebuild or recycle any "
        "service (manage lifecycle actions, recycle_giw, "
        "charter_reload/charter_pause). Do not arm or cancel a restart intent. "
        "Read-only manage probes (status, health, busy_status, "
        "restart_intent_status) are allowed.\n"
        "2. Do not call friction_close, and do not supersede or resolve "
        f"{kind}:{assertion_id} by any other tool. Filing unapplied review "
        "proposals as category=feature frictions (step 6) still applies.\n"
        "3. Do not post on agent-bus thread 12286. The dispatching seat sends "
        "the closure memo.\n"
        "4. Land as step 7 says (merge into master), then post one closeout on "
        "your dispatch thread with: land_sha (master after merge), changed "
        "paths, importer services (each service that loads a changed path and "
        'needs a restart to serve it, or "none"), the review thread id, and '
        "the filed proposal ids. Then stop. Do not wait for, probe, or claim "
        "liveness.\n"
    )
